import os
import time
import json
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# SETTINGS
# ============================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Binance Spot public market-data API
SPOT_URL = "https://data-api.binance.vision"

# Binance official Futures API
FUTURES_URLS = [
    "https://fapi.binance.com",

    # Public fallback mirror.
    # Used only if the official Futures API is unavailable
    # from the GitHub Actions runner.
    "https://api-dev.pipai.org",
]

THRESHOLD = 23.0

# 25 x 5-minute candles ≈ 2 hours
WINDOW_CANDLES = 25

STATE_FILE = "alerts.json"

MAX_WORKERS = 20

HEADERS = {
    "User-Agent": "TheKingdom/1.0"
}


# This will store the Futures API source that actually works.
ACTIVE_FUTURES_URL = None


# ============================================================
# GENERIC API REQUEST
# ============================================================

def api_get(base_url, path, params=None):
    """
    Send GET request with retry handling.
    """

    url = base_url + path

    for attempt in range(3):

        try:
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=20
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code in (429, 418):

                wait = int(
                    response.headers.get(
                        "Retry-After",
                        "10"
                    )
                )

                print(
                    f"Rate limited: {url}"
                )

                print(
                    f"Waiting {wait} seconds..."
                )

                time.sleep(wait)

                continue

            print(
                f"API error {response.status_code}: {url}"
            )

            return None

        except Exception as e:

            print(
                f"Request error: {url}"
            )

            print(e)

            if attempt < 2:
                time.sleep(2)

    return None


# ============================================================
# TELEGRAM
# ============================================================

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
        timeout=20
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise Exception(
            result.get(
                "description",
                "Telegram API error"
            )
        )

    return result


# ============================================================
# ALERT STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return set()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return set(
            data.get(
                "alerted",
                []
            )
        )

    except Exception as e:

        print(
            f"Could not load alert state: {e}"
        )

        return set()


def save_state(alerted):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            {
                "alerted": sorted(alerted)
            },
            f,
            indent=2
        )


# ============================================================
# SPOT MARKET LIST
# ============================================================

def get_spot_symbols():

    data = api_get(
        SPOT_URL,
        "/api/v3/exchangeInfo"
    )

    if not data:

        raise Exception(
            "Could not get Binance Spot exchange information."
        )

    symbols = []

    for s in data.get("symbols", []):

        if (
            s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
            and s.get("isSpotTradingAllowed") is True
        ):

            symbols.append(
                s["symbol"]
            )

    return symbols


# ============================================================
# FUTURES API CONNECTION
# ============================================================

def get_futures_exchange_info():

    global ACTIVE_FUTURES_URL

    print("")
    print("Checking Futures API...")
    print("")

    for base_url in FUTURES_URLS:

        print(
            f"Trying Futures source: {base_url}"
        )

        data = api_get(
            base_url,
            "/fapi/v1/exchangeInfo"
        )

        if data:

            ACTIVE_FUTURES_URL = base_url

            print(
                f"Futures source connected: {base_url}"
            )

            return data

        print(
            f"Futures source failed: {base_url}"
        )

    print("")
    print(
        "WARNING: No Futures API source is available."
    )
    print(
        "Spot scanning will continue."
    )
    print("")

    return None


# ============================================================
# FUTURES MARKET LIST
# ============================================================

def get_futures_symbols():

    data = get_futures_exchange_info()

    if not data:

        return []

    symbols = []

    for s in data.get("symbols", []):

        if (
            s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
        ):

            symbols.append(
                s["symbol"]
            )

    return symbols


# ============================================================
# CHECK SPOT COIN
# ============================================================

def check_spot(symbol):

    candles = api_get(
        SPOT_URL,
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

    start_price = float(
        candles[0][1]
    )

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
        * 100
    )

    return (
        "SPOT",
        symbol,
        change,
        current_price,
        highest_price,
        start_price
    )


# ============================================================
# CHECK FUTURES COIN
# ============================================================

