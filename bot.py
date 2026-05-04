import os
import time
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")              # قناة التنبيهات / المراقبة
TRADE_CHAT_ID = os.getenv("TRADE_CHAT_ID")  # قناة التوصيات VIP
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID")

REPORT_TIMEZONE = os.getenv("REPORT_TIMEZONE", "Asia/Hebron")

GATE_BASE = "https://api.gateio.ws/api/v4"
DATA_FILE = "trades_data.json"

CHECK_EVERY_SECONDS = 10
MAX_WORKERS = 10
MAX_ALERTS_PER_CYCLE = 5
ALERT_COOLDOWN_SECONDS = 45 * 60

MIN_24H_VOLUME_USDT = 700000
MIN_VOLUME_30M_USDT = 40000

SUPPORT_LOOKBACK = 30

WATCH_VOLUME_PERCENT = 55
EARLY_VOLUME_PERCENT = 80
VIP_VOLUME_PERCENT = 110
ULTRA_VIP_VOLUME_PERCENT = 140

MAX_ENTRY_DISTANCE_PERCENT = 1.8
MAX_WATCH_DISTANCE_PERCENT = 3.5
ULTRA_MAX_DISTANCE_PERCENT = 1.0

FOMO_MAX_DISTANCE_PERCENT = 1.8
FOMO_MAX_10M_MOVE = 5.0

MAX_DUMP_10M_PERCENT = -3.5
MAX_PUMP_10M_PERCENT = 8
MAX_PRICE_30M_PERCENT = 15

FAKE_PUMP_VOLUME_PERCENT = 160
FAKE_PUMP_MAX_PRICE_MOVE = 0.25

WHALE_VOLUME_DIFF_USDT = 70000

VIP_SCORE = 130
GOLD_SCORE = 160
ULTRA_VIP_SCORE = 180

last_alerts = {}
last_update_id = 0


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"active": [], "closed": []}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"active": [], "closed": []}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send(msg, symbol=None, chat_id=None):
    if not msg or not msg.strip():
        print("Empty message skipped")
        return

    if not BOT_TOKEN:
        print("Missing BOT_TOKEN")
        return

    target = chat_id if chat_id else CHAT_ID

    if not target:
        print("Missing CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": str(target),
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if symbol:
        data["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "📈 عرض الشارت", "url": f"https://www.gate.io/trade/{symbol}"}
            ]]
        }

    try:
        r = requests.post(url, json=data, timeout=15)
        print("Telegram:", r.status_code, r.text[:150])
    except Exception as e:
        print("Telegram error:", e)


def send_photo(photo_path, caption="", chat_id=None):
    if not BOT_TOKEN:
        return

    target = chat_id if chat_id else REPORT_CHAT_ID
    if not target:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    try:
        with open(photo_path, "rb") as photo:
            files = {"photo": photo}
            data = {
                "chat_id": str(target),
                "caption": caption,
                "parse_mode": "HTML"
            }
            r = requests.post(url, data=data, files=files, timeout=20)
            print("Telegram photo:", r.status_code, r.text[:120])
    except Exception as e:
        print("send_photo error:", e)


def safe_float(x):
    try:
        return float(x)
    except:
        return 0


def fmt(symbol):
    return symbol.replace("_", "/")


def is_bad_symbol(symbol):
    bad = ["3S", "3L", "5S", "5L", "BEAR", "BULL", "DOWN", "UP"]
    return any(x in symbol for x in bad)


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


def get_symbols():
    try:
        data = requests.get(f"{GATE_BASE}/spot/tickers", timeout=20).json()
    except Exception as e:
        print("get_symbols error:", e)
        return []

    symbols = []

    for item in data:
        pair = item.get("currency_pair", "")
        vol = safe_float(item.get("quote_volume", 0))

        if pair.endswith("_USDT") and not is_bad_symbol(pair) and vol >= MIN_24H_VOLUME_USDT:
            symbols.append(pair)

    return symbols


def get_klines(symbol):
    params = {
        "currency_pair": symbol,
        "interval": "1m",
        "limit": 120
    }

    try:
        return requests.get(
            f"{GATE_BASE}/spot/candlesticks",
            params=params,
            timeout=15
        ).json()
    except Exception as e:
        print(f"get_klines error {symbol}:", e)
        return []


def get_current_price(symbol):
    try:
        data = requests.get(
            f"{GATE_BASE}/spot/tickers",
            params={"currency_pair": symbol},
            timeout=10
        ).json()

        if isinstance(data, list) and data:
            return safe_float(data[0].get("last"))
    except Exception as e:
        print("get_current_price error:", e)

    return 0


def calculate_support_30m(data):
    lows = []

    for candle in data[-SUPPORT_LOOKBACK:]:
        low = safe_float(candle[4])
        if low > 0:
            lows.append(low)

    return min(lows) if lows else None


def calculate_resistance_30m(data):
    highs = []

    for candle in data[-SUPPORT_LOOKBACK:]:
        high = safe_float(candle[3])
        if high > 0:
            highs.append(high)

    return max(highs) if highs else None


def whale_text(a):
    if a["vol_diff30"] >= WHALE_VOLUME_DIFF_USDT:
        return "🐋 <b>Whale Activity Detected</b>"
    return ""


def vip_label(a):
    if a["score"] >= ULTRA_VIP_SCORE:
        return "🚀 ULTRA VIP"
    if a["score"] >= GOLD_SCORE:
        return "💎 GOLD VIP"
    if a["score"] >= VIP_SCORE:
        return "🔥 VIP"
    if a["score"] >= 100:
        return "🟡 جيد"
    return "⚪ مراقبة"


def trade_plan(a):
    price = a["price"]
    entry = a["support_30m"] if a["support_30m"] else price

    stop_loss = entry * 0.985

    if a["score"] >= GOLD_SCORE:
        target1 = entry * 1.03
        target2 = entry * 1.06
    elif a["score"] >= VIP_SCORE:
        target1 = entry * 1.025
        target2 = entry * 1.05
    else:
        target1 = entry * 1.02
        target2 = entry * 1.04

    distance = ((price - entry) / entry) * 100 if entry > 0 else 0

    risk = entry - stop_loss
    reward = target1 - entry
    rr = reward / risk if risk > 0 else 0

    if distance <= 0.6:
        status = "✅ دخول ممتاز — السعر قريب من الدعم"
    elif distance <= MAX_ENTRY_DISTANCE_PERCENT:
        status = "🟡 دخول مقبول — الأفضل انتظار نزول بسيط"
    else:
        status = "🚫 السعر بعيد — لا تدخل ماركت"

    return {
        "entry": entry,
        "stop_loss": stop_loss,
        "target1": target1,
        "target2": target2,
        "distance": distance,
        "rr": rr,
        "status": status
    }


