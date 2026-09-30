import os
import time
import json
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

BINANCE_URL = "https://data-api.binance.vision"

THRESHOLD = 30.0
WINDOW_CANDLES = 25
STATE_FILE = "alerts.json"

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
                wait = int(r.headers.get("Retry-After", "10"))
                print(f"Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue

            print(
                f"API error {r.status_code}: "
                f"{url}"
            )

            return None

        except Exception as e:
            print(f"Request error: {e}")
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
        json.dump(
            {
                "alerted": sorted(alerted)
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
            symbols.append(s["symbol"])

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
    start_price = float(candles[0][1])

    # Highest price touched during
    # the 2-hour window
    highest_price = max(
        float(candle[2])
        for candle in candles
    )

    current_price = float(
        candles[-1][4]
    )

    change = (
        (highest_price - start_price)
        / start_price
    ) * 100

    return (
        change,
        current_price,
        highest_price,
        start_price
    )


def main():

    print("================================")
    print("The Kingdom scanner started")
    print("Threshold: +30%")
    print("Window: 2 hours")
    print("================================")

    symbols = get_symbols()

    print(
        f"Found {len(symbols)} "
        f"USDT spot pairs."
    )

    alerted = load_state()

    new_alerts = []

    for number, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            result = check_coin(symbol)

            if result is None:
                continue

            (
                change,
                current_price,
                highest_price,
                start_price
            ) = result

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

                    alerted.add(symbol)

                    print(
                        f"NEW ALERT: "
                        f"{symbol} "
                        f"+{change:.2f}%"
                    )

            else:

                # Remove the coin from the
                # alert list once the rolling
                # 2-hour movement is below 30%.
                if symbol in alerted:

                    alerted.remove(symbol)

                    print(
                        f"RESET: {symbol}"
                    )

        except Exception as e:

            print(
                f"{symbol}: {e}"
            )

        # Small pause between requests
        time.sleep(0.03)

        if number % 100 == 0:

            print(
                f"Checked "
                f"{number}/{len(symbols)}"
            )

    save_state(alerted)

    print("================================")
    print(
        f"New alerts: {len(new_alerts)}"
    )
    print("Scan completed.")
    print("================================")

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

            send_telegram(message)

            print(
                f"Telegram sent: {symbol}"
            )

        except Exception as e:

            print(
                f"Telegram error: {e}"
            )


if __name__ == "__main__":
    main()
