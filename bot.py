import re
import requests
from bs4 import BeautifulSoup
from readability import Document
from youtube_transcript_api import YouTubeTranscriptApi
from openai import OpenAI

# --- Функция для определения типа ссылки ---
def detect_link_type(url):
    if 'youtube.com/watch' in url or 'youtu.be/' in url:
        return 'youtube'
    else:
        return 'article'

# --- Функция для извлечения текста из статьи ---
def extract_article_text(url):
    response = requests.get(url)
    doc = Document(response.content)
    return doc.title(), doc.summary()  # Возвращаем заголовок и HTML с текстом

# --- Функция для извлечения текста из YouTube ---
def extract_youtube_text(url):
    # Извлекаем ID видео из ссылки (пример: "dQw4w9WgXcQ")
    video_id_match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})(?:[?&]|$)', url)
    video_id = video_id_match.group(1) if video_id_match else None

    if video_id:
        transcript_list = YouTubeTranscriptApi().fetch(video_id, languages=['ru', 'en'])
        # Склеиваем все строки субтитров в один текст
        return " ".join([snippet.text for snippet in transcript_list])
    return None

# --- Функция для суммаризации через OpenAI ---
def summarize_text(text, max_length=500):
    # Ограничиваем длину текста, чтобы не превысить лимит токенов модели
    if len(text) > 12000:
        text = text[:12000] + "..."

    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "Ты — полезный ассистент, который кратко пересказывает тексты."},
            {"role": "user", "content": f"Кратко перескажи этот текст, выдели 3-5 главных мыслей:\n\n{text}"}
        ],
        max_tokens=max_length,
        temperature=0.5
    )
    return response.choices[0].message.content

# --- Пример обработки сообщения в боте ---
async def handle_link(update, context):
    url = update.message.text
    link_type = detect_link_type(url)
    
    await update.message.reply_text("🔍 Получил ссылку! Начинаю анализ...")

    try:
        if link_type == 'article':
            title, content_html = extract_article_text(url)
            # Очищаем HTML от тегов, оставляя только текст
            soup = BeautifulSoup(content_html, 'lxml')
            text = soup.get_text()
            summary = summarize_text(text)
            await update.message.reply_text(f"📄 *{title}*\n\n✨ *Краткое содержание:*\n{summary}", parse_mode='Markdown')
            
        elif link_type == 'youtube':
            transcript = extract_youtube_text(url)
            if transcript:
                summary = summarize_text(transcript)
                await update.message.reply_text(f"🎬 *Краткое содержание видео:*\n\n{summary}", parse_mode='Markdown')
            else:
                await update.message.reply_text("❌ Не удалось найти субтитры для этого видео. Возможно, они отключены.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Произошла ошибка при обработке: {str(e)}")
