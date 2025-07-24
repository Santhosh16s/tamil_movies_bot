import logging
import asyncio
import nest_asyncio
import unicodedata
import re
import sys
import os
import threading
import uvicorn
from fastapi import FastAPI
from dotenv import load_dotenv
from functools import wraps
from supabase.client import create_client, Client
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from rapidfuzz import process



load_dotenv()
nest_asyncio.apply()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")  # உங்கள் Bot Token வை இங்கே வைங்க
admin_ids_str = os.getenv("ADMIN_IDS","")
admin_ids = set(map(int, filter(None, admin_ids_str.split(","))))
PRIVATE_CHANNEL_LINK = os.getenv("PRIVATE_CHANNEL_LINK")  # உங்க Channel invite link

print("🚨 RAW ENV:", os.environ)  # Add this

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

print(f"✅ Supabase URL: {SUPABASE_URL}")
print(f"✅ Supabase KEY: {SUPABASE_KEY}")


supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

user_files = {}

def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in admin_ids:
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Load movies from Supabase ---
def load_movies_data():
    response = supabase.table("movies").select("*").execute()
    movies = response.data or []
    movies_data = {}
    for movie in movies:
        movies_data[movie['title'].lower()] = {
            'poster_url': movie['poster_url'],
            'files': {
                '480p': movie['file_480p'],
                '720p': movie['file_720p'],
                '1080p': movie['file_1080p'],
            }
        }
    return movies_data

movies_data = load_movies_data()

# --- Extract title from filename ---
def extract_title(filename):
    filename = re.sub(r"@\S+", "", filename)
    filename = re.sub(r"\b(480p|720p|1080p|x264|x265|HEVC|HDRip|WEBRip|AAC|10bit|DS4K|UNTOUCHED|mkv|mp4|HD|HQ)\b", "", filename, flags=re.IGNORECASE)
    filename = re.sub(r"[\[\]\(\)\{\}]", " ", filename)
    filename = re.sub(r"\s+", " ", filename).strip()

    match = re.search(r"([a-zA-Z\s]+)(?:\(?)(20\d{2})(?:\)?)", filename)
    if match:
        title = f"{match.group(1).strip()} ({match.group(2)})"
        return title

    title = re.split(r"[-0-9]", filename)[0].strip()
    return title

