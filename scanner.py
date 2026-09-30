import os
import time
import json
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

BASE_URL = "https://api.binance.com"

THRESHOLD = 30.0
STATE_FILE = "alerts.json"


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=15
    )

    response.raise_for_status()


def load_alerted_coins():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)

        return set(data.get("alerted", []))

    except Exception:
        return set()


def save_alerted_coins(coins):
    with open(STATE_FILE, "w") as f:
        json.dump(
            {
                "alerted": sorted(list(coins))
            },
            f,
            indent=2
        )


def get_symbols():
    url = f"{BASE_URL}/api/v3/exchangeInfo"

    response = requests.get(url, timeout=20)
    response.raise_for_status()

    data = response.json()

    symbols = []

    for symbol in data["symbols"]:

        if (
            symbol["status"] == "TRADING"
            and symbol["quoteAsset"] == "USDT"
            and symbol["isSpotTradingAllowed"]
        ):
            symbols.append(symbol["symbol"])

    return symbols


def check_coin(symbol):

    url = f"{BASE_URL}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": "5m",
        "limit": 25
    }

    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    if response.status_code != 200:
        return None

    candles = response.json()

    if len(candles) < 20:
        return None

    # Price at the beginning of the 2-hour window
    start_price = float(candles[0][1])

    # Highest price reached during the last 2 hours
    highest_price = max(
        float(candle[2])
        for candle in candles
    )

    # Current price
    current_price = float(candles[-1][4])

    change = (
        (highest_price - start_price)
        / start_price
    ) * 100

    return change, current_price, highest_price


def main():

    print("The Kingdom scanner started.")

    alerted_coins = load_alerted_coins()

    symbols = get_symbols()

    print(
        f"Scanning {len(symbols)} USDT pairs..."
    )

    new_alerts = []

    for index, symbol in enumerate(symbols, 1):

        try:

            result = check_coin(symbol)

            if result is None:
                continue

            change, current_price, highest_price = result

            # +30% reached
            if change >= THRESHOLD:

                # Only alert if this coin
                # has NOT already alerted
                if symbol not in alerted_coins:

                    new_alerts.append(
                        (
                            symbol,
                            change,
                            current_price,
                            highest_price
                        )
                    )

                    alerted_coins.add(symbol)

                    print(
                        f"NEW ALERT: "
                        f"{symbol} +{change:.2f}%"
                    )

            else:

                # Reset the coin when it falls
                # below the threshold
                if symbol in alerted_coins:

                    alerted_coins.remove(symbol)

                    print(
                        f"RESET: {symbol}"
                    )

        except Exception as error:

            print(
                f"{symbol}: {error}"
            )

        # Small delay to avoid API pressure
        time.sleep(0.08)

        if index % 100 == 0:

            print(
                f"Checked "
                f"{index}/{len(symbols)}..."
            )

    # Save alert state
    save_alerted_coins(alerted_coins)

    print("Scan completed.")

    # Send only NEW alerts
    for (
        symbol,
        change,
        current_price,
        highest_price
    ) in new_alerts:

        message = (
            "⚡ Market Alert\n\n"
            f"🪙 {symbol}\n"
            f"📈 2H Move: +{change:.2f}%\n"
            f"💰 Current: {current_price}\n"
            f"🔥 2H High: {highest_price}"
        )

        send_telegram(message)

        print(
            f"Telegram alert sent: {symbol}"
        )

    if not new_alerts:

        print(
            "No new alerts."
        )


if __name__ == "__main__":
    main()
