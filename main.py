import logging
import asyncio
import nest_asyncio
import unicodedata
import re
import sys
import os
from functools import wraps
from supabase.client import create_client, Client
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, User # User ஐ இங்கு சேர்க்கவும்
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from rapidfuzz import process
from dotenv import load_dotenv
import base64 # payload ஐ குறியாக்கம் செய்ய

# .env கோப்பிலிருந்து environment variables ஐ ஏற்றவும்
load_dotenv()
nest_asyncio.apply()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Environment variables ஐப் பெறவும்
TOKEN = os.getenv("TOKEN")
admin_ids_str = os.getenv("ADMIN_IDS", "")
admin_ids = set(map(int, filter(None, admin_ids_str.split(","))))
PRIVATE_CHANNEL_LINK = os.getenv("PRIVATE_CHANNEL_LINK")
BOT_USERNAME = os.getenv("BOT_USERNAME") # உங்கள் Bot-இன் Username ஐ .env இல் சேர்க்கவும் (எ.கா: MY_BOT_USERNAME=SK_1_bot)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Supabase client ஐ உருவாக்கவும்
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info(f"✅ Supabase URL: {SUPABASE_URL}")
    logging.info(f"✅ Supabase KEY: {SUPABASE_KEY[:5]}...")
except Exception as e:
    logging.error(f"❌ Supabase client உருவாக்க முடியவில்லை: {e}")
    sys.exit(1)

# Global variable for user files (for addmovie process)
user_files = {}

# --- Utility Functions ---

# --- Extract title from filename ---
def extract_title(filename: str) -> str:
    """ஃபைல் பெயரிலிருந்து திரைப்படத் தலைப்பைப் பிரித்தெடுக்கிறது."""
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

