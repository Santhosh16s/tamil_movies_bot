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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message # Message ஐ சேர்த்தேன்
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from rapidfuzz import process
from dotenv import load_dotenv # dotenv ஐ இங்கே import செய்யவும்

# .env கோப்பிலிருந்து environment variables ஐ ஏற்றவும்
load_dotenv()
nest_asyncio.apply()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Environment variables ஐப் பெறவும்
TOKEN = os.getenv("TOKEN")  # உங்கள் Bot Token
admin_ids_str = os.getenv("ADMIN_IDS", "")
admin_ids = set(map(int, filter(None, admin_ids_str.split(","))))
PRIVATE_CHANNEL_LINK = os.getenv("PRIVATE_CHANNEL_LINK")  # உங்கள் Channel invite link

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Supabase client ஐ உருவாக்கவும்
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info(f"✅ Supabase URL: {SUPABASE_URL}")
    logging.info(f"✅ Supabase KEY: {SUPABASE_KEY[:5]}...") # முழு key ஐ காண்பிக்க வேண்டாம்
except Exception as e:
    logging.error(f"❌ Supabase client உருவாக்க முடியவில்லை: {e}")
    sys.exit(1) # Supabase இல்லாமல் பாட் இயங்க முடியாது

# Global variable for user files (for addmovie process)
user_files = {}

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

