import logging
import asyncio
import nest_asyncio
import unicodedata
import re
import sys
import os
import time
import telegram
from rapidfuzz import process
from dotenv import load_dotenv
from functools import wraps
from supabase.client import create_client, Client
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

load_dotenv()
nest_asyncio.apply()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TOKEN = os.getenv("TOKEN")
admin_ids_str = os.getenv("ADMIN_IDS", "")
admin_ids = set(map(int, filter(None, admin_ids_str.split(","))))

# --- .env-இலிருந்து நேரடியாகப் படிக்கப்படுகிறது ---
PRIVATE_CHANNEL_LINK = os.getenv("PRIVATE_CHANNEL_LINK")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROUP_ID = int(os.getenv("GROUP_ID"))
MOVIE_UPDATE_CHANNEL_ID = int(os.getenv("MOVIE_UPDATE_CHANNEL_ID"))
MOVIE_UPDATE_CHANNEL_URL = PRIVATE_CHANNEL_LINK # இது ஒரே சேனல் என்பதால், இதை மீண்டும் பயன்படுத்தலாம்.

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info(f"✅ Supabase URL: {SUPABASE_URL}")
    logging.info(f"✅ Supabase KEY: {SUPABASE_KEY[:5]}...")
except Exception as e:
    logging.error(f"❌ Supabase client உருவாக்க முடியவில்லை: {e}")
    sys.exit(1)

user_files = {}
pending_file_requests = {}

# --- Utility Functions ---
def extract_title(filename: str) -> str:
    filename = re.sub(r"@\S+", "", filename)
    filename = re.sub(r"\b(480p|720p|1080p|x264|x265|HEVC|HDRip|WEBRip|AAC|10bit|DS4K|UNTOUCHED|mkv|mp4|HD|HQ|Tamil|Telugu|Hindi|English|Dubbed|Org|Original|Proper)\b", "", filename, flags=re.IGNORECASE)
    filename = re.sub(r"[\[\]\(\)\{\}]", " ", filename)
    filename = re.sub(r"\s+", " ", filename).strip()

    match = re.search(r"([a-zA-Z\s]+)(?:\(?)(20\d{2})(?:\)?)", filename)
    if match:
        title = f"{match.group(1).strip()} ({match.group(2)})"
        return title

    title = re.split(r"[-0-9]", filename)[0].strip()
    return title

def clean_title(title: str) -> str:
    cleaned = title.lower()
    cleaned = ''.join(c for c in cleaned if unicodedata.category(c)[0] not in ['S', 'C'])
    cleaned = re.sub(r'[^\w\s\(\)]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def load_movies_data():
    try:
        response = supabase.table("movies").select("*").execute()
        movies = response.data or []
        movies_data = {}
        for movie in movies:
            cleaned_title = clean_title(movie['title'])
            movies_data[cleaned_title] = {
                'poster_url': movie['poster_url'],
                'files': {
                    '480p': movie['file_480p'],
                    '720p': movie['file_720p'],
                    '1080p': movie['file_1080p'],
                }
            }
        logging.info(f"✅ {len(movies_data)} திரைப்படங்கள் Supabase இலிருந்து ஏற்றப்பட்டன.")
        return movies_data
    except Exception as e:
        logging.error(f"❌ Supabase இலிருந்து திரைப்படத் தரவைப் பதிவேற்ற முடியவில்லை: {e}")
        return {}

movies_data = load_movies_data()

# --- Decorator ---
def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in admin_ids:
            await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Save movie to Supabase ---
def save_movie_to_db(title: str, poster_id: str, file_ids: list) -> bool:
    try:
        cleaned_title_for_db = clean_title(title)
        logging.info(f"Saving movie with cleaned title: '{cleaned_title_for_db}'")

        data = {
            "title": cleaned_title_for_db,
            "poster_url": poster_id,
            "file_480p": file_ids[0] if len(file_ids) > 0 else None,
            "file_720p": file_ids[1] if len(file_ids) > 1 else None,
            "file_1080p": file_ids[2] if len(file_ids) > 2 else None,
            "uploaded_at": datetime.utcnow().isoformat()
        }
        response = supabase.table("movies").insert(data).execute()
        
        if response.data:
            logging.info(f"✅ திரைப்படம் '{cleaned_title_for_db}' Supabase-ல் சேமிக்கப்பட்டது.")
            return True
        else:
            error_details = "தெரியாத பிழை - டேட்டா இல்லை"
            if hasattr(response, 'postgrest_error') and response.postgrest_error:
                error_details = response.postgrest_error
            elif hasattr(response, 'error') and response.error:
                error_details = response.error
            logging.error(f"❌ Supabase Insert தோல்வியடைந்தது, பிழை: {error_details}")
            return False
    except Exception as e:
        logging.error(f"❌ Supabase Insert பிழை: {e}")
        return False
    
# --- Time difference for status ---
def time_diff(dt):
    now = datetime.now(timezone.utc)
    diff = now - dt.replace(tzinfo=timezone.utc)
    seconds = int(diff.total_seconds())

    if seconds < 60:
        return f"{seconds} வினாடிகள் முன்பு"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} நிமிடங்கள் முன்பு"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} மணிநேரம் முன்பு"
    days = hours // 24
    return f"{days} நாட்கள் முன்பு"