# --- Clean title for DB storage and comparison ---
def clean_title(title: str) -> str:
    """
    திரைப்படத் தலைப்பை சுத்தம் செய்து, lowercase ஆக மாற்றுகிறது.
    இது சேமிக்கும்போதும், தேடும்போதும், நீக்கும்போதும் ஒரே மாதிரியான தலைப்பை உறுதி செய்கிறது.
    """
    cleaned = title.lower()
    cleaned = ''.join(c for c in cleaned if unicodedata.category(c)[0] not in ['S', 'C'])
    cleaned = re.sub(r'[^\w\s\(\)]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

# --- Load movies from Supabase ---
def load_movies_data():
    """Supabase இலிருந்து திரைப்படத் தரவைப் பதிவேற்றுகிறது."""
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

# movies_data ஐ global ஆக ஏற்றவும்
movies_data = load_movies_data()

# --- Decorator for restricted commands ---
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
    """திரைப்படத் தரவை Supabase டேட்டாபேஸில் சேமிக்கிறது."""
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
            logging.error(f"❌ Supabase Insert தோல்வியடைந்தது, தரவு இல்லை: {response.status_code}")
            if response.error: # பிழையை லாக் செய்யவும்
                logging.error(f"Supabase Insert error details: {response.error}")
            return False
    except Exception as e:
        logging.error(f"❌ Supabase Insert பிழை: {e}")
        return False
    
# --- Calculate time difference for status ---
def time_diff(past_time: datetime) -> str:
    """கடந்த நேரத்திற்கும் இப்போதைய நேரத்திற்கும் உள்ள வித்தியாசத்தைக் கணக்கிடுகிறது."""
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

# --- Delete messages after 10 minutes ---
async def delete_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """ஒரு குறிப்பிட்ட காலத்திற்குப் பிறகு Telegram மெசேஜை நீக்குகிறது."""
    await asyncio.sleep(600) # 10 minutes delay
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logging.info(f"Message {message_id} in chat {chat_id} deleted after delay.")
    except Exception as e:
        logging.warning(f"Error deleting message {message_id} in chat {chat_id}: {e}")

# --- Send movie poster with resolution buttons (MODIFIED for Deep Linking) ---
async def send_movie_poster(message: Message, movie_name_key: str, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்பட போஸ்டரை 'படத்தைப் பெற' பட்டனுடன் அனுப்புகிறது, இது தனிப்பட்ட Chat-க்கு Deep Link செய்யும்."""
    movie = movies_data.get(movie_name_key)
    if not movie:
        await message.reply_text("❌ படம் கிடைக்கவில்லை அல்லது போஸ்டர் இல்லை.")
        return

    # ஒவ்வொரு Resolution-க்கும் தனித்தனி Deep Link payload ஐ உருவாக்கவும்
    # payload: "movie_name_key|resolution"
    # base64 குறியாக்கம், payload இல் உள்ள சிறப்பு எழுத்துக்களைத் தவிர்க்க
    payload_480p = base64.urlsafe_b64encode(f"{movie_name_key}|480p".encode()).decode()
    payload_720p = base64.urlsafe_b64encode(f"{movie_name_key}|720p".encode()).decode()
    payload_1080p = base64.urlsafe_b64encode(f"{movie_name_key}|1080p".encode()).decode()

    # Deep Link URL ஐ உருவாக்கவும்
    # t.me/YourBotUsername?start=payload
    bot_link_480p = f"https://t.me/{BOT_USERNAME}?start={payload_480p}"
    bot_link_720p = f"https://t.me/{BOT_USERNAME}?start={payload_720p}"
    bot_link_1080p = f"https://t.me/{BOT_USERNAME}?start={payload_1080p}"

    caption = (
        f"🎬 *{movie_name_key.title()}*\n\n"
        f"👉 <a href='{PRIVATE_CHANNEL_LINK}'>SK Movies Updates (News)🔔</a> - புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும். Join பண்ணுங்க!"
    )

    # Inline Keyboard with buttons linking to the bot's private chat
    keyboard = [
        [
            InlineKeyboardButton("480p பெற", url=bot_link_480p),
            InlineKeyboardButton("720p பெற", url=bot_link_720p),
            InlineKeyboardButton("1080p பெற", url=bot_link_1080p),
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
        logging.info(f"Sent movie poster with deep links for '{movie_name_key}' to chat {message.chat_id}.")
    except Exception as e:
        logging.error(f"❌ போஸ்டர் அனுப்ப பிழை: {e}")
        await message.reply_text("⚠️ போஸ்டர் அனுப்ப முடியவில்லை.")

# --- User Tracking Logic (reusable function) ---
async def track_user(user: User) -> bool: # Returns True if new user, False if existing
    """User-ஐ Database-இல் பதிவு செய்கிறது அல்லது ஏற்கனவே இருந்தால் லாக் செய்கிறது."""
    user_id = user.id
    try:
        response = supabase.table("users").select("user_id").eq("user_id", user_id).limit(1).execute()
        
        if not response.data: # User Database-இல் இல்லை என்றால், அதைச் சேர்க்கவும்
            user_data = {
                "user_id": user_id,
                "username": user.username if user.username else None, # Username இல்லாத User-களையும் கையாளவும்
                "first_name": user.first_name if user.first_name else None, # First Name இல்லாத User-களையும் கையாளவும்
                "last_name": user.last_name if user.last_name else None, # Last Name இல்லாத User-களையும் கையாளவும்
                "joined_at": datetime.utcnow().isoformat()
            }
            insert_response = supabase.table("users").insert(user_data).execute()
            if insert_response.data:
                logging.info(f"✅ புதிய User பதிவு செய்யப்பட்டது: {user_id}")
                return True # New user
            else:
                error_details = insert_response.error if insert_response.error else \
                                insert_response.postgrest_error if hasattr(insert_response, 'postgrest_error') else "Unknown error"
                logging.error(f"❌ User பதிவு செய்ய முடியவில்லை: {user_id}, Error: {error_details}")
                return False # Error, treat as not new for now
        else:
            logging.info(f"User {user_id} ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது.")
            return False # Existing user

    except Exception as e:
        logging.error(f"❌ User பதிவு செய்யும் பிழை: {e}")
        return False # Error, treat as not new for now

# --- General Message Tracker (அனைத்து User செயல்பாடுகளையும் பதிவு செய்ய) ---
async def general_message_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    அனைத்து User Update-களையும் (Commands, Text, Photos, Callbacks) பதிவு செய்கிறது.
    effective_user இல்லாத Update-களை கையாளுகிறது.
    """
    # effective_user இருக்கிறதா என்று சரிபார்க்கவும்
    if update.effective_user:
        # லாகிங்கிற்கான Update வகையை பாதுகாப்பாக தீர்மானிக்கவும்
        log_update_type = "Unknown"
        
        if update.message:
            if hasattr(update.message, 'content_type'):
                log_update_type = update.message.content_type
            elif update.message.text:
                log_update_type = "text"
            elif update.message.photo:
                log_update_type = "photo"
            elif update.message.document:
                log_update_type = "document"
            elif update.message.video:
                log_update_type = "video"
            elif update.message.audio:
                log_update_type = "audio"
            elif update.message.sticker:
                log_update_type = "sticker"
            elif update.message.voice:
                log_update_type = "voice"
            elif update.message.contact:
                log_update_type = "contact"
            elif update.message.location:
                log_update_type = "location"
            elif update.message.poll:
                log_update_type = "poll"
            elif update.message.game:
                log_update_type = "game"
            else:
                log_update_type = "message_other"

        elif update.callback_query:
            log_update_type = "CallbackQuery"
        elif update.inline_query:
            log_update_type = "InlineQuery"
        elif update.chosen_inline_result:
            log_update_type = "ChosenInlineResult"
        elif update.channel_post:
            log_update_type = "ChannelPost"
        elif update.edited_channel_post:
            log_update_type = "EditedChannelPost"

        logging.info(f"General tracker processing update from user: {update.effective_user.id}. Update type: {log_update_type}.")
        await track_user(update.effective_user) # அனைத்து Message-களுக்கும் User ஐப் பதிவு செய்ய
    else:
        logging.info(f"Received update without effective_user. Update ID: {update.update_id}. This update will not register a user.")

# --- /start command (MODIFIED for Deep Linking) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start கட்டளைக்கு பதிலளிக்கிறது மற்றும் Deep Link payload ஐக் கையாளுகிறது."""
    user = update.effective_user
    logging.info(f"Received /start command from user: {user.id}. Args: {context.args}")

    is_new_user = await track_user(user) # User-ஐ Database-இல் பதிவு செய்யவும் மற்றும் புதிய User ஆ என சரிபார்க்கவும்

    # Deep Link payload ஐச் சரிபார்க்கவும்
    if context.args and len(context.args) > 0:
        encoded_payload = context.args[0]
        try:
            decoded_payload = base64.urlsafe_b64decode(encoded_payload).decode()
            logging.info(f"Decoded payload: {decoded_payload}")
            
            # payload: "movie_name_key|resolution"
            if "|" in decoded_payload:
                movie_name_key, res = decoded_payload.split("|", 1) # Split only once
                logging.info(f"Attempting to send movie '{movie_name_key}' with resolution '{res}' to user {user.id}.")
                
                # புதிய User என்றால், முதலில் /start வரவேற்பு
                if is_new_user:
                    await context.bot.send_message(
                        chat_id=user.id,
                        text="🎬 SK Movies Bot-க்கு வரவேற்கிறோம்!\n\nஇங்கு நீங்கள் திரைப்படங்களை தேடலாம் மற்றும் பெறலாம்."
                    )
                    await asyncio.sleep(1) # ஒரு சிறிய தாமதம்

                movie = movies_data.get(movie_name_key)
                if movie and movie['files'].get(res):
                    file_id_to_send = movie['files'].get(res)
                    caption = (
                        f"🎬 *{movie_name_key.title()}*\n\n"
                        f"👉 <a href='{PRIVATE_CHANNEL_LINK}'>SK Movies Updates (News)🔔</a> - புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும்.\nJoin பண்ணுங்க!\n\n"
                        f"⚠️ இந்த File 10 நிமிடங்களில் நீக்கப்படும். தயவுசெய்து இந்த File ஐ உங்கள் saved messages க்கு அனுப்பி வையுங்கள்."
                    )
                    sent_msg = await context.bot.send_document(
                        chat_id=user.id, # தனிப்பட்ட Chat-க்கு அனுப்பவும்
                        document=file_id_to_send,
                        caption=caption,
                        parse_mode="HTML"
                    )
                    logging.info(f"File sent successfully to user {user.id} for movie '{movie_name_key}' resolution '{res}'.")
                    await context.bot.send_message(
                        chat_id=user.id,
                        text="✅ கோப்பு உங்களுக்கு அனுப்பப்பட்டது."
                    )
                    asyncio.create_task(delete_after_delay(context, sent_msg.chat.id, sent_msg.message_id))
                else:
                    logging.warning(f"Movie or file not found for deep link: {movie_name_key}, {res}")
                    await context.bot.send_message(
                        chat_id=user.id,
                        text="❌ நீங்கள் கோரிய படம் அல்லது Resolution கிடைக்கவில்லை."
                    )
            else:
                logging.warning(f"Invalid deep link payload format: {decoded_payload}")
                await context.bot.send_message(
                    chat_id=user.id,
                    text="⚠️ தவறான கோரிக்கை. தயவுசெய்து மீண்டும் முயற்சிக்கவும்."
                )
        except Exception as e:
            logging.error(f"❌ Deep link payload ஐ decode செய்ய முடியவில்லை: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=user.id,
                text="⚠️ கோரிக்கையைச் செயல்படுத்த முடியவில்லை. ஒரு பிழை ஏற்பட்டது."
            )
    else:
        # payload இல்லை என்றால், வழக்கமான /start வரவேற்பு
        await update.message.reply_text("🎬 தயவுசெய்து திரைப்படத்தின் பெயரை அனுப்புங்கள்!")
        logging.info(f"Regular /start reply sent to user: {user.id}")

# --- /totalusers command ---
@restricted # Admins மட்டுமே பார்க்க முடியும்
async def total_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பதிவு செய்யப்பட்ட மொத்த User-களின் எண்ணிக்கையைக் காட்டுகிறது."""
    logging.info(f"Received /totalusers command from user: {update.effective_user.id}.")
    try:
        response = supabase.table("users").select("user_id", count="exact").execute()
        total_users = response.count or 0
        logging.info(f"Total registered users: {total_users}. Sending reply.")
        await update.message.reply_text(f"📊 மொத்த பதிவு செய்யப்பட்ட User-கள்: {total_users}")
    except Exception as e:
        logging.error(f"❌ மொத்த User-களைப் பெற பிழை: {e}")
        await update.message.reply_text("❌ User எண்ணிக்கையைப் பெற முடியவில்லை.")
        
# --- /addmovie command ---
@restricted
async def addmovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """புதிய திரைப்படத்தைச் சேர்க்கும் செயல்முறையைத் தொடங்குகிறது."""
    user_id = update.message.from_user.id
    user_files[user_id] = {"poster": None, "movies": []}
    await update.message.reply_text("போஸ்டர் மற்றும் 3 movie files (480p, 720p, 1080p) அனுப்பவும்.")

# --- Save incoming files (for addmovie process) ---
async def save_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """போஸ்டர் மற்றும் மூவி ஃபைல்களைப் பெற்று Supabase-ல் சேமிக்கிறது."""
    message = update.message
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id not in user_files or (user_files[user_id]["poster"] is None and not message.photo and not message.document):
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
    logging.info(f"Received movie search '{update.message.text}' from user: {update.effective_user.id}. Attempting to process.")
    search_query = update.message.text.strip()

    global movies_data
    movies_data = load_movies_data()

    if not movies_data:
        logging.info("Movies data is empty or could not be loaded. Sending error message.")
        await update.message.reply_text("டேட்டாபேஸ் காலியாக உள்ளது அல்லது ஏற்ற முடியவில்லை. பின்னர் முயற்சிக்கவும்.")
        return

    cleaned_search_query = clean_title(search_query)

    movie_titles = list(movies_data.keys())
    
    good_matches = process.extract(cleaned_search_query, movie_titles, score_cutoff=80)

    if not good_matches:
        broad_suggestions = process.extract(cleaned_search_query, movie_titles, limit=5, score_cutoff=60)
        if broad_suggestions:
            keyboard = [[InlineKeyboardButton(m[0].title(), callback_data=f"movie|{m[0]}")] for m in broad_suggestions]
            logging.info("No good matches found. Sending broad suggestions.")
            await update.message.reply_text(
                "⚠️ நீங்கள் இந்த படங்களில் ஏதாவது குறிப்பிடுகிறீர்களா?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            logging.info("No movie found for the search query. Sending 'not found' message.")
            await update.message.reply_text("❌ படம் கிடைக்கவில்லை!")
    elif len(good_matches) == 1 and good_matches[0][1] >= 95:
        matched_title_key = good_matches[0][0]
        logging.info(f"Direct exact match found for search: '{matched_title_key}'. Sending poster with deep link.")
        await send_movie_poster(update.message, matched_title_key, context)
    else:
        keyboard = [[InlineKeyboardButton(m[0].title(), callback_data=f"movie|{m[0]}")] for m in good_matches]
        logging.info("Multiple good matches found. Sending suggestions.")
        await update.message.reply_text(
            "⚠️ நீங்கள் இந்த படங்களில் ஏதாவது குறிப்பிடுகிறீர்களா?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# --- Handle movie button click from suggestions (MODIFIED to use send_movie_poster) ---
async def movie_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பரிந்துரைக்கப்பட்ட திரைப்படப் பட்டன் கிளிக்குகளைக் கையாளுகிறது."""
    logging.info(f"Received movie button click from user: {update.effective_user.id}. Query data: {update.callback_query.data}. Attempting to send poster with deep link.")
    query = update.callback_query
    await query.answer() # Callback query ஐ உடனடியாக பதிலளிக்கவும்
    data = query.data
    
    if "|" not in data:
        logging.warning(f"Invalid callback data received: {data}")
        await query.message.reply_text("தவறான கோரிக்கை.")
        return

    prefix, movie_name_key = data.split("|", 1) # Split only once

    if movie_name_key in movies_data:
        logging.info(f"Movie button clicked for '{movie_name_key}'. Sending poster with deep link.")
        # இப்போது send_movie_poster ஐப் பயன்படுத்தவும், இது Deep Link பட்டன்களை உருவாக்கும்
        await send_movie_poster(query.message, movie_name_key, context)
    else:
        logging.warning(f"Movie '{movie_name_key}' not found for button click.")
        await query.message.reply_text("❌ படம் கிடைக்கவில்லை!")

# --- Pagination helpers ---
def get_total_movies_count() -> int:
    """டேட்டாபேஸில் உள்ள மொத்த திரைப்படங்களின் எண்ணிக்கையைப் பெறுகிறது."""
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logging.error(f"❌ மொத்த திரைப்பட எண்ணிக்கையைப் பெற பிழை: {e}")
        return 0

def load_movies_page(limit: int = 20, offset: int = 0) -> list:
    """டேட்டாபேஸில் இருந்து திரைப்படப் பட்டியலின் ஒரு பக்கத்தைப் பதிவேற்றுகிறது."""
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
    """திரைப்படப் பட்டியலைக் காட்டுகிறது."""
    logging.info(f"Received /movielist command from user: {update.effective_user.id}.")
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
        logging.info("Movielist is empty for this page. Sending 'no movies' message.")
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
    logging.info(f"Sending movielist page {page} to user {user_id}.")
    await update.message.reply_text(text, reply_markup=reply_markup)

# movielist pagination callback
async def movielist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படப் பட்டியல் பக்கவாட்டு கிளிக்குகளைக் கையாளுகிறது."""
    logging.info(f"Received movielist callback from user: {update.effective_user.id}. Query data: {update.callback_query.data}.")
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("movielist_"):
        logging.warning(f"Invalid movielist callback data: {data}")
        return

    page = int(data.split("_")[1])

    limit = 30
    offset = (page - 1) * limit
    movies = load_movies_page(limit=limit, offset=offset)
    total_movies = get_total_movies_count()
    total_pages = (total_movies + limit - 1) // limit

    if not movies:
        logging.info(f"Movielist is empty for page {page} in callback. Editing message.")
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
    logging.info(f"Editing movielist message to page {page} for user {update.effective_user.id}.")
    await query.message.edit_text(text, reply_markup=reply_markup)

# --- /status command ---
@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பாட்டின் நிலை மற்றும் டேட்டாபேஸ் தகவல்களைக் காட்டுகிறது."""
    logging.info(f"Received /status command from user: {update.effective_user.id}.")
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        total_movies = response.count or 0

        db_size_mb = "N/A" 

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
            f"• Last Upload: \"{last_title.title()}\" – {time_ago}"
        )
        logging.info(f"Sending status info to user {update.effective_user.id}.")
        await update.message.reply_text(text)
    except Exception as e:
        logging.error(f"❌ Status பிழை: {e}")
        await update.message.reply_text("❌ Status info பெற முடியவில்லை.")

# --- /adminpanel command ---
@restricted
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """அட்மின் பேனல் தகவல்களைக் காட்டுகிறது."""
    logging.info(f"Received /adminpanel command from user: {update.effective_user.id}.")
    admin_list = "\n".join([f"👤 {admin_id}" for admin_id in admin_ids])
    logging.info(f"Sending admin panel info to user {update.effective_user.id}.")
    await update.message.reply_text(f"🛠️ *Admin Panel*\n\n📋 *Admin IDs:*\n{admin_list}", parse_mode='Markdown')

# --- /addadmin <id> command ---
@restricted
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """புதிய அட்மினைச் சேர்க்கிறது."""
    logging.info(f"Received /addadmin command from user: {update.effective_user.id}. Args: {context.args}")
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /addadmin <user_id>")
        return

    try:
        new_admin_id = int(context.args[0])
        if new_admin_id in admin_ids:
            logging.info(f"User {new_admin_id} is already an admin.")
            await update.message.reply_text("⚠️ இந்த user ஏற்கனவே ஒரு admin.")
        else:
            admin_ids.add(new_admin_id)
            logging.info(f"Added new admin: {new_admin_id}")
            await update.message.reply_text(f"✅ புதிய admin சேர்க்கப்பட்டது: {new_admin_id}")
    except ValueError:
        logging.warning(f"Invalid user ID provided for /addadmin: {context.args[0]}")
        await update.message.reply_text("⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்.")

# --- /removeadmin <id> command ---
@restricted
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """அட்மினை நீக்குகிறது."""
    logging.info(f"Received /removeadmin command from user: {update.effective_user.id}. Args: {context.args}")
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /removeadmin <user_id>")
        return

    try:
        rem_admin_id = int(context.args[0])
        if rem_admin_id in admin_ids:
            if len(admin_ids) == 1:
                logging.warning(f"Attempted to remove last admin: {rem_admin_id}")
                await update.message.reply_text("⚠️ குறைந்தபட்சம் ஒரு admin இருக்க வேண்டும்.")
            else:
                admin_ids.remove(rem_admin_id)
                logging.info(f"Removed admin: {rem_admin_id}")
                await update.message.reply_text(f"✅ Admin நீக்கப்பட்டது: {rem_admin_id}")
        else:
            logging.info(f"User {rem_admin_id} is not in admin list.")
            await update.message.reply_text("❌ User admin பட்டியலில் இல்லை.")
    except ValueError:
        logging.warning(f"Invalid user ID provided for /removeadmin: {context.args[0]}")
        await update.message.reply_text("⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்.")

# --- /edittitle command ---
@restricted
async def edittitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படத் தலைப்பை மாற்றுகிறது."""
    logging.info(f"Received /edittitle command from user: {update.effective_user.id}. Args: {context.args}")
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
        if response.error: # Changed from postgrest_error to error
            logging.error(f"Supabase update error details: {response.error}")
        else:
            logging.info("Supabase update operation completed without error.")

        if response.data:
            global movies_data
            movies_data = load_movies_data()
            logging.info(f"Title updated successfully for '{old_title_raw}' to '{new_title_raw}'.")
            await update.message.reply_text(f"✅ *{old_title_raw.title()}* இன் தலைப்பு, *{new_title_raw.title()}* ஆக மாற்றப்பட்டது.", parse_mode="Markdown")
        else:
            logging.info(f"Movie '{old_title_raw}' not found for title edit.")
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பழைய பெயர் கொடுக்கவும்.")
    except Exception as e:
        logging.error(f"❌ தலைப்பு புதுப்பிப்பு பிழை: {e}")
        await update.message.reply_text("❌ தலைப்பு புதுப்பிக்க முடியவில்லை.")
        
# --- /deletemovie command ---
@restricted
async def deletemovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படத்தை டேட்டாபேஸில் இருந்து நீக்குகிறது."""
    logging.info(f"Received /deletemovie command from user: {update.effective_user.id}. Args: {context.args}")
    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Usage: `/deletemovie <movie name>`", parse_mode="Markdown")
        return

    title_raw = " ".join(args).strip()
    title_to_delete_cleaned = clean_title(title_raw)

    logging.info(f"Attempting to delete title: '{title_to_delete_cleaned}' (Raw: '{title_raw}')")

    try:
        # நீக்க முயற்சிக்கும் முன், அந்தப் படம் டேட்டாபேஸில் உள்ளதா என்று சரிபார்க்கவும்
        check_response = supabase.table("movies").select("title").eq("title", title_to_delete_cleaned).execute()
        
        if not check_response.data:
            logging.info(f"Movie '{title_to_delete_cleaned}' not found in DB for deletion.")
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பெயர் கொடுக்கவும்.")
            return

        response = supabase.table("movies").delete().eq("title", title_to_delete_cleaned).execute()
        
        logging.info(f"Supabase delete response data: {response.data}")
        if response.error: # Changed from postgrest_error to error
            logging.error(f"Supabase delete error details: {response.error}")
        else:
            logging.info("Supabase delete operation completed without error.")

        if response.data:
            global movies_data
            movies_data = load_movies_data()
            logging.info(f"Movie '{title_raw}' deleted successfully.")
            await update.message.reply_text(f"✅ *{title_raw.title()}* படத்தை நீக்கிவிட்டேன்.", parse_mode="Markdown")
        else:
            # இது பொதுவாக நடக்காது, ஏனெனில் நாம் ஏற்கனவே check_response மூலம் சரிபார்த்துள்ளோம்
            logging.error(f"Failed to delete movie '{title_raw}' despite being found.")
            await update.message.reply_text("❌ நீக்க முடியவில்லை. ஒரு பிழை ஏற்பட்டது.")
    except Exception as e:
        logging.error(f"❌ நீக்குதல் பிழை: {e}")
        await update.message.reply_text("❌ DB-இல் இருந்து நீக்க முடியவில்லை.")

# --- Pagination helpers ---
def get_total_movies_count() -> int:
    """டேட்டாபேஸில் உள்ள மொத்த திரைப்படங்களின் எண்ணிக்கையைப் பெறுகிறது."""
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logging.error(f"❌ மொத்த திரைப்பட எண்ணிக்கையைப் பெற பிழை: {e}")
        return 0

def load_movies_page(limit: int = 20, offset: int = 0) -> list:
    """டேட்டாபேஸில் இருந்து திரைப்படப் பட்டியலின் ஒரு பக்கத்தைப் பதிவேற்றுகிறது."""
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
    """திரைப்படப் பட்டியலைக் காட்டுகிறது."""
    logging.info(f"Received /movielist command from user: {update.effective_user.id}.")
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
        logging.info("Movielist is empty for this page. Sending 'no movies' message.")
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
    logging.info(f"Sending movielist page {page} to user {user_id}.")
    await update.message.reply_text(text, reply_markup=reply_markup)

# movielist pagination callback
async def movielist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படப் பட்டியல் பக்கவாட்டு கிளிக்குகளைக் கையாளுகிறது."""
    logging.info(f"Received movielist callback from user: {update.effective_user.id}. Query data: {update.callback_query.data}.")
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("movielist_"):
        logging.warning(f"Invalid movielist callback data: {data}")
        return

    page = int(data.split("_")[1])

    limit = 30
    offset = (page - 1) * limit
    movies = load_movies_page(limit=limit, offset=offset)
    total_movies = get_total_movies_count()
    total_pages = (total_movies + limit - 1) // limit

    if not movies:
        logging.info(f"Movielist is empty for page {page} in callback. Editing message.")
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
    logging.info(f"Editing movielist message to page {page} for user {update.effective_user.id}.")
    await query.message.edit_text(text, reply_markup=reply_markup)

# --- /status command ---
@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பாட்டின் நிலை மற்றும் டேட்டாபேஸ் தகவல்களைக் காட்டுகிறது."""
    logging.info(f"Received /status command from user: {update.effective_user.id}.")
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        total_movies = response.count or 0

        db_size_mb = "N/A" 

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
            f"• Last Upload: \"{last_title.title()}\" – {time_ago}"
        )
        logging.info(f"Sending status info to user {update.effective_user.id}.")
        await update.message.reply_text(text)
    except Exception as e:
        logging.error(f"❌ Status பிழை: {e}")
        await update.message.reply_text("❌ Status info பெற முடியவில்லை.")