def confidence_percent(a):
    plan = trade_plan(a)

    score_part = min(a["score"] / 200 * 40, 40)
    volume_part = min(a["p30"] / 180 * 25, 25)
    distance_part = max(0, 20 - (plan["distance"] * 10))
    whale_part = 10 if a["vol_diff30"] >= WHALE_VOLUME_DIFF_USDT else 0
    price_part = 5 if 0.2 <= a["price_change_10m"] <= 4 else 0

    confidence = score_part + volume_part + distance_part + whale_part + price_part

    return max(0, min(confidence, 95))


def analyze(symbol):
    if is_bad_symbol(symbol):
        return None

    data = get_klines(symbol)

    if not isinstance(data, list) or len(data) < 120:
        return None

    try:
        data = sorted(data, key=lambda x: int(x[0]))
    except:
        return None

    vol30 = sum(safe_float(x[1]) for x in data[-30:])
    prev30 = sum(safe_float(x[1]) for x in data[-60:-30])

    vol60 = sum(safe_float(x[1]) for x in data[-60:])
    prev60 = sum(safe_float(x[1]) for x in data[-120:-60])

    if vol30 <= 0 or prev30 <= 0 or vol60 <= 0 or prev60 <= 0:
        return None

    if vol30 < MIN_VOLUME_30M_USDT:
        return None

    p30 = ((vol30 - prev30) / prev30) * 100
    p60 = ((vol60 - prev60) / prev60) * 100

    price = safe_float(data[-1][2])
    price_10 = safe_float(data[-10][2])
    price_30 = safe_float(data[-30][2])

    if price <= 0 or price_10 <= 0 or price_30 <= 0:
        return None

    change10 = ((price - price_10) / price_10) * 100
    change30 = ((price - price_30) / price_30) * 100

    support = calculate_support_30m(data)
    resistance = calculate_resistance_30m(data)

    if not support:
        return None

    distance = ((price - support) / support) * 100 if support > 0 else 999

    # فلاتر حماية
    if change10 <= MAX_DUMP_10M_PERCENT:
        return None

    if change10 >= MAX_PUMP_10M_PERCENT:
        return None

    if change30 >= MAX_PRICE_30M_PERCENT:
        return None

    # Fake Pump Filter
    if p30 >= FAKE_PUMP_VOLUME_PERCENT and abs(change10) <= FAKE_PUMP_MAX_PRICE_MOVE:
        return None

    # لا نريد عملات بعيدة جدًا عن الدعم
    if distance > MAX_WATCH_DISTANCE_PERCENT:
        return None

    volume_score = p30 * 0.55
    price_score = change10 * 28
    liquidity_score = min(vol30 / 10000, 65)
    support_score = max(0, 45 - (distance * 18))
    whale_bonus = 30 if (vol30 - prev30) >= WHALE_VOLUME_DIFF_USDT else 0

    early_bonus = 0
    if (
        p30 >= EARLY_VOLUME_PERCENT
        and -0.2 <= change10 <= 1.2
        and change30 >= -0.7
        and distance <= 2.2
    ):
        early_bonus = 35

    score = volume_score + price_score + liquidity_score + support_score + whale_bonus + early_bonus

    return {
        "symbol": symbol,
        "pair": fmt(symbol),
        "price": price,
        "vol30": vol30,
        "vol60": vol60,
        "p30": p30,
        "p60": p60,
        "vol_diff30": vol30 - prev30,
        "vol_diff60": vol60 - prev60,
        "price_change_10m": change10,
        "price_change_30m": change30,
        "support_30m": support,
        "resistance_30m": resistance,
        "distance_from_support": distance,
        "score": score
    }


def get_alert_type(a):
    plan = trade_plan(a)

    # 🚫 FOMO Filter
    if (
        plan["distance"] > FOMO_MAX_DISTANCE_PERCENT
        or a["price_change_10m"] > FOMO_MAX_10M_MOVE
    ):
        if (
            a["p30"] >= WATCH_VOLUME_PERCENT
            and a["vol_diff30"] > 0
            and a["score"] >= 80
            and plan["distance"] <= MAX_WATCH_DISTANCE_PERCENT
        ):
            return "watch"

        return None

    # 🚀 Ultra VIP
    if (
        a["score"] >= ULTRA_VIP_SCORE
        and a["p30"] >= ULTRA_VIP_VOLUME_PERCENT
        and a["price_change_10m"] >= 0.4
        and a["price_change_10m"] <= 4.5
        and plan["distance"] <= ULTRA_MAX_DISTANCE_PERCENT
        and a["vol_diff30"] >= WHALE_VOLUME_DIFF_USDT
    ):
        return "ultra_vip"

    # 💎 GOLD VIP
    if (
        a["score"] >= GOLD_SCORE
        and a["p30"] >= VIP_VOLUME_PERCENT
        and a["price_change_10m"] >= 0.4
        and plan["distance"] <= MAX_ENTRY_DISTANCE_PERCENT
    ):
        return "gold_vip"

    # 🔥 VIP
    if (
        a["score"] >= VIP_SCORE
        and a["p30"] >= VIP_VOLUME_PERCENT
        and a["price_change_10m"] >= 0.35
        and plan["distance"] <= MAX_ENTRY_DISTANCE_PERCENT
    ):
        return "vip_trade"

    # 👀 Early VIP
    if (
        a["p30"] >= EARLY_VOLUME_PERCENT
        and -0.2 <= a["price_change_10m"] <= 1.2
        and a["price_change_30m"] >= -0.7
        and plan["distance"] <= 2.2
        and a["score"] >= 100
    ):
        return "early_vip"

    # 📊 Watch
    if (
        a["p30"] >= WATCH_VOLUME_PERCENT
        and a["vol_diff30"] > 0
        and a["score"] >= 80
    ):
        return "watch"

    return None


def register_trade(a, alert_type):
    if alert_type not in ["ultra_vip", "gold_vip", "vip_trade", "early_vip"]:
        return

    plan = trade_plan(a)
    data = load_data()

    trade_id = f"{a['symbol']}_{int(time.time())}"

    trade = {
        "id": trade_id,
        "symbol": a["symbol"],
        "pair": a["pair"],
        "type": alert_type,
        "entry": plan["entry"],
        "stop_loss": plan["stop_loss"],
        "target1": plan["target1"],
        "target2": plan["target2"],
        "score": a["score"],
        "confidence": confidence_percent(a),
        "opened_at": int(time.time()),
        "status": "active"
    }

    data["active"].append(trade)
    save_data(data)


def check_trades_results():
    import os
import time
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TRADE_CHAT_ID = os.getenv("TRADE_CHAT_ID")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID")

REPORT_TIMEZONE = os.getenv("REPORT_TIMEZONE", "Asia/Hebron")

