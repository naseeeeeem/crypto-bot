import requests
import time
from concurrent.futures import ThreadPoolExecutor

BOT_TOKEN = "8763101324:AAF5JGbRp2x9kkFFZM8_uvPSgROMDx7GhCc"
CHAT_ID = "1215070964"

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

def send(msg, symbol=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": CHAT_ID,
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
        print("Telegram:", r.status_code)
    except Exception as e:
        print("Telegram error:", e)

def is_bad_symbol(symbol):
    bad = ["3S", "3L", "5S", "5L", "BEAR", "BULL", "DOWN", "UP"]
    return any(x in symbol for x in bad)

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
    params = {
        "currency_pair": symbol,
        "interval": "1m",
        "limit": 120
    }
    return requests.get(url, params=params, timeout=15).json()

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

def fmt(symbol):
    return symbol.replace("_", "/")

def direction_icon(value):
    return "🟢" if value >= 0 else "🔴"

def strength_label(a):
    if a["p30"] >= 100 and a["price_change_30m"] > -0.5:
        return "🔥 Strong"

    elif a["p30"] >= 60 and a["vol30"] >= 30000:
        return "🟡 Medium"

    return "⚪ Watch"

def analyze(symbol):
    if is_bad_symbol(symbol):
        return None

    data = get_klines(symbol)

    if not isinstance(data, list) or len(data) < 120:
        return None

    data = sorted(data, key=lambda x: int(x[0]))

    vol30 = sum(float(x[1]) for x in data[-30:])
    prev30 = sum(float(x[1]) for x in data[-60:-30])

    vol60 = sum(float(x[1]) for x in data[-60:])
    prev60 = sum(float(x[1]) for x in data[-120:-60])

    if vol30 <= 0 or prev30 <= 0 or vol60 <= 0 or prev60 <= 0:
        return None

    if vol30 < MIN_VOLUME_30M_USDT:
        return None

    p30 = ((vol30 - prev30) / prev30) * 100
    p60 = ((vol60 - prev60) / prev60) * 100

    if p30 > MAX_VOLUME_SPIKE_PERCENT or p60 > MAX_VOLUME_SPIKE_PERCENT:
        return None

    price = float(data[-1][2])
    price_10 = float(data[-10][2])
    price_30 = float(data[-30][2])

    if price <= 0 or price_10 <= 0 or price_30 <= 0:
        return None

    price_change_10m = ((price - price_10) / price_10) * 100
    price_change_30m = ((price - price_30) / price_30) * 100

    if abs(price_change_10m) < 0.2 and p30 < 80:
        return None

    volume_score = p30 * 0.5
    price_score = price_change_10m * 25
    liquidity_score = min(vol30 / 10000, 50)

    early_bonus = 25 if (
        p30 >= 90
        and -0.2 <= price_change_10m <= 1
        and price_change_30m >= -0.5
        and vol30 >= 50000
    ) else 0

    score = volume_score + price_score + liquidity_score + early_bonus

    return {
        "symbol": symbol,
        "pair": fmt(symbol),
        "vol30": vol30,
        "vol60": vol60,
        "p30": p30,
        "p60": p60,
        "price": price,
        "price_change_10m": price_change_10m,
        "price_change_30m": price_change_30m,
        "vol_diff30": vol30 - prev30,
        "vol_diff60": vol60 - prev60,
        "score": score
    }

def msg30(a):
    icon = direction_icon(a["price_change_30m"])
    strength = strength_label(a)

    return f"""📊 <b>Volume Monitor [30min]</b>
<b>{a['pair']}</b>

💰 Price: <b>${a['price']:.6f}</b>
{icon} Change 30m: <b>{a['price_change_30m']:.2f}%</b>

📊 Volume: <b>{a['vol30']:,.0f} USDT</b>
🚀 Spike: <b>{a['p30']:.2f}%</b>
⬆️ +{a['vol_diff30']:,.0f} USDT

⭐ Signal: <b>{strength}</b>"""

def msg60(a):
    icon = direction_icon(a["price_change_30m"])
    strength = strength_label(a)

    return f"""📊 <b>Volume Monitor [60min]</b>
<b>{a['pair']}</b>

💰 Price: <b>${a['price']:.6f}</b>
{icon} Change 30m: <b>{a['price_change_30m']:.2f}%</b>

📊 Volume: <b>{a['vol60']:,.0f} USDT</b>
🚀 Spike: <b>{a['p60']:.2f}%</b>
⬆️ +{a['vol_diff60']:,.0f} USDT

⭐ Signal: <b>{strength}</b>"""

def pump(a):
    strength = strength_label(a)

    return f"""🔥🚀 <b>PUMP ALERT</b>
<b>{a['pair']}</b>

💰 Price: <b>${a['price']:.6f}</b>

🟢 10m: <b>{a['price_change_10m']:.2f}%</b>
📈 30m: <b>{a['price_change_30m']:.2f}%</b>

📊 Volume: <b>{a['vol30']:,.0f} USDT</b>
⚡ Spike: <b>{a['p30']:.2f}%</b>
⬆️ +{a['vol_diff30']:,.0f} USDT

⭐ Signal: <b>{strength}</b>"""

def early_pump(a):
    strength = strength_label(a)

    return f"""👀🚀 <b>EARLY PUMP DETECTION</b>
<b>{a['pair']}</b>

💰 Price: <b>${a['price']:.6f}</b>

🟢 10m: <b>{a['price_change_10m']:.2f}%</b>
📈 30m: <b>{a['price_change_30m']:.2f}%</b>

📊 Volume: <b>{a['vol30']:,.0f} USDT</b>
⚡ Spike: <b>{a['p30']:.2f}%</b>
⬆️ +{a['vol_diff30']:,.0f} USDT

⭐ Signal: <b>{strength}</b>"""

def get_alert_type(a):
    if (
        a["price_change_10m"] >= PUMP_PRICE_PERCENT
        and a["p30"] >= PUMP_VOLUME_PERCENT
        and a["vol_diff30"] > 0
    ):
        return "pump"

    if (
        a["p30"] >= 90
        and -0.2 <= a["price_change_10m"] <= 1
        and a["price_change_30m"] >= -0.5
        and a["vol30"] >= 50000
        and a["vol_diff30"] > 0
    ):
        return "early"

    if a["p30"] >= VOLUME_30_ALERT_PERCENT and a["vol_diff30"] > 0:
        return "v30"

    if a["p60"] >= VOLUME_60_ALERT_PERCENT and a["vol_diff60"] > 0:
        return "v60"

    return None

def process_symbol(symbol):
    try:
        a = analyze(symbol)
        if not a:
            return None

        alert_type = get_alert_type(a)
        if not alert_type:
            return None

        return (a["score"], alert_type, a)

    except Exception as e:
        print(f"Error with {symbol}: {e}")
        return None

symbols = get_usdt_symbols()
send("🚀 TEST FROM RAILWAY")
print(f"Loaded {len(symbols)} symbols")

while True:
    try:
        print("Scanning...")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            results = list(executor.map(process_symbol, symbols))

        candidates = [r for r in results if r]
        candidates.sort(key=lambda x: x[0], reverse=True)

        print(f"Candidates: {len(candidates)}")

        sent = 0

        for score, alert_type, a in candidates:
            if sent >= MAX_ALERTS_PER_CYCLE:
                break

            s = a["symbol"]

            if not can_alert(s, alert_type):
                continue

            print(
                f"Sending {s} | Type: {alert_type} | Score: {score:.2f} | "
                f"30m: {a['p30']:.2f}% | "
                f"Price10m: {a['price_change_10m']:.2f}%"
            )

            if alert_type == "pump":
                send(pump(a), s)

            elif alert_type == "early":
                send(early_pump(a), s)

            elif alert_type == "v30":
                send(msg30(a), s)

            elif alert_type == "v60":
                send(msg60(a), s)

            sent += 1
            time.sleep(1)

        print(f"Cycle done | Sent: {sent}\n")
        time.sleep(CHECK_EVERY_SECONDS)

    except Exception as e:
        print("Main Error:", e)
        time.sleep(10)