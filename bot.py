import requests
import time
import os

# ========================
# CONFIG (من Railway)
# ========================
BOT_TOKEN = os.getenv("8763101324:AAF5JGbRp2x9kkFFZM8_uvPSgROMDx7GhCc")
CHAT_ID = os.getenv("1003791131305")

BASE_URL = "https://api.gateio.ws/api/v4"
INTERVAL = 30  # seconds

# ========================
# SEND TELEGRAM
# ========================
def send(text):
    url = f"https://api.telegram.org/bot8763101324:AAF5JGbRp2x9kkFFZM8_uvPSgROMDx7GhCc/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text
    }

    try:
        r = requests.post(url, data=data, timeout=10)
        print("Telegram:", r.status_code, r.text)
    except Exception as e:
        print("Telegram ERROR:", e)

# ========================
# GET SYMBOLS
# ========================
def get_symbols():
    url = f"{BASE_URL}/spot/currency_pairs"
    data = requests.get(url).json()
    return [x["id"] for x in data if x["quote"] == "USDT"]

# ========================
# GET DATA
# ========================
def get_klines(symbol):
    url = f"{BASE_URL}/spot/candlesticks"
    params = {
        "currency_pair": symbol,
        "interval": "1m",
        "limit": 60
    }
    return requests.get(url, params=params).json()

# ========================
# ANALYZE
# ========================
def analyze(symbol):
    data = get_klines(symbol)

    if not isinstance(data, list) or len(data) < 30:
        return None

    data = sorted(data, key=lambda x: int(x[0]))

    vol_now = sum(float(x[1]) for x in data[-30:])
    vol_prev = sum(float(x[1]) for x in data[-60:-30])

    if vol_prev == 0:
        return None

    spike = (vol_now - vol_prev) / vol_prev * 100

    price = float(data[-1][2])
    price_prev = float(data[-10][2])
    price_change = (price - price_prev) / price_prev * 100

    score = spike + price_change

    return {
        "symbol": symbol,
        "price": price,
        "spike": spike,
        "price_change": price_change,
        "score": score
    }

# ========================
# FORMAT MESSAGE
# ========================
def format_msg(a):
    return f"""
🚀 {a['symbol']}

Price: {a['price']:.4f}
Spike: {a['spike']:.2f}%
Change: {a['price_change']:.2f}%

Score: {a['score']:.2f}
"""

# ========================
# MAIN LOOP
# ========================
def main():
    print("Bot started...")

    # TEST MESSAGE
    send("🔥 BOT STARTED 🔥")

    symbols = get_symbols()
    print("Symbols:", len(symbols))

    while True:
        try:
            print("Scanning...")

            results = []
            for s in symbols[:50]:  # limit for speed
                a = analyze(s)
                if a and a["spike"] >= 100:  # Strong only
                    results.append(a)

            results.sort(key=lambda x: x["score"], reverse=True)

            for r in results[:3]:  # top 3
                send(format_msg(r))

            print("Sent:", len(results))

        except Exception as e:
            print("ERROR:", e)

        time.sleep(INTERVAL)

# ========================
# RUN
# ========================
if __name__ == "__main__":
    main()