GATE_BASE = "https://api.gateio.ws/api/v4"
DATA_FILE = "trades_data.json"
ALERTS_FILE = "alerts_data.json"

CHECK_EVERY_SECONDS = 10
REFRESH_SYMBOLS_SECONDS = 300
MAX_WORKERS = 10
MAX_ALERTS_PER_CYCLE = 5
ALERT_COOLDOWN_SECONDS = 45 * 60

MIN_24H_VOLUME_USDT = 700000
MIN_VOLUME_30M_USDT = 40000

SUPPORT_LOOKBACK_1M = 30
SUPPORT_LOOKBACK_4H = 40
RESISTANCE_LOOKBACK_4H = 60

WATCH_VOLUME_PERCENT = 55
EARLY_VOLUME_PERCENT = 80
VIP_VOLUME_PERCENT = 110
ULTRA_VIP_VOLUME_PERCENT = 140

MAX_ENTRY_DISTANCE_PERCENT = 1.8
MAX_WATCH_DISTANCE_PERCENT = 3.5
ULTRA_MAX_DISTANCE_PERCENT = 1.0

FOMO_MAX_DISTANCE_PERCENT = 1.8
FOMO_MAX_10M_MOVE = 5.0

MAX_DUMP_10M_PERCENT = -3.5
MAX_PUMP_10M_PERCENT = 8
MAX_PRICE_30M_PERCENT = 15

FAKE_PUMP_VOLUME_PERCENT = 160
FAKE_PUMP_MAX_PRICE_MOVE = 0.25

WHALE_VOLUME_DIFF_USDT = 70000

VIP_SCORE = 130
GOLD_SCORE = 160
ULTRA_VIP_SCORE = 180

ENTRY_ABOVE_4H_SUPPORT = 0.005
TARGET_BELOW_4H_RESISTANCE = 0.005
MIN_RR = 1.4

last_update_id = 0


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_data():
    return load_json(DATA_FILE, {"active": [], "closed": []})


def save_data(data):
    save_json(DATA_FILE, data)


def load_alerts():
    return load_json(ALERTS_FILE, {})


def save_alerts(data):
    save_json(ALERTS_FILE, data)


def send(msg, symbol=None, chat_id=None):
    if not msg or not msg.strip():
        return

    if not BOT_TOKEN:
        print("Missing BOT_TOKEN")
        return

    target = chat_id if chat_id else CHAT_ID

    if not target:
        print("Missing CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": str(target),
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if symbol:
        data["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "📈 عرض الشارت", "url": f"https://www.gate.io/trade/{symbol}"}
            ]]
        }

    try:
        r = requests.post(url, json=data, timeout=15)
        print("Telegram:", r.status_code, r.text[:150])
    except Exception as e:
        print("Telegram error:", e)


def send_photo(photo_path, caption="", chat_id=None):
    if not BOT_TOKEN:
        return

    target = chat_id if chat_id else REPORT_CHAT_ID
    if not target:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    try:
        with open(photo_path, "rb") as photo:
            files = {"photo": photo}
            data = {
                "chat_id": str(target),
                "caption": caption,
                "parse_mode": "HTML"
            }
            r = requests.post(url, data=data, files=files, timeout=20)
            print("Telegram photo:", r.status_code, r.text[:120])
    except Exception as e:
        print("send_photo error:", e)


def safe_float(x):
    try:
        return float(x)
    except:
        return 0


def fmt(symbol):
    return symbol.replace("_", "/")


def is_bad_symbol(symbol):
    bad = ["3S", "3L", "5S", "5L", "BEAR", "BULL", "DOWN", "UP"]
    return any(x in symbol for x in bad)


def can_alert(symbol, alert_type):
    alerts = load_alerts()
    key = f"{symbol}_{alert_type}"
    now = time.time()

    last_time = alerts.get(key, 0)

    if now - last_time >= ALERT_COOLDOWN_SECONDS:
        alerts[key] = now
        save_alerts(alerts)
        return True

    return False


def get_symbols():
    try:
        data = requests.get(f"{GATE_BASE}/spot/tickers", timeout=20).json()
    except Exception as e:
        print("get_symbols error:", e)
        return []

    symbols = []

    for item in data:
        pair = item.get("currency_pair", "")
        vol = safe_float(item.get("quote_volume", 0))

        if pair.endswith("_USDT") and not is_bad_symbol(pair) and vol >= MIN_24H_VOLUME_USDT:
            symbols.append(pair)

    return symbols


def get_klines(symbol, interval="1m", limit=120):
    params = {
        "currency_pair": symbol,
        "interval": interval,
        "limit": limit
    }

    try:
        data = requests.get(
            f"{GATE_BASE}/spot/candlesticks",
            params=params,
            timeout=15
        ).json()

        if not isinstance(data, list):
            return []

        return sorted(data, key=lambda x: int(x[0]))

    except Exception as e:
        print(f"get_klines error {symbol} {interval}:", e)
        return []


def get_current_price(symbol):
    try:
        data = requests.get(
            f"{GATE_BASE}/spot/tickers",
            params={"currency_pair": symbol},
            timeout=10
        ).json()

        if isinstance(data, list) and data:
            return safe_float(data[0].get("last"))
    except Exception as e:
        print("get_current_price error:", e)

    return 0


def calculate_support(data, lookback):
    lows = []

    for candle in data[-lookback:]:
        low = safe_float(candle[4])
        if low > 0:
            lows.append(low)

    return min(lows) if lows else None


def calculate_resistance(data, lookback):
    highs = []

    for candle in data[-lookback:]:
        high = safe_float(candle[3])
        if high > 0:
            highs.append(high)

    return max(highs) if highs else None


def nearest_resistance_above(data, entry, lookback):
    highs = []

    for candle in data[-lookback:]:
        high = safe_float(candle[3])
        if high > entry:
            highs.append(high)

    return min(highs) if highs else None


def get_4h_levels(symbol):
    data_4h = get_klines(symbol, interval="4h", limit=100)

    if len(data_4h) < 50:
        return None

    support_4h = calculate_support(data_4h, SUPPORT_LOOKBACK_4H)

    if not support_4h:
        return None

    entry = support_4h * (1 + ENTRY_ABOVE_4H_SUPPORT)

    resistance_4h = nearest_resistance_above(
        data_4h,
        entry,
        RESISTANCE_LOOKBACK_4H
    )

    if not resistance_4h:
        resistance_4h = calculate_resistance(data_4h, RESISTANCE_LOOKBACK_4H)

    if not resistance_4h or resistance_4h <= entry:
        return None

    target1 = resistance_4h * (1 - TARGET_BELOW_4H_RESISTANCE)

    if target1 <= entry:
        return None

    reward_pct = ((target1 - entry) / entry) * 100

    sl_buffer_pct = max(1.2, min(2.8, reward_pct / MIN_RR))
    stop_loss = entry * (1 - sl_buffer_pct / 100)

    below_support_stop = support_4h * 0.992

    if stop_loss > below_support_stop:
        stop_loss = below_support_stop

    risk = entry - stop_loss
    reward = target1 - entry
    rr = reward / risk if risk > 0 else 0

    target2 = resistance_4h

    return {
        "support_4h": support_4h,
        "resistance_4h": resistance_4h,
        "entry": entry,
        "stop_loss": stop_loss,
        "target1": target1,
        "target2": target2,
        "rr": rr,
        "reward_pct": reward_pct,
        "sl_buffer_pct": ((entry - stop_loss) / entry) * 100
    }


