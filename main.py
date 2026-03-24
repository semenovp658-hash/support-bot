import logging
import sqlite3
import os
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

DB_NAME = 'bot_data.db'

# --- Database Functions ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            current_chat_id INTEGER,
            status TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_telegram_id INTEGER,
            moderator_telegram_id INTEGER,
            status TEXT,
            messages TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_user_chat_info(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT current_chat_id, status FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return {'current_chat_id': result[0], 'status': result[1]}
    return None

def create_new_chat(user_telegram_id, initial_message):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chats (user_telegram_id, status, messages) VALUES (?, ?, ?)",
                   (user_telegram_id, 'open', str([{'sender': user_telegram_id, 'text': initial_message}])))
    chat_id = cursor.lastrowid
    cursor.execute("INSERT OR REPLACE INTO users (user_id, current_chat_id, status) VALUES (?, ?, ?)",
                   (user_telegram_id, chat_id, 'open'))
    conn.commit()
    conn.close()
    return chat_id

def get_chat_info(chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_telegram_id, moderator_telegram_id, status, messages FROM chats WHERE chat_id = ?", (chat_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        try:
            messages = eval(result[3])
        except:
            messages = []
        return {
            'user_telegram_id': result[0],
            'moderator_telegram_id': result[1],
            'status': result[2],
            'messages': messages
        }
    return None

def update_chat_status(chat_id, status, moderator_id=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    chat_info = get_chat_info(chat_id)
    if not chat_info:
        conn.close()
        return
    
    user_id = chat_info['user_telegram_id']
    
    if moderator_id:
        cursor.execute("UPDATE chats SET status = ?, moderator_telegram_id = ? WHERE chat_id = ?", (status, moderator_id, chat_id))
        cursor.execute("UPDATE users SET status = ?, current_chat_id = ? WHERE user_id = ?", (status, chat_id, user_id))
    else:
        cursor.execute("UPDATE chats SET status = ? WHERE chat_id = ?", (status, chat_id))
        cursor.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_id))
    conn.commit()
    conn.close()

def add_message_to_chat(chat_id, sender_id, message_text):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    chat_info = get_chat_info(chat_id)
    if not chat_info:
        conn.close()
        return
    messages = chat_info['messages']
    messages.append({'sender': sender_id, 'text': message_text})
    cursor.execute("UPDATE chats SET messages = ? WHERE chat_id = ?", (str(messages), chat_id))
    conn.commit()
    conn.close()

