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
from urllib.parse import quote


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
            'poster_url': movie['poster_url'], # இது Telegram photo file_id ஆக இருக்கும்
            'files': {
                '480p': movie['file_480p'], # இவை Telegram document file_id-கள்
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
def save_movie_to_db(title, poster_id, file_ids):
    try:
        logging.info(f"Saving movie with title: '{title}'")  
        data = {
            "title": title,
            "poster_url": poster_id,
            "file_480p": file_ids[0] if len(file_ids) > 0 else None,
            "file_720p": file_ids[1] if len(file_ids) > 1 else None,
            "file_1080p": file_ids[2] if len(file_ids) > 2 else None,
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

# --- delete_after_delay function ---
async def delete_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    await asyncio.sleep(600)  # 600 வினாடிகள் = 10 நிமிடங்கள் காத்திருக்கவும்
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logging.info(f"Message {message_id} in chat {chat_id} deleted after delay.")
    except Exception as e:
        logging.error(f"Error deleting message {message_id} in chat {chat_id}: {e}")

# --- send_movie_poster function ---
async def send_movie_poster(message, movie_name, context):
    movie = movies_data.get(movie_name)
    if movie and movie['poster_url']:
        try:
            # poster_url Telegram file_id-ஐ சேமிக்கிறது என்று வைத்துக் கொள்வோம்
            await context.bot.send_photo(
                chat_id=message.chat.id,
                photo=movie['poster_url'],
                caption=f"🎬 *{movie_name.title()}*\n\n"
                        "👇 *Resolution Choose பண்ணுங்க*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("480p", callback_data=f"res_{movie_name}_480p")],
                    [InlineKeyboardButton("720p", callback_data=f"res_{movie_name}_720p")],
                    [InlineKeyboardButton("1080p", callback_data=f"res_{movie_name}_1080p")]
                ])
            )
        except Exception as e:
            logging.error(f"போஸ்டர் அனுப்ப பிழை: {e}")
            await message.reply_text("❌ போஸ்டர் அனுப்ப முடியவில்லை. படம் கிடைக்கவில்லை அல்லது பிழை.")
    else:
        await message.reply_text("❌ படம் கிடைக்கவில்லை அல்லது போஸ்டர் இல்லை.")

# --- send_movie function (for text search) ---
async def send_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    movie_name = update.message.text.lower().strip() # பயனரின் மெசேஜை சிறிய எழுத்துக்களில் எடுக்கவும்

    # சிறந்த பொருத்தத்தைக் கண்டறிய rapidfuzz ஐப் பயன்படுத்தவும்
    if not movies_data:
        await update.message.reply_text("டேட்டாபேஸ் காலியாக உள்ளது அல்லது ஏற்ற முடியவில்லை.")
        return

    # டிக்ஷனரியின் keys-ஐ மட்டுமே rapidfuzz க்கு அனுப்பவும்
    movie_titles = list(movies_data.keys())
    
    # process.extractOne முதல் பொருத்தம் மற்றும் அதன் score ஐத் தரும்
    best_match = process.extractOne(movie_name, movie_titles)

    if best_match and best_match[1] >= 80: # 80% அல்லது அதற்கு மேல் உள்ள பொருத்தத்தை மட்டும் எடுக்கவும்
        matched_title = best_match[0] # பொருத்தமான திரைப்படம்
        await send_movie_poster(update.message, matched_title, context)
    else:
        await update.message.reply_text("❌ படம் கிடைக்கவில்லை!")

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

    # Movie files (Telegram file_id-யைப் பயன்படுத்தவும்)
    if message.document:
        if len(user_files[user_id]["movies"]) >= 3:
            await message.reply_text("❗ மூன்று movie files ஏற்கனவே பெற்றுவிட்டேன்.")
            return

        movie_file_id = message.document.file_id
        movie_file_name = message.document.file_name

        user_files[user_id]["movies"].append({
            "file_id": movie_file_id, # இங்கு Telegram file_id சேமிக்கப்படும்
            "file_name": movie_file_name
        })

        await message.reply_text(
            f"🎥 Movie file {len(user_files[user_id]['movies'])} received.\n📂 `{movie_file_name}`",
            parse_mode="Markdown"
        )
        asyncio.create_task(delete_after_delay(context, chat_id, message.message_id))

    # If all files received, save to DB
    if user_files[user_id]["poster"] and len(user_files[user_id]["movies"]) == 3:
        poster_id = user_files[user_id]["poster"] # போஸ்டர் இன்னும் Telegram file_id-யாக இருக்கும்
        movies = user_files[user_id]["movies"]
        
        # files list-ன் order சரியாக இருப்பதை உறுதிப்படுத்தவும் (480p, 720p, 1080p)
        # நீங்கள் அனுப்பும் order-ஐப் பொறுத்து இது அமையும். இல்லையெனில், file_name-ல் இருந்து resolution-ஐப் பிரித்தெடுத்து வரிசைப்படுத்த வேண்டும்.
        # இப்போதைக்கு, நீங்கள் 480p, 720p, 1080p வரிசையில் ஃபைல்களை அனுப்புவீர்கள் என்று கருதுவோம்.
        telegram_file_ids_for_db = [m["file_id"] for m in movies] 
        
        title = extract_title(movies[0]["file_name"]).lower()
        title = clean_title(title)

        # DB-க்கு Telegram file_ids அனுப்பவும்
        saved = save_movie_to_db(title, poster_id, telegram_file_ids_for_db) 
        if saved:
            global movies_data
            movies_data = load_movies_data() # புதிய தரவை ஏற்றவும்
            await message.reply_text(f"✅ Movie saved as *{title.title()}*.", parse_mode="Markdown")
        else:
            await message.reply_text("❌ DB-ல் சேமிக்க முடியவில்லை.")

        user_files[user_id] = {"poster": None, "movies": []}