# --- Load movies from Supabase ---
def load_movies_data():
    """Supabase இலிருந்து திரைப்படத் தரவைப் பதிவேற்றுகிறது."""
    try:
        response = supabase.table("movies").select("*").execute()
        movies = response.data or []
        movies_data = {}
        for movie in movies:
            # தலைப்பை clean_title மூலம் சுத்தம் செய்து lowercase ஆக மாற்றவும்
            cleaned_title = clean_title(movie['title']) 
            movies_data[cleaned_title] = {
                'poster_url': movie['poster_url'], # Telegram photo file_id
                'files': {
                    '480p': movie['file_480p'], # Telegram document file_id
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

# --- Extract title from filename ---
def extract_title(filename: str) -> str:
    """ஃபைல் பெயரிலிருந்து திரைப்படத் தலைப்பைப் பிரித்தெடுக்கிறது."""
    filename = re.sub(r"@\S+", "", filename) # @username களை நீக்கவும்
    # Resolution, codec, quality indicators போன்றவற்றை நீக்கவும்
    filename = re.sub(r"\b(480p|720p|1080p|x264|x265|HEVC|HDRip|WEBRip|AAC|10bit|DS4K|UNTOUCHED|mkv|mp4|HD|HQ|Tamil|Telugu|Hindi|English|Dubbed|Org|Original|Proper)\b", "", filename, flags=re.IGNORECASE)
    filename = re.sub(r"[\[\]\(\)\{\}]", " ", filename) # அடைப்புக்குறிகளை இடைவெளியாக மாற்றவும்
    filename = re.sub(r"\s+", " ", filename).strip() # பல இடைவெளிகளை ஒற்றை இடைவெளியாக மாற்றவும்

    # (Year) உடன் தலைப்பைக் கண்டறிய முயற்சிக்கவும்
    match = re.search(r"([a-zA-Z\s]+)(?:\(?)(20\d{2})(?:\)?)", filename)
    if match:
        title = f"{match.group(1).strip()} ({match.group(2)})"
        return title

    # Year இல்லாமல் முதல் hyphen அல்லது எண்ணுக்கு முன் உள்ள பகுதியைப் பிரித்தெடுக்கவும்
    title = re.split(r"[-0-9]", filename)[0].strip()
    return title

# --- Clean title for DB storage and comparison ---
def clean_title(title: str) -> str:
    """
    திரைப்படத் தலைப்பை சுத்தம் செய்து, lowercase ஆக மாற்றுகிறது.
    இது சேமிக்கும்போதும், தேடும்போதும், நீக்கும்போதும் ஒரே மாதிரியான தலைப்பை உறுதி செய்கிறது.
    """
    # முதலில் அனைத்து எழுத்துக்களையும் lowercase ஆக மாற்றவும்
    cleaned = title.lower()
    # S (Symbol) மற்றும் C (Other) யூனிகோட் வகைகளை நீக்கவும்
    cleaned = ''.join(c for c in cleaned if unicodedata.category(c)[0] not in ['S', 'C'])
    # _ (underscore) தவிர மற்ற non-word characters மற்றும் brackets-ஐ நீக்கவும்
    # ( ) போன்ற அடைப்புக்குறிகளை நீக்காமல் வைத்திருக்க வேண்டும் என்றால், regex ஐ மாற்றலாம்.
    # தற்போதைய தேவைக்கு, தலைப்பில் () இருந்தால் அதை நீக்காமல் வைத்திருக்க, regex ஐ மாற்றுகிறேன்.
    cleaned = re.sub(r'[^\w\s\(\)]', '', cleaned) # \(\) ஐ சேர்த்தேன்
    # பல இடைவெளிகளை ஒற்றை இடைவெளியாக மாற்றி, தொடக்க மற்றும் இறுதி இடைவெளிகளை நீக்கவும்
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

# --- Save movie to Supabase ---
def save_movie_to_db(title: str, poster_id: str, file_ids: list) -> bool:
    """திரைப்படத் தரவை Supabase டேட்டாபேஸில் சேமிக்கிறது."""
    try:
        # தலைப்பை clean_title மூலம் சுத்தம் செய்து lowercase ஆக மாற்றவும்
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
    await asyncio.sleep(600)  # 600 வினாடிகள் = 10 நிமிடங்கள் காத்திருக்கவும்
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logging.info(f"Message {message_id} in chat {chat_id} deleted after delay.")
    except Exception as e:
        logging.warning(f"Error deleting message {message_id} in chat {chat_id}: {e}")

# --- Send movie poster with resolution buttons ---
# --- Send movie poster with resolution buttons ---
async def send_movie_poster(message: Message, movie_name_key: str, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்பட போஸ்டரை ரெசல்யூஷன் பட்டன்களுடன் அனுப்புகிறது."""
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
        logging.error(f"❌ போஸ்டர் அனுப்ப பிழை: {e}")
        await message.reply_text("⚠️ போஸ்டர் அனுப்ப முடியவில்லை.")

# --- /start command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start கட்டளைக்கு பதிலளிக்கிறது."""
    await update.message.reply_text("🎬 தயவுசெய்து திரைப்படத்தின் பெயரை அனுப்புங்கள்!")

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

    if user_id not in user_files or user_files[user_id]["poster"] is None and not message.photo and not message.document:
        # /addmovie அனுப்பாமல் நேரடியாக ஃபைல்களை அனுப்பினால்
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
            "file_id": movie_file_id, # Telegram file_id சேமிக்கப்படும்
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
        
        # Telegram file_id-களைப் பிரித்தெடுக்கவும்
        telegram_file_ids_for_db = [m["file_id"] for m in movies_list] 
        
        # முதல் ஃபைல் பெயரிலிருந்து தலைப்பைப் பிரித்தெடுத்து சுத்தம் செய்யவும்
        raw_title = extract_title(movies_list[0]["file_name"])
        cleaned_title = clean_title(raw_title)

        saved = save_movie_to_db(cleaned_title, poster_id, telegram_file_ids_for_db) 
        if saved:
            global movies_data
            movies_data = load_movies_data() # புதிய தரவை ஏற்றவும்
            await message.reply_text(f"✅ Movie saved as *{cleaned_title.title()}*.", parse_mode="Markdown")
        else:
            await message.reply_text("❌ DB-ல் சேமிக்க முடியவில்லை.")

        # செயல்முறை முடிந்ததும் user_files ஐ சுத்தம் செய்யவும்
        user_files[user_id] = {"poster": None, "movies": []}

# --- Send movie on text message (search) ---
async def send_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பயனரின் தேடல் வினவலுக்குப் பதிலளிக்கிறது."""
    search_query = update.message.text.strip() # clean_title இல் lower() இருப்பதால் இங்கு மாற்ற வேண்டாம்

    global movies_data
    movies_data = load_movies_data() # சமீபத்திய தரவை ஏற்றவும்

    if not movies_data:
        await update.message.reply_text("டேட்டாபேஸ் காலியாக உள்ளது அல்லது ஏற்ற முடியவில்லை. பின்னர் முயற்சிக்கவும்.")
        return

    # clean_title ஐப் பயன்படுத்தி தேடல் வினவலை சுத்தம் செய்யவும்
    cleaned_search_query = clean_title(search_query)

    # rapidfuzz மூலம் பொருத்தத்தைக் கண்டறியவும்
    movie_titles = list(movies_data.keys())
    best_match = process.extractOne(cleaned_search_query, movie_titles, score_cutoff=80) # 80% அல்லது அதற்கு மேல் உள்ள பொருத்தத்தை மட்டும் எடுக்கவும்

    if best_match:
        matched_title_key = best_match[0] # பொருத்தமான திரைப்படம்
        logging.info(f"Retrieved movie title from DB for search: '{matched_title_key}'") # லாக் சேர்க்கப்பட்டது
        await send_movie_poster(update.message, matched_title_key, context)
    else:
        # பொருத்தமான திரைப்படங்கள் இல்லை என்றால், ஒத்த பெயர்களைப் பரிந்துரைக்கவும்
        suggestions = process.extract(cleaned_search_query, movie_titles, limit=5, score_cutoff=60) # குறைந்த score_cutoff உடன் பரிந்துரைகளை வழங்கவும்
        if suggestions:
            keyboard = [[InlineKeyboardButton(m[0].title(), callback_data=f"movie_{m[0]}")] for m in suggestions]
            await update.message.reply_text(
                "⚠️ நீங்கள் இந்த படங்களில் ஏதாவது குறிப்பிடுகிறீர்களா?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text("❌ படம் கிடைக்கவில்லை!")


# --- Handle resolution button clicks ---
async def handle_resolution_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ரெசல்யூஷன் பட்டன் கிளிக்குகளைக் கையாளுகிறது மற்றும் திரைப்பட ஃபைலை அனுப்புகிறது."""
    query = update.callback_query
    await query.answer()
    try:
        _, movie_name_key, res = query.data.split("_")
        movie = movies_data.get(movie_name_key)
        if not movie:
            return await query.message.reply_text("❌ படம் கிடைக்கவில்லை!")

        file_id_to_send = movie['files'].get(res) # இது Telegram file_id

        if file_id_to_send:
            caption = (
                f"🎬 *{movie_name_key.title()}*\n\n"
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

            # Telegram file_id-கள் பொதுவாக Telegram சர்வரில் இருக்கும், எனவே delete_after_delay தேவையில்லை.
            # ஆனால் நீங்கள் மெசேஜ் டெலிட் செய்ய விரும்பினால், இந்த பகுதியை அன்கமெண்ட் செய்யலாம்.
            # asyncio.create_task(delete_after_delay(context, sent_msg.chat.id, sent_msg.message_id))
        else:
            await query.message.reply_text("⚠️ இந்த resolution-க்கு file இல்லை.")
    except Exception as e:
        logging.error(f"❌ கோப்பு அனுப்ப பிழை: {e}")
        await query.message.reply_text("⚠️ கோப்பை அனுப்ப முடியவில்லை.")

# --- Handle movie button click from suggestions ---
async def movie_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பரிந்துரைக்கப்பட்ட திரைப்படப் பட்டன் கிளிக்குகளைக் கையாளுகிறது."""
    query = update.callback_query
    await query.answer()
    data_parts = query.data.split("_")
    
    if len(data_parts) < 2:
        await query.message.reply_text("தவறான கோரிக்கை.")
        return

    # clean_title மூலம் சுத்தம் செய்யப்பட்ட பெயரைப் பயன்படுத்தவும்
    movie_name_key = "_".join(data_parts[1:]) # callback_data இல் underscore இருந்தால்
    
    if movie_name_key in movies_data:
        await send_movie_poster(query.message, movie_name_key, context)
    else:
        await query.message.reply_text("❌ படம் கிடைக்கவில்லை!")

# --- /status command ---
@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பாட்டின் நிலை மற்றும் டேட்டாபேஸ் தகவல்களைக் காட்டுகிறது."""
    try:
        response = supabase.table("movies").select("id", count="exact").execute()
        total_movies = response.count or 0

        # டேட்டாபேஸ் அளவைக் கணக்கிட முடியவில்லை என்றால், "N/A" என்று காட்டவும்
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

        await update.message.reply_text(text)
    except Exception as e:
        logging.error(f"❌ Status பிழை: {e}")
        await update.message.reply_text("❌ Status info பெற முடியவில்லை.")

# --- /adminpanel command ---
@restricted
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """அட்மின் பேனல் தகவல்களைக் காட்டுகிறது."""
    admin_list = "\n".join([f"👤 {admin_id}" for admin_id in admin_ids])
    await update.message.reply_text(f"🛠️ *Admin Panel*\n\n📋 *Admin IDs:*\n{admin_list}", parse_mode='Markdown')

# --- /addadmin <id> command ---
@restricted
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """புதிய அட்மினைச் சேர்க்கிறது."""
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
    """அட்மினை நீக்குகிறது."""
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
    
    # தலைப்புகளை clean_title மூலம் சுத்தம் செய்யவும்
    old_title_cleaned = clean_title(old_title_raw)
    new_title_cleaned = clean_title(new_title_raw)

    logging.info(f"Edittitle parsed - Old Cleaned: '{old_title_cleaned}', New Cleaned: '{new_title_cleaned}'")

    try:
        response = supabase.table("movies").update({"title": new_title_cleaned}).eq("title", old_title_cleaned).execute()

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
    """திரைப்படத்தை டேட்டாபேஸில் இருந்து நீக்குகிறது."""
    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Usage: `/deletemovie <movie name>`", parse_mode="Markdown")
        return

    title_raw = " ".join(args).strip()
    title_to_delete_cleaned = clean_title(title_raw) # தலைப்பை clean_title மூலம் சுத்தம் செய்யவும்

    logging.info(f"Attempting to delete title: '{title_to_delete_cleaned}'")

    try:
        response = supabase.table("movies").delete().eq("title", title_to_delete_cleaned).execute()

        if response.data: # Supabase client returns data if delete was successful
            global movies_data
            movies_data = load_movies_data()
            await update.message.reply_text(f"✅ *{title_raw.title()}* படத்தை நீக்கிவிட்டேன்.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பெயர் கொடுக்கவும்.")
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
        text += f"{i}. {title.title()}\n" # தலைப்பை title case இல் காட்டவும்

    keyboard = []
    if page > 1:
        keyboard.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"movielist_{page - 1}"))
    if page < total_pages:
        keyboard.append(InlineKeyboardButton("Next ➡️", callback_data=f"movielist_{page + 1}"))

    reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None
    await update.message.reply_text(text, reply_markup=reply_markup)

# movielist pagination callback
async def movielist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """திரைப்படப் பட்டியல் பக்கவாட்டு கிளிக்குகளைக் கையாளுகிறது."""
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
@restricted
async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """பாட்டை மறுதொடக்கம் செய்கிறது."""
    await update.message.reply_text("♻️ பாட்டு மீண்டும் தொடங்குகிறது (Koyeb மூலம்)...")
    sys.exit(0)

# --- Main function to setup bot ---
async def main():
    """பாட்டைத் தொடங்கி, அனைத்து ஹேண்ட்லர்களையும் பதிவு செய்கிறது."""
    app = ApplicationBuilder().token(TOKEN).build()

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

    # ஃபைல் பதிவேற்ற ஹேண்ட்லர்
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, save_file))

    # திரைப்படத் தேடல் டெக்ஸ்ட் ஹேண்ட்லர் (கட்டளைகள் தவிர்த்து)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, send_movie))

    # Callback ஹேண்ட்லர்கள்
    app.add_handler(CallbackQueryHandler(handle_resolution_click, pattern=r"^res_"))
    app.add_handler(CallbackQueryHandler(movie_button_click, pattern=r"^movie_"))
    app.add_handler(CallbackQueryHandler(movielist_callback, pattern=r"^movielist_"))

    logging.info("🚀 பாட் தொடங்குகிறது...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())

