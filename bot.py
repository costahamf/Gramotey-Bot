import os
import re
import logging
from bs4 import BeautifulSoup
from readability import Document
from youtube_transcript_api import YouTubeTranscriptApi
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from transformers import pipeline

# --- НАСТРОЙКИ ---
# Вставь сюда токен твоего бота, полученный от BotFather
BOT_TOKEN = "7962442088:AAE_KLiwfH5QRiGiCuUs1gz0Wg8ShcK4deI"

# Папка для хранения данных (убедись, что она существует)
DATA_DIR = os.getenv('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)

# Включаем логирование, чтобы видеть, что происходит
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


MODEL_NAME = None   
logger.info(f"Загрузка модели {MODEL_NAME}... Это может занять несколько минут при первом запуске.")
try:
    summarizer = pipeline("summarization", model=MODEL_NAME, device=-1) # device=-1 говорит использовать CPU
    logger.info("Модель успешно загружена.")
except Exception as e:
    logger.error(f"Не удалось загрузить основную модель: {e}")
    # Если модель не загрузилась (например, из-за нехватки памяти), используем резервный вариант на основе частотности слов.
    # Это более простой, но надежный метод, который не требует больших ресурсов.
    logger.warning("Используем резервный метод суммаризации (на основе частотности слов).")
    summarizer = None

# --- СЛУЖЕБНЫЕ ФУНКЦИИ ---

def get_youtube_transcript(url):
    """Извлекает субтитры из YouTube видео."""
    # Извлекаем ID видео из ссылки
    video_id_match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})(?:[?&]|$)', url)
    video_id = video_id_match.group(1) if video_id_match else None

    if not video_id:
        return None, "❌ Не удалось извлечь ID видео из ссылки."

    try:
        # Пытаемся получить субтитры на русском или английском языке
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        # Сначала пробуем найти русские субтитры
        try:
            transcript = transcript_list.find_transcript(['ru'])
        except:
            try:
                # Если русских нет, ищем английские
                transcript = transcript_list.find_transcript(['en'])
            except:
                # Если нет ни русских, ни английских, берем любые доступные
                transcript = transcript_list.find_transcript(transcript_list._manually_created_transcripts.keys())

        # Извлекаем текст
        if transcript:
            transcript_data = transcript.fetch()
            full_text = " ".join([entry['text'] for entry in transcript_data])
            return full_text, None
        else:
            return None, "❌ Не удалось найти субтитры для этого видео."
    except Exception as e:
        logger.error(f"Ошибка при получении субтитров: {e}")
        return None, f"❌ Ошибка при получении субтитров: {str(e)}"

def extract_article_text(url):
    """Извлекает заголовок и основной текст из статьи по URL."""
    try:
        # Загружаем страницу
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Используем readability для извлечения основного содержимого
        doc = Document(response.text)
        title = doc.title()
        # Извлекаем основной HTML-контент
        content_html = doc.summary()

        # Очищаем HTML от тегов, оставляя только текст
        soup = BeautifulSoup(content_html, 'lxml')
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text()
        # Очищаем текст от лишних пробелов и пустых строк
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)

        return title, text, None
    except requests.exceptions.RequestException as e:
        return None, None, f"❌ Ошибка при загрузке страницы: {str(e)}"
    except Exception as e:
        logger.error(f"Ошибка при обработке статьи: {e}")
        return None, None, f"❌ Ошибка при обработке статьи: {str(e)}"

def extractive_summarization(text, num_sentences=5):
    """
    Резервный метод: выделяет наиболее важные предложения на основе частотности слов.
    Используется, если модель суммаризации не загрузилась или текст слишком длинный.
    """
    import nltk
    from nltk.corpus import stopwords
    from nltk.tokenize import sent_tokenize, word_tokenize
    import string
    from collections import Counter

    # Загружаем ресурсы NLTK, если они еще не загружены
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        nltk.download('punkt', quiet=True)
    try:
        nltk.data.find('corpora/stopwords')
    except LookupError:
        nltk.download('stopwords', quiet=True)

    # Токенизируем предложения
    sentences = sent_tokenize(text)
    if len(sentences) <= num_sentences:
        return text

    # Токенизируем слова и убираем стоп-слова и пунктуацию
    stop_words = set(stopwords.words('russian') + stopwords.words('english') + list(string.punctuation))
    word_frequencies = Counter()
    for word in word_tokenize(text.lower()):
        if word not in stop_words:
            word_frequencies[word] += 1

    # Нормализуем частоты
    max_freq = max(word_frequencies.values())
    for word in word_frequencies:
        word_frequencies[word] /= max_freq

    # Оцениваем каждое предложение по сумме частот входящих в него слов
    sentence_scores = {}
    for sent in sentences:
        for word in word_tokenize(sent.lower()):
            if word in word_frequencies:
                if len(sent.split(' ')) < 30:  # Игнорируем слишком короткие предложения
                    sentence_scores[sent] = sentence_scores.get(sent, 0) + word_frequencies[word]

    # Берем топ-N предложений
    import heapq
    summary_sentences = heapq.nlargest(num_sentences, sentence_scores, key=sentence_scores.get)
    summary = ' '.join(summary_sentences)
    return summary