# --- Delete messages after 10 minutes ---
async def delete_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    await asyncio.sleep(600)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logging.info(f"Message {message_id} in chat {chat_id} deleted after delay.")
    except Exception as e:
        logging.warning(f"Error deleting message {message_id} in chat {chat_id}: {e}")

# --- Send movie poster with resolution buttons ---
async def send_movie_poster(message: Message, movie_name_key: str, context: ContextTypes.DEFAULT_TYPE):
    movie = movies_data.get(movie_name_key)
    if not movie:
        await message.reply_text("❌ படம் கிடைக்கவில்லை அல்லது போஸ்டர் இல்லை.")
        return

    caption = (
        f"🎬 *{movie_name_key.title()}*\n\n"
        f"👉 <a href='{PRIVATE_CHANNEL_LINK}'>SK Movies Updates (News)🔔</a> - புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும். Join பண்ணுங்க!"
    )

    keyboard = [
        [
            InlineKeyboardButton("480p", callback_data=f"res|{movie_name_key}|480p"),
            InlineKeyboardButton("720p", callback_data=f"res|{movie_name_key}|720p"),
            InlineKeyboardButton("1080p", callback_data=f"res|{movie_name_key}|1080p"),
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
        logging.error(f"❌ போஸ்டர் அனுப்ப பிழை: {e}")
        await message.reply_text("⚠️ போஸ்டர் அனுப்ப முடியவில்லை.")

# --- User Tracking Logic (reusable function) ---
async def track_user(user: telegram.User):
    """பயனரை Database-இல் பதிவு செய்கிறது அல்லது ஏற்கனவே இருந்தால் லாக் செய்கிறது மற்றும் message_count-ஐ புதுப்பிக்கிறது."""
    user_id = user.id
    try:
        response = supabase.table("users").select("user_id, message_count").eq("user_id", user_id).limit(1).execute()

        if not response.data:
            user_data = {
                "user_id": user_id,
                "username": user.username if user.username else None,
                "first_name": user.first_name if user.first_name else None,
                "last_name": user.last_name if user.last_name else None,
                "joined_at": datetime.utcnow().isoformat(),
                "message_count": 0
            }
            insert_response = supabase.table("users").insert(user_data).execute()
            if insert_response.data:
                logging.info(f"✅ புதிய பயனர் பதிவு செய்யப்பட்டது: {user_id} (மெசேஜ் கவுண்ட்: 1)")
            else:
                error_details = "தெரியாத பிழை"
                if hasattr(insert_response, 'postgrest_error') and insert_response.postgrest_error:
                    error_details = insert_response.postgrest_error
                elif hasattr(insert_response, 'error') and insert_response.error:
                    error_details = insert_response.error
                logging.error(f"❌ பயனர் பதிவு செய்ய முடியவில்லை: {user_id}, பிழை: {error_details}")
        else:
            current_message_count = response.data[0].get("message_count", 0)
            new_message_count = current_message_count + 1

            update_response = supabase.table("users").update({"message_count": new_message_count}).eq("user_id", user_id).execute()
            if update_response.data:
                logging.info(f"பயனர் {user_id} இன் மெசேஜ் கவுண்ட் புதுப்பிக்கப்பட்டது: {new_message_count}")
            else:
                error_details = "தெரியாத பிழை"
                if hasattr(update_response, 'postgrest_error') and update_response.postgrest_error:
                    error_details = update_response.postgrest_error
                elif hasattr(update_response, 'error') and update_response.error:
                    error_details = update_response.error
                logging.error(f"❌ பயனர் {user_id} இன் மெசேஜ் கவுண்ட் புதுப்பிக்க முடியவில்லை: {error_details}")

    except Exception as e:
        logging.error(f"❌ பயனர் பதிவு அல்லது புதுப்பித்தல் பிழை: {e}")

# --- General Message Tracker (அனைத்து User செயல்பாடுகளையும் பதிவு செய்ய) ---
async def general_message_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """அனைத்து பயனர் அப்டேட்களையும் (கமெண்ட்கள், டெக்ஸ்ட், போட்டோக்கள், கால்பேக்குகள்) பதிவு செய்கிறது
    மற்றும் message_count-ஐ புதுப்பிக்கிறது."""
    if update.effective_user:
        await track_user(update.effective_user)
    else:
        logging.info(f"effective_user இல்லாத அப்டேட் பெறப்பட்டது. அப்டேட் ID: {update.update_id}")

# --- /start command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start கட்டளைக்கு பதிலளிக்கிறது மற்றும் User-ஐ Database-இல் பதிவு செய்கிறது."""
    user = update.effective_user
    user_id = user.id

    try:
        response = supabase.table("users").select("user_id").eq("user_id", user_id).limit(1).execute()
        
        if not response.data:
            user_data = {
                "user_id": user_id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "joined_at": datetime.utcnow().isoformat()
            }
            insert_response = supabase.table("users").insert(user_data).execute()
            if insert_response.data:
                logging.info(f"✅ புதிய User பதிவு செய்யப்பட்டது: {user_id}")
            else:
                logging.error(f"❌ User பதிவு செய்ய முடியவில்லை: {user_id}, Error: {insert_response.error}")
        else:
            logging.info(f"User {user_id} ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது.")

    except Exception as e:
        logging.error(f"❌ User பதிவு செய்யும் பிழை: {e}")

    await update.message.reply_text(f"வணக்கம் {user.first_name}! 👋\n\n"
        "🎬 லேட்டஸ்ட் 2025 HD தமிழ் படங்கள் வேண்டுமா? ✨\n"
        "விளம்பரமில்லா உடனடி தேடலுடன், தரமான சினிமா அனுபவம் இங்கே! 🍿\n\n"
        "🎬 தயவுசெய்து திரைப்படத்தின் பெயரை டைப் செய்து அனுப்புங்கள்!")

# --- /totalusers command ---
@restricted
async def total_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பதிவு செய்யப்பட்ட மொத்த பயனர்களின் எண்ணிக்கையைக் காட்டுகிறது."""
    try:
        response = supabase.table("users").select("user_id", count="exact").execute()
        
        total_users = response.count or 0
        
        await update.message.reply_text(f"📊 மொத்த பதிவு செய்யப்பட்ட பயனர்கள்: {total_users}")
        
    except Exception as e:
        logging.error(f"❌ மொத்த பயனர்களைப் பெற பிழை: {e}")
        await update.message.reply_text("❌ பயனர் எண்ணிக்கையைப் பெற முடியவில்லை.")

# --- /addmovie command ---
@restricted
async def addmovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_files[user_id] = {"poster": None, "movies": []}
    await update.message.reply_text("போஸ்டர் மற்றும் 3 movie files (480p, 720p, 1080p) அனுப்பவும்.")

# --- Save incoming files (for addmovie process) ---
async def save_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id not in user_files or (user_files[user_id]["poster"] is None and not message.photo and not message.document):
        await message.reply_text("❗ முதலில் /addmovie அனுப்பவும்.")
        return

    if message.photo:
        file_id = message.photo[-1].file_id
        user_files[user_id]["poster"] = file_id
        await message.reply_text("🖼️ Poster received.")
        asyncio.create_task(delete_after_delay(context, chat_id, message.message_id))
        return

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

    if user_files[user_id]["poster"] and len(user_files[user_id]["movies"]) == 3:
        poster_id = user_files[user_id]["poster"]
        movies_list = user_files[user_id]["movies"]
        
        telegram_file_ids_for_db = [m["file_id"] for m in movies_list] 
        
        raw_title = extract_title(movies_list[0]["file_name"])
        cleaned_title = clean_title(raw_title)

        saved = save_movie_to_db(cleaned_title, poster_id, telegram_file_ids_for_db) 
        if saved:
            global movies_data
            movies_data = load_movies_data()
            await message.reply_text(f"✅ Movie saved as *{cleaned_title.title()}*.", parse_mode="Markdown")
        else:
            await message.reply_text("❌ DB-ல் சேமிக்க முடியவில்லை.")

        user_files[user_id] = {"poster": None, "movies": []}

# --- Send movie on text message (search) ---
async def send_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பயனரின் தேடல் வினவலுக்குப் பதிலளிக்கிறது."""
    search_query = update.message.text.strip()

    global movies_data
    movies_data = load_movies_data()

    if not movies_data:
        await update.message.reply_text("டேட்டாபேஸ் காலியாக உள்ளது அல்லது ஏற்ற முடியவில்லை. பின்னர் முயற்சிக்கவும்.")
        return

    cleaned_search_query = clean_title(search_query)
    movie_titles = list(movies_data.keys())

    good_matches = process.extract(cleaned_search_query, movie_titles, score_cutoff=80)

    if not good_matches:
        broad_suggestions = process.extract(cleaned_search_query, movie_titles, limit=5, score_cutoff=60)
        if broad_suggestions:
            keyboard = [[InlineKeyboardButton(m[0].title(), callback_data=f"movie|{m[0]}")] for m in broad_suggestions]
            await update.message.reply_text(
                "⚠️ நீங்கள் இந்த படங்களில் ஏதாவது குறிப்பிடுகிறீர்களா?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text("❌ மன்னிக்கவும், இந்தத் திரைப்படம் எங்கள் Database-இல் இல்லை\n\n🎬 2025 இல் வெளியான தமிழ் HD திரைப்படங்கள் மட்டுமே இங்கு கிடைக்கும்✨.\n\nஉங்களுக்கு எதுவும் சந்தேகங்கள் இருந்ததால் இந்த குழுவில் கேட்கலாம் https://t.me/skmoviesdiscussion")
    elif len(good_matches) == 1 and good_matches[0][1] >= 95:
        matched_title_key = good_matches[0][0]
        logging.info(f"Direct exact match found for search: '{matched_title_key}'")
        await send_movie_poster(update.message, matched_title_key, context)
    else:
        keyboard = [[InlineKeyboardButton(m[0].title(), callback_data=f"movie|{m[0]}")] for m in good_matches]
        await update.message.reply_text(
            "⚠️ நீங்கள் இந்த படங்களில் ஏதாவது குறிப்பிடுகிறீர்களா?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# --- புதிய செயல்பாடு: பயனர் சந்தாவை சரிபார்க்கும் ---
async def is_user_subscribed(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    பயனர் சேனலில் உள்ளாரா என சரிபார்க்கும் செயல்பாடு.
    """
    try:
        user_status = await context.bot.get_chat_member(
            chat_id=MOVIE_UPDATE_CHANNEL_ID, user_id=chat_id
        )
        return user_status.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"❌ பயனரின் சந்தாவை சரிபார்க்க பிழை: {e}")
        return False

# --- மாற்றப்பட்ட செயல்பாடு: handle_resolution_click ---
# --- மாற்றப்பட்ட செயல்பாடு: handle_resolution_click ---
async def handle_resolution_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data is None or "|" not in query.data:
        return await query.message.reply_text("தவறான கோரிக்கை.")

    _, movie_name_key, res = query.data.split("|", 2)

    # பயனர் சேனலில் இணைந்திருக்கிறாரா என்பதை சரிபார்க்கவும்
    is_subscribed = await is_user_subscribed(user_id, context)

    # பயனர் இணைக்கவில்லை என்றால், சேனலில் இணையச் சொல்லும் மெசேஜை அனுப்பவும்.
    if not is_subscribed:
        await query.message.reply_text(
            "⚠️ இந்த திரைப்படத்தைப் பெற, முதலில் நமது சேனலில் இணையவும்.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("சேனலில் இணைய இங்கே கிளிக் செய்யவும்", url=PRIVATE_CHANNEL_LINK)],
                [InlineKeyboardButton("மீண்டும் முயற்சிக்கவும்", callback_data=f"tryagain|{movie_name_key}|{res}")]
            ]),
        )
        return

    # பயனர் ஏற்கனவே இணைந்திருந்தால், திரைப்படத்தை அனுப்பவும்.
    movie = movies_data.get(movie_name_key)
    if not movie:
        return await query.message.reply_text("❌ மன்னிக்கவும், இந்தத் திரைப்படம் எங்கள் Database-இல் இல்லை\n\n🎬 2025 இல் வெளியான தமிழ் HD திரைப்படங்கள் மட்டுமே இங்கு கிடைக்கும்✨.\n\nஉங்களுக்கு எதுவும் சந்தேகங்கள் இருந்தால் இந்த குழுவில் கேட்கலாம் https://t.me/skmoviesdiscussion")

    file_id_to_send = movie['files'].get(res)

    if not file_id_to_send:
        return await query.message.reply_text("⚠️ இந்த resolution-க்கு file இல்லை.")

    try:
        caption = (
            f"🎬 *{movie_name_key.title()}* - {res}p\n\n"
            f"👉 <a href='{PRIVATE_CHANNEL_LINK}'>SK Movies Updates (News)🔔</a> - புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும்.\nJoin பண்ணுங்க!\n\n"
            f"⚠️ இந்த File 10 நிமிடங்களில் நீக்கப்படும். தயவுசெய்து File ஐ உங்கள் Saved Messages-க்குப் Forward பண்ணி வையுங்கள்."
        )
        sent_msg = await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=file_id_to_send,
            caption=caption,
            parse_mode="HTML"
        )
        asyncio.create_task(delete_after_delay(context, sent_msg.chat.id, sent_msg.message_id))
    except Exception as e:
        logging.error(f"❌ கோப்பு அனுப்ப பிழை: {e}")
        await query.message.reply_text("⚠️ கோப்பை அனுப்ப முடியவில்லை. தயவுசெய்து மீண்டும் முயற்சிக்கவும்.")


# --- புதிய செயல்பாடு: மீண்டும் முயற்சிக்கவும் பட்டனைக் கையாளும் ---
async def handle_try_again_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    'மீண்டும் முயற்சிக்கவும்' பட்டனைக் கிளிக் செய்யும்போது, இந்த செயல்பாடு இயங்கும்.
    """
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('|')
    movie_name_key = data[1]
    res = data[2]

    # பயனர் இப்போது சேனலில் இணைந்திருக்கிறாரா என மீண்டும் சரிபார்க்கவும்
    if await is_user_subscribed(query.from_user.id, context):
        # இணைந்திருந்தால், திரைப்படத்தை அனுப்பவும்
        await query.message.edit_text(f"✅ நீங்கள் இப்போது சேனலில் இணைந்துவிட்டீர்கள். உங்கள் திரைப்படம் அனுப்பப்படுகிறது...", parse_mode="Markdown")
        # send_movie logic ஐ நேரடியாக இங்கே அழைக்கலாம்
        movie = movies_data.get(movie_name_key)
        if not movie:
            return await query.message.reply_text("❌ மன்னிக்கவும், இந்தத் திரைப்படம் எங்கள் Database-இல் இல்லை.")
        
        file_id_to_send = movie['files'].get(res)
        if not file_id_to_send:
            return await query.message.reply_text("⚠️ இந்த resolution-க்கு file இல்லை.")

        try:
            caption = (
                f"🎬 *{movie_name_key.title()}* - {res}p\n\n"
                f"👉 <a href='{PRIVATE_CHANNEL_LINK}'>SK Movies Updates (News)🔔</a> - புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும்.\nJoin பண்ணுங்க!\n\n"
                f"⚠️ இந்த File 10 நிமிடங்களில் நீக்கப்படும். தயவுசெய்து File ஐ உங்கள் Saved Messages-க்குப் Forward பண்ணி வையுங்கள்."
            )
            sent_msg = await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=file_id_to_send,
                caption=caption,
                parse_mode="HTML"
            )
            asyncio.create_task(delete_after_delay(context, sent_msg.chat.id, sent_msg.message_id))
        except Exception as e:
            logging.error(f"❌ கோப்பு அனுப்ப பிழை: {e}")
            await query.message.reply_text("⚠️ கோப்பை அனுப்ப முடியவில்லை. தயவுசெய்து மீண்டும் முயற்சிக்கவும்.")

    else:
        # இணைக்கவில்லை என்றால், அதே மெசேஜை மீண்டும் அனுப்பவும்.
        await query.message.edit_text(
            "⚠️ நீங்கள் இன்னும் சேனலில் இணையவில்லை. முதலில் இணைந்த பிறகு மீண்டும் முயற்சிக்கவும்.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("சேனலில் இணைய இங்கே கிளிக் செய்யவும்", url=PRIVATE_CHANNEL_LINK)],
                [InlineKeyboardButton("மீண்டும் முயற்சிக்கவும்", callback_data=query.data)]
            ]),
        )

# --- Handle movie button click from suggestions ---
async def movie_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if "|" not in data:
        await query.message.reply_text("தவறான கோரிக்கை.")
        return

    prefix, movie_name_key = data.split("|", 1)

    if movie_name_key in movies_data:
        await send_movie_poster(query.message, movie_name_key, context)
    else:
        await query.message.reply_text("❌ மன்னிக்கவும், இந்தத் திரைப்படம் எங்கள் Database-இல் இல்லை\n\n🎬 2025 இல் வெளியான தமிழ் HD திரைப்படங்கள் மட்டுமே இங்கு கிடைக்கும்✨.\n\nஉங்களுக்கு எதுவும் சந்தேகங்கள் இருந்தால் இந்த குழுவில் கேட்கலாம் https://t.me/skmoviesdiscussion")

# --- /status command ---
@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows the current status of the bot, including the number of movies and upload details.
    """
    try:
        # Supabase-ல் இருந்து மொத்த திரைப்படங்களின் எண்ணிக்கையைப் பெறுதல்.
        response = supabase.table("movies").select("id", count="exact").execute()
        total_movies = response.count or 0

        db_size_mb = "N/A"  # டேட்டாபேஸ் அளவை நேரடியாக Supabase API மூலம் பெற முடியாது.

        # கடைசியாகப் பதிவேற்றப்பட்ட திரைப்படத்தின் தகவலைப் பெறுதல்.
        # uploaded_at-ஐ பயன்படுத்தி வரிசைப்படுத்துவது சிறந்தது.
        last_movie_resp = supabase.table("movies").select("title", "uploaded_at").order("uploaded_at", desc=True).limit(1).execute()
        
        last = last_movie_resp.data[0] if last_movie_resp.data else None
        
        if last:
            last_title = last['title']
            # datetime.fromisoformat-ஐ பயன்படுத்தி நேரத்தை சரியாக மாற்றுதல்.
            last_upload_time = datetime.fromisoformat(last['uploaded_at'])
            time_ago = time_diff(last_upload_time)
        else:
            last_title = "இல்லை"
            time_ago = "N/A"

        text = (
            f"📊 *Bot Status:*\n"
            f"----------------------------------\n"
            f"• *மொத்த திரைப்படங்கள்:* `{total_movies}`\n"
            f"• *டேட்டாபேஸ் அளவு:* `{db_size_mb}`\n"
            f"• *கடைசியாகப் பதிவேற்றம்:* \"*{last_title.title()}*\" – _{time_ago}_"
        )

        await update.message.reply_text(text, parse_mode='Markdown')
        
    except Exception as e:
        # Supabase-ல் இருந்து தரவுகளைப் பெற பிழை ஏற்பட்டால்,
        # அதை இங்கே கையாண்டு, பயனருக்குத் தெளிவான பிழைச் செய்தியை அனுப்புகிறது.
        logging.error(f"❌ Status பிழை: {e}")
        await update.message.reply_text("❌ Status info பெற முடியவில்லை.")


# --- /adminpanel command ---
@restricted
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_list = "\n".join([f"👤 {admin_id}" for admin_id in admin_ids])
    await update.message.reply_text(f"🛠️ *Admin Panel*\n\n📋 *Admin IDs:*\n{admin_list}", parse_mode='Markdown')

# --- /addadmin <id> command ---
@restricted
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /addadmin <user_id>")
        return

    try:
        new_admin_id = int(context.args[0])
        if new_admin_id in admin_ids:
            await update.message.reply_text("⚠️ இந்த user ஏற்கனவே ஒரு admin.")
        else:
            admin_ids.add(new_admin_id)
            await update.message.reply_text(f"✅ புதிய admin சேர்க்கப்பட்டது: {new_admin_id}")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்.")

# --- /removeadmin <id> command ---
@restricted
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /removeadmin <user_id>")
        return

    try:
        rem_admin_id = int(context.args[0])
        if rem_admin_id in admin_ids:
            if len(admin_ids) == 1:
                await update.message.reply_text("⚠️ குறைந்தபட்சம் ஒரு admin இருக்க வேண்டும்.")
            else:
                admin_ids.remove(rem_admin_id)
                await update.message.reply_text(f"✅ Admin நீக்கப்பட்டது: {rem_admin_id}")
        else:
            await update.message.reply_text("❌ User admin பட்டியலில் இல்லை.")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்.")

# --- /edittitle command ---
@restricted
async def edittitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படத் தலைப்பை மாற்றுகிறது."""
    args = context.args
    logging.info(f"Edittitle args: {args}")
    if len(args) < 1 or "|" not in " ".join(args):
        await update.message.reply_text("⚠️ Usage: `/edittitle <old title> | <new title>`", parse_mode="Markdown")
        return

    full_args = " ".join(args)
    old_title_raw, new_title_raw = map(lambda x: x.strip(), full_args.split("|", 1))
    
    cleaned_old_title = clean_title(old_title_raw)
    cleaned_new_title = clean_title(new_title_raw)

    logging.info(f"Edittitle parsed - Old Cleaned: '{cleaned_old_title}' (Raw: '{old_title_raw}'), New Cleaned: '{cleaned_new_title}' (Raw: '{new_title_raw}')")

    try:
        response = supabase.table("movies").update({"title": cleaned_new_title}).eq("title", cleaned_old_title).execute()
        
        logging.info(f"Supabase update response data: {response.data}")
        if hasattr(response, 'postgrest_error') and response.postgrest_error:
            logging.error(f"Supabase update PostgREST error: {response.postgrest_error}")
        elif hasattr(response, 'error') and response.error:
            logging.error(f"Supabase update error (old format): {response.error}")
        else:
            logging.info("Supabase update operation completed without PostgREST error.")

        if response.data:
            global movies_data
            movies_data = load_movies_data()
            await update.message.reply_text(f"✅ *{old_title_raw.title()}* இன் தலைப்பு, *{new_title_raw.title()}* ஆக மாற்றப்பட்டது.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பழைய பெயர் கொடுக்கவும்.")
    except Exception as e:
        logging.error(f"❌ தலைப்பு புதுப்பிப்பு பிழை: {e}")
        await update.message.reply_text("❌ தலைப்பு புதுப்பிக்க முடியவில்லை.")
        
# --- /deletemovie command ---
@restricted
async def deletemovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    திரைப்படத்தை டேட்டாபேஸில் இருந்து நீக்குகிறது.
    பயன்பாடு: /deletemovie <திரைப்படப் பெயர்>
    """
    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Usage: `/deletemovie <movie name>`", parse_mode="Markdown")
        return
    
    # திரைப்படப் பெயரைச் சரியாகச் சுத்தம் செய்தல்
    title_to_delete_cleaned = " ".join(args).strip().title()

    logging.info(f"Attempting to delete title: '{title_to_delete_cleaned}'")
    
    try:
        # Supabase-ல் இருந்து திரைப்படம் நீக்க கோரிக்கை அனுப்புதல்
        response = supabase.table("movies").delete().eq("title", title_to_delete_cleaned).execute()
        
        # நீக்கப்பட்ட திரைப்படங்களின் எண்ணிக்கையைப் பெறுதல்
        # Supabase delete operation-க்கு பின் response.data-வில் நீக்கப்பட்ட item-கள் இருக்கும்.
        deleted_count = len(response.data) if response.data else 0

        if deleted_count > 0:
            # திரைப்படம் வெற்றிகரமாக நீக்கப்பட்டால்
            await update.message.reply_text(f"✅ *{title_to_delete_cleaned}* படத்தை நீக்கிவிட்டேன்.", parse_mode="Markdown")
        else:
            # திரைப்படம் கண்டுபிடிக்கப்படவில்லை என்றால்
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பெயர் கொடுக்கவும்.")
    
    except Exception as e:
        logging.error(f"❌ நீக்குதல் பிழை: {e}")
        await update.message.reply_text("❌ DB-இல் இருந்து நீக்க முடியவில்லை.")


# --- Pagination helpers ---
def get_total_movies_count() -> int:
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logging.error(f"❌ மொத்த திரைப்பட எண்ணிக்கையைப் பெற பிழை: {e}")
        return 0

def load_movies_page(limit: int = 20, offset: int = 0) -> list:
    try:
        response = supabase.table("movies").select("title").order("title", desc=False).range(offset, offset + limit - 1).execute()
        movies = response.data or []
        return [m['title'] for m in movies]
    except Exception as e:
        logging.error(f"❌ திரைப்படப் பக்கத்தைப் பதிவேற்ற பிழை: {e}")
        return []

# --- /movielist command ---
@restricted
async def movielist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
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
    
user_post_mode = {}
user_timers = {}

async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in admin_ids:
        await update.message.reply_text("❌ இந்த command admins மட்டும் பயன்படுத்த முடியும்.")
        return

    user_post_mode[user_id] = True
    await update.message.reply_text("✅ போஸ்ட் mode-ல் உள்ளீர்கள். 30s inactivity-க்கு பிறகு auto exit ஆகும்.")

    # Start/reset timeout task
    if user_id in user_timers:
        user_timers[user_id].cancel()  # cancel existing timer

    user_timers[user_id] = asyncio.create_task(post_mode_timeout(user_id, context))

async def post_mode_timeout(user_id, context, timeout=30):
    try:
        await asyncio.sleep(timeout)
        # Timeout expired, remove user from post_mode
        if user_post_mode.get(user_id):
            user_post_mode.pop(user_id)
            await context.bot.send_message(chat_id=user_id, text="⏰ 30 வினாடி inactivity-க்கு பிறகு போஸ்ட் mode நிறுத்தப்பட்டது.")
    except asyncio.CancelledError:
        # Timer was reset/cancelled due to user activity
        pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_post_mode.get(user_id):
        chat_id = GROUP_ID
        msg = update.message

        # Reset timeout timer on each message
        if user_id in user_timers:
            user_timers[user_id].cancel()
        user_timers[user_id] = asyncio.create_task(post_mode_timeout(user_id, context))

        # Forward messages as before
        if msg.text:
            await context.bot.send_message(chat_id=chat_id, text=msg.text)

        elif msg.photo:
            await context.bot.send_photo(chat_id=chat_id, photo=msg.photo[-1].file_id, caption=msg.caption)

        elif msg.video:
            await context.bot.send_video(chat_id=chat_id, video=msg.video.file_id, caption=msg.caption)

        elif msg.audio:
            await context.bot.send_audio(chat_id=chat_id, audio=msg.audio.file_id, caption=msg.caption)

        elif msg.document:
            await context.bot.send_document(chat_id=chat_id, document=msg.document.file_id, caption=msg.caption)

        elif msg.poll:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=msg.poll.question,
                options=[o.text for o in msg.poll.options],
                is_anonymous=msg.poll.is_anonymous,
                allows_multiple_answers=msg.poll.allows_multiple_answers
            )

        elif msg.location:
            await context.bot.send_location(chat_id=chat_id, latitude=msg.location.latitude, longitude=msg.location.longitude)

        await update.message.reply_text("✅ Content group-க்கு அனுப்பப்பட்டது.")

# --- /restart command ---
@restricted
async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("♻️ பாட்டு மீண்டும் தொடங்குகிறது (Koyeb மூலம்)...")
    sys.exit(0)

# --- இங்குதான் முக்கிய மாற்றம் ---
async def start_with_payload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await track_user(user)

    payload = context.args[0] if context.args else None
    user_id = user.id

    if payload and payload.startswith("sendfile_"):
        try:
            full_movie_res_string = payload[len("sendfile_"):]
            movie_name_key_parts = full_movie_res_string.rsplit('_', 1)

            if len(movie_name_key_parts) == 2:
                movie_name_key = movie_name_key_parts[0]
                res = movie_name_key_parts[1]
            else:
                raise ValueError("Invalid payload format (movie_name_key or resolution missing)")

            logging.info(f"Start with payload detected for user {user_id}: {payload}")

            movie = movies_data.get(movie_name_key)
            if not movie:
                await update.message.reply_text("❌ மன்னிக்கவும், இந்தத் திரைப்படம் எங்கள் Database-இல் இல்லை.")
                return

            file_id_to_send = movie['files'].get(res)

            if file_id_to_send:
                caption = (
                    f"🎬 *{movie_name_key.title()}* - {res}\n\n"
                    f"👉 <a href='{PRIVATE_CHANNEL_LINK}'>SK Movies Updates (News)🔔</a> - புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும்.\nJoin பண்ணுங்க!\n\n"
                    f"⚠️ இந்த File 10 நிமிடங்களில் நீக்கப்படும். தயவுசெய்து இந்த File ஐ உங்கள் saved messages க்கு அனுப்பி வையுங்கள்."
                )
                sent_msg = await context.bot.send_document(
                    chat_id=user.id,
                    document=file_id_to_send,
                    caption=caption,
                    parse_mode="HTML"
                )
                await update.message.reply_text("✅ உங்கள் கோப்பு இங்கே!")
                asyncio.create_task(delete_after_delay(context, sent_msg.chat.id, sent_msg.message_id))

                if user_id in pending_file_requests:
                    del pending_file_requests[user_id]
            else:
                await update.message.reply_text("⚠️ இந்த resolution-க்கு file இல்லை.")

        except Exception as e:
            logging.error(f"❌ ஸ்டார்ட் பேலோடுடன் கோப்பு அனுப்ப பிழை: {e}")
            await update.message.reply_text("கோப்பைப் பெற முடியவில்லை. மீண்டும் முயற்சி செய்யுங்கள்.")
    else:
        await update.message.reply_text(f"வணக்கம் {user.first_name}! 👋\n\n"
            "🎬 லேட்டஸ்ட் 2025 HD தமிழ் படங்கள் வேண்டுமா? ✨\n"
            "விளம்பரமில்லா உடனடி தேடலுடன், தரமான சினிமா அனுபவம் இங்கே! 🍿\n\n"
            "🎬 தயவுசெய்து திரைப்படத்தின் பெயரை டைப் செய்து அனுப்புங்கள்!")

# --- Main function to setup bot ---
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_with_payload))
    app.add_handler(CommandHandler("totalusers", total_users_command))
    app.add_handler(CommandHandler("addmovie", addmovie))
    app.add_handler(CommandHandler("post", post_command))
    app.add_handler(CommandHandler("deletemovie", deletemovie))
    app.add_handler(CommandHandler("edittitle", edittitle))
    app.add_handler(CommandHandler("movielist", movielist))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("adminpanel", admin_panel))
    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("removeadmin", remove_admin))
    app.add_handler(CommandHandler("restart", restart_bot))

    app.add_handler(MessageHandler(filters.ALL, general_message_tracker), -1)


    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, save_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, send_movie))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_resolution_click, pattern=r"^res\|"))
    app.add_handler(CallbackQueryHandler(movie_button_click, pattern=r"^movie\|"))
    app.add_handler(CallbackQueryHandler(movielist_callback, pattern=r"^movielist_"))
    
    # --- புதிய Handler-ஐ இங்கே சேர்க்கவும் ---
    app.add_handler(CallbackQueryHandler(handle_try_again_click, pattern=r'^tryagain\|'))

    logging.info("🚀 பாட் தொடங்குகிறது...")
    await app.run_polling()
    
if __name__ == "__main__":
    asyncio.run(main())
