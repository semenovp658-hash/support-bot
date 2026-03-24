import logging
import os
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set")

MODERATOR_IDS_STR = os.environ.get("MODERATOR_IDS")
if not MODERATOR_IDS_STR:
    raise ValueError("MODERATOR_IDS environment variable not set")
MODERATOR_IDS = [int(x.strip()) for x in MODERATOR_IDS_STR.split(",")]

DB_NAME = 'bot_support.db'

# --- Database Functions ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Stores current interaction state
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            moderator_id INTEGER,
            last_message_id INTEGER,
            state TEXT
        )
    """)
    conn.commit()
    conn.close()

def set_session(user_id, moderator_id=None, state='idle'):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO sessions (user_id, moderator_id, state) VALUES (?, ?, ?)",
                   (user_id, moderator_id, state))
    conn.commit()
    conn.close()

def get_session(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT moderator_id, state FROM sessions WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return {'moderator_id': result[0], 'state': result[1]}
    return None

def get_user_by_moderator(moderator_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM sessions WHERE moderator_id = ? AND state = 'replying'", (moderator_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def clear_session(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    welcome_text = (
        f"Привет, {user.mention_html()}! Я бот поддержки.\n\n"
        f"**Наши тарифы:**\n"
        f"· Чеки от 100 до 999 ₽ → 12% \n"
        f"· Чеки от 1000 до 4999 ₽ → 10% \n"
        f"· Чеки от 5000 до 9999 ₽ → 8% \n"
        f"· Чеки от 10 000+ ₽ → 5.5% \n\n"
        f"**Подтверждение сделок:**\n"
        f"— Вручную\n\n"
        f"**Курс конвертации при зачислении депозита:**\n"
        f"— Rapira\n\n"
        f"**Подскажите, пожалуйста, какой у вас вопрос?**"
    )
    keyboard = [[InlineKeyboardButton("Наш ТГК", url="https://t.me/DripDropInfo")]]
    await update.message.reply_html(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in MODERATOR_IDS:
        target_user_id = get_user_by_moderator(user_id)
        if target_user_id:
            clear_session(target_user_id)
            await update.message.reply_text(f"✅ Диалог с пользователем {target_user_id} завершен.")
            await context.bot.send_message(chat_id=target_user_id, text="🏁 Модератор завершил диалог.")
        else:
            await update.message.reply_text("❌ У вас нет активного диалога для завершения.")
    else:
        clear_session(user_id)
        await update.message.reply_text("✅ Ваш диалог завершен. Если у вас новый вопрос, просто напишите его.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text

    # 1. Moderator is replying to a specific user
    target_user_id = get_user_by_moderator(user_id)
    if target_user_id:
        try:
            keyboard = [[InlineKeyboardButton("Ответить", callback_data=f"user_reply")]]
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"👨‍💻 Ответ модератора:\n\n{text}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            set_session(target_user_id, moderator_id=None, state='idle') # Reset state after reply
            await update.message.reply_text("✅ Ваш ответ отправлен пользователю.")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при отправке: {e}")
        return

    # 2. User is sending a message (either first or reply)
    if user_id not in MODERATOR_IDS:
        keyboard = [[InlineKeyboardButton("Ответить", callback_data=f"mod_reply_{user_id}")]]
        for mod_id in MODERATOR_IDS:
            try:
                await context.bot.send_message(
                    chat_id=mod_id,
                    text=f"🆘 Сообщение от пользователя {user_id}:\n\n{text}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except:
                continue
        await update.message.reply_text("⏳ Ваше сообщение отправлено модераторам. Ожидайте ответа.")
    else:
        await update.message.reply_text("⚠️ Чтобы ответить пользователю, нажмите кнопку «Ответить» под его сообщением.")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    await query.answer()

    if data.startswith("mod_reply_"):
        target_user_id = int(data.split("_")[2])
        # Check if another moderator is already replying
        current_session = get_session(target_user_id)
        if current_session and current_session['state'] == 'replying' and current_session['moderator_id'] != user_id:
            await query.answer("⚠️ Другой модератор уже отвечает на это сообщение!", show_alert=True)
            return

        set_session(target_user_id, moderator_id=user_id, state='replying')
        await query.edit_message_text(f"📝 Вы отвечаете пользователю {target_user_id}. Введите текст ответа:")

    elif data == "user_reply":
        await query.edit_message_text("📝 Введите ваше сообщение для модератора:")

def main() -> None:
    init_db()
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("end", end_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))

    application.run_polling()

if __name__ == "__main__":
    main()
