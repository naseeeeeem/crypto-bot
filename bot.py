import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE_URL = "https://api.gateio.ws/api/v4"

CHECK_EVERY_SECONDS = 5
MAX_WORKERS = 10
ALERT_COOLDOWN_SECONDS = 30 * 60
MAX_ALERTS_PER_CYCLE = 5

MIN_24H_VOLUME_USDT = 500000
MIN_VOLUME_30M_USDT = 30000

MAX_VOLUME_SPIKE_PERCENT = 1000
MIN_SPIKE_PERCENT = 100

last_alerts = {}


def send(msg, symbol=None):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": str(CHAT_ID),
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if symbol:
        chart_url = f"https://www.gate.io/trade/{symbol}"
        data["reply_markup"] = {
            "inline_keyboard": [[{"text": "📈 Show Chart", "url": chart_url}]]
        }

    try:
        r = requests.post(url, json=data, timeout=15)
        print("Telegram:", r.status_code, r.text)
    except Exception as e:
        print("Telegram error:", e)


def is_bad_symbol(symbol):
    bad = ["3S", "3L", "5S", "5L", "BEAR", "BULL", "DOWN", "UP"]
    return any(x in symbol for x in bad)


def fmt(symbol):
    return symbol.replace("_", "/")


def strength_label(a):
    if a["p30"] >= 100 and a["price_change_30m"] > -0.5:
        return "🔥 Strong"
    elif a["p30"] >= 60:
        return "🟡 Medium"
    return "⚪ Watch"


def get_usdt_symbols():
    url = f"{BASE_URL}/spot/tickers"
    data = requests.get(url, timeout=20).json()

    symbols = []

    for item in data:
        pair = item.get("currency_pair", "")
        volume_24h = float(item.get("quote_volume", 0) or 0)

        if (
            pair.endswith("_USDT")
            and not is_bad_symbol(pair)
            and volume_24h >= MIN_24H_VOLUME_USDT
        ):
            symbols.append(pair)

    return symbols


def get_klines(symbol):
    url = f"{BASE_URL}/spot/candlesticks"
    params = {
        "currency_pair": symbol,
        "interval": "1m",
        "limit": 120
    }
    return requests.get(url, params=params, timeout=15).json()


def can_alert(symbol):
    now = time.time()

    if symbol not in last_alerts:
        last_alerts[symbol] = now
        return True

    if now - last_alerts[symbol] >= ALERT_COOLDOWN_SECONDS:
        last_alerts[symbol] = now
        return True

    return False


def analyze(symbol):
    data = get_klines(symbol)

    if not isinstance(data, list) or len(data) < 120:
        return None

    data = sorted(data, key=lambda x: int(x[0]))

    vol30 = sum(float(x[1]) for x in data[-30:])
    prev30 = sum(float(x[1]) for x in data[-60:-30])

    if vol30 <= 0 or prev30 <= 0:
        return None

    if vol30 < MIN_VOLUME_30M_USDT:
        return None

    p30 = ((vol30 - prev30) / prev30) * 100

    if p30 < MIN_SPIKE_PERCENT:
        return None

    if p30 > MAX_VOLUME_SPIKE_PERCENT:
        return None

    price = float(data[-1][2])
    price_10 = float(data[-10][2])
    price_30 = float(data[-30][2])

    if price <= 0 or price_10 <= 0 or price_30 <= 0:
        return None

    price_change_10m = ((price - price_10) / price_10) * 100
    price_change_30m = ((price - price_30) / price_30) * 100

    score = p30 + (price_change_10m * 20) + min(vol30 / 10000, 50)

    return {
        "symbol": symbol,
        "pair": fmt(symbol),
        "price": price,
        "vol30": vol30,
        "p30": p30,
        "price_change_10m": price_change_10m,
        "price_change_30m": price_change_30m,
        "score": score
    }


def build_msg(a):
    return f"""📊 <b>{a['pair']}</b> on Gate.io

💰 Price: <b>${a['price']:.6f}</b>
🟢 10m: <b>{a['price_change_10m']:.2f}%</b>
📈 30m: <b>{a['price_change_30m']:.2f}%</b>

📊 Volume 30m: <b>{a['vol30']:,.0f} USDT</b>
🚀 Spike: <b>{a['p30']:.2f}%</b>

⭐ Signal: <b>{strength_label(a)}</b>"""


def process_symbol(symbol):
    try:
        return analyze(symbol)
    except Exception as e:
        print(f"Error with {symbol}:", e)
        return None


def main():
    print("Bot Started")

    symbols = get_usdt_symbols()
    print(f"Loaded {len(symbols)} symbols")

    while True:
        try:
            print("Scanning...")

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                results = list(executor.map(process_symbol, symbols))

            signals = [r for r in results if r]
            signals.sort(key=lambda x: x["score"], reverse=True)

            sent = 0

            for signal in signals:
                if sent >= MAX_ALERTS_PER_CYCLE:
                    break

                if not can_alert(signal["symbol"]):
                    continue

                send(build_msg(signal), signal["symbol"])
                sent += 1
                time.sleep(1)

            print(f"Cycle done | Sent: {sent}")
            time.sleep(CHECK_EVERY_SECONDS)

        except Exception as e:
            print("Main error:", e)
            time.sleep(10)


if __name__ == "__main__":
    main()