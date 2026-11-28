import logging
import asyncio
import hashlib
import html
import traceback
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
    
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)

API_ID = 33048137
API_HASH = "94a4d1f437612096d1a5e5128e46b0d8"
BOT_TOKEN = "9008700016:AAF_17P26Xllb0W5g2LJTlCW6itSfJI_Pus"

MONGO_URI = (
    "mongodb://animebot:Animezone2026@"
    "ac-0ia0zfl-shard-00-00.xkwletj.mongodb.net:27017,"
    "ac-0ia0zfl-shard-00-01.xkwletj.mongodb.net:27017,"
    "ac-0ia0zfl-shard-00-02.xkwletj.mongodb.net:27017/"
    "?ssl=true&authSource=admin&retryWrites=true&w=majority"
)


app = Client("anime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

db_client = None
db = None
seasons_collection = None
master_menus_collection = None


user_sessions = {}
master_menu_sessions = {}

KEEP_ANIMES = {"testanime"}

def make_season_code(anime_name: str) -> str:
    digest = hashlib.md5(anime_name.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"s_{digest}"

def make_menu_code(anime_name: str) -> str:
    digest = hashlib.md5(anime_name.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"m_{digest}"

def format_menu_season_label(index_num: int) -> str:
    return f"Season {index_num:02d}"

def build_embedded_menu_html(anime_name: str, entries: list) -> str:
    safe_title = html.escape(anime_name)
    lines = [f"<b>🎬 ANIME: {safe_title}</b>", ""]
    for idx, item in enumerate(entries, start=1):
        label = html.escape(item.get("label") or format_menu_season_label(idx))
        link = (item.get("link") or "").strip()
        if link:
            safe_link = html.escape(link, quote=True)
            lines.append(f'❖ <a href="{safe_link}">{label}</a>')
        else:
            lines.append(f"❖ {label}")
    return "\n".join(lines)

def extract_wait_seconds(err: Exception):
    val = getattr(err, "value", None)
    if isinstance(val, (int, float)):
        return float(val)
    msg = str(err)
    for token in msg.replace("(", " ").replace(")", " ").split():
        if token.isdigit():
            return float(token)
    return None

async def safe_reply(message: Message, text: str, reply_markup=None):
    for _ in range(4):
        try:
            return await message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.DISABLED
            )
        except Exception as e:
            logging.error(f"safe_reply error: {repr(e)}")
            wait_sec = extract_wait_seconds(e)
            if wait_sec is not None:
                await asyncio.sleep(wait_sec + 1)
            else:
                await asyncio.sleep(1.5)
    return None

async def safe_reply_html(message: Message, text: str):
    for _ in range(4):
        try:
            return await message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
        except Exception as e:
            logging.error(f"safe_reply_html error: {repr(e)}")
            wait_sec = extract_wait_seconds(e)
            if wait_sec is not None:
                await asyncio.sleep(wait_sec + 1)
            else:
                await asyncio.sleep(1.5)
    return None

async def send_video_item(client: Client, chat_id: int, entry: dict):
    caption_text = entry.get("caption") or None
    file_id = entry["file_id"]

    try:
        await client.send_cached_media(
            chat_id=chat_id,
            file_id=file_id,
            caption=caption_text,
            parse_mode=ParseMode.DISABLED
        )
        return True
    except Exception as e1:
        logging.warning(f"send_cached_media failed, trying send_video: {repr(e1)}")
        try:
            await client.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=caption_text,
                parse_mode=ParseMode.DISABLED
            )
            return True
        except Exception as e2:
            logging.warning(f"send_video failed, trying send_document: {repr(e2)}")
            await client.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=caption_text,
                parse_mode=ParseMode.DISABLED
            )
            return True

