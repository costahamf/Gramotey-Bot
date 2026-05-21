import logging
import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY не задан")

MAX_TEXT_LENGTH = 3000

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== ВЫЗОВ OPENROUTER (русская модель) ==========
async def call_openrouter(text: str) -> str:
    """
    Отправляет текст в OpenRouter. Используется модель mistral-7b-instruct:free,
    которая хорошо знает русский язык.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "mistralai/mistral-7b-instruct:free",  # бесплатная, отличный русский
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — профессиональный редактор русского языка. Твоя задача: "
                    "исправить орфографические, грамматические и пунктуационные ошибки в тексте. "
                    "Сохрани исходный смысл и стиль. Верни только исправленный текст, "
                    "без пояснений, без кавычек, без Markdown. Обязательно отвечай на русском языке."
                )
            },
            {
                "role": "user",
                "content": text
            }
        ],
        "temperature": 0.3,
        "max_tokens": 2000
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, timeout=30) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"OpenRouter error {response.status}: {error_text}")
                raise Exception(f"HTTP {response.status}")

            data = await response.json()
            try:
                reply = data['choices'][0]['message']['content'].strip()
                return reply
            except (KeyError, IndexError) as e:
                logger.error(f"Неожиданный ответ OpenRouter: {data}")
                raise Exception("Invalid response format")

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для автоматического исправления текста на русском языке.\n\n"
        "📝 Просто отправь текст, и я с помощью ИИ исправлю орфографию, грамматику и пунктуацию.\n\n"
        "⚡️ Бесплатно, без подписок и лишних отчётов."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Как пользоваться:*\n\n"
        "1️⃣ Отправь текст на русском (до 3000 символов).\n"
        "2️⃣ Бот сразу пришлёт идеально исправленный вариант.\n"
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

    processing_msg = await update.message.reply_text("🤖 Исправляю текст с помощью ИИ...")

    try:
        corrected = await call_openrouter(user_text)

        if corrected and corrected != user_text:
            await update.message.reply_text(f"📝 *Исправленный текст:*\n\n{corrected}", parse_mode="Markdown")
        elif corrected == user_text:
            await update.message.reply_text("✅ Ошибок не найдено. Текст уже правильный.")
        else:
            await update.message.reply_text("❌ Не удалось получить исправленный текст. Попробуйте ещё раз.")

    except asyncio.TimeoutError:
        await update.message.reply_text("❌ Сервис ИИ не отвечает. Попробуйте позже.")
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

    logger.info("🚀 Бот-корректор на OpenRouter (русская модель) запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