# --- Clean title for DB storage ---
def clean_title(title):
    cleaned = ''.join(c for c in title if unicodedata.category(c)[0] not in ['S', 'C'])
    cleaned = re.sub(r'[^\w\s\(\)]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

# --- Save movie to DB ---
# --- Save movie to Supabase ---
def save_movie_to_db(title, poster_id, file_ids):
    try:
        data = {
            "title": title,
            "poster_url": poster_id,
            "file_480p": file_ids[0],
            "file_720p": file_ids[1],
            "file_1080p": file_ids[2],
            "uploaded_at": datetime.utcnow().isoformat()
        }
        supabase.table("movies").insert(data).execute()
        logging.info(f"Saved movie: {title}")
        return True
    except Exception as e:
        logging.error(f"Supabase Insert error: {e}")
        return False
    
# --- Calculate time difference for status ---
def time_diff(past_time):
    now = datetime.utcnow()
    diff = now - past_time

    seconds = diff.total_seconds()
    minutes = seconds / 60
    hours = minutes / 60
    days = hours / 24

    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    elif minutes < 60:
        return f"{int(minutes)} minutes ago"
    elif hours < 24:
        return f"{int(hours)} hours ago"
    else:
        return f"{int(days)} days ago"

# --- /start command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 தயவுசெய்து திரைப்படத்தின் பெயரை அனுப்புங்கள்!")

# --- /addmovie command ---
async def addmovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்")
        return

    user_files[user_id] = {"poster": None, "movies": []}
    await update.message.reply_text("போஸ்டர் மற்றும் 3 movie files (480p, 720p, 1080p) அனுப்பவும்.")

# --- Save incoming files ---
async def save_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id not in user_files:
        await message.reply_text("❗ முதலில் /addmovie அனுப்பவும்.")
        return

    # Poster
    if message.photo:
        file_id = message.photo[-1].file_id
        user_files[user_id]["poster"] = file_id
        await message.reply_text("🖼️ Poster received.")
        asyncio.create_task(delete_after_delay(context, chat_id, message.message_id))
        return

    # Movie files
    if message.document:
        if len(user_files[user_id]["movies"]) >= 3:
            await message.reply_text("❗ மூன்று movie files ஏற்கனவே பெற்றுவிட்டேன்.")
            return

        movie_file_id = message.document.file_id
        movie_file_name = message.document.file_name

        user_files[user_id]["movies"].append({
            "file_id": movie_file_id,
            "file_name": movie_file_name
        })

        await message.reply_text(
            f"🎥 Movie file {len(user_files[user_id]['movies'])} received.\n📂 `{movie_file_name}`",
            parse_mode="Markdown"
        )
        asyncio.create_task(delete_after_delay(context, chat_id, message.message_id))

    # If all files received, save to DB
    if user_files[user_id]["poster"] and len(user_files[user_id]["movies"]) == 3:
        poster_id = user_files[user_id]["poster"]
        movies = user_files[user_id]["movies"]
        file_ids = [m["file_id"] for m in movies]
        title = extract_title(movies[0]["file_name"]).lower()
        title = clean_title(title)

        saved = save_movie_to_db(title, poster_id, file_ids)
        if saved:
            global movies_data
            movies_data = load_movies_data()
            await message.reply_text(f"✅ Movie saved as *{title.title()}*.", parse_mode="Markdown")
        else:
            await message.reply_text("❌ DB-ல் சேமிக்க முடியவில்லை.")

        user_files[user_id] = {"poster": None, "movies": []}

# --- Delete messages after 10 minutes ---
async def delete_after_delay(context, chat_id, message_id):
    await asyncio.sleep(600)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logging.warning(f"Delete failed: {e}")

# --- Send movie on text message (search) ---
async def send_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    movie_name = update.message.text.strip().lower()

    global movies_data
    movies_data = load_movies_data()

    if movie_name in movies_data:
        await send_movie_poster(update.message, movie_name, context)
    else:
        matches = process.extract(movie_name, movies_data.keys(), limit=5, score_cutoff=80)
        if matches:
            keyboard = [[InlineKeyboardButton(m[0].title(), callback_data=f"movie_{m[0]}")] for m in matches]
            await update.message.reply_text(
                "⚠️ நீங்கள் இந்த படங்களில் ஏதாவது குறிப்பிடுகிறீர்களா?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text("❌ படம் கிடைக்கவில்லை!")

# --- Send movie poster with resolution buttons ---
async def send_movie_poster(message, movie_name_key, context):
    movie = movies_data[movie_name_key]

    caption = (
        f"🎬 {movie_name_key.title()}\n\n"
        f"👉 <a href='{PRIVATE_CHANNEL_LINK}'>SK Movies Updates (News)🔔</a> - புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும். Join பண்ணுங்க!"
    )

    keyboard = [
        [
            InlineKeyboardButton("480p", callback_data=f"res_{movie_name_key}_480p"),
            InlineKeyboardButton("720p", callback_data=f"res_{movie_name_key}_720p"),
            InlineKeyboardButton("1080p", callback_data=f"res_{movie_name_key}_1080p"),
        ]
    ]

    try:
        sent = await message.reply_photo(
            movie["poster_url"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        asyncio.create_task(delete_after_delay(context, message.chat_id, sent.message_id))
    except Exception as e:
        logging.error(f"Poster error: {e}")
        await message.reply_text("⚠️ போஸ்டர் அனுப்ப முடியவில்லை.")

# --- Handle resolution button clicks ---
async def handle_resolution_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        _, movie_name, res = query.data.split("_")
        movie = movies_data.get(movie_name)
        if not movie:
            return await query.message.reply_text("❌ படம் கிடைக்கவில்லை!")

        file_url = movie['files'].get(res)
        if file_url:
            caption = (
                f"🎬 {movie_name.title()}\n\n"
                f"👉 <a href='{PRIVATE_CHANNEL_LINK}'>SK Movies Updates (News)🔔</a> - புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும்.\nJoin பண்ணுங்க!\n\n"
                f"⚠️ இந்த File 10 நிமிடங்களில் நீக்கப்படும். தயவுசெய்து இந்த File ஐ உங்கள் saved messages க்கு அனுப்பி வையுங்கள்."
            )

            sent_msg = await context.bot.send_document(
                chat_id=query.from_user.id,
                document=file_url,
                caption=caption,
                parse_mode="HTML"
            )

            await query.message.reply_text("✅ கோப்பு உங்களுக்கு தனிப்பட்ட மெசேஜாக அனுப்பப்பட்டது.")

            async def delete_sent_file():
                await asyncio.sleep(600)
                try:
                    await context.bot.delete_message(chat_id=sent_msg.chat.id, message_id=sent_msg.message_id)
                except Exception as e:
                    logging.error(f"File delete error: {e}")

            asyncio.create_task(delete_sent_file())
        else:
            await query.message.reply_text("⚠️ இந்த resolution-க்கு file இல்லை.")
    except Exception as e:
        logging.error(f"File send error: {e}")
        await query.message.reply_text("⚠️ கோப்பை அனுப்ப முடியவில்லை.")

# --- Handle movie button click from suggestions ---
async def movie_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    movie_name = query.data.split("_")[1]
    if movie_name in movies_data:
        await send_movie_poster(query.message, movie_name, context)
    else:
        await query.answer("❌ படம் கிடைக்கவில்லை!")

# --- /status command ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்.")
        return

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS total FROM movies")
    total_movies = cursor.fetchone()['total']

    cursor.execute("SHOW TABLE STATUS LIKE 'movies'")
    status = cursor.fetchone()
    size_in_bytes = status['Data_length'] + status.get('Index_length', 0)
    db_size_mb = round(size_in_bytes / (1024 * 1024), 2)

    cursor.execute("SELECT title, uploaded_at FROM movies ORDER BY id DESC LIMIT 1")
    last = cursor.fetchone()
    if last:
        last_title = last['title']
        last_upload_time = last['uploaded_at']
        time_ago = time_diff(last_upload_time)
    else:
        last_title = "None"
        time_ago = "N/A"

    cursor.close()
    db.close()

    text = (
        f"📊 Bot Status:\n"
        f"• Total Movies: {total_movies}\n"
        f"• Database Size: {db_size_mb} MB\n"
        f"• Last Upload: \"{last_title}\" – {time_ago}"
    )

    await update.message.reply_text(text)

# --- /adminpanel command ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ உங்களுக்கு இந்த கட்டளை அனுமதி இல்லை.")
        return

    admin_list = "\n".join([f"👤 {admin_id}" for admin_id in admin_ids])
    await update.message.reply_text(f"🛠️ *Admin Panel*\n\n📋 *Admin IDs:*\n{admin_list}", parse_mode='Markdown')

# --- /addadmin <id> command ---
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ Access denied.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: /addadmin <user_id>")
        return

    new_admin_id = int(context.args[0])
    if new_admin_id in admin_ids:
        await update.message.reply_text("⚠️ This user is already an admin.")
    else:
        admin_ids.add(new_admin_id)
        await update.message.reply_text(f"✅ Added new admin: {new_admin_id}")

# --- /removeadmin <id> command ---
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ Access denied.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: /removeadmin <user_id>")
        return

    rem_admin_id = int(context.args[0])
    if rem_admin_id in admin_ids:
        if len(admin_ids) == 1:
            await update.message.reply_text("⚠️ At least one admin must remain.")
        else:
            admin_ids.remove(rem_admin_id)
            await update.message.reply_text(f"✅ Removed admin: {rem_admin_id}")
    else:
        await update.message.reply_text("❌ User not in admin list.")

# --- /edittitle command ---
async def edittitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("⚠️ Usage: `/edittitle <old title> | <new title>`", parse_mode="Markdown")
        return

    full_args = " ".join(args)
    if "|" not in full_args:
        await update.message.reply_text("⚠️ Usage: `/edittitle <old title> | <new title>`", parse_mode="Markdown")
        return

    old_title, new_title = map(lambda x: x.strip().lower(), full_args.split("|", 1))

    try:
        supabase.table("movies").update({"title": new_title}).eq("title", old_title).execute()

        if supabase:
            global movies_data
            movies_data = load_movies_data()
            await update.message.reply_text(f"✅ *{old_title.title()}* இன் title, *{new_title.title()}* ஆக மாற்றப்பட்டது.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கலை. சரியான பழைய பெயர் கொடுக்கவும்.")
    except Exception as e:
        logging.error(f"Title update error: {e}")
        await update.message.reply_text("❌ Title update செய்ய முடியவில்லை.")

# --- /deletemovie command ---
async def deletemovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Usage: `/deletemovie <movie name>`", parse_mode="Markdown")
        return

    title = " ".join(args).strip().lower()

    try:
        supabase.table("movies").delete().eq("title", title).execute()

        if supabase:
            global movies_data
            movies_data = load_movies_data()
            await update.message.reply_text(f"✅ *{title.title()}* படத்தை delete பண்ணிட்டேன்.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கலை. சரியான பெயர் கொடுக்கவும்.")
    except Exception as e:
        logging.error(f"Delete error: {e}")
        await update.message.reply_text("❌ DB-இல் இருந்து delete பண்ண முடியலை.")

# --- Pagination helpers ---
def get_total_movies_count():
    response = supabase.table("movies").select("id", count="exact").execute()
    return response.count or 0

def load_movies_page(limit=20, offset=0):
    response = supabase.table("movies").select("title").order("title", ascending=True).range(offset, offset + limit - 1).execute()
    movies = response.data or []
    return [m['title'] for m in movies]

# --- /movielist command ---
async def movielist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்")
        return

    page = 1
    if context.args:
        try:
            page = int(context.args[0])
            if page < 1:
                page = 1
        except ValueError:
            page = 1

    limit = 20
    offset = (page - 1) * limit
    movies = load_movies_page(limit=limit, offset=offset)
    total_movies = get_total_movies_count()
    total_pages = (total_movies + limit - 1) // limit

    if not movies:
        await update.message.reply_text("❌ இந்த பக்கத்தில் படம் இல்லை.")
        return

    text = f"🎬 Movies List - பக்கம் {page}/{total_pages}\n\n"
    for i, title in enumerate(movies, start=offset + 1):
        text += f"{i}. {title.title()}\n"

    keyboard = []
    if page > 1:
        keyboard.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"movielist_{page - 1}"))
    if page < total_pages:
        keyboard.append(InlineKeyboardButton("Next ➡️", callback_data=f"movielist_{page + 1}"))

    reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None
    await update.message.reply_text(text, reply_markup=reply_markup)

# --- movielist pagination callback ---
async def movielist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("movielist_"):
        return

    page = int(data.split("_")[1])

    limit = 20
    offset = (page - 1) * limit
    movies = load_movies_page(limit=limit, offset=offset)
    total_movies = get_total_movies_count()
    total_pages = (total_movies + limit - 1) // limit

    if not movies:
        await query.message.edit_text("❌ இந்த பக்கத்தில் படம் இல்லை.")
        return

    text = f"🎬 Movies List - பக்கம் {page}/{total_pages}\n\n"
    for i, title in enumerate(movies, start=offset + 1):
        text += f"{i}. {title.title()}\n"

    keyboard = []
    if page > 1:
        keyboard.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"movielist_{page - 1}"))
    if page < total_pages:
        keyboard.append(InlineKeyboardButton("Next ➡️", callback_data=f"movielist_{page + 1}"))

    reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None
    await query.message.edit_text(text, reply_markup=reply_markup)

# --- /restart command ---
async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admin_ids:
        return

    await update.message.reply_text("♻️ பாட்டு மீண்டும் தொடங்குகிறது (Koyeb மூலம்)...")
    sys.exit(0)

# --- Main function to setup bot ---
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Register commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addmovie", addmovie))
    app.add_handler(CommandHandler("deletemovie", deletemovie))
    app.add_handler(CommandHandler("edittitle", edittitle))
    app.add_handler(CommandHandler("movielist", movielist))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("adminpanel", admin_panel))
    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("removeadmin", remove_admin))
    app.add_handler(CommandHandler("restart", restart_bot))

    # File upload handler
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, save_file))

    # Movie search text handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, send_movie))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_resolution_click, pattern=r"^res_"))
    app.add_handler(CallbackQueryHandler(movie_button_click, pattern=r"^movie_"))
    app.add_handler(CallbackQueryHandler(movielist_callback, pattern=r"^movielist_"))

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())