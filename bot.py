import requests
import time
import os
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

VOLUME_30_ALERT_PERCENT = 50
VOLUME_60_ALERT_PERCENT = 80

PUMP_PRICE_PERCENT = 1
PUMP_VOLUME_PERCENT = 40

MAX_VOLUME_SPIKE_PERCENT = 1000

last_alerts = {}

# ================= SEND =================
def send(msg, symbol=None):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Missing BOT_TOKEN or CHAT_ID")
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
            "inline_keyboard": [[{"text": "📈 عرض الشارت", "url": chart_url}]]
        }

    try:
        r = requests.post(url, json=data, timeout=15)
        print("Telegram:", r.status_code, r.text)
    except Exception as e:
        print("Telegram error:", e)

# ================= HELPERS =================
def is_bad_symbol(symbol):
    bad = ["3S", "3L", "5S", "5L", "BEAR", "BULL", "DOWN", "UP"]
    return any(x in symbol for x in bad)

def fmt(symbol):
    return symbol.replace("_", "/")

def direction_icon(v):
    return "🟢" if v >= 0 else "🔴"

# ================= SIGNAL (ARABIC) =================
def strength_label(a):
    if a["p30"] >= 100:
        return "🔥 قوي"
    elif a["p30"] >= 60:
        return "🟡 متوسط"
    return "⚪ ضعيف"

# ================= DATA =================
def get_usdt_symbols():
    url = f"{BASE_URL}/spot/tickers"
    data = requests.get(url, timeout=20).json()

    symbols = []
    for item in data:
        pair = item.get("currency_pair", "")
        vol = float(item.get("quote_volume", 0) or 0)

        if pair.endswith("_USDT") and not is_bad_symbol(pair) and vol >= MIN_24H_VOLUME_USDT:
            symbols.append(pair)

    return symbols

def get_klines(symbol):
    url = f"{BASE_URL}/spot/candlesticks"
    params = {"currency_pair": symbol, "interval": "1m", "limit": 120}
    return requests.get(url, params=params, timeout=15).json()

# ================= LOGIC =================
def can_alert(symbol, alert_type):
    key = f"{symbol}_{alert_type}"
    now = time.time()

    if key not in last_alerts:
        last_alerts[key] = now
        return True

    if now - last_alerts[key] >= ALERT_COOLDOWN_SECONDS:
        last_alerts[key] = now
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

    if p30 > MAX_VOLUME_SPIKE_PERCENT:
        return None

    price = float(data[-1][2])
    price_10 = float(data[-10][2])
    price_30 = float(data[-30][2])

    price_change_10m = ((price - price_10) / price_10) * 100
    price_change_30m = ((price - price_30) / price_30) * 100

    return {
        "symbol": symbol,
        "pair": fmt(symbol),
        "price": price,
        "vol30": vol30,
        "p30": p30,
        "price_change_10m": price_change_10m,
        "price_change_30m": price_change_30m,
        "score": p30 + price_change_10m * 20
    }

# ================= MESSAGES =================
def build_msg(a):
    icon = direction_icon(a["price_change_30m"])
    strength = strength_label(a)

    return f"""📊 <b>{a['pair']}</b>

💰 السعر: <b>${a['price']:.6f}</b>
{icon} 30m: <b>{a['price_change_30m']:.2f}%</b>

📊 حجم التداول: <b>{a['vol30']:,.0f}</b>
🚀 الفوليوم: <b>{a['p30']:.2f}%</b>

⭐ الإشارة: <b>{strength}</b>"""

# ================= MAIN =================
def process_symbol(symbol):
    try:
        return analyze(symbol)
    except:
        return None

def main():
    print("🚀 Bot Started")

    symbols = get_usdt_symbols()
    print("Symbols:", len(symbols))

    while True:
        try:
            print("Scanning...")

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                results = list(ex.map(process_symbol, symbols))

            signals = [r for r in results if r]
            signals.sort(key=lambda x: x["score"], reverse=True)

            sent = 0

            for s in signals:
                if sent >= MAX_ALERTS_PER_CYCLE:
                    break

                if not can_alert(s["symbol"], "main"):
                    continue

                send(build_msg(s), s["symbol"])
                sent += 1
                time.sleep(1)

            print("Sent:", sent)
            time.sleep(CHECK_EVERY_SECONDS)

        except Exception as e:
            print("Error:", e)
            time.sleep(10)

if __name__ == "__main__":
    main()