def whale_text(a):
    if a["vol_diff30"] >= WHALE_VOLUME_DIFF_USDT:
        return "🐋 <b>Whale Activity Detected</b>"
    return ""


def signal_quality(score):
    if score >= 180:
        return "💎 Elite"
    elif score >= 150:
        return "🔥 Strong"
    elif score >= 110:
        return "🟡 Good"
    return "🔴 Risky"


def vip_label(a):
    if a["score"] >= ULTRA_VIP_SCORE:
        return "🚀 ULTRA VIP"
    if a["score"] >= GOLD_SCORE:
        return "💎 GOLD VIP"
    if a["score"] >= VIP_SCORE:
        return "🔥 VIP"
    if a["score"] >= 100:
        return "🟡 جيد"
    return "⚪ مراقبة"


def trade_plan(a):
    price = a["price"]

    entry = a["entry_4h"]
    stop_loss = a["stop_loss_4h"]
    target1 = a["target1_4h"]
    target2 = a["target2_4h"]

    distance_to_entry = ((price - entry) / entry) * 100 if entry > 0 else 0
    distance_from_support = ((price - a["support_4h"]) / a["support_4h"]) * 100 if a["support_4h"] > 0 else 999

    risk = entry - stop_loss
    reward = target1 - entry
    rr = reward / risk if risk > 0 else 0

    if price <= entry:
        status = "✅ السعر عند أو تحت منطقة الدخول — دخول Limit مناسب"
    elif distance_to_entry <= 0.6:
        status = "🟢 قريب جدًا من الدخول — لا تدخل ماركت بعنف"
    elif distance_to_entry <= MAX_ENTRY_DISTANCE_PERCENT:
        status = "🟡 السعر أعلى من الدخول — الأفضل انتظار رجوع لمنطقة الدخول"
    else:
        status = "🚫 FOMO — السعر بعيد عن دخول 4H"

    return {
        "entry": entry,
        "stop_loss": stop_loss,
        "target1": target1,
        "target2": target2,
        "distance": distance_to_entry,
        "distance_from_support": distance_from_support,
        "rr": rr,
        "status": status
    }


def confidence_percent(a):
    plan = trade_plan(a)

    score_part = min(a["score"] / 200 * 40, 40)
    volume_part = min(a["p30"] / 180 * 25, 25)
    distance_part = max(0, 20 - max(plan["distance"], 0) * 10)
    whale_part = 10 if a["vol_diff30"] >= WHALE_VOLUME_DIFF_USDT else 0
    rr_part = 5 if plan["rr"] >= MIN_RR else 0
    price_part = 5 if 0.2 <= a["price_change_10m"] <= 4 else 0

    confidence = score_part + volume_part + distance_part + whale_part + rr_part + price_part

    return max(0, min(confidence, 95))


def analyze(symbol):
    if is_bad_symbol(symbol):
        return None

    data = get_klines(symbol, interval="1m", limit=120)

    if not isinstance(data, list) or len(data) < 120:
        return None

    levels_4h = get_4h_levels(symbol)

    if not levels_4h:
        return None

    vol30 = sum(safe_float(x[1]) for x in data[-30:])
    prev30 = sum(safe_float(x[1]) for x in data[-60:-30])

    vol60 = sum(safe_float(x[1]) for x in data[-60:])
    prev60 = sum(safe_float(x[1]) for x in data[-120:-60])

    if vol30 <= 0 or prev30 <= 0 or vol60 <= 0 or prev60 <= 0:
        return None

    if vol30 < MIN_VOLUME_30M_USDT:
        return None

    p30 = ((vol30 - prev30) / prev30) * 100
    p60 = ((vol60 - prev60) / prev60) * 100

    price = safe_float(data[-1][2])
    price_10 = safe_float(data[-10][2])
    price_30 = safe_float(data[-30][2])

    if price <= 0 or price_10 <= 0 or price_30 <= 0:
        return None

    change10 = ((price - price_10) / price_10) * 100
    change30 = ((price - price_30) / price_30) * 100

    support_1m = calculate_support(data, SUPPORT_LOOKBACK_1M)
    resistance_1m = calculate_resistance(data, SUPPORT_LOOKBACK_1M)

    if not support_1m:
        return None

    distance_4h_entry = ((price - levels_4h["entry"]) / levels_4h["entry"]) * 100

    if change10 <= MAX_DUMP_10M_PERCENT:
        return None

    if change10 >= MAX_PUMP_10M_PERCENT:
        return None

    if change30 >= MAX_PRICE_30M_PERCENT:
        return None

    if p30 >= FAKE_PUMP_VOLUME_PERCENT and abs(change10) <= FAKE_PUMP_MAX_PRICE_MOVE:
        return None

    if distance_4h_entry > MAX_WATCH_DISTANCE_PERCENT:
        return None

    if levels_4h["rr"] < 1.0:
        return None

    volume_score = p30 * 0.55
    price_score = change10 * 28
    liquidity_score = min(vol30 / 10000, 65)
    entry_score = max(0, 45 - max(distance_4h_entry, 0) * 18)
    rr_score = min(levels_4h["rr"] * 12, 25)
    whale_bonus = 30 if (vol30 - prev30) >= WHALE_VOLUME_DIFF_USDT else 0

    early_bonus = 0
    if (
        p30 >= EARLY_VOLUME_PERCENT
        and -0.2 <= change10 <= 1.2
        and change30 >= -0.7
        and distance_4h_entry <= 2.2
    ):
        early_bonus = 35

    score = volume_score + price_score + liquidity_score + entry_score + rr_score + whale_bonus + early_bonus

    return {
        "symbol": symbol,
        "pair": fmt(symbol),
        "price": price,
        "vol30": vol30,
        "vol60": vol60,
        "p30": p30,
        "p60": p60,
        "vol_diff30": vol30 - prev30,
        "vol_diff60": vol60 - prev60,
        "price_change_10m": change10,
        "price_change_30m": change30,
        "support_1m": support_1m,
        "resistance_1m": resistance_1m,
        "support_4h": levels_4h["support_4h"],
        "resistance_4h": levels_4h["resistance_4h"],
        "entry_4h": levels_4h["entry"],
        "stop_loss_4h": levels_4h["stop_loss"],
        "target1_4h": levels_4h["target1"],
        "target2_4h": levels_4h["target2"],
        "rr_4h": levels_4h["rr"],
        "score": score
    }


