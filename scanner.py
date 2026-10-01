import os
import time
import json
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# --------------------------------
# API ENDPOINTS
# --------------------------------

SPOT_URL = "https://data-api.binance.vision"
FUTURES_URL = "https://fapi.binance.com"

# --------------------------------
# SETTINGS
# --------------------------------

THRESHOLD = 23.0
WINDOW_CANDLES = 25
STATE_FILE = "alerts.json"

# Safe parallel workers
MAX_WORKERS = 20

HEADERS = {
    "User-Agent": "TheKingdom/1.0"
}


# --------------------------------
# API REQUEST
# --------------------------------

def api_get(base_url, path, params=None):

    url = base_url + path

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


# --------------------------------
# TELEGRAM
# --------------------------------

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


# --------------------------------
# ALERT STATE
# --------------------------------

def load_state():

    if not os.path.exists(STATE_FILE):

        return set()

    try:

        with open(
            STATE_FILE,
            "r"
        ) as f:

            data = json.load(f)

        old_alerts = data.get(
            "alerted",
            []
        )

        alerted = set()

        for item in old_alerts:

            # Old version stored only:
            # ARKUSDT
            #
            # Treat old alerts as Spot alerts.

            if ":" not in item:

                alerted.add(
                    f"SPOT:{item}"
                )

            else:

                alerted.add(item)

        return alerted

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


# --------------------------------
# GET SPOT SYMBOLS
# --------------------------------

def get_spot_symbols():

    data = api_get(
        SPOT_URL,
        "/api/v3/exchangeInfo"
    )

    if not data:

        raise Exception(
            "Could not get Spot exchange information."
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


# --------------------------------
# GET USDT PERPETUAL FUTURES
# --------------------------------

def get_futures_symbols():

    data = api_get(
        FUTURES_URL,
        "/fapi/v1/exchangeInfo"
    )

    if not data:

        raise Exception(
            "Could not get Futures exchange information."
        )

    symbols = []

    for s in data["symbols"]:

        if (
            s["status"] == "TRADING"
            and s["contractType"] == "PERPETUAL"
            and s["quoteAsset"] == "USDT"
        ):

            symbols.append(
                s["symbol"]
            )

    return symbols


# --------------------------------
# CHECK COIN
# --------------------------------

def check_coin(
    market,
    symbol
):

    if market == "SPOT":

        base_url = SPOT_URL

        display_symbol = symbol

        path = "/api/v3/klines"

    else:

        base_url = FUTURES_URL

        # Binance API uses ARKUSDT
        # TradingView-style display:
        # ARKUSDT.P

        display_symbol = (
            f"{symbol}.P"
        )

        path = "/fapi/v1/klines"

    candles = api_get(
        base_url,
        path,
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
    # beginning of window

    change = (
        (highest_price - start_price)
        / start_price
    ) * 100

    return (
        market,
        symbol,
        display_symbol,
        change,
        current_price,
        highest_price,
        start_price
    )


# --------------------------------
# MAIN
# --------------------------------

def main():

    print("================================")
    print(
        "The Kingdom scanner started"
    )

    print(
        "Threshold: +23%"
    )

    print(
        "Window: 2 hours"
    )

    print(
        f"Parallel workers: "
        f"{MAX_WORKERS}"
    )

    print(
        "Markets: Spot + USDT Perpetual"
    )

    print("================================")

    start_time = time.time()

    # --------------------------------
    # GET SPOT SYMBOLS
    # --------------------------------

    spot_symbols = get_spot_symbols()

    print(
        f"Spot USDT pairs: "
        f"{len(spot_symbols)}"
    )

    # --------------------------------
    # GET FUTURES SYMBOLS
    # --------------------------------

    futures_symbols = get_futures_symbols()

    print(
        f"USDT Perpetual pairs: "
        f"{len(futures_symbols)}"
    )

    # --------------------------------
    # CREATE SCAN TASKS
    # --------------------------------

    tasks = []

    for symbol in spot_symbols:

        tasks.append(
            (
                "SPOT",
                symbol
            )
        )

    for symbol in futures_symbols:

        tasks.append(
            (
                "FUTURES",
                symbol
            )
        )

    print(
        f"Total markets to scan: "
        f"{len(tasks)}"
    )

    # --------------------------------
    # LOAD ALERT STATE
    # --------------------------------

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

        futures = {}

        for market, symbol in tasks:

            future = executor.submit(
                check_coin,
                market,
                symbol
            )

            futures[future] = (
                market,
                symbol
            )

        for future in as_completed(
            futures
        ):

            market, symbol = futures[
                future
            ]

            completed += 1

            try:

                result = future.result()

                if result is None:

                    continue

                (
                    market,
                    symbol,
                    display_symbol,
                    change,
                    current_price,
                    highest_price,
                    start_price
                ) = result

                # --------------------------------
                # UNIQUE STATE KEY
                # --------------------------------

                state_key = (
                    f"{market}:{symbol}"
                )

                # --------------------------------
                # NEW ALERT
                # --------------------------------

                if change >= THRESHOLD:

                    if state_key not in alerted:

                        new_alerts.append(
                            (
                                market,
                                symbol,
                                display_symbol,
                                change,
                                current_price,
                                highest_price,
                                start_price
                            )
                        )

                        alerted.add(
                            state_key
                        )

                        print(
                            f"NEW ALERT: "
                            f"{display_symbol} "
                            f"+{change:.2f}%"
                        )

                # --------------------------------
                # RESET
                # --------------------------------

                else:

                    if state_key in alerted:

                        alerted.remove(
                            state_key
                        )

                        print(
                            f"RESET: "
                            f"{display_symbol}"
                        )

            except Exception as e:

                print(
                    f"{market} "
                    f"{symbol}: {e}"
                )

            # --------------------------------
            # PROGRESS
            # --------------------------------

            if (
                completed % 100 == 0
                or completed == len(tasks)
            ):

                print(
                    f"Checked "
                    f"{completed}/"
                    f"{len(tasks)}"
                )

    # --------------------------------
    # SAVE STATE
    # --------------------------------

    save_state(
        alerted
    )

    elapsed = (
        time.time()
        - start_time
    )

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
    # TELEGRAM ALERTS
    # --------------------------------

    for (
        market,
        symbol,
        display_symbol,
        change,
        current_price,
        highest_price,
        start_price
    ) in new_alerts:

        if market == "SPOT":

            market_name = "Spot"

        else:

            market_name = (
                "USDT Perpetual"
            )

        message = (
            "⚡ Market Alert\n\n"
            f"🪙 {display_symbol}\n"
            f"📊 Market: {market_name}\n"
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
                f"{display_symbol}"
            )

        except Exception as e:

            print(
                f"Telegram error: {e}"
            )


# --------------------------------
# START
# --------------------------------

if __name__ == "__main__":

    main()