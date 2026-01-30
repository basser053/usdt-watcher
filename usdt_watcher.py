import os
import json
import time
import requests
from datetime import datetime
from base58 import b58decode_check
from decimal import Decimal, InvalidOperation

# ================= ENV (GitHub Secrets) =================
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

# ================= HELPERS =================
def b58_to_hex(addr: str) -> str:
    return b58decode_check(addr).hex()

def pad32(h: str) -> str:
    return h.rjust(64, "0")

def short_address(addr: str) -> str:
    return "..." + addr[-6:]

def fmt_like_site_decimal(d: Decimal, max_decimals: int = 6) -> str:
    """
    شكل قريب من TronScan:
    - فواصل آلاف
    - حتى 6 منازل
    - بدون أصفار زائدة
    """
    q = Decimal("1." + ("0" * max_decimals))
    d = d.quantize(q)

    s = format(d, "f").rstrip("0").rstrip(".")
    if s == "" or s == "-0":
        s = "0"

    if "." in s:
        whole, frac = s.split(".", 1)
        return f"{int(whole):,}.{frac}"
    return f"{int(s):,}"

def parse_usdt_balance(raw) -> Decimal:
    """
    TronGrid ممكن يرجّع USDT:
    - '123.45' جاهز
    - '123450000' خام بدون نقطة => لازم / 1e6
    """
    s = str(raw).strip()
    if not s or s.lower() == "none":
        return Decimal("0")
    try:
        if "." in s:
            return Decimal(s)
        return Decimal(s) / Decimal("1000000")
    except (InvalidOperation, ValueError):
        return Decimal("0")

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

    r = requests.post(TRONGRID_TRIGGER, headers=HEADERS, data=json.dumps(payload), timeout=20)
    r.raise_for_status()
    result = r.json().get("constant_result")
    if not result:
        raise RuntimeError("❌ TronGrid: constant_result فاضي")
    return int(result[0], 16) == 1

def get_balances():
    r = requests.get(TRONGRID_ACCOUNT, headers=HEADERS, timeout=20)
    r.raise_for_status()
    resp = r.json()

    data_list = resp.get("data", [])
    if not data_list:
        return Decimal("0"), Decimal("0")

    data = data_list[0]

    # TRX: sun -> TRX
    trx = Decimal(str(data.get("balance", 0))) / Decimal("1000000")

    # USDT TRC20
    usdt_raw = "0"
    for token in data.get("trc20", []):
        if USDT_CONTRACT in token:
            usdt_raw = token[USDT_CONTRACT]
            break

    usdt = parse_usdt_balance(usdt_raw)
    return usdt, trx

# ================= TELEGRAM =================
def send_telegram(text: str, loud: bool = True):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_notification": (not loud),  # loud=True => صوت
        },
        timeout=20,
    )

# ================= MAIN =================
def run_once():
    blocked = is_blacklisted(ADDRESS)
    usdt_balance, trx_balance = get_balances()

    # GitHub Actions time = UTC
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    short_addr = short_address(ADDRESS)

    usdt_txt = fmt_like_site_decimal(usdt_balance, max_decimals=6)
    trx_txt = fmt_like_site_decimal(trx_balance, max_decimals=6)

    if blocked:
        msg = (
            "🚫 تنبيه تجميد USDT\n\n"
            f"📍 العنوان:\n{short_addr}\n\n"
            "⚠️ الحالة:\nمُجمَّد (Blacklisted)\n\n"
            f"💵 رصيد USDT:\n{usdt_txt}\n\n"
            f"⚡ رصيد TRX:\n{trx_txt}\n\n"
            f"⏰ وقت الفحص:\n{now}"
        )
    else:
        msg = (
            "✅ حالة USDT طبيعية\n\n"
            f"📍 العنوان:\n{short_addr}\n\n"
            "🟢 الحالة:\nغير مُجمَّد (سليم)\n\n"
            f"💵 رصيد USDT:\n{usdt_txt}\n\n"
            f"⚡ رصيد TRX:\n{trx_txt}\n\n"
            f"⏰ وقت الفحص:\n{now}"
        )

    # 🔔 “رنّة طويلة” عملياً: 3 رسائل بصوت
    send_telegram(msg, loud=True)


if __name__ == "__main__":
    try:
        run_once()
    except Exception as e:
        # خليه بصوت لأنه مهم تعرف إن في مشكلة
        send_telegram(f"⚠️ خطأ أثناء الفحص:\n{e}", loud=True)
        raise
