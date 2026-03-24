import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration (replace with your actual values or environment variables) ---
import os

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set")
# MODERATOR_IDS should be a list of integers
# Example: MODERATOR_IDS = [123456789, 987654321]
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
        return {
            'user_telegram_id': result[0],
            'moderator_telegram_id': result[1],
            'status': result[2],
            'messages': eval(result[3]) # Storing messages as string, converting back to list
        }
    return None

def update_chat_status(chat_id, status, moderator_id=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if moderator_id:
        cursor.execute("UPDATE chats SET status = ?, moderator_telegram_id = ? WHERE chat_id = ?", (status, moderator_id, chat_id))
        cursor.execute("UPDATE users SET status = ?, current_chat_id = ? WHERE user_id = ?", (status, chat_id, get_chat_info(chat_id)['user_telegram_id']))
    else:
        cursor.execute("UPDATE chats SET status = ? WHERE chat_id = ?", (status, chat_id))
        user_telegram_id = get_chat_info(chat_id)['user_telegram_id']
        cursor.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_telegram_id))
    conn.commit()
    conn.close()

def add_message_to_chat(chat_id, sender_id, message_text):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    chat_info = get_chat_info(chat_id)
    messages = chat_info['messages']
    messages.append({'sender': sender_id, 'text': message_text})
    cursor.execute("UPDATE chats SET messages = ? WHERE chat_id = ?", (str(messages), chat_id))
    conn.commit()
    conn.close()

def get_open_chats_for_moderator():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, user_telegram_id, messages FROM chats WHERE status = 'open'")
    results = cursor.fetchall()
    conn.close()
    open_chats = []
    for chat_id, user_telegram_id, messages_str in results:
        messages = eval(messages_str)
        open_chats.append({
            'chat_id': chat_id,
            'user_telegram_id': user_telegram_id,
            'initial_message': messages[0]['text'] if messages else 'No initial message'
        })
    return open_chats

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
        if not get_moderator_current_chat(mod_id):
            await application.bot.send_message(
                chat_id=mod_id,
                text=f"Новый запрос от пользователя {user_telegram_id} (Чат ID: {chat_id}):\n\n{message_text}",
                reply_markup=reply_markup
            )

async def get_reply_keyboard(chat_id, is_moderator=False):
    if is_moderator:
        keyboard = [
            [InlineKeyboardButton("Ответить", callback_data=f"reply_to_user_{chat_id}")],
            [InlineKeyboardButton("Закончить диалог", callback_data=f"end_chat_{chat_id}")]
        ]
    else:
        keyboard = [[InlineKeyboardButton("Ответить", callback_data=f"reply_to_moderator_{chat_id}")]]
    return InlineKeyboardMarkup(keyboard)

# --- Command Handlers ---
async def start(update: Update, context) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я бот поддержки. Напишите свой вопрос, и я передам его модераторам.\n\n"
        f"**Наши тарифы:**\n"
        f"· Чеки от 100 до 999 ₽ → 12% \n"
        f"· Чеки от 1000 до 4999 ₽ → 10% \n"
        f"· Чеки от 5000 до 9999 ₽ → 8% \n"
        f"· Чеки от 10 000+ ₽ → 5.5% \n\n"
        f"**Подтверждение сделок:**\n"
        f"— Вручную\n\n"
        f"**Курс конвертации при зачислении депозита:**\n"
        f"— Rapira\n\n"
        f"**Подскажите, пожалуйста, какой у вас вопрос?**",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Наш ТГК", url="https://t.me/DripDropInfo")]])
    )

async def help_command(update: Update, context) -> None:
    await update.message.reply_text("Я бот поддержки. Отправьте мне сообщение, чтобы связаться с модератором.")

# --- Message Handler ---
async def handle_user_message(update: Update, context) -> None:
    user_telegram_id = update.effective_user.id
    message_text = update.message.text

    if user_telegram_id in MODERATOR_IDS:
        # Moderator sending a message
        current_mod_chat_id = get_moderator_current_chat(user_telegram_id)
        if current_mod_chat_id:
            chat_info = get_chat_info(current_mod_chat_id)
            if chat_info and chat_info['status'] == 'in_progress':
                add_message_to_chat(current_mod_chat_id, user_telegram_id, message_text)
                await context.bot.send_message(chat_id=chat_info['user_telegram_id'], text=f"Модератор: {message_text}")
                # Send reply button to moderator
                reply_markup = await get_reply_keyboard(current_mod_chat_id, is_moderator=True)
                await update.message.reply_text("Ваше сообщение отправлено пользователю.", reply_markup=reply_markup)
            else:
                await update.message.reply_text("Этот диалог либо завершен, либо неактивен.")
        else:
            await update.message.reply_text("Вы не ведете активный диалог. Чтобы взять новый, нажмите 'Ответить' в уведомлении.")
        return

    # User sending a message
    user_chat_info = get_user_chat_info(user_telegram_id)

    if not user_chat_info or user_chat_info['status'] == 'closed':
        chat_id = create_new_chat(user_telegram_id, message_text)
        await update.message.reply_text("Ваш запрос отправлен модераторам. Ожидайте ответа.")
        await notify_moderators(context.application, user_telegram_id, message_text, chat_id)
    else:
        # User is already in an active chat
        chat_id = user_chat_info['current_chat_id']
        chat_info = get_chat_info(chat_id)
        if chat_info and chat_info['status'] == 'in_progress' and chat_info['moderator_telegram_id']:
            add_message_to_chat(chat_id, user_telegram_id, message_text)
            moderator_telegram_id = chat_info['moderator_telegram_id']
            await context.bot.send_message(chat_id=moderator_telegram_id, text=f"Пользователь {user_telegram_id}: {message_text}")
            # Send reply button to user
            reply_markup = await get_reply_keyboard(chat_id, is_moderator=False)
            await update.message.reply_text("Ваше сообщение отправлено модератору.", reply_markup=reply_markup)
        elif chat_info and chat_info['status'] == 'open':
            add_message_to_chat(chat_id, user_telegram_id, message_text)
            await update.message.reply_text("Ваш запрос уже в очереди. Ожидайте, пока модератор возьмет его.")
        else:
            # This case should ideally not happen if statuses are managed correctly
            await update.message.reply_text("Произошла ошибка или ваш предыдущий диалог был закрыт. Пожалуйста, начните новый запрос.")
            # Optionally, reset user's chat status here if it's inconsistent

