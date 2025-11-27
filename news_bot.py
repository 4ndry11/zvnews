"""
Бот мониторинга финансовых новостей
Автоматически ищет новости через GNews API и отправляет в Telegram на украинском языке
Любой пользователь может подписаться через /start
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
import threading


# ==================== КОНФИГУРАЦИЯ ====================
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "180"))
CHECK_HOURS = int(os.getenv("CHECK_HOURS", "3"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
SUBSCRIBERS_FILE = "subscribers.json"


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


# ==================== УПРАВЛЕНИЕ ПОДПИСЧИКАМИ ====================
class SubscriberManager:
    """Управление подписчиками бота"""

    def __init__(self, filename: str = SUBSCRIBERS_FILE):
        self.filename = filename
        self.subscribers = self.load_subscribers()

    def load_subscribers(self) -> set:
        """Загрузить список подписчиков из файла"""
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    subscribers = set(data.get('subscribers', []))
                    logger.info(f"Загружено {len(subscribers)} подписчиков")
                    return subscribers
        except Exception as e:
            logger.error(f"Ошибка при загрузке подписчиков: {str(e)}")
        return set()

    def save_subscribers(self):
        """Сохранить список подписчиков в файл"""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump({'subscribers': list(self.subscribers)}, f, ensure_ascii=False, indent=2)
            logger.info(f"Сохранено {len(self.subscribers)} подписчиков")
        except Exception as e:
            logger.error(f"Ошибка при сохранении подписчиков: {str(e)}")

    def add_subscriber(self, chat_id: str) -> bool:
        """Добавить подписчика"""
        if chat_id not in self.subscribers:
            self.subscribers.add(chat_id)
            self.save_subscribers()
            logger.info(f"Новый подписчик: {chat_id}")
            return True
        return False

    def remove_subscriber(self, chat_id: str) -> bool:
        """Удалить подписчика"""
        if chat_id in self.subscribers:
            self.subscribers.remove(chat_id)
            self.save_subscribers()
            logger.info(f"Подписчик удален: {chat_id}")
            return True
        return False

    def get_subscribers(self) -> list:
        """Получить список всех подписчиков"""
        return list(self.subscribers)


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
        self.queries = [
            # === БАНКРОТСТВО ===
            # Английский
            {"query": "bankruptcy", "lang": "en", "theme": "Банкрутство"},
            {"query": "personal bankruptcy", "lang": "en", "theme": "Банкрутство фізичних осіб"},
            {"query": "business bankruptcy", "lang": "en", "theme": "Банкрутство бізнесу"},
            {"query": "corporate bankruptcy", "lang": "en", "theme": "Банкрутство компаній"},
            # Украинский
            {"query": "банкрутство", "lang": "uk", "theme": "Банкрутство"},
            {"query": "банкрутство фізичних осіб", "lang": "uk", "theme": "Банкрутство фізичних осіб"},
            {"query": "банкрутство компанії", "lang": "uk", "theme": "Банкрутство бізнесу"},
            {"query": "банкрутство підприємства", "lang": "uk", "theme": "Банкрутство бізнесу"},
            # Русский
            {"query": "банкротство", "lang": "ru", "theme": "Банкрутство"},
            {"query": "банкротство физических лиц", "lang": "ru", "theme": "Банкрутство фізичних осіб"},
            {"query": "банкротство компании", "lang": "ru", "theme": "Банкрутство бізнесу"},
            # Немецкий
            {"query": "insolvenz", "lang": "de", "theme": "Банкрутство"},
            {"query": "privatinsolvenz", "lang": "de", "theme": "Банкрутство фізичних осіб"},
            {"query": "firmeninsolvenz", "lang": "de", "theme": "Банкрутство бізнесу"},

            # === РЕСТРУКТУРИЗАЦИЯ ДОЛГОВ ===
            # Английский
            {"query": "debt restructuring", "lang": "en", "theme": "Реструктуризація боргів"},
            {"query": "loan restructuring", "lang": "en", "theme": "Реструктуризація кредитів"},
            # Украинский
            {"query": "реструктуризація боргів", "lang": "uk", "theme": "Реструктуризація боргів"},
            {"query": "реструктуризація кредиту", "lang": "uk", "theme": "Реструктуризація кредитів"},
            {"query": "реструктуризація заборгованості", "lang": "uk", "theme": "Реструктуризація боргів"},
            # Русский
            {"query": "реструктуризация долгов", "lang": "ru", "theme": "Реструктуризація боргів"},
            {"query": "реструктуризация кредита", "lang": "ru", "theme": "Реструктуризація кредитів"},
            # Немецкий
            {"query": "umschuldung", "lang": "de", "theme": "Реструктуризація боргів"},
            {"query": "schuldenbereinigung", "lang": "de", "theme": "Реструктуризація боргів"},

            # === СУДЕБНЫЕ ПРОЦЕССЫ ===
            # Английский
            {"query": "bankruptcy court", "lang": "en", "theme": "Судові справи"},
            {"query": "insolvency proceedings", "lang": "en", "theme": "Судові справи"},
            {"query": "bankruptcy case", "lang": "en", "theme": "Судові справи"},
            # Украинский
            {"query": "справа про банкрутство", "lang": "uk", "theme": "Судові справи"},
            {"query": "процедура банкрутства", "lang": "uk", "theme": "Судові справи"},
            {"query": "господарський суд банкрутство", "lang": "uk", "theme": "Судові справи"},
            {"query": "арбітражний керуючий", "lang": "uk", "theme": "Судові справи"},
            # Русский
            {"query": "дело о банкротстве", "lang": "ru", "theme": "Судові справи"},
            {"query": "процедура банкротства", "lang": "ru", "theme": "Судові справи"},
            {"query": "арбитражный управляющий", "lang": "ru", "theme": "Судові справи"},
            # Немецкий
            {"query": "insolvenzverfahren", "lang": "de", "theme": "Судові справи"},
            {"query": "insolvenzgericht", "lang": "de", "theme": "Судові справи"},

            # === ПРОБЛЕМЫ С КРЕДИТАМИ ===
            # Английский
            {"query": "consumer debt", "lang": "en", "theme": "Споживчі борги"},
            {"query": "loan default", "lang": "en", "theme": "Прострочені кредити"},
            {"query": "mortgage foreclosure", "lang": "en", "theme": "Іпотечні проблеми"},
            {"query": "credit card debt", "lang": "en", "theme": "Кредитні борги"},
            {"query": "overdue loan", "lang": "en", "theme": "Прострочені кредити"},
            # Украинский
            {"query": "споживчий кредит", "lang": "uk", "theme": "Споживчі борги"},
            {"query": "прострочений кредит", "lang": "uk", "theme": "Прострочені кредити"},
            {"query": "заборгованість по кредиту", "lang": "uk", "theme": "Кредитна заборгованість"},
            {"query": "проблемна іпотека", "lang": "uk", "theme": "Іпотечні проблеми"},
            {"query": "борг по кредиту", "lang": "uk", "theme": "Кредитні борги"},
            # Русский
            {"query": "потребительский кредит", "lang": "ru", "theme": "Споживчі борги"},
            {"query": "просроченный кредит", "lang": "ru", "theme": "Прострочені кредити"},
            {"query": "задолженность по кредиту", "lang": "ru", "theme": "Кредитна заборгованість"},
            {"query": "проблемная ипотека", "lang": "ru", "theme": "Іпотечні проблеми"},
            # Немецкий
            {"query": "verbraucherkredit", "lang": "de", "theme": "Споживчі борги"},
            {"query": "kreditausfall", "lang": "de", "theme": "Прострочені кредити"},
            {"query": "hypothekenschulden", "lang": "de", "theme": "Іпотечні проблеми"},

            # === НЕПЛАТЕЖЕСПОСОБНОСТЬ ===
            # Английский
            {"query": "insolvency", "lang": "en", "theme": "Неплатоспроможність"},
            {"query": "financial distress", "lang": "en", "theme": "Фінансові проблеми"},
            {"query": "unable to pay debts", "lang": "en", "theme": "Неплатоспроможність"},
            # Украинский
            {"query": "неплатоспроможність", "lang": "uk", "theme": "Неплатоспроможність"},
            {"query": "неплатоспроможність боржника", "lang": "uk", "theme": "Неплатоспроможність"},
            {"query": "фінансові труднощі", "lang": "uk", "theme": "Фінансові проблеми"},
            # Русский
            {"query": "неплатежеспособность", "lang": "ru", "theme": "Неплатоспроможність"},
            {"query": "неплатежеспособность должника", "lang": "ru", "theme": "Неплатоспроможність"},
            {"query": "финансовые трудности", "lang": "ru", "theme": "Фінансові проблеми"},
            # Немецкий
            {"query": "zahlungsunfähigkeit", "lang": "de", "theme": "Неплатоспроможність"},
            {"query": "überschuldung", "lang": "de", "theme": "Неплатоспроможність"},

            # === КОЛЛЕКТОРЫ И ВЗЫСКАНИЕ ===
            # Английский
            {"query": "debt collection", "lang": "en", "theme": "Стягнення боргів"},
            {"query": "debt collector", "lang": "en", "theme": "Колектори"},
            {"query": "debt recovery", "lang": "en", "theme": "Стягнення боргів"},
            # Украинский
            {"query": "колекторське агентство", "lang": "uk", "theme": "Колектори"},
            {"query": "стягнення боргів", "lang": "uk", "theme": "Стягнення боргів"},
            {"query": "колектори", "lang": "uk", "theme": "Колектори"},
            # Русский
            {"query": "коллекторское агентство", "lang": "ru", "theme": "Колектори"},
            {"query": "взыскание долгов", "lang": "ru", "theme": "Стягнення боргів"},
            {"query": "коллекторы", "lang": "ru", "theme": "Колектори"},
            # Немецкий
            {"query": "inkasso", "lang": "de", "theme": "Колектори"},
            {"query": "schuldeneintreibung", "lang": "de", "theme": "Стягнення боргів"},

            # === ЛИКВИДАЦИЯ И САНАЦИЯ ===
            # Английский
            {"query": "liquidation", "lang": "en", "theme": "Ліквідація"},
            {"query": "company liquidation", "lang": "en", "theme": "Ліквідація компанії"},
            {"query": "creditor protection", "lang": "en", "theme": "Захист кредиторів"},
            {"query": "debt settlement", "lang": "en", "theme": "Врегулювання боргів"},
            # Украинский
            {"query": "ліквідація підприємства", "lang": "uk", "theme": "Ліквідація"},
            {"query": "санація підприємства", "lang": "uk", "theme": "Санація"},
            {"query": "кредиторська заборгованість", "lang": "uk", "theme": "Борги"},
            {"query": "мирова угода", "lang": "uk", "theme": "Мирова угода"},
            # Русский
            {"query": "ликвидация предприятия", "lang": "ru", "theme": "Ліквідація"},
            {"query": "санация предприятия", "lang": "ru", "theme": "Санація"},
            {"query": "кредиторская задолженность", "lang": "ru", "theme": "Борги"},
            {"query": "мировое соглашение", "lang": "ru", "theme": "Мирова угода"},
            # Немецкий
            {"query": "liquidation unternehmen", "lang": "de", "theme": "Ліквідація"},
            {"query": "gläubigerschutz", "lang": "de", "theme": "Захист кредиторів"},

            # === ФИНАНСОВЫЕ ОБЯЗАТЕЛЬСТВА ===
            # Английский
            {"query": "unpaid debts", "lang": "en", "theme": "Несплачені борги"},
            {"query": "debt burden", "lang": "en", "theme": "Боргове навантаження"},
            {"query": "creditor claims", "lang": "en", "theme": "Вимоги кредиторів"},
            {"query": "payment default", "lang": "en", "theme": "Неплатежі"},
            # Украинский
            {"query": "несплачені борги", "lang": "uk", "theme": "Несплачені борги"},
            {"query": "боргове навантаження", "lang": "uk", "theme": "Боргове навантаження"},
            {"query": "вимоги кредиторів", "lang": "uk", "theme": "Вимоги кредиторів"},
            {"query": "прострочена заборгованість", "lang": "uk", "theme": "Прострочені борги"},
            # Русский
            {"query": "неоплаченные долги", "lang": "ru", "theme": "Несплачені борги"},
            {"query": "долговая нагрузка", "lang": "ru", "theme": "Боргове навантаження"},
            {"query": "требования кредиторов", "lang": "ru", "theme": "Вимоги кредиторів"},
            {"query": "просроченная задолженность", "lang": "ru", "theme": "Прострочені борги"},
            # Немецкий
            {"query": "unbezahlte schulden", "lang": "de", "theme": "Несплачені борги"},
            {"query": "schuldenlast", "lang": "de", "theme": "Боргове навантаження"},

            # === БАНКОВСКИЕ КРЕДИТЫ И ПРОБЛЕМЫ ===
            # Английский
            {"query": "bank loan problems", "lang": "en", "theme": "Проблеми з банківськими кредитами"},
            {"query": "non performing loan", "lang": "en", "theme": "Проблемні кредити"},
            {"query": "loan write off", "lang": "en", "theme": "Списання кредитів"},
            # Украинский
            {"query": "проблемний кредит банку", "lang": "uk", "theme": "Проблемні кредити"},
            {"query": "списання боргу", "lang": "uk", "theme": "Списання боргів"},
            {"query": "реструктуризація іпотеки", "lang": "uk", "theme": "Реструктуризація іпотеки"},
            # Русский
            {"query": "проблемный кредит банка", "lang": "ru", "theme": "Проблемні кредити"},
            {"query": "списание долга", "lang": "ru", "theme": "Списання боргів"},
            {"query": "реструктуризация ипотеки", "lang": "ru", "theme": "Реструктуризація іпотеки"},
            # Немецкий
            {"query": "problemkredit", "lang": "de", "theme": "Проблемні кредити"},
            {"query": "kreditabschreibung", "lang": "de", "theme": "Списання кредитів"},
        ]

    def search_news(self, query: str, lang: str, from_date: str = None, to_date: str = None, max_results: int = 10):
        """Поиск новостей по запросу"""
        url = f"{self.base_url}/search?q={urllib.parse.quote(query)}&lang={lang}&max={max_results}&apikey={self.api_key}"
        if from_date:
            url += f"&from={from_date}"
        if to_date:
            url += f"&to={to_date}"

        try:
            with urllib.request.urlopen(url) as response:
                data = json.loads(response.read().decode("utf-8"))
                logger.info(f"[{lang}] {query}: {data.get('totalArticles', 0)}")
                return data
        except Exception as e:
            logger.error(f"Ошибка при запросе {query}: {str(e)}")
            return None

    def get_recent_news(self, hours: int = 1) -> list:
        """Получить новости за последние N часов"""
        now = datetime.now()
        from_date = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_date = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        all_new_articles = []
        for query_config in self.queries:
            data = self.search_news(query_config["query"], query_config["lang"], from_date, to_date)
            if data and data.get("articles"):
                for article in data["articles"]:
                    url = article.get("url", "")
                    if url and url not in self.processed_urls:
                        self.processed_urls.add(url)
                        all_new_articles.append({
                            "theme": query_config["theme"],
                            "lang": query_config["lang"],
                            "title": article.get("title", ""),
                            "description": article.get("description", ""),
                            "url": url,
                            "source": article.get("source", {}).get("name", ""),
                            "publishedAt": article.get("publishedAt", "")
                        })
            time.sleep(0.5)

        logger.info(f"Новых статей: {len(all_new_articles)}")
        return all_new_articles

    def clear_processed_cache(self):
        self.processed_urls.clear()


# ==================== КЛАСС ДЛЯ TELEGRAM ====================
class TelegramBot:
    """Класс для работы с Telegram API"""

    def __init__(self, bot_token: str, subscriber_manager: SubscriberManager):
        self.bot_token = bot_token
        self.subscriber_manager = subscriber_manager
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.last_update_id = 0

    def get_updates(self) -> list:
        """Получить обновления от Telegram"""
        url = f"{self.base_url}/getUpdates?offset={self.last_update_id + 1}&timeout=30"
        try:
            with urllib.request.urlopen(url, timeout=35) as response:
                result = json.loads(response.read().decode('utf-8'))
                if result.get('ok'):
                    return result.get('result', [])
        except Exception as e:
            logger.error(f"Ошибка получения обновлений: {str(e)}")
        return []

    def send_message(self, chat_id: str, text: str) -> bool:
        """Отправить сообщение"""
        url = f"{self.base_url}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }).encode('utf-8')

        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result.get('ok', False)
        except Exception as e:
            logger.error(f"Ошибка отправки в {chat_id}: {str(e)}")
            return False

    def format_article(self, article: dict) -> str:
        """Форматировать статью"""
        title = article.get("title_uk", article.get("title", ""))
        desc = article.get("description_uk", article.get("description", ""))
        theme = article.get("theme", "Новини")
        source = article.get("source", "")
        url = article.get("url", "")
        date = article.get("publishedAt", "")

        if date:
            try:
                dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                date = dt.strftime("%d.%m.%Y %H:%M")
            except:
                pass

        return f"""<b>📰 {theme}</b>

