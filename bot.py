import asyncio
import logging
import os
import re
from collections import defaultdict, deque
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from transformers import T5ForConditionalGeneration, T5Tokenizer
from natasha import Doc, Segmenter, NewsNERTagger, NewsEmbedding

# ========== НАСТРОЙКИ ==========
# Токен теперь берется из переменной окружения (БЕЗОПАСНО!)
API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")

# Лимит сообщений на пользователя (можно сделать настраиваемым для каждого чата)
DEFAULT_LIMIT = 100
# Максимальная длина одного сообщения Telegram
MAX_MESSAGE_LEN = 4000

# Настройки модели суммаризации
MODEL_NAME = "cointegrated/rut5-base-absum"  # для слабых серверов замените на "cointegrated/rut5-small-absum"
MAX_INPUT_TOKENS = 1024
MAX_OUTPUT_TOKENS = 200

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ МОДЕЛЕЙ ==========
logger.info("Загрузка модели суммаризации...")
tokenizer = T5Tokenizer.from_pretrained(MODEL_NAME, legacy=False)
model = T5ForConditionalGeneration.from_pretrained(MODEL_NAME)
logger.info("Модель загружена")

logger.info("Загрузка Natasha...")
segmenter = Segmenter()
ner_tagger = NewsNERTagger(NewsEmbedding())
logger.info("Natasha готова")

# ========== ХРАНИЛИЩЕ ==========
# Структура: messages_store[chat_id][user_id] = deque(maxlen=limit)
# Это позволяет разделять сообщения по разным чатам
messages_store: Dict[int, Dict[int, deque]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=DEFAULT_LIMIT)))
# Лимиты для каждого чата (можно расширить под каждого пользователя)
chat_limits: Dict[int, int] = defaultdict(lambda: DEFAULT_LIMIT)

# ========== ОБРАБОТЧИК ТЕКСТА ==========
class TextProcessor:
    @staticmethod
    def clean_text(text: str) -> str:
        """Удаляет ссылки, лишние пробелы, оставляет только буквы, цифры и базовую пунктуацию."""
        text = re.sub(r'http\S+', '', text)          # удалить ссылки
        text = re.sub(r'\s+', ' ', text)              # схлопнуть пробелы
        text = re.sub(r'[^\w\s.,!?а-яА-Я]', '', text) # удалить спецсимволы
        return text.strip()

    @staticmethod
    def extract_named_entities(text: str) -> str:
        """Извлекает имена, организации, локации, даты с помощью Natasha."""
        doc = Doc(text)
        doc.segment(segmenter)
        doc.tag_ner(ner_tagger)

        entities = {"PER": [], "ORG": [], "LOC": [], "DATE": []}
        for span in doc.spans:
            if span.type in entities and span.text.strip() != "Я":
                entities[span.type].append(span.text.strip())

        result = []
        if entities["PER"]:
            result.append(f"👤 Персоны: {', '.join(set(entities['PER']))}")
        if entities["ORG"]:
            result.append(f"🏢 Организации: {', '.join(set(entities['ORG']))}")
        if entities["LOC"]:
            result.append(f"🌍 Локации: {', '.join(set(entities['LOC']))}")
        if entities["DATE"]:
            result.append(f"📅 Даты: {', '.join(set(entities['DATE']))}")
        return "\n".join(result)

    @staticmethod
    def split_text(text: str, max_tokens: int = 500) -> List[str]:
        """
        Разбивает текст на части, не превышающие max_tokens токенов модели.
        Использует прямой токенизатор для скорости.
        """
        tokens = tokenizer.encode(text, add_special_tokens=False)
        chunks = []
        for i in range(0, len(tokens), max_tokens):
            chunk_tokens = tokens[i:i+max_tokens]
            chunk_text = tokenizer.decode(chunk_tokens, skip_special_tokens=True)
            chunks.append(chunk_text)
        return chunks

