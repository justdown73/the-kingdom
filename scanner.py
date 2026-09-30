import os
import time
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

BASE_URL = "https://api.binance.com"

THRESHOLD = 30.0
HOURS = 2


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    r = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=15
    )

    r.raise_for_status()


def get_symbols():
    url = f"{BASE_URL}/api/v3/exchangeInfo"

    data = requests.get(url, timeout=20).json()

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

    # Get the last 2 hours using 5-minute candles
    url = f"{BASE_URL}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": "5m",
        "limit": 25
    }

    r = requests.get(url, params=params, timeout=10)

    if r.status_code != 200:
        return None

    candles = r.json()

    if len(candles) < 20:
        return None

    # Price at the beginning of the 2-hour window
    start_price = float(candles[0][1])

    # Highest price reached during the window
    highest_price = max(float(c[2]) for c in candles)

    change = ((highest_price - start_price) / start_price) * 100

    # Current price
    current_price = float(candles[-1][4])

    return change, current_price, highest_price


def main():

    print("The Kingdom scanner started.")

    symbols = get_symbols()

    print(f"Scanning {len(symbols)} Binance USDT pairs...")

    alerts = []

    for i, symbol in enumerate(symbols, 1):

        try:

            result = check_coin(symbol)

            if result is None:
                continue

            change, current_price, highest_price = result

            if change >= THRESHOLD:

                alerts.append(
                    (
                        symbol,
                        change,
                        current_price,
                        highest_price
                    )
                )

                print(
                    f"ALERT: {symbol} +{change:.2f}%"
                )

        except Exception as e:

            print(f"{symbol}: {e}")

        # Prevent API rate-limit problems
        time.sleep(0.08)

        if i % 100 == 0:
            print(f"Checked {i}/{len(symbols)} coins...")

    print("Scan completed.")

    if not alerts:

        print("No alerts found.")

        return

    for symbol, change, current_price, highest_price in alerts:

        message = (
            "⚡ Market Alert\n\n"
            f"🪙 {symbol}\n"
            f"📈 2H High: +{change:.2f}%\n"
            f"💰 Current: {current_price}\n"
            f"🔥 High: {highest_price}"
        )

        send_telegram(message)

        print(f"Telegram alert sent: {symbol}")


if __name__ == "__main__":
    main()
