"""
Бот мониторинга финансовых новостей
Автоматически ищет новости через GNews API и отправляет в Telegram на украинском языке
"""
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
import time
import logging
import sys
import os


# ==================== КОНФИГУРАЦИЯ ====================
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "60"))
CHECK_HOURS = int(os.getenv("CHECK_HOURS", "1"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('news_bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


# ==================== КЛАСС ДЛЯ ПЕРЕВОДА ====================
class Translator:
    """Класс для перевода текстов на украинский язык"""

    def __init__(self):
        self.target_lang = "uk"

    def translate_to_ukrainian(self, text: str, source_lang: str = "auto") -> str:
        """Перевести текст на украинский язык"""
        if source_lang == "uk" or not text or text == "N/A":
            return text

        try:
            base_url = "https://translate.googleapis.com/translate_a/single"
            params = {
                "client": "gtx",
                "sl": source_lang,
                "tl": self.target_lang,
                "dt": "t",
                "q": text
            }
            url = f"{base_url}?{urllib.parse.urlencode(params)}"
            headers = {"User-Agent": "Mozilla/5.0"}
            request = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
                if result and len(result) > 0 and result[0]:
                    translated_parts = [part[0] for part in result[0] if part[0]]
                    return "".join(translated_parts)
                return text
        except Exception as e:
            logger.error(f"Ошибка при переводе: {str(e)}")
            return text

    def translate_article(self, article: dict) -> dict:
        """Перевести заголовок и описание статьи"""
        translated = article.copy()
        source_lang = article.get("lang", "auto")

        if article.get("title") and article["title"] != "N/A":
            translated["title_uk"] = self.translate_to_ukrainian(article["title"], source_lang)
        else:
            translated["title_uk"] = article.get("title", "")

        if article.get("description") and article["description"] != "N/A":
            translated["description_uk"] = self.translate_to_ukrainian(article["description"], source_lang)
        else:
            translated["description_uk"] = article.get("description", "")

        return translated


# ==================== КЛАСС ДЛЯ ПОЛУЧЕНИЯ НОВОСТЕЙ ====================
class NewsFetcher:
    """Класс для получения новостей из GNews API"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://gnews.io/api/v4"
        self.processed_urls = set()

        # Запросы по темам
        self.queries = [
            {"query": "bankruptcy", "lang": "en", "theme": "Банкрутство"},
            {"query": "банкрутство", "lang": "uk", "theme": "Банкрутство"},
            {"query": "банкротство", "lang": "ru", "theme": "Банкрутство"},
            {"query": "banks", "lang": "en", "theme": "Банки"},
            {"query": "банки", "lang": "uk", "theme": "Банки"},
            {"query": "банки", "lang": "ru", "theme": "Банки"},
            {"query": "credits", "lang": "en", "theme": "Кредити"},
            {"query": "loans", "lang": "en", "theme": "Кредити"},
            {"query": "кредити", "lang": "uk", "theme": "Кредити"},
            {"query": "кредиты", "lang": "ru", "theme": "Кредити"},
            {"query": "legislation", "lang": "en", "theme": "Законодавство"},
            {"query": "law", "lang": "en", "theme": "Законодавство"},
            {"query": "законодавство", "lang": "uk", "theme": "Законодавство"},
            {"query": "законодательство", "lang": "ru", "theme": "Законодавство"},
        ]

    def search_news(self, query: str, lang: str, from_date: str = None, to_date: str = None, max_results: int = 10):
        """Поиск новостей по запросу"""
        url = f"{self.base_url}/search?q={urllib.parse.quote(query)}&lang={lang}&max={max_results}&apikey={self.api_key}"
        if from_date:
            url += f"&from={from_date}"
        if to_date:
            url += f"&to={to_date}"

        try:
            start_time = time.time()
            with urllib.request.urlopen(url) as response:
                data = json.loads(response.read().decode("utf-8"))
                elapsed = time.time() - start_time
                logger.info(f"Запрос [{lang}] {query}: найдено {data.get('totalArticles', 0)}")
                return data, elapsed, None
        except urllib.error.HTTPError as e:
            elapsed = time.time() - start_time
            error_msg = f"HTTP {e.code}: {e.reason}"
            try:
                error_body = json.loads(e.read().decode('utf-8'))
                if 'errors' in error_body:
                    error_msg += f" - {error_body['errors']}"
            except:
                pass
            logger.error(f"Ошибка HTTP при запросе {query}: {error_msg}")
            return None, elapsed, error_msg
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Ошибка при запросе {query}: {str(e)}")
            return None, elapsed, str(e)

    def get_recent_news(self, hours: int = 1) -> list:
        """Получить новости за последние N часов"""
        now = datetime.now()
        from_date = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_date = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        logger.info(f"Поиск новостей с {from_date} до {to_date}")
        all_new_articles = []

        for query_config in self.queries:
            query = query_config["query"]
            lang = query_config["lang"]
            theme = query_config["theme"]

            data, elapsed, error = self.search_news(query, lang, from_date=from_date, to_date=to_date, max_results=10)

            if data and data.get("articles"):
                for article in data.get("articles", []):
                    article_url = article.get("url", "")
                    if article_url in self.processed_urls:
                        continue

                    self.processed_urls.add(article_url)
                    article_info = {
                        "theme": theme,
                        "query": query,
                        "lang": lang,
                        "title": article.get("title", "N/A"),
                        "description": article.get("description", "N/A"),
                        "url": article_url,
                        "source": article.get("source", {}).get("name", "N/A"),
                        "publishedAt": article.get("publishedAt", "N/A"),
                        "image": article.get("image", None)
                    }
                    all_new_articles.append(article_info)

            time.sleep(0.5)

        logger.info(f"Найдено новых статей: {len(all_new_articles)}")
        return all_new_articles

    def clear_processed_cache(self):
        """Очистить кэш обработанных URL"""
        old_count = len(self.processed_urls)
        self.processed_urls.clear()
        logger.info(f"Очищен кэш обработанных URL: {old_count} записей")


# ==================== КЛАСС ДЛЯ TELEGRAM ====================
class TelegramBot:
    """Класс для отправки сообщений в Telegram"""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Отправить текстовое сообщение"""
        url = f"{self.base_url}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False
        }

        try:
            encoded_data = urllib.parse.urlencode(data).encode('utf-8')
            request = urllib.request.Request(url, data=encoded_data)
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                if result.get('ok'):
                    logger.info("Сообщение отправлено в Telegram")
                    return True
                else:
                    logger.error(f"Ошибка Telegram API: {result}")
                    return False
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {str(e)}")
            return False

    def format_article_message(self, article: dict) -> str:
        """Форматировать статью для Telegram"""
        theme = article.get("theme", "Новини")
        title_uk = article.get("title_uk", article.get("title", "Без заголовка"))
        description_uk = article.get("description_uk", article.get("description", ""))
        source = article.get("source", "N/A")
        url = article.get("url", "")
        published_at = article.get("publishedAt", "N/A")

        if published_at != "N/A":
            try:
                dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                published_at = dt.strftime("%d.%m.%Y %H:%M")
            except:
                pass

        message = f"""<b>📰 {theme}</b>

<b>{title_uk}</b>

{description_uk}

<b>Джерело:</b> {source}
<b>Дата:</b> {published_at}

<a href="{url}">📎 Читати оригінал статті</a>"""

        return message.strip()

    def send_article(self, article: dict) -> bool:
        """Отправить статью в Telegram"""
        message = self.format_article_message(article)
        return self.send_message(message)

    def send_articles_batch(self, articles: list) -> int:
        """Отправить пакет статей"""
        sent_count = 0
        if not articles:
            logger.info("Нет новых статей для отправки")
            return 0

        header = f"<b>🔔 Знайдено {len(articles)} нових статей</b>"
        self.send_message(header)

        for article in articles:
            if self.send_article(article):
                sent_count += 1
            time.sleep(0.5)

        summary = f"<b>✅ Відправлено {sent_count} із {len(articles)} статей</b>"
        self.send_message(summary)
        logger.info(f"Отправлено {sent_count}/{len(articles)} статей")
        return sent_count

    def send_error(self, error_message: str) -> bool:
        """Отправить сообщение об ошибке"""
        message = f"<b>⚠️ Помилка</b>\n\n{error_message}"
        return self.send_message(message)

    def test_connection(self) -> bool:
        """Проверить соединение с Telegram API"""
        url = f"{self.base_url}/getMe"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                if result.get('ok'):
                    bot_info = result.get('result', {})
                    logger.info(f"Telegram бот подключен: @{bot_info.get('username')}")
                    return True
                else:
                    logger.error(f"Ошибка подключения к Telegram: {result}")
                    return False
        except Exception as e:
            logger.error(f"Ошибка при проверке подключения: {str(e)}")
            return False


# ==================== ОСНОВНОЙ КЛАСС БОТА ====================
class NewsMonitorBot:
    """Основной класс бота для мониторинга новостей"""

    def __init__(self, gnews_api_key: str, telegram_bot_token: str, telegram_chat_id: str):
        self.news_fetcher = NewsFetcher(gnews_api_key)
        self.translator = Translator()
        self.telegram_bot = TelegramBot(telegram_bot_token, telegram_chat_id)
        logger.info("NewsMonitorBot инициализирован")

    def check_and_send_news(self, hours: int = 1) -> int:
        """Проверить и отправить новости"""
        try:
            logger.info(f"Проверка новостей за последние {hours} час(ов)")
            articles = self.news_fetcher.get_recent_news(hours=hours)

            if not articles:
                logger.info("Новых статей не найдено")
                return 0

            logger.info(f"Найдено {len(articles)} новых статей. Перевод...")
            translated_articles = []

            for article in articles:
                try:
                    translated = self.translator.translate_article(article)
                    translated_articles.append(translated)
                except Exception as e:
                    logger.error(f"Ошибка при переводе: {str(e)}")
                    translated_articles.append(article)

            logger.info(f"Переведено {len(translated_articles)} статей. Отправка...")
            sent_count = self.telegram_bot.send_articles_batch(translated_articles)
            logger.info(f"Отправлено {sent_count} статей")
            return sent_count

        except Exception as e:
            error_msg = f"Ошибка при проверке новостей: {str(e)}"
            logger.error(error_msg)
            try:
                self.telegram_bot.send_error(error_msg)
            except:
                pass
            return 0

    def run_continuous(self, interval_minutes: int = 60, check_hours: int = 1):
        """Непрерывная работа бота"""
        logger.info(f"Запуск в непрерывном режиме")
        logger.info(f"Интервал: {interval_minutes} мин, Поиск за: {check_hours} час(ов)")

        if not self.telegram_bot.test_connection():
            logger.error("Не удалось подключиться к Telegram!")
            return

        self.telegram_bot.send_message(
            f"<b>🤖 Бот моніторингу новин запущено!</b>\n\n"
            f"⏱ Інтервал перевірки: {interval_minutes} хв\n"
            f"📅 Пошук новин за: {check_hours} год"
        )

        iteration = 0
        interval_seconds = interval_minutes * 60

        try:
            while True:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"Итерация #{iteration} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*80}")

                try:
                    sent_count = self.check_and_send_news(hours=check_hours)
                    logger.info(f"Отправлено статей: {sent_count}")
                except Exception as e:
                    logger.error(f"Ошибка: {str(e)}")

                if iteration % (24 * (60 // interval_minutes)) == 0:
                    self.news_fetcher.clear_processed_cache()

                logger.info(f"Следующая проверка через {interval_minutes} минут...")
                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("\n\nОстановка бота...")
            self.telegram_bot.send_message("<b>🛑 Бот зупинено</b>")
        except Exception as e:
            error_msg = f"Критическая ошибка: {str(e)}"
            logger.error(error_msg)
            try:
                self.telegram_bot.send_error(error_msg)
            except:
                pass


# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================
def main():
    """Главная функция запуска"""
    print("="*80)
    print(" "*20 + "БОТ МОНИТОРИНГА ФИНАНСОВЫХ НОВОСТЕЙ")
    print("="*80)
    print(f"\nВремя запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Проверка конфигурации
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("❌ TELEGRAM_BOT_TOKEN не настроен!")
        logger.error("Установите переменную окружения TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "YOUR_TELEGRAM_CHAT_ID":
        logger.error("❌ TELEGRAM_CHAT_ID не настроен!")
        logger.error("Установите переменную окружения TELEGRAM_CHAT_ID")
        sys.exit(1)

    logger.info("✅ Конфигурация валидна")
    logger.info(f"GNews API Key: {GNEWS_API_KEY[:10]}...")
    logger.info(f"Telegram Bot Token: {TELEGRAM_BOT_TOKEN[:10]}...")
    logger.info(f"Telegram Chat ID: {TELEGRAM_CHAT_ID}")

    try:
        bot = NewsMonitorBot(
            gnews_api_key=GNEWS_API_KEY,
            telegram_bot_token=TELEGRAM_BOT_TOKEN,
            telegram_chat_id=TELEGRAM_CHAT_ID
        )
        logger.info("✅ Бот инициализирован")

        # Запуск в непрерывном режиме
        bot.run_continuous(
            interval_minutes=CHECK_INTERVAL_MINUTES,
            check_hours=CHECK_HOURS
        )

    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