# --- /adminpanel command ---
@restricted
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """அட்மின் பேனல் தகவல்களைக் காட்டுகிறது."""
    logging.info(f"Received /adminpanel command from user: {update.effective_user.id}.")
    admin_list = "\n".join([f"👤 {admin_id}" for admin_id in admin_ids])
    logging.info(f"Sending admin panel info to user {update.effective_user.id}.")
    await update.message.reply_text(f"🛠️ *Admin Panel*\n\n📋 *Admin IDs:*\n{admin_list}", parse_mode='Markdown')

# --- /addadmin <id> command ---
@restricted
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """புதிய அட்மினைச் சேர்க்கிறது."""
    logging.info(f"Received /addadmin command from user: {update.effective_user.id}. Args: {context.args}")
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /addadmin <user_id>")
        return

    try:
        new_admin_id = int(context.args[0])
        if new_admin_id in admin_ids:
            logging.info(f"User {new_admin_id} is already an admin.")
            await update.message.reply_text("⚠️ இந்த user ஏற்கனவே ஒரு admin.")
        else:
            admin_ids.add(new_admin_id)
            logging.info(f"Added new admin: {new_admin_id}")
            await update.message.reply_text(f"✅ புதிய admin சேர்க்கப்பட்டது: {new_admin_id}")
    except ValueError:
        logging.warning(f"Invalid user ID provided for /addadmin: {context.args[0]}")
        await update.message.reply_text("⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்.")

