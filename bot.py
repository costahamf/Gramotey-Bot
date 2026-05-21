import logging
import os
import re
import asyncio
import aiohttp
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
        "👋 Привет! Я бот для проверки орфографии, грамматики и пунктуации русского языка.\n\n"
        "📝 Просто отправь текст, и я найду ошибки с помощью LanguageTool.\n\n"
        "⚡️ Бесплатно, без подписок!",
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
        "Использую публичный API LanguageTool — мощный инструмент проверки грамматики и пунктуации.\n"
        "Бот не сохраняет ваши тексты.\n\n"
        "Работает без Java, без ключей, с ограничением до 20 запросов в минуту.",
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
            "ℹ️ Бесплатный бот на основе LanguageTool. Без сохранения текстов.",
            reply_markup=await start_keyboard(),
        )

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def clean_text(text: str) -> str:
    """Удаляет лишние пробелы и символы."""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

async def safe_delete(message):
    """Безопасно удаляет сообщение, игнорируя ошибку 'Message to delete not found'"""
    if message:
        try:
            await message.delete()
        except Exception as e:
            if "Message to delete not found" in str(e):
                pass
            else:
                logger.warning(f"Не удалось удалить сообщение: {e}")

# ========== ОСНОВНАЯ ЛОГИКА (LanguageTool) ==========
async def check_text_languagetool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    if user_text.startswith('/'):
        return

    if len(user_text) > MAX_TEXT_LENGTH:
        await update.message.reply_text(f"⚠️ Текст слишком длинный. Максимум {MAX_TEXT_LENGTH} символов.")
        return

    processing_msg = await update.message.reply_text("🔍 Проверяю текст через LanguageTool...")

    text = clean_text(user_text)
    if not text:
        await safe_delete(processing_msg)
        await update.message.reply_text("❌ Текст не содержит значимых символов.")
        return

    url = "https://api.languagetool.org/v2/check"
    data = {
        "text": text,
        "language": "ru-RU"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, timeout=30) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}")

                result = await response.json()
                matches = result.get("matches", [])

                if not matches:
                    await update.message.reply_text("✅ Ошибок не найдено!")
                    await safe_delete(processing_msg)
                    return

                report_parts = []
                for idx, match in enumerate(matches[:30], 1):
                    message = match.get("message", "Ошибка")
                    replacements = match.get("replacements", [])
                    replacements_text = ", ".join([rep.get("value", "") for rep in replacements[:5]]) if replacements else "нет вариантов"
                    offset = match.get("offset", 0)
                    length = match.get("length", 0)
                    start_ctx = max(0, offset - 20)
                    end_ctx = min(len(text), offset + length + 20)
                    context = text[start_ctx:end_ctx].replace('\n', ' ')
                    error_word = text[offset:offset+length] if offset+length <= len(text) else "???"

                    error_msg = (
                        f"{idx}. 🔍 {message}\n"
                        f"📝 Ошибка: `{error_word}`\n"
                        f"📖 Контекст: `...{context}...`\n"
                        f"➜ *Варианты:* {replacements_text}"
                    )
                    report_parts.append(error_msg)

                if len(matches) > 30:
                    report_parts.append(f"\n... и ещё {len(matches)-30} ошибок.")

                final_report = "\n\n".join(report_parts)
                for i in range(0, len(final_report), 4096):
                    await update.message.reply_text(final_report[i:i+4096], parse_mode="Markdown")

    except asyncio.TimeoutError:
        await update.message.reply_text("❌ Сервис проверки не отвечает. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Ошибка LanguageTool: {e}")
        await update.message.reply_text("❌ Произошла ошибка при проверке текста. Попробуйте позже.")
    finally:
        await safe_delete(processing_msg)

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_text_languagetool))

    logger.info("Бот на LanguageTool запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
