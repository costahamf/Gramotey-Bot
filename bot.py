import logging
import os
import re
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Импорт модели для пунктуации (локальная нейросеть)
from sbert_punc_case_ru import SbertPuncCase

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")

MAX_TEXT_LENGTH = 3000  # Ограничение для модели пунктуации

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ НЕЙРОСЕТИ (ЛОКАЛЬНО) ==========
logger.info("Загрузка модели SbertPuncCase... (это займёт ~30 секунд при первом запуске)")
try:
    punct_model = SbertPuncCase()
    logger.info("✅ Модель пунктуации загружена и готова к работе")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки модели: {e}")
    punct_model = None

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def clean_text(text: str) -> str:
    """Простая очистка текста: убираем лишние пробелы."""
    return re.sub(r'\s+', ' ', text).strip()

async def correct_with_languagetool(text: str) -> str:
    """
    Отправляем текст в LanguageTool (публичный API, бесплатный).
    Возвращает текст с исправленной орфографией и базовой грамматикой.
    """
    url = "https://api.languagetool.org/v2/check"
    data = {
        "text": text,
        "language": "ru-RU",
        "disabledRules": "WHITESPACE_RULE"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, timeout=10) as response:
                if response.status != 200:
                    logger.warning(f"LanguageTool вернул статус {response.status}")
                    return text

                result = await response.json()
                matches = result.get("matches", [])

                if not matches:
                    return text

                # Применяем исправления (берём первое предложенное)
                text_chars = list(text)
                sorted_matches = sorted(matches, key=lambda x: x['offset'], reverse=True)

                for match in sorted_matches:
                    offset = match['offset']
                    length = match['length']
                    replacements = match.get('replacements', [])
                    if replacements:
                        correction = replacements[0].get('value', '')
                        if correction:
                            text_chars[offset:offset+length] = list(correction)

                return ''.join(text_chars)

    except asyncio.TimeoutError:
        logger.warning("Таймаут LanguageTool")
        return text
    except Exception as e:
        logger.error(f"Ошибка LanguageTool: {e}")
        return text

def restore_punctuation_and_case(text: str) -> str:
    """
    Восстанавливает пунктуацию и регистр с помощью локальной нейросети SbertPuncCase.
    Модель работает только со строками в нижнем регистре.
    """
    if not punct_model:
        return text

    if len(text) > MAX_TEXT_LENGTH:
        logger.warning(f"Текст слишком длинный ({len(text)} символов), не отправляем в модель")
        return text

    try:
        # Модель ожидает текст в нижнем регистре
        lower_text = text.lower()
        # Запускаем восстановление пунктуации и регистра
        restored = punct_model.punctuate(lower_text)
        return restored
    except Exception as e:
        logger.error(f"Ошибка в пунктуационной модели: {e}")
        return text

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для автоматического исправления текста.\n\n"
        "📝 Просто отправь текст на русском языке — я:\n"
        "1️⃣ Исправлю орфографию и грамматику через LanguageTool\n"
        "2️⃣ Расставлю знаки препинания и заглавные буквы через нейросеть\n\n"
        "⚡️ Бесплатно и без подписок!"
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

    processing_msg = await update.message.reply_text("🔍 Обрабатываю текст (орфография + пунктуация)...")

    original_text = clean_text(user_text)
    if not original_text:
        await processing_msg.delete()
        await update.message.reply_text("❌ Текст не содержит значимых символов.")
        return

    try:
        # ШАГ 1: Исправляем орфографию и грамматику через LanguageTool
        step1_text = await correct_with_languagetool(original_text)

        # ШАГ 2: Восстанавливаем пунктуацию и регистр через нейросеть
        final_text = restore_punctuation_and_case(step1_text)

        # Если текст изменился — отправляем результат
        if final_text != original_text:
            await update.message.reply_text(f"📝 *Исправленный текст:*\n\n{final_text}", parse_mode="Markdown")
        else:
            await update.message.reply_text("✅ Ошибок не найдено. Текст уже правильный.")

    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке текста. Попробуйте ещё раз.")
    finally:
        await processing_msg.delete()

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 Бот-корректор запущен...")
    logger.info("📌 Первый запуск модели может занять до 30 секунд")
    app.run_polling()

if __name__ == "__main__":
    main()