async def send_single_item(client: Client, chat_id: int, entry: dict):
    item_type = entry.get("type")
    order_num = entry.get("order", "?")
    max_retries = 2

    for attempt in range(1, max_retries + 1):
        try:
            if item_type == "sticker":
                await client.send_sticker(
                    chat_id=chat_id,
                    sticker=entry["file_id"]
                )
            elif item_type == "photo":
                await client.send_photo(
                    chat_id=chat_id,
                    photo=entry["file_id"],
                    caption=entry.get("caption") or None,
                    parse_mode=ParseMode.DISABLED
                )
            elif item_type == "text":
                await client.send_message(
                    chat_id=chat_id,
                    text=entry["text"],
                    parse_mode=ParseMode.DISABLED
                )
            elif item_type == "video":
                await send_video_item(client, chat_id, entry)
            return True
        except Exception as e:
            wait_sec = extract_wait_seconds(e)
            if wait_sec is not None:
                sleep_time = wait_sec + 1
                logging.warning(f"FloodWait on item #{order_num}, sleeping {sleep_time}s")
                await asyncio.sleep(sleep_time)
            else:
                logging.error(f"Attempt {attempt}/{max_retries} failed for item #{order_num} ({item_type}): {repr(e)}")
                traceback.print_exc()
                if attempt < max_retries:
                    await asyncio.sleep(1)
                else:
                    return False

async def send_season_items(client: Client, chat_id: int, season_doc: dict):
    items = season_doc.get("items", [])
    sorted_items = sorted(
        items,
        key=lambda x: x.get("order", 0)
    )
    sent_ok = 0
    failed_orders = []

    for entry in sorted_items:
        ok = await send_single_item(client, chat_id, entry)
        if ok:
            sent_ok += 1
        else:
            failed_orders.append(entry.get("order", "?"))
        await asyncio.sleep(0.3)

    return sent_ok, len(sorted_items), failed_orders

def format_session_summary(anime_name: str, session: dict) -> str:
    counts = session["counts"]
    total = counts["video"] + counts["photo"] + counts["sticker"] + counts["text"]
    return (
        f"📋 {anime_name} - Formation Info\n"
        f"• 🎬 Videos: {counts['video']}\n"
        f"• 🖼️ Banner Photos: {counts['photo']}\n"
        f"• 🌟 Stickers: {counts['sticker']}\n"
        f"• 📝 Texts/Posts: {counts['text']}\n"
        f"• 📦 Total Saved Items: {total}"
    )

# =========================================================
# LOCKED SECTION: /season complete & /done (Untouched Core Flow)
# =========================================================

@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) == 2:
        code = parts[1].strip()
        if code.startswith("s_"):
            season_doc = await seasons_collection.find_one({"season_code": code})
            if not season_doc:
                await safe_reply(message, "❌ এই সিজন লিংকটি আর পাওয়া যাচ্ছে না।")
                return
            await safe_reply(message, f"📤 {season_doc['anime_name']} পাঠানো হচ্ছে...")
            sent_ok, total_items, failed_orders = await send_season_items(client, message.chat.id, season_doc)
            if failed_orders:
                await safe_reply(
                    message,
                    f"⚠️ ডেলিভারি শেষ হয়েছে: {sent_ok}/{total_items} টি আইটেম এসেছে। সিরিয়াল #{failed_orders} পাঠাতে সমস্যা হয়েছে।"
                )
            return

        if code.startswith("m_"):
            menu_doc = await master_menus_collection.find_one({"menu_code": code})
            if not menu_doc:
                await safe_reply(message, "❌ এই মাস্টার মেনু লিংকটি পাওয়া যায়নি।")
                return
            menu_html = build_embedded_menu_html(
                menu_doc["anime_name"],
                menu_doc.get("seasons", [])
            )
            await safe_reply_html(message, menu_html)
            return

    await safe_reply(
        message,
        "👋 স্বাগতম! আপনার অ্যানিমে টেলিগ্রাম বট প্রস্তুত রয়েছে।\n\n"
        "সিজন শুরু করতে:\n/season complete [Anime Name]\n"
        "শেষ হলে:\n/done\n"
        "মাস্টার মেনু বানাতে:\n/buildmenu [Anime Name]"
    )

