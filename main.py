import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import nest_asyncio
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import os
import httpx
import re
from flask import Flask, Response, request
import sys
from threading import Thread

# Инициализация Flask app для health check
flask_app = Flask(__name__)
nest_asyncio.apply()

# Глобальная переменная для приложения Telegram
telegram_app = None

@flask_app.route('/health')
def health_check():
    """Endpoint для health check на Render"""
    return Response("OK", status=200)


@flask_app.route(f'/{os.getenv("TELEGRAM_TOKEN")}', methods=['POST'])
def telegram_webhook():
    if telegram_app is None:
        return "Application not initialized", 500
        
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    asyncio.run_coroutine_threadsafe(
        telegram_app.process_update(update),
        telegram_app.update_queue._loop
    )
    return "OK", 200

def setup_logging():
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(log_format)
    
    logging.basicConfig(level=logging.INFO, handlers=[])
    logger = logging.getLogger()
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    file_handler = RotatingFileHandler(
        filename=logs_dir / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pybit").setLevel(logging.WARNING)
    
    logger.info("Logging setup complete")

async def setup_telegram_app():
    """Настройка и возврат Telegram приложения"""
    global telegram_app
    
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        raise ValueError("TELEGRAM_TOKEN environment variable is not set")
    
    telegram_app = ApplicationBuilder().token(TOKEN).build()
    
    # Регистрация обработчиков
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CommandHandler("about", about_command))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    
    return telegram_app

async def run_webhook():
    """Запуск в режиме webhook"""
    global telegram_app
    
    await setup_telegram_app()
    render_host = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    webhook_url = f"https://{render_host}/{os.getenv('TELEGRAM_TOKEN')}"
    
    await telegram_app.bot.set_webhook(webhook_url)
    logging.info(f"Webhook set up: {webhook_url}")
    
    # Запуск Flask в отдельном потоке
    flask_thread = Thread(target=lambda: flask_app.run(
        host='0.0.0.0', 
        port=5000,
        debug=False,
        use_reloader=False
    ))
    flask_thread.daemon = True
    flask_thread.start()
    
    # Бесконечный цикл для поддержания работы приложения
    while True:
        await asyncio.sleep(3600)  # Проверка каждые 60 минут

async def run_polling():
    """Запуск в режиме polling (для локальной разработки)"""
    global telegram_app
    
    await setup_telegram_app()
    await telegram_app.run_polling()


# Клавиатура с кнопками команд
buttons = [
    [KeyboardButton("🔎 Поиск монет")],
    [KeyboardButton("/start"), KeyboardButton("/help")],
    [KeyboardButton("/about")]
]
keyboard = ReplyKeyboardMarkup(buttons, resize_keyboard=True)


# Таймфреймы — инлайн кнопки с расширенным списком
timeframe_buttons = [
    [InlineKeyboardButton("1 минута", callback_data="tf_1m"),
     InlineKeyboardButton("3 минуты", callback_data="tf_3m"),
     InlineKeyboardButton("5 минут", callback_data="tf_5m")],
    [InlineKeyboardButton("15 минут", callback_data="tf_15m"),
     InlineKeyboardButton("30 минут", callback_data="tf_30m"),
     InlineKeyboardButton("1 час", callback_data="tf_1h")],
    [InlineKeyboardButton("2 часа", callback_data="tf_2h"),
     InlineKeyboardButton("4 часа", callback_data="tf_4h"),
     InlineKeyboardButton("6 часов", callback_data="tf_6h")],
    [InlineKeyboardButton("12 часов", callback_data="tf_12h"),
     InlineKeyboardButton("1 день", callback_data="tf_1d"),
     InlineKeyboardButton("1 неделя", callback_data="tf_1w")]
]
timeframe_keyboard = InlineKeyboardMarkup(timeframe_buttons)

# Маппинг таймфреймов для Bybit API
TIMEFRAME_MAP = {
    '1m': '1',
    '3m': '3',
    '5m': '5',
    '15m': '15',
    '30m': '30',
    '1h': '60',
    '2h': '120',
    '4h': '240',
    '6h': '360',
    '12h': '720',
    '1d': 'D',
    '1w': 'W'
}