<b>{title}</b>

{desc}

<b>Джерело:</b> {source}
<b>Дата:</b> {date}

<a href="{url}">📎 Читати оригінал</a>"""

    def broadcast_articles(self, articles: list):
        """Разослать статьи всем подписчикам"""
        subscribers = self.subscriber_manager.get_subscribers()
        if not subscribers:
            logger.info("Нет подписчиков для рассылки")
            return

        if not articles:
            logger.info("Нет новых статей для рассылки")
            return

        logger.info(f"Рассылка {len(articles)} статей для {len(subscribers)} подписчиков")

        for chat_id in subscribers:
            self.send_message(chat_id, f"<b>🔔 Знайдено {len(articles)} нових статей</b>")
            for article in articles:
                message = self.format_article(article)
                self.send_message(chat_id, message)
                time.sleep(0.5)
            self.send_message(chat_id, f"<b>✅ Усі новини відправлено</b>")

    def process_updates(self):
        """Обработать обновления (команды пользователей)"""
        updates = self.get_updates()
        for update in updates:
            self.last_update_id = update.get('update_id', 0)
            message = update.get('message', {})
            chat_id = str(message.get('chat', {}).get('id', ''))
            text = message.get('text', '')

            if text == '/start':
                if self.subscriber_manager.add_subscriber(chat_id):
                    self.send_message(chat_id,
                        "<b>✅ Вітаємо!</b>\n\n"
                        "Ви підписались на фінансові новини.\n"
                        "Ви будете отримувати переведені новини кожної години.\n\n"
                        "Команди:\n"
                        "/start - Підписатись\n"
                        "/stop - Відписатись\n"
                        "/status - Статус підписки"
                    )
                else:
                    self.send_message(chat_id, "<b>ℹ️ Ви вже підписані</b>")

            elif text == '/stop':
                if self.subscriber_manager.remove_subscriber(chat_id):
                    self.send_message(chat_id, "<b>👋 Ви відписались від новин</b>")
                else:
                    self.send_message(chat_id, "<b>ℹ️ Ви не були підписані</b>")

            elif text == '/status':
                is_subscribed = chat_id in self.subscriber_manager.get_subscribers()
                status = "✅ Підписано" if is_subscribed else "❌ Не підписано"
                self.send_message(chat_id, f"<b>Статус:</b> {status}")


# ==================== ОСНОВНОЙ КЛАСС БОТА ====================
class NewsMonitorBot:
    """Основной бот"""

    def __init__(self, gnews_api_key: str, telegram_bot_token: str):
        self.subscriber_manager = SubscriberManager()
        self.news_fetcher = NewsFetcher(gnews_api_key)
        self.translator = Translator()
        self.telegram_bot = TelegramBot(telegram_bot_token, self.subscriber_manager)
        self.running = True

    def check_commands_loop(self):
        """Поток для обработки команд"""
        logger.info("Запущен обработчик команд")
        while self.running:
            try:
                self.telegram_bot.process_updates()
            except Exception as e:
                logger.error(f"Ошибка в обработчике команд: {str(e)}")
            time.sleep(1)

    def check_and_send_news(self, hours: int = 1):
        """Проверить и отправить новости"""
        logger.info(f"Проверка новостей за {hours} час(ов)...")
        articles = self.news_fetcher.get_recent_news(hours)

        if not articles:
            return

        logger.info(f"Перевод {len(articles)} статей...")
        translated = [self.translator.translate_article(a) for a in articles]

        logger.info("Рассылка новостей...")
        self.telegram_bot.broadcast_articles(translated)

    def run(self):
        """Главный цикл бота"""
        if not TELEGRAM_BOT_TOKEN:
            logger.error("❌ TELEGRAM_BOT_TOKEN не установлен!")
            return

        logger.info("🤖 Бот запущен")
        logger.info(f"Подписчиков: {len(self.subscriber_manager.get_subscribers())}")

        # Запускаем обработчик команд в отдельном потоке
        commands_thread = threading.Thread(target=self.check_commands_loop, daemon=True)
        commands_thread.start()

        iteration = 0
        try:
            while True:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"Итерация #{iteration} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*80}")

                self.check_and_send_news(hours=CHECK_HOURS)

                if iteration % 24 == 0:
                    self.news_fetcher.clear_processed_cache()

                logger.info(f"Следующая проверка через {CHECK_INTERVAL_MINUTES} мин...")
                time.sleep(CHECK_INTERVAL_MINUTES * 60)

        except KeyboardInterrupt:
            logger.info("\n\n👋 Остановка бота...")
            self.running = False


# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================
def main():
    print("="*80)
    print(" "*15 + "БОТ МОНИТОРИНГА ФИНАНСОВЫХ НОВОСТЕЙ")
    print("="*80)
    print(f"\nВремя запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    bot = NewsMonitorBot(GNEWS_API_KEY, TELEGRAM_BOT_TOKEN)
    bot.run()


if __name__ == "__main__":
    main()