# --- handle_resolution_click செயல்பாட்டில் மாற்றம் ---
# Telegram file_id-யை நேரடியாகப் பயன்படுத்தவும்
async def handle_resolution_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        _, movie_name, res = query.data.split("_")
        movie = movies_data.get(movie_name)
        if not movie:
            return await query.message.reply_text("❌ படம் கிடைக்கவில்லை!")

        file_id_to_send = movie['files'].get(res) # இது Telegram file_id

        if file_id_to_send:
            caption = (
                f"🎬 {movie_name.title()}\n\n"
                f"👉 <a href='{PRIVATE_CHANNEL_LINK}'>SK Movies Updates (News)🔔</a> - புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும்.\nJoin பண்ணுங்க!\n\n"
                f"⚠️ இந்த File 10 நிமிடங்களில் நீக்கப்படும். தயவுசெய்து இந்த File ஐ உங்கள் saved messages க்கு அனுப்பி வையுங்கள்."
            )

            sent_msg = await context.bot.send_document(
                chat_id=query.from_user.id,
                document=file_id_to_send, # Telegram file_id-யைப் பயன்படுத்தவும்
                caption=caption,
                parse_mode="HTML"
            )

            await query.message.reply_text("✅ கோப்பு உங்களுக்கு தனிப்பட்ட மெசேஜாக அனுப்பப்பட்டது.")

            # File 10 நிமிடங்களில் நீக்கப்படும் செயல்பாடு இங்கு தேவைப்படாது,
            # ஏனெனில் இது Telegram சர்வரில் உள்ளது, உங்கள் பாட் டவுன்லோட் செய்து அனுப்புவதில்லை.
            # நீங்கள் இதை நீக்கலாம் அல்லது உங்கள் விருப்பப்படி வைத்துக் கொள்ளலாம்.
            # async def delete_sent_file():
            #     await asyncio.sleep(600)
            #     try:
            #         await context.bot.delete_message(chat_id=sent_msg.chat.id, message_id=sent_msg.message_id)
            #     except Exception as e:
            #         logging.error(f"File delete error: {e}")
            # asyncio.create_task(delete_sent_file())
        else:
            await query.message.reply_text("⚠️ இந்த resolution-க்கு file இல்லை.")
    except Exception as e:
        logging.error(f"File send error: {e}")
        await query.message.reply_text("⚠️ கோப்பை அனுப்ப முடியவில்லை.")


# --- Handle movie button click from suggestions ---
async def movie_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_parts = query.data.split("_")
    
    if len(data_parts) < 2:
        await query.message.reply_text("தவறான கோரிக்கை.")
        return

    movie_name = "_".join(data_parts[1:]) # movie_name underscore-ஐக் கொண்டிருந்தால் சரிசெய்யப்பட்டது

    if movie_name in movies_data:
        await send_movie_poster(query.message, movie_name, context)
    else:
        await query.message.reply_text("❌ படம் கிடைக்கவில்லை!")

# --- /status command ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்.")
        return

    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        total_movies = response.count or 0

        # response2 = supabase.rpc("get_movies_table_size").execute() # இந்த வரியை கமெண்ட் செய்யவும்
        # db_size_mb = round(response2.data[0]['size_bytes'] / (1024*1024), 2) if response2.data else 0 # இந்த வரியையும் கமெண்ட் செய்யவும்
        db_size_mb = "கணக்கிட முடியவில்லை" # அல்லது நீங்கள் "N/A" (Not Available) என்றும் கொடுக்கலாம்

        last_movie_resp = supabase.table("movies").select("title", "uploaded_at").order("id", desc=True).limit(1).execute()
        last = last_movie_resp.data[0] if last_movie_resp.data else None
        if last:
            last_title = last['title']
            last_upload_time = datetime.fromisoformat(last['uploaded_at'])
            time_ago = time_diff(last_upload_time)
        else:
            last_title = "None"
            time_ago = "N/A"

        text = (
            f"📊 Bot Status:\n"
            f"• Total Movies: {total_movies}\n"
            f"• Database Size: {db_size_mb}\n"
            f"• Last Upload: \"{last_title}\" – {time_ago}"
        )

        await update.message.reply_text(text)
    except Exception as e:
        logging.error(f"Status error: {e}")
        await update.message.reply_text("❌ Status info பெற முடியவில்லை.")

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

    try:
        new_admin_id = int(context.args[0])
        if new_admin_id in admin_ids:
            await update.message.reply_text("⚠️ This user is already an admin.")
        else:
            admin_ids.add(new_admin_id)
            await update.message.reply_text(f"✅ Added new admin: {new_admin_id}")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID. Please provide a number.")