# Ограничение количества одновременно обрабатываемых запросов
MAX_CONCURRENT_REQUESTS = 20

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Добро пожаловать в Bybit Pump Screener — ваш помощник в скальпинге. Выберите действие ниже.",
        reply_markup=keyboard
    )

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *О боте*\n\n"
        "Этот бот помогает трейдерам быстро находить топ-3 🔥 монеты с самым быстрым ростом на спотовом рынке Bybit\n"
        "за выбранный таймфрейм (от 1 минуты до недели).\n\n"
        "📊 Используйте кнопку *'🔎 Поиск монет'* для запуска анализа. Бот проанализирует все USDT-пары и покажет, где был наибольший рост.\n\n"
        "Полезно для скальпинга, торговли на импульсах и поиска точек входа.\n\n"
        "💡 Разработано как MVP для трейдеров и энтузиастов криптовалют. Поддержка testnet и реальных данных.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠 *Помощь по командам*\n\n"
        "/start — запустить бота и отобразить кнопки\n"
        "/help — показать это сообщение\n"
        "/about — узнать, для чего нужен бот\n\n"
        "🔎 *Как пользоваться ботом:*\n"
        "1. Нажмите кнопку *'🔎 Поиск монет'*\n"
        "2. Выберите интересующий таймфрейм (например, 5 минут)\n"
        "3. Бот покажет топ-3 монеты с наибольшим ростом за выбранный период.\n\n"
        "📉 Данные берутся с *спотового рынка Bybit*.\n"
        "👨‍💻 Подходит для трейдеров, желающих быстро находить движущиеся монеты.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"

async def fetch_symbol_data(client, symbol, interval):
    """Получает данные для одного символа с обработкой ошибок"""
    try:
        # Очищаем символ от непечатных символов
        clean_symbol = re.sub(r'[^\x20-\x7E]', '', symbol)
        
        resp = await client.get(BYBIT_KLINE_URL, params={
            "category": "spot",
            "symbol": clean_symbol,
            "interval": interval,
            "limit": 2
        }, timeout=10)
        
        data = resp.json()
        
        # Проверка кода ответа API
        if data.get("retCode") != 0:
            logging.warning(f"API error for {clean_symbol}: {data.get('retMsg', 'Unknown error')}")
            return None
            
        kline_data = data["result"].get("list", [])
        if len(kline_data) < 2:
            logging.info(f"Not enough data for {clean_symbol}: {len(kline_data)} candles")
            return None
            
        # Проверка структуры данных свечи
        if len(kline_data[0]) < 5:
            logging.warning(f"Invalid candle data for {clean_symbol}")
            return None
            
        last_close = float(kline_data[0][4])
        prev_close = float(kline_data[1][4])
        
        # Проверка корректности цен
        if last_close <= 0 or prev_close <= 0:
            logging.warning(f"Invalid prices for {clean_symbol}: {prev_close} -> {last_close}")
            return None
            
        pct_change = (last_close - prev_close) / prev_close * 100
        return {"symbol": clean_symbol, "change_pct": pct_change}
        
    except Exception as e:
        logging.error(f"Error processing {symbol}: {str(e)}")
        return None

