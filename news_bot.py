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
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES"))
CHECK_HOURS = int(os.getenv("CHECK_HOURS"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Определяем директорию для хранения данных
# На Render с диском используем /data, локально - текущую директорию
DATA_DIR = os.getenv("DATA_DIR", ".")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

SUBSCRIBERS_FILE = os.path.join(DATA_DIR, "subscribers.json")
SENT_NEWS_FILE = os.path.join(DATA_DIR, "sent_news.json")
BOT_STATE_FILE = os.path.join(DATA_DIR, "bot_state.json")


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


# ==================== КЛАСС ДЛЯ ОТСЛЕЖИВАНИЯ ОТПРАВЛЕННЫХ НОВОСТЕЙ ====================
class SentNewsTracker:
    """Класс для отслеживания отправленных новостей с умной фильтрацией дублей"""

    def __init__(self, filename: str = SENT_NEWS_FILE):
        self.filename = filename
        self.sent_news = self.load_sent_news()

    def load_sent_news(self) -> dict:
        """Загрузить историю отправленных новостей"""
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"Загружено {len(data)} отправленных новостей")
                    return data
        except Exception as e:
            logger.error(f"Ошибка при загрузке истории новостей: {str(e)}")
        return {}

    def save_sent_news(self):
        """Сохранить историю отправленных новостей"""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.sent_news, f, ensure_ascii=False, indent=2)
            logger.info(f"Сохранено {len(self.sent_news)} записей отправленных новостей")
        except Exception as e:
            logger.error(f"Ошибка при сохранении истории новостей: {str(e)}")

    def is_duplicate(self, url: str, title: str) -> bool:
        """
        Проверить, является ли новость дубликатом
        Проверяем по URL и по похожести заголовка
        """
        # Проверка по точному URL
        if url in self.sent_news:
            sent_time = self.sent_news[url].get('sent_at')
            # Если новость была отправлена менее 7 дней назад - это дубль
            try:
                sent_dt = datetime.fromisoformat(sent_time)
                if (datetime.now() - sent_dt).days < 7:
                    logger.info(f"Дубликат по URL (отправлено {sent_time}): {url}")
                    return True
            except:
                pass

        # Проверка по похожести заголовка (для случаев, когда один URL но разные домены)
        title_lower = title.lower().strip()
        for data in self.sent_news.values():
            existing_title = data.get('title', '').lower().strip()
            sent_time = data.get('sent_at')

            # Если заголовки очень похожи (более 85% совпадения)
            if self._similarity(title_lower, existing_title) > 0.85:
                try:
                    sent_dt = datetime.fromisoformat(sent_time)
                    # Если похожая новость была отправлена менее 3 дней назад
                    if (datetime.now() - sent_dt).days < 3:
                        logger.info(f"Дубликат по заголовку: '{title}' похож на '{existing_title}'")
                        return True
                except:
                    pass

        return False

    def _similarity(self, s1: str, s2: str) -> float:
        """Вычислить похожесть двух строк (коэффициент Жаккара)"""
        if not s1 or not s2:
            return 0.0

        # Разбиваем на слова
        words1 = set(s1.split())
        words2 = set(s2.split())

        # Коэффициент Жаккара: пересечение / объединение
        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def mark_as_sent(self, url: str, title: str):
        """Отметить новость как отправленную"""
        self.sent_news[url] = {
            'title': title,
            'sent_at': datetime.now().isoformat()
        }
        self.save_sent_news()

    def cleanup_old_entries(self, days: int = 30):
        """Удалить записи старше N дней"""
        cutoff_date = datetime.now() - timedelta(days=days)
        initial_count = len(self.sent_news)

        self.sent_news = {
            url: data for url, data in self.sent_news.items()
            if datetime.fromisoformat(data['sent_at']) > cutoff_date
        }

        removed = initial_count - len(self.sent_news)
        if removed > 0:
            logger.info(f"Удалено {removed} старых записей (старше {days} дней)")
            self.save_sent_news()