# --- /removeadmin <id> command ---
@restricted
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """அட்மினை நீக்குகிறது."""
    logging.info(f"Received /removeadmin command from user: {update.effective_user.id}. Args: {context.args}")
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /removeadmin <user_id>")
        return

    try:
        rem_admin_id = int(context.args[0])
        if rem_admin_id in admin_ids:
            if len(admin_ids) == 1:
                logging.warning(f"Attempted to remove last admin: {rem_admin_id}")
                await update.message.reply_text("⚠️ குறைந்தபட்சம் ஒரு admin இருக்க வேண்டும்.")
            else:
                admin_ids.remove(rem_admin_id)
                logging.info(f"Removed admin: {rem_admin_id}")
                await update.message.reply_text(f"✅ Admin நீக்கப்பட்டது: {rem_admin_id}")
        else:
            logging.info(f"User {rem_admin_id} is not in admin list.")
            await update.message.reply_text("❌ User admin பட்டியலில் இல்லை.")
    except ValueError:
        logging.warning(f"Invalid user ID provided for /removeadmin: {context.args[0]}")
        await update.message.reply_text("⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்.")

# --- /edittitle command ---
@restricted
async def edittitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படத் தலைப்பை மாற்றுகிறது."""
    logging.info(f"Received /edittitle command from user: {update.effective_user.id}. Args: {context.args}")
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
        if response.error: # Changed from postgrest_error to error
            logging.error(f"Supabase update error details: {response.error}")
        else:
            logging.info("Supabase update operation completed without error.")

        if response.data:
            global movies_data
            movies_data = load_movies_data()
            logging.info(f"Title updated successfully for '{old_title_raw}' to '{new_title_raw}'.")
            await update.message.reply_text(f"✅ *{old_title_raw.title()}* இன் தலைப்பு, *{new_title_raw.title()}* ஆக மாற்றப்பட்டது.", parse_mode="Markdown")
        else:
            logging.info(f"Movie '{old_title_raw}' not found for title edit.")
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பழைய பெயர் கொடுக்கவும்.")
    except Exception as e:
        logging.error(f"❌ தலைப்பு புதுப்பிப்பு பிழை: {e}")
        await update.message.reply_text("❌ தலைப்பு புதுப்பிக்க முடியவில்லை.")
        
# --- /deletemovie command ---
@restricted
async def deletemovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படத்தை டேட்டாபேஸில் இருந்து நீக்குகிறது."""
    logging.info(f"Received /deletemovie command from user: {update.effective_user.id}. Args: {context.args}")
    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Usage: `/deletemovie <movie name>`", parse_mode="Markdown")
        return

    title_raw = " ".join(args).strip()
    title_to_delete_cleaned = clean_title(title_raw)

    logging.info(f"Attempting to delete title: '{title_to_delete_cleaned}' (Raw: '{title_raw}')")

    try:
        # நீக்க முயற்சிக்கும் முன், அந்தப் படம் டேட்டாபேஸில் உள்ளதா என்று சரிபார்க்கவும்
        check_response = supabase.table("movies").select("title").eq("title", title_to_delete_cleaned).execute()
        
        if not check_response.data:
            logging.info(f"Movie '{title_to_delete_cleaned}' not found in DB for deletion.")
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பெயர் கொடுக்கவும்.")
            return

        response = supabase.table("movies").delete().eq("title", title_to_delete_cleaned).execute()
        
        logging.info(f"Supabase delete response data: {response.data}")
        if response.error: # Changed from postgrest_error to error
            logging.error(f"Supabase delete error details: {response.error}")
        else:
            logging.info("Supabase delete operation completed without error.")

        if response.data:
            global movies_data
            movies_data = load_movies_data()
            logging.info(f"Movie '{title_raw}' deleted successfully.")
            await update.message.reply_text(f"✅ *{title_raw.title()}* படத்தை நீக்கிவிட்டேன்.", parse_mode="Markdown")
        else:
            # இது பொதுவாக நடக்காது, ஏனெனில் நாம் ஏற்கனவே check_response மூலம் சரிபார்த்துள்ளோம்
            logging.error(f"Failed to delete movie '{title_raw}' despite being found.")
            await update.message.reply_text("❌ நீக்க முடியவில்லை. ஒரு பிழை ஏற்பட்டது.")
    except Exception as e:
        logging.error(f"❌ நீக்குதல் பிழை: {e}")
        await update.message.reply_text("❌ DB-இல் இருந்து நீக்க முடியவில்லை.")