# ========== АСИНХРОННАЯ ГЕНЕРАЦИЯ СУММАРИЗАЦИИ ==========
async def generate_summary(text: str) -> str:
    """Асинхронная обертка для синхронной модели (не блокирует event loop)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_generate_summary, text)

def _sync_generate_summary(text: str) -> str:
    """Синхронная часть генерации."""
    try:
        # Токенизация
        inputs = tokenizer(
            text,
            max_length=MAX_INPUT_TOKENS,
            truncation=True,
            padding="max_length",
            return_tensors="pt"
        )
        # Генерация
        summary_ids = model.generate(
            inputs.input_ids,
            max_length=MAX_OUTPUT_TOKENS,
            min_length=50,
            length_penalty=1.0,
            num_beams=5,
            repetition_penalty=1.2,
            no_repeat_ngram_size=3,
            temperature=0.7,
            top_k=50,
            top_p=0.9,
            do_sample=True
        )
        return tokenizer.decode(summary_ids[0], skip_special_tokens=True)
    except Exception as e:
        logger.error(f"Ошибка генерации суммаризации: {e}")
        return "Не удалось сгенерировать суммаризацию"

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🤖 Привет! Я бот для суммаризации сообщений в групповых чатах.\n\n"
        "📌 Доступные команды:\n"
        "/summary — получить краткую сводку по последним сообщениям (по умолчанию 100)\n"
        "/set_limit <число> — установить лимит сообщений для этого чата\n"
        "/stats — показать статистику по накопленным сообщениям\n"
        "/help — это сообщение\n\n"
        "💡 *Совет*: используйте бота в группе, он автоматически накапливает сообщения."
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message)  # повторно используем текст

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    chat_id = message.chat.id
    store = messages_store.get(chat_id, {})
    total_users = len(store)
    total_messages = sum(len(q) for q in store.values())
    limit = chat_limits[chat_id]
    await message.answer(
        f"📊 Статистика по этому чату:\n"
        f"👥 Участников с сообщениями: {total_users}\n"
        f"💬 Всего сообщений в памяти: {total_messages}\n"
        f"📏 Лимит на пользователя: {limit}"
    )

@dp.message(Command("set_limit"))
async def cmd_set_limit(message: Message):
    chat_id = message.chat.id
    try:
        limit = int(message.get_args())
        if limit <= 0:
            await message.answer("❌ Лимит должен быть положительным числом.")
            return
        chat_limits[chat_id] = limit
        # Обновляем maxlen для всех очередей этого чата
        for user_id, q in messages_store[chat_id].items():
            q.maxlen = limit
        await message.answer(f"✅ Новый лимит сообщений на пользователя: {limit}")
    except ValueError:
        await message.answer("❌ Укажите число. Пример: `/set_limit 50`", parse_mode="Markdown")

@dp.message(Command("summary"))
async def cmd_summary(message: Message):
    chat_id = message.chat.id
    store = messages_store.get(chat_id, {})
    if not store:
        await message.answer("📭 Нет сообщений для анализа. Подождите, пока участники напишут что-нибудь.")
        return

    processing_msg = await message.answer("⏳ Собираю данные и генерирую сводку... (может занять до 30 секунд)")

    try:
        summaries = []
        for user_id, user_q in store.items():
            # Получаем username (если есть) или используем user_id
            user = await bot.get_chat(user_id)
            username = user.username or f"User_{user_id}"

            # Объединяем все сообщения пользователя
            full_text = " ".join(user_q)
            if not full_text.strip():
                continue

            clean_text = TextProcessor.clean_text(full_text)
            # Извлекаем сущности
            entities = TextProcessor.extract_named_entities(clean_text)

            # Разбиваем длинный текст на части и суммаризуем каждую
            chunks = TextProcessor.split_text(clean_text, max_tokens=500)
            chunk_summaries = []
            for chunk in chunks:
                summ = await generate_summary(chunk)
                if summ:
                    chunk_summaries.append(summ)
            user_summary = " ".join(chunk_summaries)

            if user_summary:
                summary_block = f"👤 *{username}*: {user_summary}"
                if entities:
                    summary_block += f"\n{entities}"
                summaries.append(summary_block)

        if not summaries:
            await message.answer("⚠️ Не удалось сформировать сводку (возможно, слишком мало текста).")
            return

        # Объединяем все сводки и разбиваем на части (Telegram limit 4096)
        full_summary = "\n\n".join(summaries)
        for i in range(0, len(full_summary), MAX_MESSAGE_LEN):
            await message.answer(full_summary[i:i+MAX_MESSAGE_LEN], parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка в /summary: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при генерации сводки. Попробуйте позже.")
    finally:
        await processing_msg.delete()

# ========== ХРАНЕНИЕ СООБЩЕНИЙ ==========
@dp.message()
async def store_message(message: Message):
    # Игнорируем команды (они уже обработаны выше)
    if message.text and message.text.startswith('/'):
        return
    # Игнорируем сообщения от ботов (чтобы не зацикливаться)
    if message.from_user.is_bot:
        return
    # Храним только текстовые сообщения (можно расширить под фото/видео, но пока не нужно)
    if not message.text:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    limit = chat_limits[chat_id]
    # Получаем очередь для этого чата и пользователя
    user_queue = messages_store[chat_id][user_id]
    # Если лимит изменился, обновляем maxlen
    if user_queue.maxlen != limit:
        user_queue.maxlen = limit
    # Добавляем сообщение
    user_queue.append(message.text)

    logger.debug(f"Сохранено сообщение от {user_id} в чате {chat_id}")

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    async def main():
        await dp.start_polling(bot)
    asyncio.run(main())