def get_moderator_current_chat(moderator_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM chats WHERE moderator_telegram_id = ? AND status = 'in_progress'", (moderator_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

# --- Helper Functions ---
async def notify_moderators(application, user_telegram_id, message_text, chat_id):
    keyboard = [[InlineKeyboardButton("Ответить", callback_data=f"take_chat_{chat_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    for mod_id in MODERATOR_IDS:
        try:
            await application.bot.send_message(
                chat_id=mod_id,
                text=f"🆘 Новый запрос от пользователя {user_telegram_id} (ID чата: {chat_id}):\n\n{message_text}",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error notifying moderator {mod_id}: {e}")

async def get_reply_keyboard(chat_id, is_moderator=False):
    if is_moderator:
        keyboard = [
            [InlineKeyboardButton("💬 Написать ответ", callback_data=f"reply_hint_{chat_id}")],
            [InlineKeyboardButton("✅ Закончить диалог", callback_data=f"end_chat_{chat_id}")]
        ]
    else:
        keyboard = [[InlineKeyboardButton("💬 Ответить модератору", callback_data=f"reply_hint_{chat_id}")]]
    return InlineKeyboardMarkup(keyboard)

# --- Command Handlers ---
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

# --- Message Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text

    # Moderator Logic
    if user_id in MODERATOR_IDS:
        chat_id = get_moderator_current_chat(user_id)
        if chat_id:
            chat_info = get_chat_info(chat_id)
            if chat_info and chat_info['status'] == 'in_progress':
                add_message_to_chat(chat_id, user_id, text)
                await context.bot.send_message(
                    chat_id=chat_info['user_telegram_id'], 
                    text=f"👨‍💻 Ответ модератора:\n\n{text}",
                    reply_markup=await get_reply_keyboard(chat_id, is_moderator=False)
                )
                await update.message.reply_text("✅ Сообщение отправлено пользователю.", reply_markup=await get_reply_keyboard(chat_id, is_moderator=True))
            else:
                await update.message.reply_text("❌ Диалог не активен.")
        else:
            # Check if user is trying to start a chat as a user but they are a moderator
            # To avoid confusion, moderators cannot send support requests to themselves
            await update.message.reply_text("⚠️ Вы находитесь в режиме модератора. Чтобы ответить пользователю, сначала возьмите чат через кнопку «Ответить» в уведомлении.")
        return

    # User Logic
    user_info = get_user_chat_info(user_id)
    if not user_info or user_info['status'] == 'closed':
        new_chat_id = create_new_chat(user_id, text)
        await update.message.reply_text("⏳ Ваш запрос отправлен модераторам. Ожидайте ответа.")
        await notify_moderators(context.application, user_id, text, new_chat_id)
    else:
        chat_id = user_info['current_chat_id']
        chat_info = get_chat_info(chat_id)
        if chat_info and chat_info['status'] == 'in_progress' and chat_info['moderator_telegram_id']:
            add_message_to_chat(chat_id, user_id, text)
            await context.bot.send_message(
                chat_id=chat_info['moderator_telegram_id'], 
                text=f"👤 Сообщение от пользователя {user_id}:\n\n{text}",
                reply_markup=await get_reply_keyboard(chat_id, is_moderator=True)
            )
            await update.message.reply_text("✅ Сообщение доставлено модератору.", reply_markup=await get_reply_keyboard(chat_id, is_moderator=False))
        elif chat_info and chat_info['status'] == 'open':
            add_message_to_chat(chat_id, user_id, text)
            await update.message.reply_text("⏳ Ваш запрос уже в очереди. Модератор скоро ответит.")
        else:
            # Handle edge cases like status being 'open' but no chat_info found
            new_chat_id = create_new_chat(user_id, text)
            await update.message.reply_text("⏳ Ваш запрос отправлен модераторам. Ожидайте ответа.")
            await notify_moderators(context.application, user_id, text, new_chat_id)

# --- Callback Query Handler ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    await query.answer()

    if data.startswith("take_chat_"):
        chat_id = int(data.split("_")[1])
        chat_info = get_chat_info(chat_id)

        if user_id not in MODERATOR_IDS:
            await query.answer("Вы не модератор!", show_alert=True)
            return

        if chat_info and chat_info['status'] == 'open':
            if not get_moderator_current_chat(user_id):
                update_chat_status(chat_id, 'in_progress', user_id)
                await query.edit_message_text(f"🤝 Вы взяли чат #{chat_id}.\nПользователь ID: {chat_info['user_telegram_id']}\n\nНапишите ответ пользователю прямо сюда.")
                await context.bot.send_message(
                    chat_id=chat_info['user_telegram_id'],
                    text="👨‍💻 Модератор подключился к диалогу. Можете задавать вопросы."
                )
            else:
                await query.answer("У вас уже есть активный чат!", show_alert=True)
        else:
            await query.edit_message_text("⚠️ Этот чат уже взят или закрыт.")

    elif data.startswith("reply_hint_"):
        await context.bot.send_message(chat_id=user_id, text="⌨️ Просто введите текст сообщения и отправьте его.")

    elif data.startswith("end_chat_"):
        chat_id = int(data.split("_")[1])
        chat_info = get_chat_info(chat_id)

        if user_id in MODERATOR_IDS and chat_info and chat_info['moderator_telegram_id'] == user_id:
            update_chat_status(chat_id, 'closed')
            await query.edit_message_text(f"✅ Чат #{chat_id} завершен.")
            await context.bot.send_message(
                chat_id=chat_info['user_telegram_id'],
                text="🏁 Модератор завершил диалог. Спасибо за обращение!"
            )

def main() -> None:
    init_db()
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))

    application.run_polling()

if __name__ == "__main__":
    main()