def get_alert_type(a):
    plan = trade_plan(a)

    if (
        plan["distance"] > FOMO_MAX_DISTANCE_PERCENT
        or a["price_change_10m"] > FOMO_MAX_10M_MOVE
    ):
        if (
            a["p30"] >= WATCH_VOLUME_PERCENT
            and a["vol_diff30"] > 0
            and a["score"] >= 80
            and plan["distance"] <= MAX_WATCH_DISTANCE_PERCENT
        ):
            return "watch"

        return None

    if (
        a["score"] >= ULTRA_VIP_SCORE
        and a["p30"] >= ULTRA_VIP_VOLUME_PERCENT
        and 0.4 <= a["price_change_10m"] <= 4.5
        and plan["distance"] <= ULTRA_MAX_DISTANCE_PERCENT
        and a["vol_diff30"] >= WHALE_VOLUME_DIFF_USDT
        and plan["rr"] >= MIN_RR
    ):
        return "ultra_vip"

    if (
        a["score"] >= GOLD_SCORE
        and a["p30"] >= VIP_VOLUME_PERCENT
        and a["price_change_10m"] >= 0.4
        and plan["distance"] <= MAX_ENTRY_DISTANCE_PERCENT
        and plan["rr"] >= MIN_RR
    ):
        return "gold_vip"

    if (
        a["score"] >= VIP_SCORE
        and a["p30"] >= VIP_VOLUME_PERCENT
        and a["price_change_10m"] >= 0.35
        and plan["distance"] <= MAX_ENTRY_DISTANCE_PERCENT
        and plan["rr"] >= 1.2
    ):
        return "vip_trade"

    if (
        a["p30"] >= EARLY_VOLUME_PERCENT
        and -0.2 <= a["price_change_10m"] <= 1.2
        and a["price_change_30m"] >= -0.7
        and plan["distance"] <= 2.2
        and a["score"] >= 100
        and plan["rr"] >= 1.1
    ):
        return "early_vip"

    if (
        a["p30"] >= WATCH_VOLUME_PERCENT
        and a["vol_diff30"] > 0
        and a["score"] >= 80
    ):
        return "watch"

    return None


def register_trade(a, alert_type):
    if alert_type not in ["ultra_vip", "gold_vip", "vip_trade", "early_vip"]:
        return

    plan = trade_plan(a)
    data = load_data()

    trade_id = f"{a['symbol']}_{int(time.time())}"

    trade = {
        "id": trade_id,
        "symbol": a["symbol"],
        "pair": a["pair"],
        "type": alert_type,
        "entry": plan["entry"],
        "stop_loss": plan["stop_loss"],
        "target1": plan["target1"],
        "target2": plan["target2"],
        "support_4h": a["support_4h"],
        "resistance_4h": a["resistance_4h"],
        "score": a["score"],
        "quality": signal_quality(a["score"]),
        "confidence": confidence_percent(a),
        "opened_at": int(time.time()),
        "status": "active",
        "tp1_hit": False,
        "tp2_hit": False
    }

    data["active"].append(trade)
    save_data(data)


def check_trades_results():
    data = load_data()
    active = data.get("active", [])
    closed = data.get("closed", [])

    still_active = []

    for trade in active:
        symbol = trade["symbol"]
        price = get_current_price(symbol)

        if price <= 0:
            still_active.append(trade)
            continue

        tp1_hit = trade.get("tp1_hit", False)

        if price <= trade["stop_loss"]:
            trade["status"] = "loss"
            trade["closed_at"] = int(time.time())
            trade["closed_price"] = price
            closed.append(trade)

            send(
                f"🛑 <b>SL HIT</b>\n{trade['pair']} | السعر: <b>{price}</b>",
                symbol,
                REPORT_CHAT_ID or TRADE_CHAT_ID
            )
            continue

        if not tp1_hit and price >= trade["target1"]:
            trade["tp1_hit"] = True

            send(
                f"🎯 <b>TP1 HIT</b>\n{trade['pair']} | السعر: <b>{price}</b>",
                symbol,
                REPORT_CHAT_ID or TRADE_CHAT_ID
            )

            still_active.append(trade)
            continue

        if price >= trade["target2"]:
            trade["status"] = "win"
            trade["closed_at"] = int(time.time())
            trade["closed_price"] = price
            trade["tp2_hit"] = True
            closed.append(trade)

            send(
                f"🚀 <b>TP2 HIT</b>\n{trade['pair']} | السعر: <b>{price}</b>",
                symbol,
                REPORT_CHAT_ID or TRADE_CHAT_ID
            )
            continue

        still_active.append(trade)

    data["active"] = still_active
    data["closed"] = closed
    save_data(data)


def best_trading_time_text():
    data = load_data()
    closed = data.get("closed", [])

    if not closed:
        return "🧠 لا يوجد بيانات كافية لتحليل أفضل وقت تداول."

    tz = ZoneInfo(REPORT_TIMEZONE)
    hours = {}

    for t in closed:
        opened_at = t.get("opened_at")
        if not opened_at:
            continue

        hour = datetime.fromtimestamp(opened_at, tz).hour

        if hour not in hours:
            hours[hour] = {"total": 0, "wins": 0}

        hours[hour]["total"] += 1

        if t.get("status") == "win":
            hours[hour]["wins"] += 1

    valid = []

    for hour, s in hours.items():
        if s["total"] >= 2:
            win_rate = (s["wins"] / s["total"]) * 100
            valid.append((win_rate, s["total"], hour, s["wins"]))

    if not valid:
        return "🧠 لا يوجد صفقات كافية بعد لتحديد أفضل وقت تداول."

    valid.sort(reverse=True)
    best = valid[0]

    return f"""🧠 <b>أفضل وقت تداول حتى الآن</b>

⏰ الساعة الأفضل: <b>{best[2]:02d}:00</b> بتوقيت {REPORT_TIMEZONE}
✅ نجاح: <b>{best[3]}</b>
📊 عدد الصفقات: <b>{best[1]}</b>
📈 Win Rate: <b>{best[0]:.1f}%</b>"""