# --- Pagination helpers ---
def get_total_movies_count() -> int:
    """டேட்டாபேஸில் உள்ள மொத்த திரைப்படங்களின் எண்ணிக்கையைப் பெறுகிறது."""
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logging.error(f"❌ மொத்த திரைப்பட எண்ணிக்கையைப் பெற பிழை: {e}")
        return 0

def load_movies_page(limit: int = 20, offset: int = 0) -> list:
    """டேட்டாபேஸில் இருந்து திரைப்படப் பட்டியலின் ஒரு பக்கத்தைப் பதிவேற்றுகிறது."""
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
    """திரைப்படப் பட்டியலைக் காட்டுகிறது."""
    logging.info(f"Received /movielist command from user: {update.effective_user.id}.")
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
        logging.info("Movielist is empty for this page. Sending 'no movies' message.")
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
    logging.info(f"Sending movielist page {page} to user {user_id}.")
    await update.message.reply_text(text, reply_markup=reply_markup)

# movielist pagination callback
async def movielist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படப் பட்டியல் பக்கவாட்டு கிளிக்குகளைக் கையாளுகிறது."""
    logging.info(f"Received movielist callback from user: {update.effective_user.id}. Query data: {update.callback_query.data}.")
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("movielist_"):
        logging.warning(f"Invalid movielist callback data: {data}")
        return

    page = int(data.split("_")[1])

    limit = 30
    offset = (page - 1) * limit
    movies = load_movies_page(limit=limit, offset=offset)
    total_movies = get_total_movies_count()
    total_pages = (total_movies + limit - 1) // limit

    if not movies:
        logging.info(f"Movielist is empty for page {page} in callback. Editing message.")
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
    logging.info(f"Editing movielist message to page {page} for user {update.effective_user.id}.")
    await query.message.edit_text(text, reply_markup=reply_markup)

# --- /status command ---
@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பாட்டின் நிலை மற்றும் டேட்டாபேஸ் தகவல்களைக் காட்டுகிறது."""
    logging.info(f"Received /status command from user: {update.effective_user.id}.")
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        total_movies = response.count or 0

        db_size_mb = "N/A" 

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
            f"• Last Upload: \"{last_title.title()}\" – {time_ago}"
        )
        logging.info(f"Sending status info to user {update.effective_user.id}.")
        await update.message.reply_text(text)
    except Exception as e:
        logging.error(f"❌ Status பிழை: {e}")
        await update.message.reply_text("❌ Status info பெற முடியவில்லை.")

