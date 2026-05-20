import os
import re
import logging
from bs4 import BeautifulSoup
from readability import Document
from youtube_transcript_api import YouTubeTranscriptApi
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import nltk
from nltk.corpus import stopwords
from nltk.tokenize import sent_tokenize, word_tokenize
import string
from collections import Counter
import heapq

# --- НАСТРОЙКИ ---
BOT_TOKEN = "7962442088:AAE_KLiwfH5QRiGiCuUs1gz0Wg8ShcK4deI"

DATA_DIR = os.getenv('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Загружаем ресурсы NLTK при старте (один раз)
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt', quiet=True)
try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords', quiet=True)

# --- СЛУЖЕБНЫЕ ФУНКЦИИ ---

def get_youtube_transcript(url):
    """Извлекает субтитры из YouTube видео, включая автогенерируемые."""
    video_id_match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})(?:[?&]|$)', url)
    video_id = video_id_match.group(1) if video_id_match else None

    if not video_id:
        return None, "❌ Не удалось извлечь ID видео из ссылки."

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Пытаемся найти подходящие субтитры: сначала русские, потом английские, потом любые
        transcript = None
        try:
            transcript = transcript_list.find_transcript(['ru', 'en'])
        except NoTranscriptFound:
            # Если нет ни русских, ни английских, пробуем взять первый доступный
            try:
                transcript = list(transcript_list)[0]
            except:
                pass

        if transcript:
            # Если это автогенерируемые, они могут быть помечены, но всё равно работают
            data = transcript.fetch()
            full_text = " ".join([entry['text'] for entry in data])
            return full_text, None
        else:
            return None, "❌ К сожалению, для этого видео нет субтитров. Попробуйте другое видео или отправьте ссылку на статью."
    except TranscriptsDisabled:
        return None, "❌ Субтитры для этого видео отключены автором."
    except NoTranscriptFound:
        return None, "❌ Не найдено субтитров на русском или английском языке. Попробуйте другое видео."
    except Exception as e:
        logger.error(f"Ошибка при получении субтитров: {e}")
        return None, f"❌ Ошибка при получении субтитров: {str(e)}"

def extract_article_text(url):
    """Извлекает заголовок и основной текст из статьи."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        doc = Document(response.text)
        title = doc.title()
        content_html = doc.summary()

        soup = BeautifulSoup(content_html, 'lxml')
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)

        return title, text, None
    except Exception as e:
        logger.error(f"Ошибка при обработке статьи: {e}")
        return None, None, f"❌ Ошибка: {str(e)}"

def extractive_summarization(text, num_sentences=5):
    """
    Выделяет наиболее важные предложения на основе частотности слов (без нейросетей).
    """
    # Токенизируем предложения
    sentences = sent_tokenize(text)
    if len(sentences) <= num_sentences:
        return text

    # Стоп-слова для русского и английского
    stop_words = set(stopwords.words('russian') + stopwords.words('english') + list(string.punctuation))
    
    # Считаем частоту слов
    word_frequencies = Counter()
    for word in word_tokenize(text.lower()):
        if word not in stop_words:
            word_frequencies[word] += 1

    # Нормализуем
    if word_frequencies:
        max_freq = max(word_frequencies.values())
        for word in word_frequencies:
            word_frequencies[word] /= max_freq

    # Оцениваем предложения
    sentence_scores = {}
    for sent in sentences:
        for word in word_tokenize(sent.lower()):
            if word in word_frequencies:
                if len(sent.split(' ')) < 30:  # игнорируем слишком короткие
                    sentence_scores[sent] = sentence_scores.get(sent, 0) + word_frequencies[word]

    # Берём топ предложений
    if sentence_scores:
        summary_sentences = heapq.nlargest(num_sentences, sentence_scores, key=sentence_scores.get)
        summary = ' '.join(summary_sentences)
        return summary
    else:
        # Если ничего не нашли, возвращаем первые num_sentences предложений
        return ' '.join(sentences[:num_sentences])

def summarize_text(text):
    """Основная функция суммаризации (только extractive)."""
    if not text or len(text.strip()) == 0:
        return "❌ Нет текста для суммаризации."
    return extractive_summarization(text)

# --- ОБРАБОТЧИКИ БОТА ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Привет! Я бот для краткого пересказа статей и видео.\n\n"
        "📌 Отправь мне ссылку на статью или YouTube видео.\n"
        "Я извлеку главные мысли и пришлю краткое содержание.\n\n"
        "⚡️ Работает полностью локально, без передачи данных.",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Как пользоваться:\n\n"
        "1. Скопируй ссылку на статью или YouTube видео.\n"
        "2. Отправь ссылку в чат.\n"
        "3. Получи краткий пересказ (3-5 предложений).\n\n"
        "Доступные команды:\n/start - Приветствие\n/help - Справка"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text.strip()
    
    is_youtube = 'youtube.com/watch' in user_message or 'youtu.be/' in user_message
    is_article = user_message.startswith('http') and not is_youtube

    if is_youtube:
        await update.message.reply_text("🎬 Обрабатываю YouTube видео...")
        transcript, error = get_youtube_transcript(user_message)
        if error:
            await update.message.reply_text(error)
            return
        if not transcript:
            await update.message.reply_text("❌ Не удалось извлечь субтитры.")
            return
        
        summary = summarize_text(transcript)
        await update.message.reply_text(f"🎬 *Краткое содержание:*\n\n{summary}", parse_mode='Markdown')

    elif is_article:
        await update.message.reply_text("📄 Обрабатываю статью...")
        title, text, error = extract_article_text(user_message)
        if error:
            await update.message.reply_text(error)
            return
        if not text:
            await update.message.reply_text("❌ Не удалось извлечь текст.")
            return
        
        summary = summarize_text(text)
        response = f"📄 *{title}*\n\n✨ *Краткое содержание:*\n{summary}"
        if len(response) > 4096:
            for i in range(0, len(response), 4096):
                await update.message.reply_text(response[i:i+4096], parse_mode='Markdown')
        else:
            await update.message.reply_text(response, parse_mode='Markdown')
    else:
        await update.message.reply_text("👋 Отправь ссылку на статью или YouTube видео.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🤖 Бот запущен и готов к работе (экстрактивный метод).")
    app.run_polling()

if __name__ == '__main__':
    main()