def build_report():
    data = load_data()
    closed = data.get("closed", [])
    active = data.get("active", [])

    total = len(closed)
    wins = len([t for t in closed if t.get("status") == "win"])
    losses = len([t for t in closed if t.get("status") == "loss"])
    win_rate = (wins / total * 100) if total > 0 else 0

    by_type = {}

    for t in closed:
        typ = t.get("type", "unknown")
        if typ not in by_type:
            by_type[typ] = {"total": 0, "wins": 0, "losses": 0}

        by_type[typ]["total"] += 1

        if t.get("status") == "win":
            by_type[typ]["wins"] += 1
        elif t.get("status") == "loss":
            by_type[typ]["losses"] += 1

    type_lines = ""

    for typ, s in by_type.items():
        wr = (s["wins"] / s["total"] * 100) if s["total"] > 0 else 0
        type_lines += f"\n• {typ}: {s['wins']}/{s['total']} — <b>{wr:.1f}%</b>"

    if not type_lines:
        type_lines = "\nلا يوجد نتائج مغلقة بعد."

    best_time = best_trading_time_text()

    return f"""📊 <b>تقرير أداء البوت VIP</b>

📌 الصفقات المغلقة: <b>{total}</b>
✅ ناجحة: <b>{wins}</b>
🛑 خاسرة: <b>{losses}</b>
📈 Win Rate: <b>{win_rate:.1f}%</b>

⏳ صفقات نشطة حاليًا: <b>{len(active)}</b>

📂 <b>حسب نوع الإشارة:</b>{type_lines}

{best_time}

🕒 الأوامر:
/report
/chart
/besttime
/help"""


def create_performance_chart():
    try:
        import matplotlib.pyplot as plt
    except Exception as e:
        print("matplotlib not available:", e)
        return None

    data = load_data()
    closed = data.get("closed", [])

    if not closed:
        return None

    wins = 0
    losses = 0
    labels = []
    values = []

    for i, t in enumerate(closed, start=1):
        if t.get("status") == "win":
            wins += 1
        elif t.get("status") == "loss":
            losses += 1

        total = wins + losses
        win_rate = (wins / total * 100) if total else 0

        labels.append(i)
        values.append(win_rate)

    path = "/tmp/performance_chart.png"

    plt.figure(figsize=(8, 4))
    plt.plot(labels, values, marker="o")
    plt.title("VIP Bot Win Rate Performance")
    plt.xlabel("Closed Trades")
    plt.ylabel("Win Rate %")
    plt.ylim(0, 100)
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(path)
    plt.close()

    return path


def check_report_command():
    global last_update_id

    if not BOT_TOKEN:
        return

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

        params = {
            "offset": last_update_id + 1,
            "timeout": 0,
            "allowed_updates": ["message"]
        }

        res = requests.get(url, params=params, timeout=5)
        data = res.json()

        if not data.get("ok"):
            print("getUpdates error:", data)
            return

        for update in data.get("result", []):
            last_update_id = update.get("update_id", last_update_id)

            msg = update.get("message")
            if not msg:
                continue

            text = msg.get("text", "").strip()
            chat_id = msg["chat"]["id"]

            print("Command received:", text)

            if text == "/report" or text.startswith("/report@"):
                report = build_report()

                if REPORT_CHAT_ID:
                    send(report, chat_id=REPORT_CHAT_ID)
                    send("✅ تم إرسال التقرير إلى قناة التقارير", chat_id=chat_id)
                else:
                    send(report, chat_id=chat_id)

            elif text == "/chart" or text.startswith("/chart@"):
                chart = create_performance_chart()

                if chart:
                    send_photo(
                        chart,
                        caption="📈 <b>رسم بياني لأداء Win Rate</b>",
                        chat_id=REPORT_CHAT_ID or chat_id
                    )
                    send("✅ تم إرسال الرسم البياني", chat_id=chat_id)
                else:
                    send("لا يوجد بيانات كافية للرسم البياني بعد.", chat_id=chat_id)

            elif text == "/besttime" or text.startswith("/besttime@"):
                send(best_trading_time_text(), chat_id=REPORT_CHAT_ID or chat_id)

            elif text == "/help" or text.startswith("/help@"):
                send(
                    """🤖 <b>أوامر البوت</b>

/report - إرسال تقرير الأداء
/chart - إرسال الرسم البياني
/besttime - تحليل أفضل وقت تداول
/help - عرض الأوامر""",
                    chat_id=chat_id
                )

    except Exception as e:
        print("Report command error:", e)


def vip_message(a, title):
    plan = trade_plan(a)
    whale = whale_text(a)
    label = vip_label(a)
    quality = signal_quality(a["score"])

    return f"""{title}
<b>{a['pair']}</b> على Gate.io

🏷️ التصنيف: <b>{label}</b>
📊 الجودة: <b>{quality}</b>
💰 السعر الحالي: <b>${a['price']:.6f}</b>

🕓 <b>خطة 4 ساعات</b>
📌 دعم 4H: <b>${a['support_4h']:.6f}</b>
🟢 Entry قبل الدعم بـ 0.5%: <b>${plan['entry']:.6f}</b>
🛑 Stop Loss مناسب: <b>${plan['stop_loss']:.6f}</b>
🎯 Target 1 قبل المقاومة بـ 0.5%: <b>${plan['target1']:.6f}</b>
🚀 Target 2 عند المقاومة: <b>${plan['target2']:.6f}</b>
🧱 مقاومة 4H: <b>${a['resistance_4h']:.6f}</b>

📍 بُعد السعر عن الدخول: <b>{plan['distance']:.2f}%</b>
⚖️ R/R: <b>{plan['rr']:.2f}</b>

🟢 تغير 10د: <b>{a['price_change_10m']:.2f}%</b>
📈 تغير 30د: <b>{a['price_change_30m']:.2f}%</b>

📊 فوليوم 30د: <b>{a['vol30']:,.0f} USDT</b>
🚀 ارتفاع الفوليوم: <b>{a['p30']:.2f}%</b>
⬆️ زيادة الفوليوم: <b>{a['vol_diff30']:,.0f} USDT</b>

{whale}

🧠 VIP Smart Score: <b>{a['score']:.2f}</b>
🎯 Confidence: <b>{confidence_percent(a):.1f}%</b>

{plan['status']}

⚠️ ليست توصية شراء مباشرة."""


def early_message(a):
    return vip_message(a, "👀🚀 <b>Early VIP Signal</b>")