# --- /adminpanel command ---
@restricted
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """அட்மின் பேனல் தகவல்களைக் காட்டுகிறது."""
    logging.info(f"Received /adminpanel command from user: {update.effective_user.id}.")
    admin_list = "\n".join([f"👤 {admin_id}" for admin_id in admin_ids])
    logging.info(f"Sending admin panel info to user {update.effective_user.id}.")
    await update.message.reply_text(f"🛠️ *Admin Panel*\n\n📋 *Admin IDs:*\n{admin_list}", parse_mode='Markdown')

# --- /addadmin <id> command ---
@restricted
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """புதிய அட்மினைச் சேர்க்கிறது."""
    logging.info(f"Received /addadmin command from user: {update.effective_user.id}. Args: {context.args}")
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /addadmin <user_id>")
        return

    try:
        new_admin_id = int(context.args[0])
        if new_admin_id in admin_ids:
            logging.info(f"User {new_admin_id} is already an admin.")
            await update.message.reply_text("⚠️ இந்த user ஏற்கனவே ஒரு admin.")
        else:
            admin_ids.add(new_admin_id)
            logging.info(f"Added new admin: {new_admin_id}")
            await update.message.reply_text(f"✅ புதிய admin சேர்க்கப்பட்டது: {new_admin_id}")
    except ValueError:
        logging.warning(f"Invalid user ID provided for /addadmin: {context.args[0]}")
        await update.message.reply_text("⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்.")