# --- Callback Query Handler ---
async def button(update: Update, context) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    user_telegram_id = query.from_user.id

    if data.startswith("take_chat_"):
        chat_id = int(data.split("_")[1])
        chat_info = get_chat_info(chat_id)

        if user_telegram_id not in MODERATOR_IDS:
            await query.edit_message_text(text="Вы не являетесь модератором.")
            return

        if chat_info and chat_info['status'] == 'open':
            if not get_moderator_current_chat(user_telegram_id):
                update_chat_status(chat_id, 'in_progress', user_telegram_id)

                await query.edit_message_text(text=f"Вы взяли диалог с пользователем {chat_info['user_telegram_id']} (Чат ID: {chat_id})."
                                                   f" Теперь вы можете общаться с ним напрямую.")
                # Notify user
                await context.bot.send_message(chat_id=chat_info['user_telegram_id'],
                                               text=f"Модератор {query.from_user.first_name} присоединился к чату. Можете задавать свои вопросы.")
                # Send reply/end chat buttons to moderator
                reply_markup = await get_reply_keyboard(chat_id, is_moderator=True)
                await context.bot.send_message(chat_id=user_telegram_id, text="Нажмите 'Ответить' чтобы продолжить диалог, или 'Закончить диалог'", reply_markup=reply_markup)

                # Notify other moderators that chat is taken
                for mod_id in MODERATOR_IDS:
                    if mod_id != user_telegram_id:
                        await context.bot.send_message(chat_id=mod_id, text=f"Диалог с пользователем {chat_info['user_telegram_id']} (Чат ID: {chat_id}) был взят модератором {query.from_user.first_name}.")
            else:
                await query.edit_message_text(text="Вы уже ведете другой диалог. Завершите его, чтобы взять новый.")
        elif chat_info and chat_info['status'] == 'in_progress':
            await query.edit_message_text(text="Этот диалог уже взят другим модератором.")
        else:
            await query.edit_message_text(text="Этот диалог больше не доступен.")

    elif data.startswith("reply_to_user_") or data.startswith("reply_to_moderator_"):
        chat_id = int(data.split("_")[1])
        chat_info = get_chat_info(chat_id)

        if not chat_info or chat_info['status'] != 'in_progress':
            await query.edit_message_text(text="Этот диалог либо завершен, либо неактивен.")
            return

        if user_telegram_id == chat_info['moderator_telegram_id']:
            # Moderator wants to reply
            await query.edit_message_text(text="Отправьте ваше сообщение пользователю.")
        elif user_telegram_id == chat_info['user_telegram_id']:
            # User wants to reply
            await query.edit_message_text(text="Отправьте ваше сообщение модератору.")
        else:
            await query.edit_message_text(text="Вы не участвуете в этом диалоге.")

    elif data.startswith("end_chat_"):
        chat_id = int(data.split("_")[1])
        chat_info = get_chat_info(chat_id)

        if user_telegram_id not in MODERATOR_IDS or chat_info['moderator_telegram_id'] != user_telegram_id:
            await query.edit_message_text(text="Вы не можете завершить этот диалог.")
            return

        if chat_info and chat_info['status'] == 'in_progress':
            update_chat_status(chat_id, 'closed')

            await query.edit_message_text(text=f"Диалог с пользователем {chat_info['user_telegram_id']} (Чат ID: {chat_id}) завершен.")
            await context.bot.send_message(chat_id=chat_info['user_telegram_id'], text="Модератор завершил диалог. Если у вас есть новые вопросы, пожалуйста, начните новый запрос.")
        else:
            await query.edit_message_text(text="Этот диалог уже завершен или неактивен.")

def main() -> None:
    init_db()
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_message))
    application.add_handler(CallbackQueryHandler(button))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
