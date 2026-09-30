import os
import time
import json
import requests

# ==============================
# CONFIGURATION
# ==============================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Binance official alternative API endpoints
BINANCE_URLS = [
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
    "https://api-gcp.binance.com",
]

THRESHOLD = 30.0

# 2 hours = 24 x 5-minute candles
CANDLE_INTERVAL = "5m"
CANDLE_LIMIT = 25

STATE_FILE = "alerts.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


# ==============================
# BINANCE API
# ==============================

def get_working_endpoint():

    for base_url in BINANCE_URLS:

        try:

            url = f"{base_url}/api/v3/ping"

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=10
            )

            if response.status_code == 200:

                print(
                    f"Using Binance endpoint: {base_url}"
                )

                return base_url

            print(
                f"{base_url} returned "
                f"HTTP {response.status_code}"
            )

        except Exception as error:

            print(
                f"{base_url} failed: {error}"
            )

    raise Exception(
        "All Binance API endpoints failed."
    )


def get_symbols(base_url):

    url = f"{base_url}/api/v3/exchangeInfo"

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

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


def get_klines(base_url, symbol):

    url = f"{base_url}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": CANDLE_INTERVAL,
        "limit": CANDLE_LIMIT
    }

    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=10
    )

    if response.status_code != 200:

        print(
            f"{symbol}: HTTP "
            f"{response.status_code}"
        )

        return None

    return response.json()


# ==============================
# TELEGRAM
# ==============================

def send_telegram(message):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=15
    )

    response.raise_for_status()


# ==============================
# ALERT STATE
# ==============================

def load_alerted_coins():

    if not os.path.exists(STATE_FILE):

        return set()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        return set(
            data.get("alerted", [])
        )

    except Exception:

        return set()


def save_alerted_coins(coins):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            {
                "alerted": sorted(
                    list(coins)
                )
            },
            file,
            indent=2
        )


# ==============================
# CHECK COIN
# ==============================

def check_coin(base_url, symbol):

    candles = get_klines(
        base_url,
        symbol
    )

    if not candles:

        return None

    if len(candles) < 20:

        return None

    # Price at the beginning
    # of the 2-hour window
    start_price = float(
        candles[0][1]
    )

    # Highest price reached
    # during the 2-hour window
    highest_price = max(
        float(candle[2])
        for candle in candles
    )

    # Latest price
    current_price = float(
        candles[-1][4]
    )

    # Percentage increase from
    # beginning of window to highest price
    change = (
        (
            highest_price
            - start_price
        )
        / start_price
    ) * 100

    return (
        change,
        current_price,
        highest_price,
        start_price
    )


# ==============================
# MAIN SCANNER
# ==============================

def main():

    print(
        "================================"
    )

    print(
        "The Kingdom scanner started."
    )

    print(
        "Threshold: +30%"
    )

    print(
        "Window: 2 hours"
    )

    print(
        "================================"
    )

    # Find a Binance endpoint
    base_url = get_working_endpoint()

    # Get all Binance USDT spot pairs
    symbols = get_symbols(
        base_url
    )

    print(
        f"Found {len(symbols)} "
        f"USDT pairs."
    )

    # Load previous alert state
    alerted_coins = (
        load_alerted_coins()
    )

    new_alerts = []

    reset_coins = []

    # Scan every coin
    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            result = check_coin(
                base_url,
                symbol
            )

            if result is None:

                continue

            (
                change,
                current_price,
                highest_price,
                start_price
            ) = result

            # ==========================
            # +30% TOUCHED
            # ==========================

            if change >= THRESHOLD:

                if symbol not in alerted_coins:

                    new_alerts.append(
                        (
                            symbol,
                            change,
                            current_price,
                            highest_price,
                            start_price
                        )
                    )

                    alerted_coins.add(
                        symbol
                    )

                    print(
                        f"NEW ALERT: "
                        f"{symbol} "
                        f"+{change:.2f}%"
                    )

            # ==========================
            # BELOW +30%
            # ==========================

            else:

                if symbol in alerted_coins:

                    alerted_coins.remove(
                        symbol
                    )

                    reset_coins.append(
                        symbol
                    )

                    print(
                        f"RESET: {symbol}"
                    )

        except Exception as error:

            print(
                f"{symbol}: {error}"
            )

        # Small delay to reduce
        # API pressure
        time.sleep(0.05)

        if index % 100 == 0:

            print(
                f"Checked "
                f"{index}/{len(symbols)}..."
            )

    # Save state
    save_alerted_coins(
        alerted_coins
    )

    print(
        "================================"
    )

    print(
        "Scan completed."
    )

    print(
        f"New alerts: {len(new_alerts)}"
    )

    print(
        f"Reset coins: {len(reset_coins)}"
    )

    print(
        "================================"
    )

    # ==============================
    # SEND TELEGRAM ALERTS
    # ==============================

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
                f"Telegram alert sent: "
                f"{symbol}"
            )

        except Exception as error:

            print(
                f"Telegram error for "
                f"{symbol}: {error}"
            )


# ==============================
# START
# ==============================

if __name__ == "__main__":

    main()