# --- /removeadmin <id> command ---
@restricted
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """அட்மினை நீக்குகிறது."""
    logging.info(f"Received /removeadmin command from user: {update.effective_user.id}. Args: {context.args}")
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /removeadmin <user_id>")
        return

    try:
        rem_admin_id = int(context.args[0])
        if rem_admin_id in admin_ids:
            if len(admin_ids) == 1:
                logging.warning(f"Attempted to remove last admin: {rem_admin_id}")
                await update.message.reply_text("⚠️ குறைந்தபட்சம் ஒரு admin இருக்க வேண்டும்.")
            else:
                admin_ids.remove(rem_admin_id)
                logging.info(f"Removed admin: {rem_admin_id}")
                await update.message.reply_text(f"✅ Admin நீக்கப்பட்டது: {rem_admin_id}")
        else:
            logging.info(f"User {rem_admin_id} is not in admin list.")
            await update.message.reply_text("❌ User admin பட்டியலில் இல்லை.")
    except ValueError:
        logging.warning(f"Invalid user ID provided for /removeadmin: {context.args[0]}")
        await update.message.reply_text("⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்.")

# --- /edittitle command ---
@restricted
async def edittitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படத் தலைப்பை மாற்றுகிறது."""
    logging.info(f"Received /edittitle command from user: {update.effective_user.id}. Args: {context.args}")
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
        if response.error: # Changed from postgrest_error to error
            logging.error(f"Supabase update error details: {response.error}")
        else:
            logging.info("Supabase update operation completed without error.")

        if response.data:
            global movies_data
            movies_data = load_movies_data()
            logging.info(f"Title updated successfully for '{old_title_raw}' to '{new_title_raw}'.")
            await update.message.reply_text(f"✅ *{old_title_raw.title()}* இன் தலைப்பு, *{new_title_raw.title()}* ஆக மாற்றப்பட்டது.", parse_mode="Markdown")
        else:
            logging.info(f"Movie '{old_title_raw}' not found for title edit.")
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பழைய பெயர் கொடுக்கவும்.")
    except Exception as e:
        logging.error(f"❌ தலைப்பு புதுப்பிப்பு பிழை: {e}")
        await update.message.reply_text("❌ தலைப்பு புதுப்பிக்க முடியவில்லை.")
        
# --- /deletemovie command ---
@restricted
async def deletemovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படத்தை டேட்டாபேஸில் இருந்து நீக்குகிறது."""
    logging.info(f"Received /deletemovie command from user: {update.effective_user.id}. Args: {context.args}")
    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Usage: `/deletemovie <movie name>`", parse_mode="Markdown")
        return

    title_raw = " ".join(args).strip()
    title_to_delete_cleaned = clean_title(title_raw)

    logging.info(f"Attempting to delete title: '{title_to_delete_cleaned}' (Raw: '{title_raw}')")

    try:
        # நீக்க முயற்சிக்கும் முன், அந்தப் படம் டேட்டாபேஸில் உள்ளதா என்று சரிபார்க்கவும்
        check_response = supabase.table("movies").select("title").eq("title", title_to_delete_cleaned).execute()
        
        if not check_response.data:
            logging.info(f"Movie '{title_to_delete_cleaned}' not found in DB for deletion.")
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பெயர் கொடுக்கவும்.")
            return

        response = supabase.table("movies").delete().eq("title", title_to_delete_cleaned).execute()
        
        logging.info(f"Supabase delete response data: {response.data}")
        if response.error: # Changed from postgrest_error to error
            logging.error(f"Supabase delete error details: {response.error}")
        else:
            logging.info("Supabase delete operation completed without error.")

        if response.data:
            global movies_data
            movies_data = load_movies_data()
            logging.info(f"Movie '{title_raw}' deleted successfully.")
            await update.message.reply_text(f"✅ *{title_raw.title()}* படத்தை நீக்கிவிட்டேன்.", parse_mode="Markdown")
        else:
            # இது பொதுவாக நடக்காது, ஏனெனில் நாம் ஏற்கனவே check_response மூலம் சரிபார்த்துள்ளோம்
            logging.error(f"Failed to delete movie '{title_raw}' despite being found.")
            await update.message.reply_text("❌ நீக்க முடியவில்லை. ஒரு பிழை ஏற்பட்டது.")
    except Exception as e:
        logging.error(f"❌ நீக்குதல் பிழை: {e}")
        await update.message.reply_text("❌ DB-இல் இருந்து நீக்க முடியவில்லை.")

