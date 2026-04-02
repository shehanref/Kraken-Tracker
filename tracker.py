import logging
import requests
import os
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, Defaults
from colorama import init, Fore, Style
from flask import Flask
from threading import Thread

# Flask server for Render keep-alive
server = Flask('')

@server.route('/')
def home():
    return "AMI Terminal is Online"

def run():
    # রেন্ডার PORT ইনভারনমেন্ট ভেরিয়েবল ব্যবহার করে, ডিফল্ট ১০০০০
    port = int(os.environ.get('PORT', 10000))
    server.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True # থ্রেডটিকে ডেমোন হিসেবে সেট করা হলো
    t.start()

init(autoreset=True)

# --- CONFIGURATION ---
TOKEN = os.environ.get("BOT_TOKEN")
PAIR = "AMIUSD"
MARCH_23_TS = 1711152000 
GROUP_CHAT_ID = int(os.environ.get("GROUP_ID", 0))
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
MIN_TRADE_USD = 50.0   

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.ERROR)

def log_hacker(msg, type="sys"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    colors = {"buy": Fore.GREEN, "sell": Fore.RED, "sys": Fore.CYAN, "cmd": Fore.YELLOW}
    color = colors.get(type, Fore.WHITE)
    print(f"{color}[{timestamp}] {msg}{Style.RESET_ALL}")

# --- SECURITY CHECK ---
async def is_authorized(update: Update):
    chat_type = update.effective_chat.type
    user_id = update.effective_user.id
    if chat_type == "private":
        if user_id == ADMIN_ID: return True
        await update.message.reply_text("ওই মামা না পিলিজ")
        return False
    return True

# --- DATA ENGINE ---
def get_kraken_data(endpoint, extra_params=None):
    url = f"https://api.kraken.com/0/public/{endpoint}"
    params = {"pair": PAIR}
    if extra_params: params.update(extra_params)
    try:
        res = requests.get(url, params=params, timeout=10).json()
        if "result" in res:
            result_data = res["result"]
            for key in result_data:
                if key != "last": return result_data[key]
    except: return None
    return None

async def get_price():
    data = get_kraken_data("Ticker")
    return float(data['c'][0]) if data else 0.0

# --- DASHBOARD LOGIC ---
async def update_dashboard(context: ContextTypes.DEFAULT_TYPE, target_chat_id=None):
    final_chat_id = target_chat_id if target_chat_id else GROUP_CHAT_ID
    ticker = get_kraken_data("Ticker")
    ohlc_1h = get_kraken_data("OHLC", {"interval": 60})
    ohlc_1d = get_kraken_data("OHLC", {"interval": 1440})

    if not ticker or not ohlc_1h or not ohlc_1d: return

    price = float(ticker['c'][0])
    vol_24h = float(ticker['v'][1])
    vol_6h = sum(float(hour[6]) for hour in ohlc_1h[-6:])
    vol_7d = sum(float(day[6]) for day in ohlc_1d[-7:])
    vol_march = sum(float(day[6]) for day in ohlc_1d if day[0] >= MARCH_23_TS)
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    msg = (
        f"🖥 *AMI TERMINAL DASHBOARD*\n\n"
        f"🎯 *Current Price:* `${price:,.4f}`\n\n"
        f"🕒 *Last 6 Hours:* \n🪙 `{vol_6h:,.0f} AMI` | 💵 `${vol_6h * price:,.0f}`\n\n"
        f"📅 *Last 24 Hours:* \n🪙 `{vol_24h:,.0f} AMI` | 💵 `${vol_24h * price:,.0f}`\n\n"
        f"📊 *Last 7 Days:* \n🪙 `{vol_7d:,.0f} AMI` | 💵 `${vol_7d * price:,.0f}`\n\n"
        f"🏆 *Since March 23:* \n🪙 `{vol_march:,.0f} AMI` | 💵 `${vol_march * price:,.0f}`\n\n"
        f"⚡ _Updated: {timestamp}_"
    )

    try:
        await context.bot.send_message(chat_id=final_chat_id, text=msg)
    except Exception as e:
        log_hacker(f"Dashboard Send Failed: {e}", "sell")

# --- LIVE FEED ---
async def trade_scanner(context: ContextTypes.DEFAULT_TYPE):
    if 'seen' not in context.bot_data: context.bot_data['seen'] = set()
    trades = get_kraken_data("Trades")
    if trades and isinstance(trades, list):
        if not context.bot_data.get('init'):
            for t in trades: context.bot_data['seen'].add(t[2])
            context.bot_data['init'] = True
            return
        for t in trades:
            tid = t[2]
            if tid in context.bot_data['seen']: continue
            p, q, side = float(t[0]), float(t[1]), t[3]
            usd_val = p * q
            if usd_val < MIN_TRADE_USD:
                context.bot_data['seen'].add(tid)
                continue
            whale = " 🚨 WHALE 🚨" if usd_val > 1000 else ""
            msg = (f"{'🟢 BUY' if side == 'b' else '🔴 SELL'}{whale}\n"
                   f"🪙 `{q:,.0f} AMI` | 💵 `${usd_val:,.2f}`\n"
                   f"🎯 Price: `${p:,.4f}`")
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=msg)
            context.bot_data['seen'].add(tid)

# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    await update.message.reply_text("👾 *Terminal Access Granted*\n/trader | /allday | /vol | /indiv_vol | /indiv_vol_23 | /status")

async def cmd_trader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    jid = f"j_{GROUP_CHAT_ID}"
    jobs = context.job_queue.get_jobs_by_name(jid)
    if jobs:
        for j in jobs: j.schedule_removal()
        await update.message.reply_text("🛑 *Live Feed: OFFLINE*")
    else:
        context.job_queue.run_repeating(trade_scanner, interval=8, chat_id=GROUP_CHAT_ID, name=jid)
        await update.message.reply_text("⚡ *Live Feed: ONLINE*")

async def cmd_allday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    data = get_kraken_data("Ticker")
    if data:
        ami_v, price = float(data['v'][1]), float(data['c'][0])
        await update.message.reply_text(f"🕒 *24H Rolling Volume*\n🪙 `{ami_v:,.0f} AMI`\n💵 `${ami_v * price:,.2f}`")

async def cmd_vol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    data = get_kraken_data("OHLC", {"interval": 1440})
    price = await get_price()
    if data:
        total_ami = sum(float(day[6]) for day in data)
        await update.message.reply_text(f"🌍 *Total Volume (Listing to Now)*\n🪙 `{total_ami:,.0f} AMI`\n💵 `${total_ami * price:,.2f}`")

async def cmd_indiv_vol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    data = get_kraken_data("OHLC", {"interval": 1440})
    price = await get_price()
    if data:
        msg = "📅 *Last 7 Days Breakdown*\n"
        for d in data[-7:]:
            date = datetime.fromtimestamp(d[0]).strftime('%d %b')
            ami = float(d[6])
            msg += f"• `{date}`: `{ami:,.0f} AMI` | `${ami*price:,.0f}`\n"
        await update.message.reply_text(msg)

async def cmd_indiv_vol_23(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    data = get_kraken_data("OHLC", {"interval": 1440})
    price = await get_price()
    if data:
        msg = "📅 *Daily Vol (Since Mar 23)*\n"
        total = 0
        for d in data:
            if d[0] >= MARCH_23_TS:
                date = datetime.fromtimestamp(d[0]).strftime('%d %b')
                ami = float(d[6])
                total += ami
                msg += f"• `{date}`: `{ami:,.0f} AMI`\n"
        msg += f"\n🏆 *Cumulative: `${total * price:,.2f}`*"
        await update.message.reply_text(msg)

if __name__ == '__main__':
    log_hacker("CORE INITIALIZED", "sys")
    
    # বোট শুরু করার আগে Keep Alive সার্ভার চালু করা হলো
    keep_alive()
    
    # Builder-এ .job_queue() যুক্ত করা হয়েছে
    # Timeouts বাড়ানো হয়েছে যাতে কানেকশন ড্রপ না হয়
    app = Application.builder() \
        .token(TOKEN) \
        .job_queue() \
        .connect_timeout(40) \
        .read_timeout(40) \
        .write_timeout(40) \
        .pool_timeout(40) \
        .defaults(Defaults(parse_mode='Markdown')) \
        .build()
    
    # ড্যাশবোর্ড আপডেট শিডিউলার
    app.job_queue.run_repeating(lambda c: update_dashboard(c), interval=600, first=10)
    
    # কমান্ড হ্যান্ডলারসমূহ
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("trader", cmd_trader))
    app.add_handler(CommandHandler("allday", cmd_allday))
    app.add_handler(CommandHandler("vol", cmd_vol))
    app.add_handler(CommandHandler("indiv_vol", cmd_indiv_vol))
    app.add_handler(CommandHandler("indiv_vol_23", cmd_indiv_vol_23))
    app.add_handler(CommandHandler("status", lambda u, c: update_dashboard(c, u.effective_chat.id)))
    
    log_hacker("POLLING STARTED...", "sys")
    app.run_polling(drop_pending_updates=True)