@app.on_message(filters.command("clean") & filters.regex("tests"))
async def clean_test_seasons(client, message: Message):
    all_seasons = await seasons_collection.find().to_list(length=200)
    deleted_names = []

    for doc in all_seasons:
        title = doc.get("anime_name", "")
        if title.strip().lower() not in KEEP_ANIMES:
            await seasons_collection.delete_one({"_id": doc["_id"]})
            deleted_names.append(title)

    if not deleted_names:
        await safe_reply(message, "ℹ️ TestAnime ছাড়া ডিলিট করার মতো আর কোনো সিজন পাওয়া যায়নি।")
        return

    await safe_reply(
        message,
        "🗑️ TestAnime ছাড়া বাকি সিজনগুলো মুছে ফেলা হয়েছে:\n• " + "\n• ".join(deleted_names)
    )

@app.on_message(filters.command("season"))
async def season_handler(client, message: Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3 or args[1].lower() != "complete":
        await safe_reply(message, "⚠️ সঠিক নিয়মে কমান্ড দিন:\n/season complete [Anime Name]")
        return

    anime_name = args[2].strip()
    user_id = message.from_user.id

    user_sessions[user_id] = {
        "anime_name": anime_name,
        "items": [],
        "counts": {
            "video": 0,
            "photo": 0,
            "sticker": 0,
            "text": 0
        }
    }

    await safe_reply(
        message,
        f"✅ অ্যানিমে: {anime_name} এর জন্য সেশন শুরু হয়েছে!\n\n"
        "স্টিকার, ব্যানার ছবি, পোস্ট ও ভিডিও ধাপে ধাপে পাঠান।\n"
        "সব দেওয়া শেষ হলে লিখুন: /done"
    )

@app.on_message(
    (filters.video | filters.photo | filters.sticker | filters.text)
    & ~filters.bot
    & ~filters.command(["start", "season", "done", "master", "clean", "buildmenu", "savemenu", "showmenu"])
)
async def collect_items(client, message: Message):
    user_id = message.from_user.id

    if user_id in master_menu_sessions and message.text:
        m_session = master_menu_sessions[user_id]
        raw_link = message.text.strip()
        next_num = len(m_session["seasons"]) + 1
        label = format_menu_season_label(next_num)
        m_session["seasons"].append({
            "season_number": next_num,
            "label": label,
            "link": raw_link
        })
        next_label = format_menu_season_label(next_num + 1)
        await safe_reply(
            message,
            f"Done ✅ {label} এর লিংক যুক্ত হয়েছে!\n\n"
            f"এখন {next_label}-এর লিংক দিন (অথবা সব শেষ হলে /savemenu লিখুন)।"
        )
        return

    if user_id not in user_sessions:
        return

    session = user_sessions[user_id]
    anime_name = session["anime_name"]
    next_order = len(session["items"]) + 1

    if message.video:
        session["items"].append({
            "order": next_order,
            "type": "video",
            "file_id": message.video.file_id,
            "caption": message.caption or ""
        })
        session["counts"]["video"] += 1
        added_label = f"📥 ভিডিও যুক্ত হয়েছে (#{next_order})"

    elif message.photo:
        session["items"].append({
            "order": next_order,
            "type": "photo",
            "file_id": message.photo.file_id,
            "caption": message.caption or ""
        })
        session["counts"]["photo"] += 1
        added_label = f"🖼️ ব্যানার ছবি যুক্ত হয়েছে (#{next_order})"

    elif message.sticker:
        session["items"].append({
            "order": next_order,
            "type": "sticker",
            "file_id": message.sticker.file_id,
            "emoji": message.sticker.emoji or ""
        })
        session["counts"]["sticker"] += 1
        added_label = f"🌟 স্টিকার যুক্ত হয়েছে (#{next_order})"

    elif message.text:
        session["items"].append({
            "order": next_order,
            "type": "text",
            "text": message.text
        })
        session["counts"]["text"] += 1
        added_label = f"📝 পোস্ট/লেখা যুক্ত হয়েছে (#{next_order})"

    summary = format_session_summary(anime_name, session)
    await safe_reply(message, f"{added_label}\n\n{summary}")

@app.on_message(filters.command("done"))
async def done_handler(client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_sessions:
        await safe_reply(message, "⚠️ কোনো অ্যাক্টিভ আপলোড সেশন নেই! প্রথমে /season complete [Anime Name] ব্যবহার করুন।")
        return

    session_data = user_sessions.pop(user_id)
    anime_name = session_data["anime_name"]

    if not session_data["items"]:
        await safe_reply(message, "❌ কোনো স্টিকার, ছবি, পোস্ট বা ভিডিও আপলোড করা হয়নি!")
        return

    season_code = make_season_code(anime_name)
    await seasons_collection.update_one(
        {"anime_name": anime_name},
        {
            "$set": {
                "season_code": season_code,
                "items": session_data["items"],
                "counts": session_data["counts"]
            }
        },
        upsert=True
    )

    bot_info = await client.get_me()
    deep_link = f"https://t.me/{bot_info.username}?start={season_code}"
    summary = format_session_summary(anime_name, session_data)

    await safe_reply(
        message,
        f"🎉 {anime_name} সম্পূর্ণভাবে সেভ হয়েছে!\n\n"
        f"{summary}\n\n"
        f"🔗 Complete Season Link:\n{deep_link}"
    )

@app.on_message(filters.command("master") & filters.regex("menu"))
async def master_menu(client, message: Message):
    all_seasons = await seasons_collection.find().to_list(length=100)

    if not all_seasons:
        await safe_reply(message, "📭 বর্তমানে কোনো অ্যানিমে সিজন ডাটাবেজে সেভ করা নেই।")
        return

    keyboard = []
    for item in all_seasons:
        anime_title = item["anime_name"]
        keyboard.append([InlineKeyboardButton(f"🎬 {anime_title}", callback_data=f"anime_{anime_title}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_reply(
        message,
        "📂 Asian Anime Zone - Master Menu\n\nনিচের বাটন থেকে আপনার পছন্দের অ্যানিমে সিজন বেছে নিন:",
        reply_markup=reply_markup
    )

@app.on_callback_query(filters.regex("^anime_"))
async def anime_button_handler(client, callback_query: CallbackQuery):
    anime_name = callback_query.data.replace("anime_", "", 1)
    season_doc = await seasons_collection.find_one({"anime_name": anime_name})

    if not season_doc:
        await callback_query.answer("❌ এই সিজনটি ডাটাবেজে পাওয়া যায়নি।", show_alert=True)
        return

    await callback_query.answer(f"📤 {anime_name} পাঠানো হচ্ছে...")
    await send_season_items(client, callback_query.message.chat.id, season_doc)

# =========================================================
# SEPARATE SECTION: Embedded Season Link Builder (/buildmenu, /savemenu, /showmenu)
# =========================================================

@app.on_message(filters.command("buildmenu"))
async def build_menu_start(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await safe_reply(message, "⚠️ অ্যানিমের নাম দিন:\n/buildmenu [Anime Name]")
        return

    anime_name = parts[1].strip()
    user_id = message.from_user.id
    master_menu_sessions[user_id] = {
        "anime_name": anime_name,
        "seasons": []
    }

    await safe_reply(
        message,
        f"📂 Master Menu তৈরি শুরু হয়েছে: {anime_name}\n\n"
        "এখন Season 01-এর লিংক দিন।\n"
        "সব সিজন লিংক দেওয়া হলে লিখুন: /savemenu"
    )

@app.on_message(filters.command("savemenu"))
async def save_menu_finish(client, message: Message):
    user_id = message.from_user.id
    if user_id not in master_menu_sessions:
        await safe_reply(message, "⚠️ কোনো Master Menu সেশন চালু নেই! প্রথমে /buildmenu [Anime Name] দিন।")
        return

    m_session = master_menu_sessions.pop(user_id)
    anime_name = m_session["anime_name"]
    entries = m_session["seasons"]

    if not entries:
        await safe_reply(message, "❌ অন্তত একটি সিজনের লিংক দিন, তারপর /savemenu লিখুন।")
        return

    menu_code = make_menu_code(anime_name)
    await master_menus_collection.update_one(
        {"anime_name": anime_name},
        {
            "$set": {
                "menu_code": menu_code,
                "seasons": entries
            }
        },
        upsert=True
    )

    bot_info = await client.get_me()
    menu_link = f"https://t.me/{bot_info.username}?start={menu_code}"
    menu_html = build_embedded_menu_html(anime_name, entries)

    await safe_reply_html(message, menu_html)
    await safe_reply(
        message,
        f"✅ {anime_name}-এর Master Menu সেভ হয়েছে!\n\n"
        f"🔗 Master Menu Link:\n{menu_link}"
    )

@app.on_message(filters.command("showmenu"))
async def show_menu_handler(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        target_name = parts[1].strip()
        menu_doc = await master_menus_collection.find_one({"anime_name": target_name})
        if not menu_doc:
            await safe_reply(message, f"❌ '{target_name}' নামের কোনো Master Menu পাওয়া যায়নি।")
            return
        menu_html = build_embedded_menu_html(
            menu_doc["anime_name"],
            menu_doc.get("seasons", [])
        )
        await safe_reply_html(message, menu_html)
        return

    all_menus = await master_menus_collection.find().to_list(length=100)
    if not all_menus:
        await safe_reply(message, "📭 এখনো কোনো Master Menu সেভ করা নেই। শুরু করতে লিখুন:\n/buildmenu [Anime Name]")
        return

    keyboard = []
    for m in all_menus:
        title = m["anime_name"]
        keyboard.append([InlineKeyboardButton(f"🎬 {title}", callback_data=f"mview_{title}")])

    await safe_reply(
        message,
        "📂 Saved Master Menus\n\nনিচের বাটন থেকে যেকোনো Master Menu দেখুন:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@app.on_callback_query(filters.regex("^mview_"))
async def mview_button_handler(client, callback_query: CallbackQuery):
    anime_name = callback_query.data.replace("mview_", "", 1)
    menu_doc = await master_menus_collection.find_one({"anime_name": anime_name})
    if not menu_doc:
        await callback_query.answer("❌ এই Master Menu পাওয়া যায়নি।", show_alert=True)
        return
        await callback_query.answer(f"📂 {anime_name}")
    menu_html = build_embedded_menu_html(
        menu_doc["anime_name"],
        menu_doc.get("seasons", [])
    )
    await callback_query.message.reply_text(
        menu_html,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True

    )

import os
from aiohttp import web

async def start_health_server():
    async def health(request):
        return web.Response(text="Bot is alive")

    app_web = web.Application()
    app_web.router.add_get("/", health)
    app_web.router.add_get("/health", health)

    runner = web.AppRunner(app_web)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Health server running on port {port}")


async def main():
    global db_client, db, seasons_collection, master_menus_collection
    db_client = AsyncIOMotorClient(MONGO_URI)
    db = db_client["anime_database"]
    seasons_collection = db["seasons"]
    master_menus_collection = db["master_menus"]

    await start_health_server()
    print("🤖 Bot is starting smoothly...")
    async with app:
        from pyrogram import idle
        await idle()


if __name__ == "__main__":
        app.loop.run_until_complete(main())
    
    

    
    
    
    

