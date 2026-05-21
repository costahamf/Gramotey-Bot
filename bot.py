import asyncio
import logging
import os
import re
from io import BytesIO

import language_tool_python
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")

MAX_TEXT_LENGTH = 5000  # Максимальная длина текста (символов)
MAX_MESSAGE_LEN = 4096  # Telegram лимит на одно сообщение

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ LanguageTool (ОФЛАЙН/ОНЛАЙН) ==========
# LanguageTool поддерживает русский язык и его можно использовать локально
# Если в вашем окружении возникают проблемы, установите remote_server="https://languagetool.org/api/v2/"
# или оставьте пустым для автоматического скачивания локальной версии.
try:
    tool = language_tool_python.LanguageTool("ru-RU")
    logger.info("LanguageTool инициализирован для русского языка.")
except Exception as e:
    logger.error(f"Ошибка инициализации LanguageTool: {e}")
    tool = None

# ========== КЛАВИАТУРЫ ==========
async def start_keyboard():
    keyboard = [
        [InlineKeyboardButton("📖 Помощь", callback_data="help")],
        [InlineKeyboardButton("ℹ️ О боте", callback_data="about")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "👋 Привет! Я бот для проверки орфографии и грамматики.\n\n"
        "📝 Просто отправь мне текст на русском языке, и я найду в нём ошибки.\n\n"
        "⚡️ Работает полностью бесплатно!",
        reply_markup=await start_keyboard(),
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📖 *Как пользоваться ботом:*\n\n"
        "1️⃣ Отправь мне любой текст на русском языке.\n"
        "2️⃣ Я проанализирую его и найду орфографические и грамматические ошибки.\n"
        "3️⃣ В ответе я покажу ошибки, их тип и предложу варианты исправления.\n\n"
        "✨ *Особенности:*\n"
        "• Поддерживаются тексты до 5000 символов.\n"
        "• Работает полностью бесплатно и без ограничений.\n"
        "• Не требует подписки на каналы.\n\n"
        "🔧 Доступные команды:\n"
        "/start - Приветственное сообщение\n"
        "/help - Показать эту справку\n"
        "/about - Информация о боте"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /about"""
    await update.message.reply_text(
        "ℹ️ *О боте*\n\n"
        "Этот бот создан для проверки орфографии и грамматики текстов на русском языке.\n\n"
        "🛠 *Технологии:*\n"
        "• LanguageTool - мощный инструмент проверки грамматики (Open Source)\n"
        "• Python + библиотека python-telegram-bot\n\n"
        "📡 *Приватность:*\n"
        "Бот не сохраняет и не передаёт ваши тексты третьим лицам.\n\n"
        "💡 *Совет:* Для наилучшего результата отправляйте текст небольшими частями (до 1000 символов).",
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-кнопок"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "help":
        await query.edit_message_text(
            "📖 Отправьте мне текст на русском языке, и я найду в нём ошибки.\n\n"
            "Поддерживаются тексты до 5000 символов.",
            reply_markup=await start_keyboard(),
        )
    elif data == "about":
        await query.edit_message_text(
            "ℹ️ Бот использует LanguageTool — Open Source инструмент проверки грамматики.\n\n"
            "Бот не сохраняет ваши тексты.",
            reply_markup=await start_keyboard(),
        )

# ========== ОСНОВНАЯ ЛОГИКА ПРОВЕРКИ ==========
def clean_text(text: str) -> str:
    """Очищает текст от лишних пробелов и невидимых символов."""
    # Заменяем множественные пробелы на один
    text = re.sub(r'\s+', ' ', text)
    # Удаляем символы управления (оставляем только печатные)
    text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\r')
    return text.strip()

async def check_text(text: str) -> str:
    if not tool:
        return "❌ LanguageTool не инициализирован."

    if len(text) > MAX_TEXT_LENGTH:
        return f"⚠️ Текст слишком длинный. Максимум {MAX_TEXT_LENGTH} символов."

    text = clean_text(text)
    if not text:
        return "❌ Текст пуст."

    try:
        loop = asyncio.get_running_loop()
        matches = await loop.run_in_executor(None, tool.check, text)

        if not matches:
            return "✅ Ошибок не найдено!"

        result_parts = []
        for i, match in enumerate(matches[:30]):
            error_type = "🔤 Орфография" if "SPELLING" in str(match.ruleId) else "📐 Грамматика"
            error_word = match.context[match.offset:match.offset+match.errorLength]
            context_clean = match.context.replace('`', '')  # убираем кавычки

            correction_text = ""
            if match.replacements:
                repl = ", ".join(match.replacements[:5])
                correction_text = "➜ *Варианты:* " + repl

            # Формируем строку без f-строк (используем конкатенацию)
            error_msg = (
                str(i+1) + ". **" + error_type + "**\n"
                "📝 Ошибка: `" + error_word + "`\n"
                "📖 Контекст: " + context_clean + "\n"
                + correction_text
            )
            result_parts.append(error_msg)

        if len(matches) > 30:
            result_parts.append("\n... и ещё " + str(len(matches) - 30) + " ошибок.")

        return "\n\n".join(result_parts)

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return "❌ Ошибка при проверке текста."

# ========== ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ==========
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения и команды"""
    user_text = update.message.text

    # Игнорируем команды (они уже обработаны выше)
    if user_text.startswith('/'):
        return

    # Ограничиваем длину текста для проверки
    if len(user_text) > MAX_TEXT_LENGTH:
        await update.message.reply_text(
            f"⚠️ Текст слишком длинный ({len(user_text)} символов).\n"
            f"Пожалуйста, отправьте текст частями не более {MAX_TEXT_LENGTH} символов."
        )
        return

    # Отправляем сообщение о начале проверки
    processing_msg = await update.message.reply_text("🔍 Проверяю текст, пожалуйста, подождите...")

    try:
        # Выполняем проверку текста
        result = await check_text(user_text)

        # Отправляем результат (разбиваем на части, если он слишком длинный)
        if len(result) > MAX_MESSAGE_LEN:
            for i in range(0, len(result), MAX_MESSAGE_LEN):
                await update.message.reply_text(
                    result[i:i+MAX_MESSAGE_LEN],
                    parse_mode="Markdown"
                )
        else:
            await update.message.reply_text(result, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка в handle_text: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла непредвиденная ошибка. Попробуйте ещё раз.")
    finally:
        # Удаляем сообщение "Проверяю..."
        await processing_msg.delete()

# ========== ЗАПУСК БОТА ==========
def main():
    """Запуск бота"""
    app = Application.builder().token(BOT_TOKEN).build()

    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))

    # Регистрируем обработчик inline-кнопок
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Регистрируем обработчик текстовых сообщений (кроме команд)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 Бот для проверки орфографии запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
