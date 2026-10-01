import os
import time
import json
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SPOT_URL = "https://data-api.binance.vision"
FUTURES_URL = "https://fapi.binance.com"

THRESHOLD = 23.0
WINDOW_CANDLES = 25
STATE_FILE = "alerts.json"

MAX_WORKERS = 20

HEADERS = {"User-Agent": "TheKingdom/1.0"}


def api_get(base_url, path, params=None):
    url = base_url + path

    for attempt in range(3):
        try:
            r = requests.get(
                url, params=params, headers=HEADERS, timeout=20
            )

            if r.status_code == 200:
                return r.json()

            if r.status_code in (429, 418):
                wait = int(r.headers.get("Retry-After", "10"))
                print(f"Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue

            if r.status_code == 451:
                print(f"Blocked (451, region restricted): {url}")
                return None

            print(f"API error {r.status_code}: {url}")
            return None

        except Exception as e:
            print(f"Request error: {e}")
            time.sleep(2)

    return None


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    r = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": message},
        timeout=20,
    )
    r.raise_for_status()

    result = r.json()
    if not result.get("ok"):
        raise Exception(result.get("description", "Telegram API error"))

    return result


def load_state():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        return set(data.get("alerted", []))
    except Exception:
        return set()


def save_state(alerted):
    with open(STATE_FILE, "w") as f:
        json.dump({"alerted": sorted(alerted)}, f, indent=2)


def get_spot_symbols():
    data = api_get(SPOT_URL, "/api/v3/exchangeInfo")

    if not data:
        raise Exception("Could not get Binance spot exchange information.")

    return [
        s["symbol"]
        for s in data["symbols"]
        if s["status"] == "TRADING"
        and s["quoteAsset"] == "USDT"
        and s["isSpotTradingAllowed"]
    ]


def get_futures_symbols():
    # USDT-M perpetual only (TradingView style: ARKUSDT.P)
    data = api_get(FUTURES_URL, "/fapi/v1/exchangeInfo")

    if not data:
        print("WARNING: Could not get futures exchange information.")
        return []

    return [
        s["symbol"]
        for s in data["symbols"]
        if s["status"] == "TRADING"
        and s["quoteAsset"] == "USDT"
        and s["contractType"] == "PERPETUAL"
    ]


def build_targets():
    """
    Returns list of (key, symbol, market).
    key = state/alert name: ARKUSDT (spot) or ARKUSDT.P (futures)
    Futures coins that already exist on spot are skipped (no duplicates).
    """
    spot = get_spot_symbols()
    futures = get_futures_symbols()

    spot_set = set(spot)

    targets = [(s, s, "SPOT") for s in spot]

    futures_only = 0
    for s in futures:
        if s in spot_set:
            continue
        targets.append((f"{s}.P", s, "FUTURES"))
        futures_only += 1

    print(f"Spot pairs: {len(spot)}")
    print(f"Futures-only pairs: {futures_only}")

    return targets


def check_coin(key, symbol, market):
    if market == "SPOT":
        base, path = SPOT_URL, "/api/v3/klines"
    else:
        base, path = FUTURES_URL, "/fapi/v1/klines"

    candles = api_get(
        base,
        path,
        {
            "symbol": symbol,
            "interval": "5m",
            "limit": WINDOW_CANDLES,
        },
    )

    if not candles or len(candles) < 20:
        return None

    start_price = float(candles[0][1])
    highest_price = max(float(c[2]) for c in candles)
    current_price = float(candles[-1][4])

    change = ((highest_price - start_price) / start_price) * 100

    return (key, market, change, current_price, highest_price, start_price)


def main():
    print("================================")
    print("The Kingdom scanner started")
    print(f"Threshold: +{THRESHOLD}%")
    print("Window: 2 hours")
    print("Markets: Spot + USDT-M Futures")
    print(f"Parallel workers: {MAX_WORKERS}")
    print("================================")

    start_time = time.time()

    targets = build_targets()
    total = len(targets)

    print(f"Total to scan: {total}")

    alerted = load_state()
    new_alerts = []
    completed = 0

    print("Starting parallel scan...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(check_coin, key, symbol, market): key
            for (key, symbol, market) in targets
        }

        for future in as_completed(futures):
            key = futures[future]
            completed += 1

            try:
                result = future.result()

                if result is not None:
                    (
                        key,
                        market,
                        change,
                        current_price,
                        highest_price,
                        start_price,
                    ) = result

                    if change >= THRESHOLD:
                        if key not in alerted:
                            new_alerts.append(
                                (
                                    key,
                                    market,
                                    change,
                                    current_price,
                                    highest_price,
                                    start_price,
                                )
                            )
                            alerted.add(key)
                            print(f"NEW ALERT: {key} +{change:.2f}%")
                    else:
                        if key in alerted:
                            alerted.remove(key)
                            print(f"RESET: {key}")

            except Exception as e:
                print(f"{key}: {e}")

            if completed % 100 == 0 or completed == total:
                print(f"Checked {completed}/{total}")

    save_state(alerted)

    elapsed = time.time() - start_time

    print("================================")
    print(f"New alerts: {len(new_alerts)}")
    print(f"Scan time: {elapsed:.1f} seconds")
    print("Scan completed.")
    print("================================")

    for (
        key,
        market,
        change,
        current_price,
        highest_price,
        start_price,
    ) in new_alerts:

        message = (
            "⚡ Market Alert\n\n"
            f"🪙 {key}\n"
            f"🏷 Market: {market}\n"
            f"📈 2H Move: +{change:.2f}%\n"
            f"💰 Current: {current_price}\n"
            f"🔥 2H High: {highest_price}\n"
            f"📍 2H Start: {start_price}"
        )

        try:
            send_telegram(message)
            print(f"Telegram sent: {key}")
        except Exception as e:
            print(f"Telegram error: {e}")


if __name__ == "__main__":
    main()