# --- /removeadmin <id> command ---
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ Access denied.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: /removeadmin <user_id>")
        return

    try:
        rem_admin_id = int(context.args[0])
        if rem_admin_id in admin_ids:
            if len(admin_ids) == 1:
                await update.message.reply_text("⚠️ At least one admin must remain.")
            else:
                admin_ids.remove(rem_admin_id)
                await update.message.reply_text(f"✅ Removed admin: {rem_admin_id}")
        else:
            await update.message.reply_text("❌ User not in admin list.")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID. Please provide a number.")

# --- /edittitle command ---
async def edittitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்.")
        return

    args = context.args
    logging.info(f"Edittitle args: {args}") # இதைச் சேர்க்கவும்
    if len(args) < 1: # 최소 ஒரு ஆர்குமென்ட் தேவை - பழைய பெயர் அல்லது "| புதிய பெயர்"
        await update.message.reply_text("⚠️ Usage: `/edittitle <old title> | <new title>`", parse_mode="Markdown")
        return

    full_args = " ".join(args)
    logging.info(f"Edittitle full_args: {full_args}") # இதைச் சேர்க்கவும்
    if "|" not in full_args:
        await update.message.reply_text("⚠️ Usage: `/edittitle <old title> | <new title>`", parse_mode="Markdown")
        return

    old_title_raw, new_title_raw = map(lambda x: x.strip(), full_args.split("|", 1))
    
    # Clean and lower case titles for comparison and storage
    old_title = clean_title(old_title_raw).lower()
    new_title = clean_title(new_title_raw).lower()

    logging.info(f"Edittitle parsed - Old: '{old_title}', New: '{new_title}'") # இதைச் சேர்க்கவும்

    try:
        response = supabase.table("movies").update({"title": new_title}).eq("title", old_title).execute()

        if response.data: # Supabase client returns data if update was successful
            global movies_data
            movies_data = load_movies_data()
            await update.message.reply_text(f"✅ *{old_title_raw.title()}* இன் title, *{new_title_raw.title()}* ஆக மாற்றப்பட்டது.", parse_mode="Markdown")
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

    title_raw = " ".join(args).strip()
    title_to_delete = clean_title(title_raw).lower() # Clean and lower for comparison

    # இந்த வரியைச் சேர்க்கவும்:
    logging.info(f"Attempting to delete title: '{title_to_delete}'")

    try:
        response = supabase.table("movies").delete().eq("title", title_to_delete).execute()

        if response.data:
            global movies_data
            movies_data = load_movies_data()
            await update.message.reply_text(f"✅ *{title_raw.title()}* படத்தை delete பண்ணிட்டேன்.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கலை. சரியான பெயர் கொடுக்கவும்.")
    except Exception as e:
        logging.error(f"Delete error: {e}")
        await update.message.reply_text("❌ DB-இல் இருந்து delete பண்ண முடியலை.")

# --- Pagination helpers ---
def get_total_movies_count():
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logging.error(f"Error getting total movie count: {e}")
        return 0

def load_movies_page(limit=20, offset=0):
    response = supabase.table("movies").select("title").order("title", desc=False).range(offset, offset + limit - 1).execute()
    movies = response.data or []
    return [m['title'] for m in movies]

# --- /movielist command ---
async def movielist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்")
        return

    logging.info(f"User {user_id} requested /movielist command.") 

    page = 1
    if context.args:
        try:
            page = int(context.args[0])
            if page < 1:
                page = 1
        except ValueError:
            page = 1

    limit = 30
    offset = (page - 1) * limit

    movies = load_movies_page(limit=limit, offset=offset)
    total_movies = get_total_movies_count()
    total_pages = (total_movies + limit - 1) // limit

    logging.info(f"Movielist details - Page: {page}, Offset: {offset}, Total Movies: {total_movies}, Total Pages: {total_pages}, Movies on page: {len(movies)}")

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

# movielist pagination callback
async def movielist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("movielist_"):
        return

    page = int(data.split("_")[1])

    limit = 30
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