# ==================== КЛАСС ДЛЯ ПОЛУЧЕНИЯ НОВОСТЕЙ ====================
class NewsFetcher:
    """Класс для получения новостей из GNews API"""

    def __init__(self, api_key: str, sent_news_tracker: SentNewsTracker):
        self.api_key = api_key
        self.base_url = "https://gnews.io/api/v4"
        self.sent_news_tracker = sent_news_tracker
        self.queries = [
            # === АНГЛИЙСКИЕ ЗАПРОСЫ (США, Великобритания, международные) ===
            {"query": "bankruptcy", "lang": "en", "theme": "Банкрутство"},
            {"query": "personal bankruptcy", "lang": "en", "theme": "Банкрутство фізичних осіб"},
            {"query": "business bankruptcy", "lang": "en", "theme": "Банкрутство бізнесу"},
            {"query": "corporate bankruptcy", "lang": "en", "theme": "Банкрутство компаній"},
            {"query": "chapter 11", "lang": "en", "theme": "Банкрутство"},
            {"query": "chapter 7", "lang": "en", "theme": "Банкрутство"},
            {"query": "debt restructuring", "lang": "en", "theme": "Реструктуризація боргів"},
            {"query": "loan restructuring", "lang": "en", "theme": "Реструктуризація кредитів"},
            {"query": "bankruptcy court", "lang": "en", "theme": "Судові справи"},
            {"query": "insolvency proceedings", "lang": "en", "theme": "Судові справи"},
            {"query": "bankruptcy case", "lang": "en", "theme": "Судові справи"},
            {"query": "consumer debt", "lang": "en", "theme": "Споживчі борги"},
            {"query": "loan default", "lang": "en", "theme": "Прострочені кредити"},
            {"query": "mortgage foreclosure", "lang": "en", "theme": "Іпотечні проблеми"},
            {"query": "credit card debt", "lang": "en", "theme": "Кредитні борги"},
            {"query": "overdue loan", "lang": "en", "theme": "Прострочені кредити"},
            {"query": "insolvency", "lang": "en", "theme": "Неплатоспроможність"},
            {"query": "financial distress", "lang": "en", "theme": "Фінансові проблеми"},
            {"query": "unable to pay debts", "lang": "en", "theme": "Неплатоспроможність"},

            # === УКРАИНСКИЕ ЗАПРОСЫ - БАНКРОТСТВО ===
            {"query": "банкрутство", "lang": "uk", "theme": "Банкрутство"},
            {"query": "банкрутство фізичних осіб", "lang": "uk", "theme": "Банкрутство фізичних осіб"},
            {"query": "банкрутство компанії", "lang": "uk", "theme": "Банкрутство бізнесу"},
            {"query": "банкрутство підприємства", "lang": "uk", "theme": "Банкрутство бізнесу"},
            {"query": "банкрутство ФОП Україна", "lang": "uk", "theme": "Банкрутство підприємців"},
            {"query": "банкрутство ТОВ Україна", "lang": "uk", "theme": "Банкрутство бізнесу"},
            {"query": "ліквідація ТОВ Україна", "lang": "uk", "theme": "Ліквідація бізнесу"},
            {"query": "малий бізнес банкрутство", "lang": "uk", "theme": "Банкрутство бізнесу"},
            {"query": "неплатоспроможність", "lang": "uk", "theme": "Неплатоспроможність"},
            {"query": "неплатоспроможність боржника", "lang": "uk", "theme": "Неплатоспроможність"},
            {"query": "фінансові труднощі", "lang": "uk", "theme": "Фінансові проблеми"},

            # === БАНКРОТСТВО В УКРАИНЕ ===
            {"query": "банкрутство в україні", "lang": "uk", "theme": "Банкрутство в Україні"},
            {"query": "банкрутство Україна 2024", "lang": "uk", "theme": "Банкрутство в Україні"},
            {"query": "банкрутство Україна 2025", "lang": "uk", "theme": "Банкрутство в Україні"},
            {"query": "процедура банкрутства україна", "lang": "uk", "theme": "Банкрутство в Україні"},
            {"query": "визнання банкрутом Україна", "lang": "uk", "theme": "Банкрутство в Україні"},
            {"query": "фізична особа банкрут Україна", "lang": "uk", "theme": "Банкрутство фізичних осіб"},
            {"query": "як оголосити себе банкрутом", "lang": "uk", "theme": "Банкрутство фізичних осіб"},
            {"query": "статистика банкрутств Україна", "lang": "uk", "theme": "Банкрутство в Україні"},
            {"query": "масове банкрутство Україна", "lang": "uk", "theme": "Банкрутство в Україні"},
            {"query": "банкрутство українців", "lang": "uk", "theme": "Банкрутство фізичних осіб"},

            # === СУДОВІ СПРАВИ ТА ПРАВОВІ ПИТАННЯ ===
            {"query": "господарський суд України", "lang": "uk", "theme": "Судові справи"},
            {"query": "справа про банкрутство Україна", "lang": "uk", "theme": "Судові справи"},
            {"query": "рішення суду банкрутство", "lang": "uk", "theme": "Судові справи"},
            {"query": "апеляція банкрутство Україна", "lang": "uk", "theme": "Судові справи"},
            {"query": "арбітражний керуючий Україна", "lang": "uk", "theme": "Судові справи"},
            {"query": "ліквідатор Україна", "lang": "uk", "theme": "Судові справи"},
            {"query": "кредитор банкрутство Україна", "lang": "uk", "theme": "Судові справи"},
            {"query": "боржник банкрутство Україна", "lang": "uk", "theme": "Судові справи"},
            {"query": "процедура банкрутства", "lang": "uk", "theme": "Судові справи"},
            {"query": "господарський суд банкрутство", "lang": "uk", "theme": "Судові справи"},
            {"query": "судова справа банкрутство", "lang": "uk", "theme": "Судові справи"},
            {"query": "розпорядник майна боржника", "lang": "uk", "theme": "Судові справи"},
            {"query": "санація підприємства Україна", "lang": "uk", "theme": "Судові справи"},
            {"query": "ліквідаційна комісія", "lang": "uk", "theme": "Судові справи"},

            # === ЗАКОНОДАВСТВО УКРАЇНИ ПРО БАНКРУТСТВО ===
            {"query": "закон про банкрутство Україна", "lang": "uk", "theme": "Законодавство України"},
            {"query": "кодекс з банкрутства", "lang": "uk", "theme": "Законодавство України"},
            {"query": "зміни в законі про банкрутство", "lang": "uk", "theme": "Законодавство України"},
            {"query": "нові правила банкрутства", "lang": "uk", "theme": "Законодавство України"},
            {"query": "реформа банкрутства Україна", "lang": "uk", "theme": "Законодавство України"},
            {"query": "мораторій банкрутство Україна", "lang": "uk", "theme": "Законодавство України"},
            {"query": "зміни в процедурі банкрутства", "lang": "uk", "theme": "Законодавство України"},
            {"query": "законопроект банкрутство", "lang": "uk", "theme": "Законодавство України"},
            {"query": "регулювання банкрутства", "lang": "uk", "theme": "Законодавство України"},
            {"query": "правила неплатоспроможності", "lang": "uk", "theme": "Законодавство України"},
            {"query": "постанова про банкрутство", "lang": "uk", "theme": "Законодавство України"},
            {"query": "закон про реструктуризацію", "lang": "uk", "theme": "Законодавство України"},
            {"query": "нормативна база банкрутство", "lang": "uk", "theme": "Законодавство України"},

            # === КРЕДИТИ ТА БОРГИ В УКРАЇНІ ===
            {"query": "кредит в Україні", "lang": "uk", "theme": "Кредити в Україні"},
            {"query": "банківський кредит Україна", "lang": "uk", "theme": "Кредити в Україні"},
            {"query": "проблемний кредит Україна", "lang": "uk", "theme": "Проблемні кредити"},
            {"query": "прострочений кредит банк", "lang": "uk", "theme": "Прострочені кредити"},
            {"query": "заборгованість банку Україна", "lang": "uk", "theme": "Кредитна заборгованість"},
            {"query": "реструктуризація кредиту банк", "lang": "uk", "theme": "Реструктуризація кредитів"},
            {"query": "списання боргу банк Україна", "lang": "uk", "theme": "Списання боргів"},
            {"query": "іпотека Україна", "lang": "uk", "theme": "Іпотека в Україні"},
            {"query": "реструктуризація іпотеки Україна", "lang": "uk", "theme": "Іпотека в Україні"},
            {"query": "проблемна іпотека банк", "lang": "uk", "theme": "Іпотечні проблеми"},
            {"query": "споживчий кредит", "lang": "uk", "theme": "Споживчі борги"},
            {"query": "прострочений кредит", "lang": "uk", "theme": "Прострочені кредити"},
            {"query": "заборгованість по кредиту", "lang": "uk", "theme": "Кредитна заборгованість"},
            {"query": "борг по кредиту", "lang": "uk", "theme": "Кредитні борги"},
            {"query": "автокредит Україна", "lang": "uk", "theme": "Кредити в Україні"},
            {"query": "кредит готівкою", "lang": "uk", "theme": "Кредити в Україні"},
            {"query": "кредит на картку", "lang": "uk", "theme": "Кредити в Україні"},
            {"query": "мікрокредит Україна", "lang": "uk", "theme": "Кредити в Україні"},

            # === РЕСТРУКТУРИЗАЦІЯ БОРГІВ ===
            {"query": "реструктуризація боргів", "lang": "uk", "theme": "Реструктуризація боргів"},
            {"query": "реструктуризація кредиту", "lang": "uk", "theme": "Реструктуризація кредитів"},
            {"query": "реструктуризація заборгованості", "lang": "uk", "theme": "Реструктуризація боргів"},
            {"query": "реструктуризація боргів фізичних осіб", "lang": "uk", "theme": "Реструктуризація боргів"},
            {"query": "реструктуризація заборгованості Україна", "lang": "uk", "theme": "Реструктуризація боргів"},
            {"query": "програма реструктуризації кредитів", "lang": "uk", "theme": "Реструктуризація кредитів"},

            # === УКРАЇНСЬКІ БАНКИ ===
            {"query": "Приватбанк кредит", "lang": "uk", "theme": "Українські банки"},
            {"query": "Ощадбанк Україна", "lang": "uk", "theme": "Українські банки"},
            {"query": "Монобанк кредит", "lang": "uk", "theme": "Українські банки"},
            {"query": "ПриватБанк борг", "lang": "uk", "theme": "Українські банки"},
            {"query": "кредит банку Україна", "lang": "uk", "theme": "Українські банки"},
            {"query": "українські банки проблеми", "lang": "uk", "theme": "Українські банки"},
            {"query": "ПУМБ кредит", "lang": "uk", "theme": "Українські банки"},
            {"query": "Альфа-банк Україна", "lang": "uk", "theme": "Українські банки"},
            {"query": "ОТП банк кредит", "lang": "uk", "theme": "Українські банки"},
            {"query": "Укргазбанк кредит", "lang": "uk", "theme": "Українські банки"},
            {"query": "Укрексімбанк", "lang": "uk", "theme": "Українські банки"},
            {"query": "Креді Агріколь Україна", "lang": "uk", "theme": "Українські банки"},
            {"query": "Укрсиббанк", "lang": "uk", "theme": "Українські банки"},
            {"query": "Райффайзен банк Україна", "lang": "uk", "theme": "Українські банки"},
            {"query": "Таскомбанк", "lang": "uk", "theme": "Українські банки"},
            {"query": "Універсал банк Україна", "lang": "uk", "theme": "Українські банки"},
            {"query": "банки України новини", "lang": "uk", "theme": "Українські банки"},
            {"query": "банківська система України", "lang": "uk", "theme": "Українські банки"},
            {"query": "банківські кредити 2025", "lang": "uk", "theme": "Українські банки"},
            {"query": "проблеми банків Україна", "lang": "uk", "theme": "Українські банки"},
            {"query": "НБУ банки", "lang": "uk", "theme": "Українські банки"},
            {"query": "Національний банк України", "lang": "uk", "theme": "Українські банки"},

            # === КОЛЕКТОРИ В УКРАЇНІ ===
            {"query": "колектори Україна", "lang": "uk", "theme": "Колектори в Україні"},
            {"query": "колекторське агентство Україна", "lang": "uk", "theme": "Колектори в Україні"},
            {"query": "права боржника Україна", "lang": "uk", "theme": "Права боржників"},
            {"query": "захист від колекторів", "lang": "uk", "theme": "Захист від колекторів"},
            {"query": "стягнення боргу Україна", "lang": "uk", "theme": "Стягнення боргів"},
            {"query": "боротьба з колекторами", "lang": "uk", "theme": "Захист від колекторів"},
            {"query": "колекторські дії", "lang": "uk", "theme": "Колектори в Україні"},
            {"query": "незаконні дії колекторів", "lang": "uk", "theme": "Колектори в Україні"},

            # === ФІНАНСОВА ГРАМОТНІСТЬ ===
            {"query": "фінансова грамотність Україна", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "як уникнути банкрутства", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "управління боргами", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "фінансова безпека Україна", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "особисті фінанси Україна", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "фінансове планування", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "як погасити борги", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "кредитна історія Україна", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "кредитний рейтинг Україна", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "фінансові поради Україна", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "як вийти з боргів", "lang": "uk", "theme": "Фінансова грамотність"},
            {"query": "фінансова освіта", "lang": "uk", "theme": "Фінансова грамотність"},

            # === ЕКОНОМІКА УКРАЇНИ ===
            {"query": "фінансова криза Україна", "lang": "uk", "theme": "Економіка України"},
            {"query": "борги українців", "lang": "uk", "theme": "Економіка України"},
            {"query": "неплатоспроможність Україна", "lang": "uk", "theme": "Економіка України"},
            {"query": "банківська криза Україна", "lang": "uk", "theme": "Економіка України"},
            {"query": "економічна ситуація Україна", "lang": "uk", "theme": "Економіка України"},
            {"query": "економіка України 2025", "lang": "uk", "theme": "Економіка України"},
            {"query": "фінансовий сектор України", "lang": "uk", "theme": "Економіка України"},
            {"query": "борги населення Україна", "lang": "uk", "theme": "Економіка України"},

            # === ЮРИДИЧНА ДОПОМОГА ===
            {"query": "юрист банкрутство Україна", "lang": "uk", "theme": "Юридична допомога"},
            {"query": "адвокат банкрутство Київ", "lang": "uk", "theme": "Юридична допомога"},
            {"query": "безкоштовна юридична консультація", "lang": "uk", "theme": "Юридична допомога"},
            {"query": "допомога боржникам Україна", "lang": "uk", "theme": "Допомога боржникам"},
            {"query": "куди звернутися з боргами", "lang": "uk", "theme": "Допомога боржникам"},
            {"query": "юридична консультація банкрутство", "lang": "uk", "theme": "Юридична допомога"},
            {"query": "адвокат з боргів", "lang": "uk", "theme": "Юридична допомога"},
            {"query": "правовий захист боржників", "lang": "uk", "theme": "Юридична допомога"},

            # === СОЦІАЛЬНІ ПРОГРАМИ ===
            {"query": "соціальна підтримка Україна", "lang": "uk", "theme": "Соціальні програми"},
            {"query": "програма підтримки боржників", "lang": "uk", "theme": "Соціальні програми"},
            {"query": "держпрограма реструктуризації", "lang": "uk", "theme": "Соціальні програми"},
            {"query": "пільги боржникам Україна", "lang": "uk", "theme": "Соціальні програми"},
            {"query": "державна допомога боржникам", "lang": "uk", "theme": "Соціальні програми"},
            {"query": "соціальний захист боржників", "lang": "uk", "theme": "Соціальні програми"},

            # === БОРГИ ПІДПРИЄМСТВ ===
            {"query": "борги підприємства Україна", "lang": "uk", "theme": "Борги підприємств"},
            {"query": "заборгованість компанії", "lang": "uk", "theme": "Борги підприємств"},
            {"query": "банкрутство великих компаній", "lang": "uk", "theme": "Банкрутство бізнесу"},
            {"query": "банкрутство підприємств 2025", "lang": "uk", "theme": "Банкрутство бізнесу"},
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
        duplicates_count = 0

        for query_config in self.queries:
            data = self.search_news(query_config["query"], query_config["lang"], from_date, to_date)
            if data and data.get("articles"):
                for article in data["articles"]:
                    url = article.get("url", "")
                    title = article.get("title", "")

                    # Проверяем, не является ли это дубликатом
                    if url and not self.sent_news_tracker.is_duplicate(url, title):
                        all_new_articles.append({
                            "theme": query_config["theme"],
                            "lang": query_config["lang"],
                            "title": title,
                            "description": article.get("description", ""),
                            "url": url,
                            "source": article.get("source", {}).get("name", ""),
                            "publishedAt": article.get("publishedAt", "")
                        })
                    else:
                        duplicates_count += 1
            time.sleep(0.5)

        logger.info(f"Новых статей: {len(all_new_articles)}, отфильтровано дублей: {duplicates_count}")
        return all_new_articles


# ==================== КЛАСС ДЛЯ TELEGRAM ====================
class TelegramBot:
    """Класс для работы с Telegram API"""

    def __init__(self, bot_token: str, subscriber_manager: SubscriberManager):
        self.bot_token = bot_token
        self.subscriber_manager = subscriber_manager
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.last_update_id = self._load_bot_state()

    def _load_bot_state(self) -> int:
        """Загрузить состояние бота (last_update_id)"""
        try:
            if os.path.exists(BOT_STATE_FILE):
                with open(BOT_STATE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    last_id = data.get('last_update_id', 0)
                    logger.info(f"Загружен last_update_id: {last_id}")
                    return last_id
        except Exception as e:
            logger.error(f"Ошибка при загрузке состояния бота: {str(e)}")
        return 0

    def _save_bot_state(self):
        """Сохранить состояние бота (last_update_id)"""
        try:
            with open(BOT_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump({'last_update_id': self.last_update_id}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка при сохранении состояния бота: {str(e)}")

    def get_updates(self) -> list:
        """Получить обновления от Telegram"""
        url = f"{self.base_url}/getUpdates?offset={self.last_update_id + 1}&timeout=30"
        try:
            with urllib.request.urlopen(url, timeout=35) as response:
                result = json.loads(response.read().decode('utf-8'))
                if result.get('ok'):
                    updates = result.get('result', [])
                    if updates:
                        logger.info(f"📨 Получено обновлений: {len(updates)}")
                    return updates
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
                success = result.get('ok', False)
                if success:
                    logger.info(f"✅ Сообщение отправлено в {chat_id}")
                else:
                    logger.error(f"❌ Ошибка отправки в {chat_id}: {result}")
                return success
        except Exception as e:
            logger.error(f"❌ Ошибка отправки в {chat_id}: {str(e)}")
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

    def broadcast_articles(self, articles: list, sent_news_tracker: SentNewsTracker):
        """Разослать статьи всем подписчикам"""
        subscribers = self.subscriber_manager.get_subscribers()
        if not subscribers:
            logger.info("Нет подписчиков для рассылки")
            return

        if not articles:
            logger.info("Нет новых статей для рассылки")
            return

        logger.info(f"Рассылка {len(articles)} статей для {len(subscribers)} подписчиков")

        # Отмечаем новости как отправленные сразу, чтобы не отправлять их повторно
        for article in articles:
            sent_news_tracker.mark_as_sent(
                article.get('url', ''),
                article.get('title_uk', article.get('title', ''))
            )

        # Рассылаем новости всем подписчикам
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
            self._save_bot_state()  # Сохраняем после каждого обновления

            message = update.get('message', {})
            chat_id = str(message.get('chat', {}).get('id', ''))
            text = message.get('text', '')
            username = message.get('from', {}).get('username', 'Unknown')

            logger.info(f"👤 Команда от @{username} (ID: {chat_id}): {text}")

            if text == '/start':
                if self.subscriber_manager.add_subscriber(chat_id):
                    logger.info(f"✅ Новый подписчик: @{username} ({chat_id})")
                    self.send_message(chat_id,
                        "<b>✅ Вітаємо!</b>\n\n"
                        "Ви підписались на фінансові новини.\n"
                        "Ви будете отримувати переведені новини кожні 3 години.\n\n"
                        "Команди:\n"
                        "/start - Підписатись\n"
                        "/stop - Відписатись\n"
                        "/status - Статус підписки"
                    )
                else:
                    logger.info(f"ℹ️ Повторная подписка: @{username} ({chat_id})")
                    self.send_message(chat_id, "<b>ℹ️ Ви вже підписані</b>")

            elif text == '/stop':
                if self.subscriber_manager.remove_subscriber(chat_id):
                    logger.info(f"👋 Отписка: @{username} ({chat_id})")
                    self.send_message(chat_id, "<b>👋 Ви відписались від новин</b>")
                else:
                    logger.info(f"ℹ️ Попытка отписки незарегистрированного: @{username} ({chat_id})")
                    self.send_message(chat_id, "<b>ℹ️ Ви не були підписані</b>")

            elif text == '/status':
                is_subscribed = chat_id in self.subscriber_manager.get_subscribers()
                status = "✅ Підписано" if is_subscribed else "❌ Не підписано"
                logger.info(f"ℹ️ Проверка статуса: @{username} ({chat_id}) - {status}")
                self.send_message(chat_id, f"<b>Статус:</b> {status}")


# ==================== ОСНОВНОЙ КЛАСС БОТА ====================
class NewsMonitorBot:
    """Основной бот"""

    def __init__(self, gnews_api_key: str, telegram_bot_token: str):
        self.subscriber_manager = SubscriberManager()
        self.sent_news_tracker = SentNewsTracker()
        self.news_fetcher = NewsFetcher(gnews_api_key, self.sent_news_tracker)
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
        self.telegram_bot.broadcast_articles(translated, self.sent_news_tracker)

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

                # Периодически очищаем старые записи (раз в сутки)
                if iteration % 24 == 0:
                    self.sent_news_tracker.cleanup_old_entries(days=30)

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
