import os
import json
import requests
from datetime import datetime, timezone
from base58 import b58decode_check
from decimal import Decimal, InvalidOperation

# ================= ENV =================
BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")
ADDRESS = os.getenv("WATCH_ADDRESS")

if not BOT_TOKEN or not CHAT_ID or not TRONGRID_API_KEY or not ADDRESS:
    raise SystemExit("❌ ناقص Secrets: TG_BOT_TOKEN / TG_CHAT_ID / TRONGRID_API_KEY / WATCH_ADDRESS")

# ================= CONSTANTS =================
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRONGRID_TRIGGER = "https://api.trongrid.io/wallet/triggerconstantcontract"
TRONGRID_ACCOUNT = f"https://api.trongrid.io/v1/accounts/{ADDRESS}"

HEADERS = {
    "TRON-PRO-API-KEY": TRONGRID_API_KEY,
    "Content-Type": "application/json",
}

STATE_FILE = "state.json"   # سنحفظ آخر حالة هنا (لتنبيه فك التجميد)

# ================= HELPERS =================
def b58_to_hex(addr: str) -> str:
    return b58decode_check(addr).hex()

def pad32(h: str) -> str:
    return h.rjust(64, "0")

def short_last6(addr: str) -> str:
    return "..." + addr[-6:]

def fmt_like_site(d: Decimal, decimals: int) -> str:
    """
    مثل المواقع: فواصل آلاف + عدد منازل ثابت (USDT=2, TRX=6)
    مثال: 1,234.50 أو 0.000022
    """
    q = Decimal("1." + ("0" * decimals))
    try:
        d2 = d.quantize(q)
    except InvalidOperation:
        d2 = Decimal("0").quantize(q)

    s = format(d2, "f")  # ثابت
    if "." in s:
        whole, frac = s.split(".", 1)
        try:
            whole_i = int(whole)
        except ValueError:
            whole_i = 0
        return f"{whole_i:,}.{frac}"
    return s

def parse_usdt_balance(raw) -> Decimal:
    s = str(raw).strip()
    if not s or s.lower() == "none":
        return Decimal("0")
    try:
        if "." in s:
            return Decimal(s)
        return Decimal(s) / Decimal("1000000")  # خام / 1e6
    except (InvalidOperation, ValueError):
        return Decimal("0")

def send_telegram(text: str, loud: bool = True) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_notification": (not loud),  # loud=True => إشعار بصوت
        },
        timeout=25,
    )
    r.raise_for_status()

def load_prev_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

# ================= TRON CHECKS =================
def is_blacklisted(addr: str) -> bool:
    addr_hex = b58_to_hex(addr)

    payload = {
        "owner_address": addr_hex,
        "contract_address": b58_to_hex(USDT_CONTRACT),
        "function_selector": "isBlackListed(address)",
        "parameter": pad32(addr_hex),
        "visible": False,
    }

    r = requests.post(TRONGRID_TRIGGER, headers=HEADERS, data=json.dumps(payload), timeout=25)
    r.raise_for_status()

    result = r.json().get("constant_result", [])
    if not result:
        raise RuntimeError("TronGrid لم يرجّع constant_result")

    return int(result[0], 16) == 1

def get_balances():
    r = requests.get(TRONGRID_ACCOUNT, headers=HEADERS, timeout=25)
    r.raise_for_status()

    resp = r.json()
    data_list = resp.get("data", [])
    if not data_list:
        return Decimal("0"), Decimal("0")

    data = data_list[0]

    # TRX (sun -> TRX)
    trx = Decimal(str(data.get("balance", 0))) / Decimal("1000000")

    # USDT من trc20 list
    usdt_raw = "0"
    for token_obj in data.get("trc20", []):
        if isinstance(token_obj, dict) and USDT_CONTRACT in token_obj:
            usdt_raw = token_obj[USDT_CONTRACT]
            break

    usdt = parse_usdt_balance(usdt_raw)
    return usdt, trx

# ================= MAIN (RUN ONCE) =================
def main():
    prev = load_prev_state()
    prev_blocked = prev.get("blocked")

    blocked = is_blacklisted(ADDRESS)
    usdt_balance, trx_balance = get_balances()

    # وقت UTC (مناسب للسيرفر)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    addr_short = short_last6(ADDRESS)

    # تنسيق “مثل الموقع”
    usdt_txt = fmt_like_site(usdt_balance, decimals=2)  # USDT غالبًا 2
    trx_txt = fmt_like_site(trx_balance, decimals=6)    # TRX غالبًا 6

    status_line = "مُجمَّد (Blacklisted) 🚫" if blocked else "سليم (Not Blacklisted) ✅"

    msg = (
        "📌 تقرير فحص USDT\n\n"
        f"🏷️ العنوان: {addr_short}\n"
        f"📍 الحالة: {status_line}\n\n"
        f"💵 رصيد USDT: {usdt_txt}\n"
        f"⚡ رصيد TRX: {trx_txt}\n\n"
        f"⏰ وقت الفحص: {now}"
    )

    # بدك “يرن” دائمًا: نخليها loud دائمًا
    send_telegram(msg, loud=True)

    # تنبيه إضافي إذا صار فك تجميد (من مُجمّد إلى سليم)
    if prev_blocked is True and blocked is False:
        send_telegram("🎉🎉 تم فك التجميد! العنوان صار سليم ✅ (تنبيه عاجل)", loud=True)
        send_telegram("🔔🔔🔔", loud=True)  # محاولة “رنة طويلة” عمليًا برسائل متتابعة

    save_state({"blocked": blocked, "checked_at": now})
    print(msg)

if __name__ == "__main__":
    main()