def watch_message(a):
    plan = trade_plan(a)
    whale = whale_text(a)
    quality = signal_quality(a["score"])

    return f"""📊 <b>VIP Watchlist</b>
<b>{a['pair']}</b> على Gate.io

📊 الجودة: <b>{quality}</b>
💰 السعر الحالي: <b>${a['price']:.6f}</b>

🕓 <b>خطة 4 ساعات</b>
📌 دعم 4H: <b>${a['support_4h']:.6f}</b>
🟢 Entry: <b>${plan['entry']:.6f}</b>
🛑 Stop Loss: <b>${plan['stop_loss']:.6f}</b>
🎯 Target 1: <b>${plan['target1']:.6f}</b>
🚀 Target 2: <b>${plan['target2']:.6f}</b>
🧱 مقاومة 4H: <b>${a['resistance_4h']:.6f}</b>

📍 بُعد السعر عن الدخول: <b>{plan['distance']:.2f}%</b>
⚖️ R/R: <b>{plan['rr']:.2f}</b>

🟢 تغير 10د: <b>{a['price_change_10m']:.2f}%</b>
📈 تغير 30د: <b>{a['price_change_30m']:.2f}%</b>

📊 فوليوم 30د: <b>{a['vol30']:,.0f} USDT</b>
🚀 ارتفاع الفوليوم: <b>{a['p30']:.2f}%</b>
⬆️ الزيادة: <b>{a['vol_diff30']:,.0f} USDT</b>

{whale}

🧠 Score: <b>{a['score']:.2f}</b>
🎯 Confidence: <b>{confidence_percent(a):.1f}%</b>

{plan['status']}

⚠️ مراقبة فقط — ليست توصية VIP بعد."""


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
        print(f"Error with {symbol}:", e)
        return None


symbols = get_symbols()
last_symbols_refresh = time.time()

print(f"Loaded Gate symbols: {len(symbols)}")
print("VIP BOT 4H ENTRY + 4H TARGET + FIXED TP/SL + REPORT RUNNING ✅")

while True:
    try:
        check_report_command()
        check_trades_results()

        if time.time() - last_symbols_refresh >= REFRESH_SYMBOLS_SECONDS:
            symbols = get_symbols()
            last_symbols_refresh = time.time()
            print(f"Symbols refreshed: {len(symbols)}")

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

            symbol = a["symbol"]

            if not can_alert(symbol, alert_type):
                continue

            print(
                f"Sending {symbol} | {alert_type} | "
                f"Score: {score:.2f} | p30: {a['p30']:.2f}%"
            )

            if alert_type == "ultra_vip":
                send(vip_message(a, "🚀🔥 <b>ULTRA VIP TRADE</b>"), symbol, TRADE_CHAT_ID)
                register_trade(a, alert_type)

            elif alert_type == "gold_vip":
                send(vip_message(a, "💎🔥 <b>GOLD VIP TRADE</b>"), symbol, TRADE_CHAT_ID)
                register_trade(a, alert_type)

            elif alert_type == "vip_trade":
                send(vip_message(a, "🔥 <b>VIP TRADE</b>"), symbol, TRADE_CHAT_ID)
                register_trade(a, alert_type)

            elif alert_type == "early_vip":
                send(early_message(a), symbol, TRADE_CHAT_ID)
                register_trade(a, alert_type)

            elif alert_type == "watch":
                send(watch_message(a), symbol, CHAT_ID)

            sent += 1
            time.sleep(1)

        print(f"Cycle done | Sent: {sent}\n")
        time.sleep(CHECK_EVERY_SECONDS)

    except Exception as e:
        print("Main Error:", e)
        time.sleep(10)


def best_trading_time_text():
    data = load_data()
    closed = data.get("closed", [])

    if not closed:
        return "🧠 لا يوجد بيانات كافية لتحليل أفضل وقت تداول."

    tz = ZoneInfo(REPORT_TIMEZONE)
    hours = {}

    for t in closed:
        opened_at = t.get("opened_at")
        if not opened_at:
            continue

        hour = datetime.fromtimestamp(opened_at, tz).hour

        if hour not in hours:
            hours[hour] = {"total": 0, "wins": 0}

        hours[hour]["total"] += 1

        if t.get("status") == "win":
            hours[hour]["wins"] += 1

    valid = []

    for hour, s in hours.items():
        if s["total"] >= 2:
            win_rate = (s["wins"] / s["total"]) * 100
            valid.append((win_rate, s["total"], hour, s["wins"]))

    if not valid:
        return "🧠 لا يوجد صفقات كافية بعد لتحديد أفضل وقت تداول."

    valid.sort(reverse=True)
    best = valid[0]

    return f"""🧠 <b>أفضل وقت تداول حتى الآن</b>

⏰ الساعة الأفضل: <b>{best[2]:02d}:00</b> بتوقيت {REPORT_TIMEZONE}
✅ نجاح: <b>{best[3]}</b>
📊 عدد الصفقات: <b>{best[1]}</b>
📈 Win Rate: <b>{best[0]:.1f}%</b>"""


def build_report():
    data = load_data()
    closed = data.get("closed", [])
    active = data.get("active", [])

    total = len(closed)
    wins = len([t for t in closed if t.get("status") == "win"])
    losses = len([t for t in closed if t.get("status") == "loss"])
    win_rate = (wins / total * 100) if total > 0 else 0

    by_type = {}

    for t in closed:
        typ = t.get("type", "unknown")
        if typ not in by_type:
            by_type[typ] = {"total": 0, "wins": 0, "losses": 0}

        by_type[typ]["total"] += 1

        if t.get("status") == "win":
            by_type[typ]["wins"] += 1
        elif t.get("status") == "loss":
            by_type[typ]["losses"] += 1

    type_lines = ""

    for typ, s in by_type.items():
        wr = (s["wins"] / s["total"] * 100) if s["total"] > 0 else 0
        type_lines += f"\n• {typ}: {s['wins']}/{s['total']} — <b>{wr:.1f}%</b>"

    if not type_lines:
        type_lines = "\nلا يوجد نتائج مغلقة بعد."

    best_time = best_trading_time_text()

    return f"""📊 <b>تقرير أداء البوت VIP</b>

📌 الصفقات المغلقة: <b>{total}</b>
✅ ناجحة: <b>{wins}</b>
🛑 خاسرة: <b>{losses}</b>
📈 Win Rate: <b>{win_rate:.1f}%</b>

⏳ صفقات نشطة حاليًا: <b>{len(active)}</b>

📂 <b>حسب نوع الإشارة:</b>{type_lines}

{best_time}

🕒 الأوامر:
/report
/chart
/besttime"""


def create_performance_chart():
    try:
        import matplotlib.pyplot as plt
    except Exception as e:
        print("matplotlib not available:", e)
        return None

    data = load_data()
    closed = data.get("closed", [])

    if not closed:
        return None

    wins = 0
    losses = 0
    labels = []
    values = []

    for i, t in enumerate(closed, start=1):
        if t.get("status") == "win":
            wins += 1
        elif t.get("status") == "loss":
            losses += 1

        total = wins + losses
        win_rate = (wins / total * 100) if total else 0

        labels.append(i)
        values.append(win_rate)

    path = "/tmp/performance_chart.png"

    plt.figure(figsize=(8, 4))
    plt.plot(labels, values, marker="o")
    plt.title("VIP Bot Win Rate Performance")
    plt.xlabel("Closed Trades")
    plt.ylabel("Win Rate %")
    plt.ylim(0, 100)
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(path)
    plt.close()

    return path


