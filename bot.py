import logging
import os
import re
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")

MAX_TEXT_LENGTH = 5000

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def clean_text(text: str) -> str:
    """Удаляет лишние пробелы, но не трогает знаки препинания."""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def apply_languagetool_corrections(original_text: str, matches: list) -> str:
    """
    Применяет первое предложенное исправление к каждому найденному совпадению.
    Сортирует от конца к началу, чтобы не сбить индексы.
    """
    if not matches:
        return original_text

    # Преобразуем строку в список символов для удобной замены
    text_chars = list(original_text)
    # Сортируем от большего offset к меньшему (с конца)
    sorted_matches = sorted(matches, key=lambda x: x['offset'], reverse=True)

    for match in sorted_matches:
        offset = match['offset']
        length = match['length']
        replacements = match.get('replacements', [])
        if replacements:
            # Берём самое первое предложенное исправление
            correction = replacements[0].get('value', '')
            if correction:
                # Заменяем фрагмент текста
                text_chars[offset:offset+length] = list(correction)

    return ''.join(text_chars)

# ========== ОБРАБОТЧИКИ ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для автоматического исправления орфографии, грамматики и пунктуации.\n\n"
        "📝 Просто отправь текст на русском языке — я верну его исправленную версию.\n\n"
        "⚡️ Бесплатно, без подписок и лишних отчётов."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Как пользоваться:*\n\n"
        "1️⃣ Отправь текст на русском (до 5000 символов).\n"
        "2️⃣ Бот сразу пришлёт исправленный вариант.\n"
        "3️⃣ Если ошибок нет — получишь сообщение об этом.\n\n"
        "🔧 Команды:\n"
        "/start — приветствие\n"
        "/help — справка",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if user_text.startswith('/'):
        return

    if len(user_text) > MAX_TEXT_LENGTH:
        await update.message.reply_text(f"⚠️ Текст слишком длинный. Максимум {MAX_TEXT_LENGTH} символов.")
        return

    # Сообщение "проверяю..."
    processing_msg = await update.message.reply_text("🔍 Исправляю текст...")

    text = clean_text(user_text)
    if not text:
        await processing_msg.delete()
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
                    await update.message.reply_text("✅ Ошибок не найдено. Текст уже правильный.")
                    await processing_msg.delete()
                    return

                # Применяем исправления
                corrected_text = apply_languagetool_corrections(text, matches)

                # Если текст изменился, отправляем исправленный вариант
                if corrected_text != text:
                    await update.message.reply_text(f"📝 *Исправленный текст:*\n\n{corrected_text}", parse_mode="Markdown")
                else:
                    await update.message.reply_text("✅ Ошибок не найдено. Текст уже правильный.")

    except asyncio.TimeoutError:
        await update.message.reply_text("❌ Сервис проверки не отвечает. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте ещё раз.")
    finally:
        await processing_msg.delete()

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот-корректор (LanguageTool) запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
