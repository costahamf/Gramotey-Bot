import logging
import os
import re
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")

MAX_TEXT_LENGTH = 5000

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== КЛАВИАТУРЫ ==========
async def start_keyboard():
    keyboard = [
        [InlineKeyboardButton("📖 Помощь", callback_data="help")],
        [InlineKeyboardButton("ℹ️ О боте", callback_data="about")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ========== КОМАНДЫ ==========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для проверки орфографии и грамматики русского языка.\n\n"
        "📝 Просто отправь текст, и я найду ошибки с помощью Яндекс.Спеллера.\n\n"
        "⚡️ Бесплатно, без ограничений и без подписок!",
        reply_markup=await start_keyboard(),
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Как пользоваться:*\n\n"
        "1️⃣ Отправь текст на русском (до 5000 символов).\n"
        "2️⃣ Я покажу ошибки и варианты исправлений.\n\n"
        "🔧 Команды:\n"
        "/start — приветствие\n"
        "/help — помощь\n"
        "/about — о боте",
        parse_mode="Markdown"
    )

async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *О боте*\n\n"
        "Использую API Яндекс.Спеллера — бесплатный сервис проверки русского языка.\n"
        "Бот не сохраняет ваши тексты.\n\n"
        "Работает без Java, без ключей, без ограничений.",
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "help":
        await query.edit_message_text(
            "📖 Отправьте текст на русском, и я найду ошибки.",
            reply_markup=await start_keyboard(),
        )
    elif query.data == "about":
        await query.edit_message_text(
            "ℹ️ Бесплатный бот на основе Яндекс.Спеллера. Без сохранения текстов.",
            reply_markup=await start_keyboard(),
        )

# ========== ОСНОВНАЯ ЛОГИКА (Яндекс.Спеллер) ==========
def clean_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

async def check_text_yandex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверяет текст через Яндекс.Спеллер"""
    user_text = update.message.text
    if user_text.startswith('/'):
        return

    if len(user_text) > MAX_TEXT_LENGTH:
        await update.message.reply_text(f"⚠️ Текст слишком длинный. Максимум {MAX_TEXT_LENGTH} символов.")
        return

    processing_msg = await update.message.reply_text("🔍 Проверяю текст через Яндекс.Спеллер...")

    text = clean_text(user_text)
    if not text:
        await safe_delete(processing_msg)
        await update.message.reply_text("❌ Текст не содержит значимых символов.")
        return

    try:
        url = "https://speller.yandex.net/services/spellservice.json/checkText"
        params = {
            "text": text,
            "lang": "ru",
            "options": 5   # 5 = орфография + грамматика (запятые, согласование)
        }
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: requests.get(url, params=params, timeout=10))
        
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}")

        errors = response.json()
        
        if not errors:
            await update.message.reply_text("✅ Ошибок не найдено!")
            await safe_delete(processing_msg)
            return

        result_parts = []
        for idx, err in enumerate(errors[:30], 1):
            word = err.get("word", "")
            suggestions = err.get("s", [])
            suggestions_text = ", ".join(suggestions[:5]) if suggestions else "нет вариантов"
            pos = err.get("pos", 0)
            length = err.get("len", 0)
            context_start = max(0, pos - 20)
            context_end = min(len(text), pos + length + 20)
            context = text[context_start:context_end].replace('\n', ' ')
            
            error_msg = (
                f"{idx}. 🔤 Орфография/Грамматика\n"
                f"📝 Ошибка: `{word}`\n"
                f"📖 Контекст: `...{context}...`\n"
                f"➜ *Варианты:* {suggestions_text}"
            )
            result_parts.append(error_msg)

        if len(errors) > 30:
            result_parts.append(f"\n... и ещё {len(errors)-30} ошибок.")

        final = "\n\n".join(result_parts)
        for i in range(0, len(final), 4096):
            await update.message.reply_text(final[i:i+4096], parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка Яндекс.Спеллера: {e}")
        await update.message.reply_text("❌ Сервис проверки временно недоступен. Попробуйте позже.")
    finally:
        await safe_delete(processing_msg)

async def safe_delete(message):
    """Безопасно удаляет сообщение, игнорируя ошибку 'Message to delete not found'"""
    if message:
        try:
            await message.delete()
        except Exception as e:
            if "Message to delete not found" in str(e):
                pass  # сообщение уже удалено, игнорируем
            else:
                logger.warning(f"Не удалось удалить сообщение: {e}")

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_text_yandex))

    logger.info("Бот на Яндекс.Спеллер запущен...")
    app.run_polling()

if __name__ == "__main__":
    import asyncio
    main()