def check_report_command():
    global last_update_id

    if not BOT_TOKEN:
        return

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

        params = {
            "offset": last_update_id + 1,
            "timeout": 0,
            "allowed_updates": ["message"]
        }

        res = requests.get(url, params=params, timeout=5)
        data = res.json()

        if not data.get("ok"):
            print("getUpdates error:", data)
            return

        for update in data.get("result", []):
            last_update_id = update.get("update_id", last_update_id)

            msg = update.get("message")
            if not msg:
                continue

            text = msg.get("text", "").strip()
            chat_id = msg["chat"]["id"]

            print("Command received:", text)

            if text == "/report" or text.startswith("/report@"):
                report = build_report()

                if REPORT_CHAT_ID:
                    send(report, chat_id=REPORT_CHAT_ID)

                send("✅ تم إرسال التقرير إلى قناة التقارير", chat_id=chat_id)

            elif text == "/chart" or text.startswith("/chart@"):
                chart = create_performance_chart()

                if chart:
                    send_photo(
                        chart,
                        caption="📈 <b>رسم بياني لأداء Win Rate</b>",
                        chat_id=REPORT_CHAT_ID or chat_id
                    )
                    send("✅ تم إرسال الرسم البياني إلى قناة التقارير", chat_id=chat_id)
                else:
                    send("لا يوجد بيانات كافية للرسم البياني بعد.", chat_id=chat_id)

            elif text == "/besttime" or text.startswith("/besttime@"):
                send(best_trading_time_text(), chat_id=REPORT_CHAT_ID or chat_id)
                send("✅ تم إرسال تحليل أفضل وقت تداول", chat_id=chat_id)

            elif text == "/help" or text.startswith("/help@"):
                send(
                    """🤖 <b>أوامر البوت</b>

/report - إرسال تقرير الأداء
/chart - إرسال الرسم البياني
/besttime - تحليل أفضل وقت تداول
/help - عرض الأوامر""",
                    chat_id=chat_id
                )

    except Exception as e:
        print("Report command error:", e)


def vip_message(a, title):
    plan = trade_plan(a)
    whale = whale_text(a)
    label = vip_label(a)

    return f"""{title}
<b>{a['pair']}</b> على Gate.io

🏷️ التصنيف: <b>{label}</b>
💰 السعر الحالي: <b>${a['price']:.6f}</b>
📌 دعم 30د: <b>${a['support_30m']:.6f}</b>
📍 بُعد السعر عن الدعم: <b>{plan['distance']:.2f}%</b>

🟢 تغير 10د: <b>{a['price_change_10m']:.2f}%</b>
📈 تغير 30د: <b>{a['price_change_30m']:.2f}%</b>

📊 فوليوم 30د: <b>{a['vol30']:,.0f} USDT</b>
🚀 ارتفاع الفوليوم: <b>{a['p30']:.2f}%</b>
⬆️ زيادة الفوليوم: <b>{a['vol_diff30']:,.0f} USDT</b>

{whale}

🧠 VIP Smart Score: <b>{a['score']:.2f}</b>
🎯 Confidence: <b>{confidence_percent(a):.1f}%</b>

🎯 <b>خطة VIP</b>
🟢 Entry: <b>${plan['entry']:.6f}</b>
🛑 Stop Loss: <b>${plan['stop_loss']:.6f}</b>
🎯 Target 1: <b>${plan['target1']:.6f}</b>
🚀 Target 2: <b>${plan['target2']:.6f}</b>
⚖️ R/R: <b>{plan['rr']:.2f}</b>

{plan['status']}

⚠️ ليست توصية شراء مباشرة."""


def early_message(a):
    return vip_message(a, "👀🚀 <b>Early VIP Signal</b>")


def watch_message(a):
    plan = trade_plan(a)
    whale = whale_text(a)

    return f"""📊 <b>VIP Watchlist</b>
<b>{a['pair']}</b> على Gate.io

💰 السعر: <b>${a['price']:.6f}</b>
📌 دعم 30د: <b>${a['support_30m']:.6f}</b>
📍 البعد عن الدعم: <b>{plan['distance']:.2f}%</b>

🟢 تغير 10د: <b>{a['price_change_10m']:.2f}%</b>
📈 تغير 30د: <b>{a['price_change_30m']:.2f}%</b>

📊 فوليوم 30د: <b>{a['vol30']:,.0f} USDT</b>
🚀 ارتفاع الفوليوم: <b>{a['p30']:.2f}%</b>
⬆️ الزيادة: <b>{a['vol_diff30']:,.0f} USDT</b>

{whale}

🧠 Score: <b>{a['score']:.2f}</b>
🎯 Confidence: <b>{confidence_percent(a):.1f}%</b>

👀 <b>منطقة مراقبة</b>
🟢 دخول أفضل قرب: <b>${plan['entry']:.6f}</b>
{plan['status']}

⚠️ مراقبة فقط — ليست توصية VIP بعد."""


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
        print(f"Error with {symbol}:", e)
        return None


symbols = get_symbols()
print(f"Loaded Gate symbols: {len(symbols)}")
print("VIP BOT + REPORT + ULTRA + FOMO + CONFIDENCE RUNNING ✅")

while True:
    try:
        check_report_command()
        check_trades_results()

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

            symbol = a["symbol"]

            if not can_alert(symbol, alert_type):
                continue

            print(
                f"Sending {symbol} | {alert_type} | "
                f"Score: {score:.2f} | p30: {a['p30']:.2f}%"
            )

            if alert_type == "ultra_vip":
                send(vip_message(a, "🚀🔥 <b>ULTRA VIP TRADE</b>"), symbol, TRADE_CHAT_ID)
                register_trade(a, alert_type)

            elif alert_type == "gold_vip":
                send(vip_message(a, "💎🔥 <b>GOLD VIP TRADE</b>"), symbol, TRADE_CHAT_ID)
                register_trade(a, alert_type)

            elif alert_type == "vip_trade":
                send(vip_message(a, "🔥 <b>VIP TRADE</b>"), symbol, TRADE_CHAT_ID)
                register_trade(a, alert_type)

            elif alert_type == "early_vip":
                send(early_message(a), symbol, TRADE_CHAT_ID)
                register_trade(a, alert_type)

            elif alert_type == "watch":
                send(watch_message(a), symbol, CHAT_ID)

            sent += 1
            time.sleep(1)

        print(f"Cycle done | Sent: {sent}\n")
        time.sleep(CHECK_EVERY_SECONDS)

    except Exception as e:
        print("Main Error:", e)
        time.sleep(10)