def check_futures(symbol):

    if not ACTIVE_FUTURES_URL:
        return None

    candles = api_get(
        ACTIVE_FUTURES_URL,
        "/fapi/v1/klines",
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

    start_price = float(
        candles[0][1]
    )

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
        * 100
    )

    return (
        "FUTURES",
        symbol,
        change,
        current_price,
        highest_price,
        start_price
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("================================")
    print("The Kingdom scanner started")
    print("Threshold: +23%")
    print("Window: 2 hours")
    print(f"Parallel workers: {MAX_WORKERS}")
    print("Markets: Spot + USDT Perpetual")
    print("================================")

    start_time = time.time()

    # --------------------------------------------------------
    # GET SPOT SYMBOLS
    # --------------------------------------------------------

    spot_symbols = get_spot_symbols()

    print(
        f"Spot USDT pairs: {len(spot_symbols)}"
    )

    # --------------------------------------------------------
    # GET FUTURES SYMBOLS
    # --------------------------------------------------------

    futures_symbols = get_futures_symbols()

    print(
        f"USDT Perpetual pairs: {len(futures_symbols)}"
    )

    print(
        f"Total markets to scan: "
        f"{len(spot_symbols) + len(futures_symbols)}"
    )

    # --------------------------------------------------------
    # LOAD ALERT STATE
    # --------------------------------------------------------

    alerted = load_state()

    new_alerts = []

    completed = 0

    total_markets = (
        len(spot_symbols)
        + len(futures_symbols)
    )

    # --------------------------------------------------------
    # BUILD TASK LIST
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # PARALLEL SCAN
    # --------------------------------------------------------

    print("")
    print("Starting parallel scan...")
    print("")

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        for market, symbol in tasks:

            if market == "SPOT":

                future = executor.submit(
                    check_spot,
                    symbol
                )

            else:

                future = executor.submit(
                    check_futures,
                    symbol
                )

            futures[future] = (
                market,
                symbol
            )

        # ----------------------------------------------------
        # PROCESS RESULTS
        # ----------------------------------------------------

        for future in as_completed(futures):

            market, original_symbol = futures[future]

            completed += 1

            try:

                result = future.result()

                if result is None:
                    continue

                (
                    market,
                    symbol,
                    change,
                    current_price,
                    highest_price,
                    start_price
                ) = result

                # --------------------------------------------
                # UNIQUE STATE KEY
                # --------------------------------------------

                state_key = (
                    f"{market}:{symbol}"
                )

                # --------------------------------------------
                # LEGACY SPOT STATE SUPPORT
                # --------------------------------------------

                legacy_key = symbol

                already_alerted = (
                    state_key in alerted
                    or (
                        market == "SPOT"
                        and legacy_key in alerted
                    )
                )

                # --------------------------------------------
                # ALERT CONDITION
                # --------------------------------------------

                if change >= THRESHOLD:

                    if not already_alerted:

                        new_alerts.append(
                            (
                                market,
                                symbol,
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
                            f"{market} "
                            f"{symbol} "
                            f"+{change:.2f}%"
                        )

                # --------------------------------------------
                # RESET CONDITION
                # --------------------------------------------

                else:

                    if state_key in alerted:

                        alerted.remove(
                            state_key
                        )

                        print(
                            f"RESET: "
                            f"{market} "
                            f"{symbol}"
                        )

                    # Remove old Spot state too
                    if (
                        market == "SPOT"
                        and legacy_key in alerted
                    ):

                        alerted.remove(
                            legacy_key
                        )

            except Exception as e:

                print(
                    f"{market} {original_symbol}: {e}"
                )

            # --------------------------------------------
            # PROGRESS
            # --------------------------------------------

            if (
                completed % 100 == 0
                or completed == total_markets
            ):

                print(
                    f"Checked "
                    f"{completed}/"
                    f"{total_markets}"
                )

    # --------------------------------------------------------
    # SAVE STATE
    # --------------------------------------------------------

    save_state(alerted)

    elapsed = (
        time.time()
        - start_time
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print("")
    print("================================")
    print(
        f"New alerts: {len(new_alerts)}"
    )
    print(
        f"Scan time: {elapsed:.1f} seconds"
    )
    print("Scan completed.")
    print("================================")

    # --------------------------------------------------------
    # SEND TELEGRAM ALERTS
    # --------------------------------------------------------

    for (
        market,
        symbol,
        change,
        current_price,
        highest_price,
        start_price
    ) in new_alerts:

        # --------------------------------------------
        # DISPLAY SYMBOL
        # --------------------------------------------

        if market == "FUTURES":

            display_symbol = (
                f"{symbol}.P"
            )

            market_name = (
                "USDT Perpetual"
            )

        else:

            display_symbol = symbol

            market_name = "Spot"

        # --------------------------------------------
        # MESSAGE
        # --------------------------------------------

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


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()