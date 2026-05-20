import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from collections import defaultdict, deque
from transformers import T5ForConditionalGeneration, T5Tokenizer
from natasha import Doc, Segmenter, NewsNERTagger, NewsEmbedding
import re
from typing import List, Dict

API_TOKEN = '7962442088:AAE_KLiwfH5QRiGiCuUs1gz0Wg8ShcK4deI'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Хранилище сообщений для каждого пользователя
messages_store: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))

default_message_limit = 100

# Суммаризация
MODEL_NAME = "cointegrated/rut5-base-absum"
tokenizer = T5Tokenizer.from_pretrained(MODEL_NAME, legacy=False)
model = T5ForConditionalGeneration.from_pretrained(MODEL_NAME)

# Natasha
segmenter = Segmenter()
ner_tagger = NewsNERTagger(NewsEmbedding())

class TextProcessor:
    @staticmethod
    def clean_text(text: str) -> str:
        text = re.sub(r'http\S+', '', text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s.,!?а-яА-Я]', '', text)
        return text.strip()

    @staticmethod
    def extract_named_entities(text: str) -> str:
        doc = Doc(text)
        doc.segment(segmenter)
        doc.tag_ner(ner_tagger)
        entities = {"PER": [], "ORG": [], "LOC": [], "DATE": []}

        for span in doc.spans:
            entity_text = span.text.strip()
            if span.type in entities and entity_text != "Я":
                entities[span.type].append(entity_text)

        entity_text = ""
        if entities["PER"]:
            entity_text += f"\n👤 Персоны: {', '.join(set(entities['PER']))}"
        if entities["ORG"]:
            entity_text += f"\n🏢 Организации: {', '.join(set(entities['ORG']))}"
        if entities["LOC"]:
            entity_text += f"\n🌍 Локации: {', '.join(set(entities['LOC']))}"
        if entities["DATE"]:
            entity_text += f"\n📅 Даты: {', '.join(set(entities['DATE']))}"
        
        return entity_text

    @staticmethod
    def split_text(text: str, max_tokens: int = 500) -> List[str]:
        words = text.split()
        chunks = []
        current_chunk = []
        current_length = 0
        
        for word in words:
            word_length = len(tokenizer.tokenize(word))
            if current_length + word_length > max_tokens:
                chunks.append(" ".join(current_chunk))
                current_chunk = [word]
                current_length = word_length
            else:
                current_chunk.append(word)
                current_length += word_length
        
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        
        return chunks

def generate_summary(text: str) -> str:
    try:
        inputs = tokenizer(
            text,
            max_length=1024,
            truncation=True,
            padding='max_length',
            return_tensors="pt"
        )

        summary_ids = model.generate(
            inputs.input_ids,
            max_length=200,
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
        logger.error(f"Error generating summary: {e}")
        return "Не удалось сгенерировать суммаризацию"

# /start
@dp.message(Command("start"))
async def start_command(message: Message):
    start_text = (
        "Привет! Я бот для анализа чатов и суммаризации сообщений в них. "
        "Вот что ты можешь сделать:\n\n"
        "/summary - Получить сводку всех сообщений.\n"
        "/set_limit <число> - Установить лимит сообщений для суммаризации (по умолчанию 100)."
    )
    await message.answer(start_text)

# /set_limit для изменения лимита сообщений
@dp.message(Command("set_limit"))
async def set_limit_command(message: Message):
    try:
        limit = int(message.get_args())
        if limit <= 0:
            await message.answer("Количество сообщений должно быть положительным числом.")
        else:
            global default_message_limit
            default_message_limit = limit
            for user_messages in messages_store.values():
                user_messages.maxlen = limit
            await message.answer(f"Теперь лимит сообщений для суммаризации: {default_message_limit}")
    except ValueError:
        await message.answer("Пожалуйста, укажите число для лимита.")

# /summary
@dp.message(Command("summary"))
async def summary_command(message: Message):
    if not messages_store:
        return await message.answer("Нет сообщений для анализа.")
    
    processing_message = await message.answer("⏳ Генерация сводки, подождите...")
    
    try:
        summaries = []
        for username, user_messages in messages_store.items():
            clean_text = ' '.join([TextProcessor.clean_text(msg) for msg in user_messages])
            entity_text = TextProcessor.extract_named_entities(clean_text)
            
            if clean_text.strip():
                text_chunks = TextProcessor.split_text(clean_text)
                user_summary = " ".join([generate_summary(chunk) for chunk in text_chunks])
                summaries.append(f"👤 *{username}*: {user_summary}{entity_text}")
        
        if summaries:
            summary_text = "\n\n".join(summaries)

            MAX_MESSAGE_LENGTH = 4000
            for i in range(0, len(summary_text), MAX_MESSAGE_LENGTH):
                await message.answer(summary_text[i:i + MAX_MESSAGE_LENGTH], parse_mode="Markdown")
        else:
            await message.answer("Не удалось создать сводку.")
        
    except Exception as e:
        logger.error(f"Summary error: {e}")
        await message.answer("Ошибка при генерации сводки")
    
    await bot.delete_message(message.chat.id, processing_message.message_id)

# Обработчик хранения
@dp.message()
async def store_message(message: Message):
    if message.text and message.from_user.username:
        user_messages = messages_store[message.from_user.username]
        if len(user_messages) >= default_message_limit:
            user_messages.popleft()
        user_messages.append(message.text)

if __name__ == '__main__':
    dp.run_polling(bot)
