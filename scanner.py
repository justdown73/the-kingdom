import os
import time
import json
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

BINANCE_URL = "https://data-api.binance.vision"

THRESHOLD = 23.0
WINDOW_CANDLES = 25
STATE_FILE = "alerts.json"

# Safe parallel workers
MAX_WORKERS = 20

HEADERS = {
    "User-Agent": "TheKingdom/1.0"
}


def api_get(path, params=None):
    url = BINANCE_URL + path

    for attempt in range(3):

        try:

            r = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=20
            )

            if r.status_code == 200:
                return r.json()

            if r.status_code in (429, 418):

                wait = int(
                    r.headers.get(
                        "Retry-After",
                        "10"
                    )
                )

                print(
                    f"Rate limited. "
                    f"Waiting {wait}s..."
                )

                time.sleep(wait)
                continue

            print(
                f"API error {r.status_code}: "
                f"{url}"
            )

            return None

        except Exception as e:

            print(
                f"Request error: {e}"
            )

            time.sleep(2)

    return None


def send_telegram(message):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    r = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )

    r.raise_for_status()

    result = r.json()

    if not result.get("ok"):

        raise Exception(
            result.get(
                "description",
                "Telegram API error"
            )
        )

    return result


def load_state():

    if not os.path.exists(STATE_FILE):
        return set()

    try:

        with open(
            STATE_FILE,
            "r"
        ) as f:

            data = json.load(f)

        return set(
            data.get(
                "alerted",
                []
            )
        )

    except Exception:

        return set()


def save_state(alerted):

    with open(
        STATE_FILE,
        "w"
    ) as f:

        json.dump(
            {
                "alerted": sorted(
                    alerted
                )
            },
            f,
            indent=2
        )


def get_symbols():

    data = api_get(
        "/api/v3/exchangeInfo"
    )

    if not data:

        raise Exception(
            "Could not get Binance exchange information."
        )

    symbols = []

    for s in data["symbols"]:

        if (
            s["status"] == "TRADING"
            and s["quoteAsset"] == "USDT"
            and s["isSpotTradingAllowed"]
        ):

            symbols.append(
                s["symbol"]
            )

    return symbols


def check_coin(symbol):

    candles = api_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": "5m",
            "limit": WINDOW_CANDLES
        }
    )

    if not candles:
        return None

    if len(candles) < 20:
        return None

    # Price at the beginning of
    # approximately the last 2 hours
    start_price = float(
        candles[0][1]
    )

    # Highest price touched during
    # the 2-hour window
    highest_price = max(
        float(candle[2])
        for candle in candles
    )

    # Latest candle close
    current_price = float(
        candles[-1][4]
    )

    # Maximum movement from
    # the beginning of the window
    change = (
        (highest_price - start_price)
        / start_price
    ) * 100

    return (
        symbol,
        change,
        current_price,
        highest_price,
        start_price
    )


def main():

    print("================================")
    print("The Kingdom scanner started")
    print("Threshold: +23%")
    print("Window: 2 hours")
    print(
        f"Parallel workers: {MAX_WORKERS}"
    )
    print("================================")

    start_time = time.time()

    symbols = get_symbols()

    print(
        f"Found {len(symbols)} "
        f"USDT spot pairs."
    )

    alerted = load_state()

    new_alerts = []

    completed = 0

    # --------------------------------
    # PARALLEL SCANNING
    # --------------------------------

    print(
        "Starting parallel scan..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                check_coin,
                symbol
            ): symbol
            for symbol in symbols
        }

        for future in as_completed(
            futures
        ):

            symbol = futures[
                future
            ]

            completed += 1

            try:

                result = future.result()

                if result is None:
                    continue

                (
                    symbol,
                    change,
                    current_price,
                    highest_price,
                    start_price
                ) = result

                # --------------------------------
                # NEW +23% ALERT
                # --------------------------------

                if change >= THRESHOLD:

                    if symbol not in alerted:

                        new_alerts.append(
                            (
                                symbol,
                                change,
                                current_price,
                                highest_price,
                                start_price
                            )
                        )

                        alerted.add(
                            symbol
                        )

                        print(
                            f"NEW ALERT: "
                            f"{symbol} "
                            f"+{change:.2f}%"
                        )

                # --------------------------------
                # RESET
                # --------------------------------

                else:

                    if symbol in alerted:

                        alerted.remove(
                            symbol
                        )

                        print(
                            f"RESET: {symbol}"
                        )

            except Exception as e:

                print(
                    f"{symbol}: {e}"
                )

            # Progress
            if (
                completed % 100 == 0
                or completed == len(symbols)
            ):

                print(
                    f"Checked "
                    f"{completed}/"
                    f"{len(symbols)}"
                )

    # --------------------------------
    # SAVE STATE
    # --------------------------------

    save_state(
        alerted
    )

    elapsed = time.time() - start_time

    print("================================")

    print(
        f"New alerts: "
        f"{len(new_alerts)}"
    )

    print(
        f"Scan time: "
        f"{elapsed:.1f} seconds"
    )

    print(
        "Scan completed."
    )

    print("================================")

    # --------------------------------
    # SEND TELEGRAM ALERTS
    # --------------------------------

    for (
        symbol,
        change,
        current_price,
        highest_price,
        start_price
    ) in new_alerts:

        message = (
            "⚡ Market Alert\n\n"
            f"🪙 {symbol}\n"
            f"📈 2H Move: +{change:.2f}%\n"
            f"💰 Current: {current_price}\n"
            f"🔥 2H High: {highest_price}\n"
            f"📍 2H Start: {start_price}"
        )

        try:

            send_telegram(
                message
            )

            print(
                f"Telegram sent: "
                f"{symbol}"
            )

        except Exception as e:

            print(
                f"Telegram error: {e}"
            )


if __name__ == "__main__":
    main()