def summarize_text(text):
    """Основная функция для суммаризации текста с использованием модели Hugging Face."""
    if not text or len(text.strip()) == 0:
        return "❌ Не удалось извлечь текст для суммаризации."

    # Ограничиваем длину текста, чтобы не перегружать модель
    # Большинство моделей имеют ограничение на количество токенов (около 1024)
    # Обрезаем текст до 3000 символов (примерно 500-600 слов)
    max_chars = 3000
    if len(text) > max_chars:
        # Обрезаем текст, стараясь не разрывать предложения
        truncated = text[:max_chars]
        last_period = truncated.rfind('.')
        if last_period > max_chars // 2:
            text = truncated[:last_period+1]
        else:
            text = truncated

    # Если основная модель не загружена, используем резервный метод
    if summarizer is None:
        return extractive_summarization(text)

    try:
        # Выполняем суммаризацию
        summary_result = summarizer(text, max_length=150, min_length=30, do_sample=False, truncation=True)

        # Извлекаем сгенерированный текст
        if isinstance(summary_result, list) and len(summary_result) > 0:
            return summary_result[0]['summary_text']
        else:
            return "❌ Не удалось сгенерировать краткое содержание."
    except Exception as e:
        logger.error(f"Ошибка при суммаризации: {e}")
        # Если модель упала, пробуем резервный метод
        return extractive_summarization(text)

# --- ОБРАБОТЧИКИ КОМАНД БОТА ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    await update.message.reply_text(
        "🤖 Привет! Я бот для краткого пересказа статей и видео.\n\n"
        "📌 **Как я работаю:**\n"
        "1. Отправь мне ссылку на статью или YouTube видео.\n"
        "2. Я извлеку основной текст и обработаю его.\n"
        "3. Ты получишь краткое содержание (3-5 предложений).\n\n"
        "⚡️ _Всё работает локально, без передачи данных третьим лицам._",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help."""
    await update.message.reply_text(
        "📖 **Инструкция по использованию:**\n\n"
        "1. Скопируй ссылку на статью или YouTube видео.\n"
        "2. Отправь ссылку в этот чат.\n"
        "3. Дождись обработки (это может занять 10-30 секунд).\n"
        "4. Получи краткий пересказ.\n\n"
        "🛠 Доступные команды:\n"
        "/start - Приветственное сообщение\n"
        "/help - Показать эту справку",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все текстовые сообщения, проверяя, содержит ли ссылку."""
    user_message = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Проверяем, содержит ли сообщение ссылку на YouTube или обычную статью
    is_youtube = 'youtube.com/watch' in user_message or 'youtu.be/' in user_message
    is_article = user_message.startswith('http') and not is_youtube

    if is_youtube:
        await update.message.reply_text("🎬 Это YouTube видео. Начинаю обработку...")
        # Получаем субтитры
        transcript, error = get_youtube_transcript(user_message)
        if error:
            await update.message.reply_text(error)
            return
        if not transcript:
            await update.message.reply_text("❌ Не удалось извлечь субтитры для этого видео.")
            return

        # Если субтитров слишком много, показываем прогресс
        if len(transcript) > 2000:
            progress_msg = await update.message.reply_text("📝 Субтитры получены. Генерирую краткое содержание...")
        else:
            progress_msg = None

        # Генерируем суммаризацию
        summary = summarize_text(transcript)

        if progress_msg:
            await progress_msg.delete()
        await update.message.reply_text(f"🎬 *Краткое содержание видео:*\n\n{summary}", parse_mode='Markdown')

    elif is_article:
        await update.message.reply_text("📄 Это ссылка на статью. Начинаю обработку...")

        # Извлекаем текст статьи
        title, text, error = extract_article_text(user_message)
        if error:
            await update.message.reply_text(error)
            return
        if not text:
            await update.message.reply_text("❌ Не удалось извлечь текст из статьи.")
            return

        # Если текст длинный, показываем прогресс
        if len(text) > 2000:
            progress_msg = await update.message.reply_text("📝 Текст получен. Генерирую краткое содержание...")
        else:
            progress_msg = None

        # Генерируем суммаризацию
        summary = summarize_text(text)

        if progress_msg:
            await progress_msg.delete()

        # Отправляем результат
        response_text = f"📄 *{title}*\n\n✨ *Краткое содержание:*\n{summary}"
        if len(response_text) > 4096:
            # Разбиваем на части, если сообщение слишком длинное
            for i in range(0, len(response_text), 4096):
                await update.message.reply_text(response_text[i:i+4096], parse_mode='Markdown')
        else:
            await update.message.reply_text(response_text, parse_mode='Markdown')
    else:
        # Если сообщение не содержит ссылку, отправляем подсказку
        await update.message.reply_text("👋 Отправь мне ссылку на статью или YouTube видео, и я составлю краткий пересказ.")

# --- ЗАПУСК БОТА ---
def main():
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()

    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))

    # Регистрируем обработчик текстовых сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем бота
    logger.info("🤖 Бот запущен и готов к работе...")
    app.run_polling()

if __name__ == '__main__':
    main()
