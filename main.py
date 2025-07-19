import logging
import nest_asyncio
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import os
import httpx
import re

nest_asyncio.apply()

TOKEN = os.getenv("TELEGRAM_TOKEN")

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
    # Настройка логирования
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    logging.info("Bot started and polling...")
    await app.run_polling()

if __name__ == "__main__":
    # Для тестирования работы с Bybit API
    from bybit_client import get_spot_tickers
    tickers = get_spot_tickers()
    print(f"Всего активных монет: {len(tickers)}")
    print("Примеры:", tickers[:5])
    
    asyncio.run(main())