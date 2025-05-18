import os
import logging
import random
import requests
import asyncio
from datetime import datetime
from typing import Dict, Optional, Tuple, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import feedparser

# Настройка логов
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

class ReliableNewsBot:
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.channel_id = os.getenv('TELEGRAM_CHANNEL_ID')
        
        if not self.bot_token:
            raise ValueError("Токен бота не указан в .env файле")
        
        # Настройки категорий с резервными RSS
        self.categories = {
            'tech': {
                'name': '💻 Технологии',
                'emoji': '💻',
                'rss': [
                    'https://habr.com/ru/rss/hub/python/',
                    'https://3dnews.ru/bitrix/rss.php',
                    'https://www.ixbt.com/export/news.rss',
                    'https://vc.ru/rss'
                ],
                'image_keywords': ['технологии', 'компьютер', 'робот'],
                'fallback_images': [
                    'https://images.unsplash.com/photo-1517430816045-df4b7de11d1d',
                    'https://images.unsplash.com/photo-1558494949-ef010cbdcc31'
                ],
                'fallback_articles': [
                    {'title': 'Новые технологии в IT', 'summary': 'Современные технологии развиваются быстрыми темпами...'},
                    {'title': 'Искусственный интеллект', 'summary': 'ИИ меняет наш подход к решению задач...'}
                ]
            },
            'politics': {
                'name': '🏛 Политика',
                'emoji': '🏛',
                'rss': [
                    'https://lenta.ru/rss/news',
                    'https://www.kommersant.ru/RSS/news.xml',
                    'https://ria.ru/export/rss2/politics/index.xml',
                    'https://tass.ru/rss/v2.xml'
                ],
                'image_keywords': ['политика', 'кремль', 'правительство'],
                'fallback_images': [
                    'https://images.unsplash.com/photo-1562601579-599dec564e06',
                    'https://images.unsplash.com/photo-1580130732478-4e339fb33746'
                ],
                'fallback_articles': [
                    {'title': 'Политические новости', 'summary': 'Важные политические события происходят...'},
                    {'title': 'Международные отношения', 'summary': 'Страны обсуждают новые соглашения...'}
                ]
            }
        }
        
        # Настройка сессии requests
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0'})
        self.current_article = None

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Главное меню с кнопками"""
        buttons = [
            [KeyboardButton("📰 Случайная новость")],
            [KeyboardButton("💻 Технологии"), KeyboardButton("🏛 Политика")],
            [KeyboardButton("ℹ️ Помощь")]
        ]
        
        reply_markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
        
        await self.send_with_retry(
            update.message.reply_text,
            text="📡 *Добро пожаловать в Reliable News Bot!*\n\nЯ всегда найду для вас свежие новости!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def send_with_retry(self, method, *args, max_retries=3, **kwargs):
        """Повторная попытка отправки при ошибках"""
        for attempt in range(max_retries):
            try:
                return await method(*args, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(1)
                continue

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда помощи"""
        help_text = (
            "🆘 *Доступные команды:*\n\n"
            "📰 Случайная новость - случайная новость из любой категории\n"
            "💻 Технологии - новости IT и технологий\n"
            "🏛 Политика - политические новости\n\n"
            "Бот автоматически подбирает изображения к новостям"
        )
        await self.send_with_retry(
            update.message.reply_text,
            text=help_text,
            parse_mode='Markdown'
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        text = update.message.text
        
        if text == "📰 Случайная новость":
            await self.random_news(update, context)
        elif text == "💻 Технологии":
            await self.process_category(update, context, 'tech')
        elif text == "🏛 Политика":
            await self.process_category(update, context, 'politics')
        elif text == "ℹ️ Помощь":
            await self.help_command(update, context)
        else:
            await self.send_with_retry(
                update.message.reply_text,
                text="Используйте кнопки меню для навигации"
            )

    async def random_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Случайная новость из любой категории"""
        category_id = random.choice(list(self.categories.keys()))
        await self.process_category(update, context, category_id)

    async def process_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: str):
        """Обработка выбранной категории с гарантированным результатом"""
        try:
            # Пытаемся получить свежую новость
            success = await self.try_get_fresh_article(category_id)
            
            if not success:
                # Если не получилось, используем резервную статью
                await self.use_fallback_article(category_id)
            
            # Получаем изображение (гарантированно будет из fallback)
            image_url = await self.get_image_with_fallback()
            
            # Формируем сообщение
            category = self.categories[category_id]
            post_text = (
                f"{category['emoji']} *{category['name']}*\n\n"
                f"📌 *{self.current_article['title']}*\n\n"
                f"{self.current_article['summary']}"
            )
            
            # Кнопки действий
            keyboard = [
                [
                    InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
                    InlineKeyboardButton("🔄 Еще новость", callback_data=f"category_{category_id}")
                ],
                [
                    InlineKeyboardButton("💻 Технологии", callback_data="category_tech"),
                    InlineKeyboardButton("🏛 Политика", callback_data="category_politics")
                ]
            ]
            
            # Отправка сообщения с повторными попытками
            await self.send_news_message(
                update,
                image_url=image_url,
                caption=post_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            logger.error(f"Критическая ошибка: {str(e)}")
            await self.send_with_retry(
                update.message.reply_text,
                text="📌 *Технологии*\n\n🔧 Бот временно использует резервные новости. Попробуйте позже!",
                parse_mode='Markdown'
            )
            await self.use_fallback_article('tech')
            await self.process_category(update, context, 'tech')

    async def try_get_fresh_article(self, category_id: str) -> bool:
        """Попытка получить свежую статью из RSS"""
        category = self.categories.get(category_id)
        if not category:
            return False
        
        for rss_url in category['rss']:
            try:
                response = await asyncio.to_thread(
                    self.session.get, rss_url, timeout=10
                )
                response.raise_for_status()
                
                feed = feedparser.parse(response.content)
                if not feed.entries:
                    continue
                
                entry = random.choice(feed.entries[:10])  # Берем из свежих
                
                # Очистка HTML
                summary = getattr(entry, 'summary', entry.get('description', ''))
                soup = BeautifulSoup(summary, 'html.parser')
                clean_text = soup.get_text(separator=' ', strip=True)
                
                # Умное сокращение
                clean_text = clean_text[:250] + '...' if len(clean_text) > 250 else clean_text
                
                self.current_article = {
                    'title': entry.title,
                    'link': entry.link,
                    'summary': clean_text,
                    'category': category_id,
                    'is_fallback': False
                }
                return True
                
            except Exception as e:
                logger.warning(f"Ошибка RSS {rss_url}: {str(e)}")
                continue
        
        return False

    async def use_fallback_article(self, category_id: str):
        """Использование резервной статьи"""
        category = self.categories[category_id]
        self.current_article = random.choice(category['fallback_articles'])
        self.current_article.update({
            'category': category_id,
            'is_fallback': True
        })

    async def get_image_with_fallback(self) -> str:
        """Получение изображения с резервными вариантами"""
        if not self.current_article:
            return random.choice(list(self.categories.values()))['fallback_images'][0]
        
        category = self.categories[self.current_article['category']]
        
        # 1. Пробуем извлечь из статьи (если не fallback)
        if not self.current_article.get('is_fallback', True):
            try:
                img = await self.extract_image_from_article()
                if img:
                    return img
            except Exception as e:
                logger.warning(f"Ошибка извлечения изображения: {str(e)}")
        
        # 2. Пробуем Unsplash
        try:
            keyword = random.choice(category['image_keywords'])
            url = f"https://source.unsplash.com/800x600/?{keyword}"
            
            resp = await asyncio.to_thread(self.session.head, url, timeout=5)
            if resp.status_code == 200:
                return url
        except Exception as e:
            logger.warning(f"Ошибка Unsplash: {str(e)}")
        
        # 3. Fallback изображение
        return random.choice(category['fallback_images'])

    async def extract_image_from_article(self) -> Optional[str]:
        """Извлечение изображения из статьи"""
        if not self.current_article or not self.current_article.get('link'):
            return None
            
        try:
            response = await asyncio.to_thread(
                self.session.get, self.current_article['link'], timeout=10
            )
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Ищем OpenGraph или Twitter изображение
            for meta in soup.find_all('meta'):
                if meta.get('property') in ['og:image', 'twitter:image']:
                    if meta.get('content', '').startswith('http'):
                        return meta['content']
            
            # Ищем первое подходящее изображение в статье
            for img in soup.find_all('img', src=True):
                if img['src'].startswith('http') and any(ext in img['src'] for ext in ['.jpg', '.jpeg', '.png']):
                    return img['src']
        except Exception as e:
            logger.warning(f"Ошибка парсинга статьи: {str(e)}")
            return None

    async def send_news_message(self, update, image_url: str, caption: str, reply_markup=None):
        """Безопасная отправка новости"""
        try:
            if isinstance(update, Update):
                # Новое сообщение
                await self.send_with_retry(
                    update.message.reply_photo,
                    photo=image_url,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                # Редактирование существующего
                await self.send_with_retry(
                    update.edit_message_media,
                    media=InputMediaPhoto(
                        media=image_url,
                        caption=caption,
                        parse_mode='Markdown'
                    ),
                    reply_markup=reply_markup
                )
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {str(e)}")
            # Последняя попытка - только текст
            await self.send_with_retry(
                update.message.reply_text if isinstance(update, Update) else update.edit_message_text,
                text=caption,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback-запросов"""
        query = update.callback_query
        await query.answer()
        
        try:
            if query.data.startswith("category_"):
                category_id = query.data.split('_')[1]
                await self.process_category(query, context, category_id)
            elif query.data == "publish":
                await self.publish_article(query, context)
        except Exception as e:
            logger.error(f"Ошибка callback: {str(e)}")
            await self.send_with_retry(
                query.edit_message_text,
                text="⚠️ Произошла ошибка. Попробуйте еще раз."
            )

    async def publish_article(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Публикация статьи в канал"""
        if not self.current_article:
            await self.send_with_retry(
                query.edit_message_text,
                text="⚠️ Нет данных для публикации"
            )
            return
        
        try:
            category = self.categories[self.current_article['category']]
            image_url = await self.get_image_with_fallback()
            
            post_text = (
                f"{category['emoji']} *{category['name']}*\n\n"
                f"📌 *{self.current_article['title']}*\n\n"
                f"{self.current_article['summary']}"
            )
            
            await self.send_with_retry(
                context.bot.send_photo,
                chat_id=self.channel_id,
                photo=image_url,
                caption=post_text,
                parse_mode='Markdown'
            )
            
            await self.send_with_retry(
                query.edit_message_text,
                text=f"✅ *Новость опубликована в канале!*\n\nКатегория: {category['name']}",
                parse_mode='Markdown'
            )
            
            self.current_article = None
        except Exception as e:
            logger.error(f"Ошибка публикации: {str(e)}")
            await self.send_with_retry(
                query.edit_message_text,
                text="⚠️ Не удалось опубликовать новость. Попробуйте позже."
            )

    def run(self):
        """Запуск бота с обработчиками"""
        app = Application.builder().token(self.bot_token).build()
        
        # Команды
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("random", self.random_news))
        
        # Обработчики сообщений
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # Обработчики callback
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        
        logger.info("Бот запущен и готов к работе")
        app.run_polling()

if __name__ == "__main__":
    try:
        bot = ReliableNewsBot()
        bot.run()
    except Exception as e:
        logger.critical(f"Ошибка запуска бота: {str(e)}")