# --- Pagination helpers ---
def get_total_movies_count() -> int:
    """டேட்டாபேஸில் உள்ள மொத்த திரைப்படங்களின் எண்ணிக்கையைப் பெறுகிறது."""
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logging.error(f"❌ மொத்த திரைப்பட எண்ணிக்கையைப் பெற பிழை: {e}")
        return 0

def load_movies_page(limit: int = 20, offset: int = 0) -> list:
    """டேட்டாபேஸில் இருந்து திரைப்படப் பட்டியலின் ஒரு பக்கத்தைப் பதிவேற்றுகிறது."""
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
    """திரைப்படப் பட்டியலைக் காட்டுகிறது."""
    logging.info(f"Received /movielist command from user: {update.effective_user.id}.")
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
        logging.info("Movielist is empty for this page. Sending 'no movies' message.")
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
    logging.info(f"Sending movielist page {page} to user {user_id}.")
    await update.message.reply_text(text, reply_markup=reply_markup)

# movielist pagination callback
async def movielist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படப் பட்டியல் பக்கவாட்டு கிளிக்குகளைக் கையாளுகிறது."""
    logging.info(f"Received movielist callback from user: {update.effective_user.id}. Query data: {update.callback_query.data}.")
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("movielist_"):
        logging.warning(f"Invalid movielist callback data: {data}")
        return

    page = int(data.split("_")[1])

    limit = 30
    offset = (page - 1) * limit
    movies = load_movies_page(limit=limit, offset=offset)
    total_movies = get_total_movies_count()
    total_pages = (total_movies + limit - 1) // limit

    if not movies:
        logging.info(f"Movielist is empty for page {page} in callback. Editing message.")
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
    logging.info(f"Editing movielist message to page {page} for user {update.effective_user.id}.")
    await query.message.edit_text(text, reply_markup=reply_markup)

# --- /restart command ---
@restricted
async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பாட்டை மறுதொடக்கம் செய்கிறது."""
    logging.info(f"Received /restart command from user: {update.effective_user.id}. Initiating restart.")
    await update.message.reply_text("♻️ பாட்டு மீண்டும் தொடங்குகிறது (Koyeb மூலம்)...")
    sys.exit(0)

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a message to the user."""
    logging.error(f"Exception while handling an update: {context.error}", exc_info=True)
    
    # Send a generic error message to the user (optional, but good for user experience)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ ஒரு பிழை ஏற்பட்டது. தயவுசெய்து மீண்டும் முயற்சிக்கவும் அல்லது அட்மினைத் தொடர்பு கொள்ளவும்."
            )
        except Exception as e:
            logging.error(f"Failed to send error message to user: {e}")

# --- Main function to setup bot ---
async def main():
    """பாட்டைத் தொடங்கி, அனைத்து ஹேண்ட்லர்களையும் பதிவு செய்கிறது."""
    # Network related settings added for stability
    app = ApplicationBuilder().token(TOKEN)\
        .http_version("1.1")\
        .http_connection_pool_size(50)\
        .read_timeout(30)\
        .write_timeout(30)\
        .connect_timeout(30)\
        .build()

    # பொதுவான Message Tracker ஐ முதலில் சேர்க்கவும்.
    # இது அனைத்து User செயல்பாடுகளையும் (கட்டளைகள், Text, Photos, Callbacks) பதிவு செய்யும்.
    # திருத்தப்பட்ட Message-களைத் தவிர்ப்பது, ஒரே Message-ஐ பலமுறை பதிவு செய்வதைத் தடுக்கும்.
    app.add_handler(MessageHandler(filters.ALL & ~filters.UpdateType.EDITED_MESSAGE, general_message_tracker))

    # கட்டளைகளைப் பதிவு செய்யவும்
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
    app.add_handler(CommandHandler("totalusers", total_users_command))

    # ஃபைல் பதிவேற்ற ஹேண்ட்லர்
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, save_file))

    # திரைப்படத் தேடல் டெக்ஸ்ட் ஹேண்ட்லர் (கட்டளைகள் தவிர்த்து)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, send_movie))

    # Callback ஹேண்ட்லர்கள் (handle_resolution_click நீக்கப்பட்டது, ஏனெனில் Deep Link பயன்படுத்தப்படுகிறது)
    # app.add_handler(CallbackQueryHandler(handle_resolution_click, pattern=r"^res\|")) # இந்த வரி நீக்கப்பட்டது
    app.add_handler(CallbackQueryHandler(movie_button_click, pattern=r"^movie\|"))
    app.add_handler(CallbackQueryHandler(movielist_callback, pattern=r"^movielist_"))

    # Error Handler ஐப் பதிவு செய்யவும் (அனைத்து பிழைகளையும் பிடிக்க)
    app.add_error_handler(error_handler) # இந்த வரி முக்கியம்

    logging.info("🚀 பாட் தொடங்குகிறது...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