async def get_top3_gainers(timeframe: str):
    """Возвращает топ 3 монет с наибольшим ростом за указанный период"""
    from bybit_client import get_spot_tickers
    symbols = get_spot_tickers()
    
    # Получаем формат интервала для Bybit API
    interval = TIMEFRAME_MAP.get(timeframe)
    if not interval:
        logging.error(f"Invalid timeframe: {timeframe}")
        return []
    
    results = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    
    async def fetch_with_semaphore(client, symbol, interval):
        async with semaphore:
            return await fetch_symbol_data(client, symbol, interval)
    
    async with httpx.AsyncClient() as client:
        # Создаем задачи для параллельного выполнения
        tasks = [fetch_with_semaphore(client, symbol, interval) for symbol in symbols]
        results = await asyncio.gather(*tasks)
    
    # Фильтруем None результаты (ошибки)
    valid_results = [r for r in results if r is not None]
    
    if not valid_results:
        logging.warning(f"No valid data for timeframe '{timeframe}'")
        return []
    
    # Сортируем по убыванию изменения
    valid_results.sort(key=lambda x: x["change_pct"], reverse=True)
    
    logging.info(f"Top 3 for '{timeframe}':")
    for i, coin in enumerate(valid_results[:3], 1):
        logging.info(f"{i}. {coin['symbol']}: {coin['change_pct']:.2f}%")
    
    return valid_results[:3]

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    try:
        await query.answer("Загрузка данных...")
        
        if data.startswith("tf_"):
            timeframe = data[3:]
            await query.edit_message_text(f"📊 Ищем топ 3 монеты за таймфрейм '{timeframe}'...")
            
            top3 = await get_top3_gainers(timeframe)
            
            if top3:
                msg = f"📈 Топ 3 монеты за таймфрейм '{timeframe}':\n"
                for i, coin in enumerate(top3, 1):
                    # Очищаем символ от непечатных символов для вывода
                    clean_symbol = re.sub(r'[^\x20-\x7E]', '', coin['symbol'])
                    msg += f"{i}. {clean_symbol} — {coin['change_pct']:.2f}%\n"
            else:
                msg = f"⚠️ Не удалось получить данные для таймфрейма '{timeframe}'. Попробуйте позже."
                
            await query.edit_message_text(msg)
            
    except Exception as e:
        logging.exception("Ошибка в обработке кнопки:")
        try:
            await query.edit_message_text("❌ Произошла ошибка при обработке запроса. Попробуйте другой таймфрейм.")
        except:
            pass

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🔎 Поиск монет":
        await update.message.reply_text(
            "Выберите таймфрейм для поиска монет:",
            reply_markup=timeframe_keyboard
        )
    else:
        await update.message.reply_text(
            "Неизвестная команда. Нажмите /help для списка команд.",
            reply_markup=keyboard
        )

async def main():
    """Основная функция запуска бота с обработкой всех сценариев"""
    try:
        # Инициализация логирования
        setup_logging()
        logger = logging.getLogger(__name__)
        
        # Создаем приложение Telegram
        global telegram_app
        telegram_app = await setup_telegram_app()
        
        # Определяем режим работы (Webhook/Polling)
        if os.environ.get('RENDER'):
            logger.info("Starting in WEBHOOK mode (Production)")
            
            # Получаем URL для webhook
            render_host = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            if not render_host:
                raise ValueError("RENDER_EXTERNAL_HOSTNAME environment variable is missing")
            
            token = os.getenv("TELEGRAM_TOKEN")
            if not token:
                raise ValueError("TELEGRAM_TOKEN environment variable is missing")
                
            webhook_url = f"https://{render_host}/{token}"
            
            # Настраиваем webhook
            try:
                await telegram_app.bot.set_webhook(
                    webhook_url,
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES,
                    secret_token=os.getenv("WEBHOOK_SECRET")  # Добавляем секрет для безопасности
                )
                logger.info(f"Webhook successfully configured: {webhook_url}")
            except Exception as webhook_err:
                logger.error(f"Failed to set webhook: {webhook_err}")
                raise
            
            # Запускаем Flask в отдельном потоке
            flask_thread = Thread(
                target=run_flask,
                name="FlaskThread",
                daemon=True
            )
            flask_thread.start()
            logger.info("Flask server started in background thread")
            
            # Бесконечный цикл для поддержания работы
            try:
                while True:
                    await asyncio.sleep(3600)  # Проверка каждые 60 минут
            except asyncio.CancelledError:
                logger.info("Received cancellation signal")
                
        else:
            logger.info("Starting in POLLING mode (Development)")
            try:
                await telegram_app.run_polling(
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES,
                    close_loop=False
                )
            except asyncio.CancelledError:
                logger.info("Polling mode cancelled")
            
    except Exception as e:
        logger.critical(f"Fatal error in main: {str(e)}", exc_info=True)
        
        # Пытаемся корректно остановить приложение
        try:
            if telegram_app:
                await telegram_app.stop()
                logger.info("Telegram application stopped gracefully")
        except Exception as stop_err:
            logger.error(f"Error during shutdown: {stop_err}")
        
        # В production окружении пробрасываем исключение дальше
        if os.environ.get('RENDER'):
            raise
        sys.exit(1)
        
def run_flask():
    """Запуск Flask сервера"""
    flask_app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        use_reloader=False
    )

if __name__ == "__main__":
    import signal
    def shutdown(signum, frame):
        logging.info("Bot stopped by signal")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.critical(f"Unexpected error: {e}", exc_info=True)

