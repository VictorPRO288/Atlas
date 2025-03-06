import requests
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram import F

# 🔹 Замените на ваш токен бота
TOKEN = "7973682034:AAF1hOAXBuWX5ylEjhMcSmDDGeJhnFb26qs"
# 🔹 Укажите свой Telegram ID, чтобы получать сообщения только вы
YOUR_TELEGRAM_ID = 978523669  

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Глобальная переменная для хранения задачи
periodic_task = None

# Максимальная длина сообщения в Telegram
MAX_MESSAGE_LENGTH = 4096

# 🔍 Функция запроса к сайту
# 🔍 Функция запроса к сайту
async def get_bus_info():
    date = '2025-03-09'
    places = {
        "Novogrudok": "c624785",
        "Minsk": "c625144"
    }

    url = f'https://atlasbus.by/api/search?from_id={places["Novogrudok"]}&to_id={places["Minsk"]}&calendar_width=30&date={date}&passengers=1'
    response = requests.get(url)
    
    if response.status_code != 200:
        return "❌ Ошибка при получении данных"

    data = response.json().get('rides', [])

    if not data:
        return "❌ Нет доступных рейсов"

    available_rides = [ride for ride in data if ride["freeSeats"] > 0]

    if available_rides:
        message = "✅ Билеты есть\n\n🚌 **Доступные рейсы:**\n\n"
    else:
        return "❌ Билетов нет в наличии"

    for ride in available_rides:
        message += f"🚏 *Маршрут:* {ride['name']}\n💰 *Цена:* {ride['onlinePrice']} BYN\n🎟 *Свободных мест:* {ride['freeSeats']}\n"

        if ride['pickupStops']:
            stop = ride['pickupStops'][0]
            message += f"🚦 *Отправление:* {stop['datetime']}\n📍 *Место:* {stop['desc']}\n"

        message += "🚏 *Прибытие:*\n"
        for stop in ride['dischargeStops']:
            message += f"   🕒 {stop['datetime']} - 📍 {stop['desc']}\n"

        message += "\n" + "#" * 30 + "\n\n"

    return message

# 📩 Обработчик команды /start
@dp.message(Command("start"))
async def start(message: Message):
    if message.from_user.id == YOUR_TELEGRAM_ID:
        await message.answer("Привет! Отправь /bus, чтобы получить информацию о рейсах.")
    else:
        await message.answer("❌ У вас нет доступа к этому боту.")

# 🚍 Обработчик команды /bus
@dp.message(Command("bus"))
async def send_bus_info(message: Message):
    if message.from_user.id == YOUR_TELEGRAM_ID:
        info = await get_bus_info()

        # Разбиваем сообщение на части, если оно слишком длинное
        if len(info) > MAX_MESSAGE_LENGTH:
            for i in range(0, len(info), MAX_MESSAGE_LENGTH):
                part = info[i:i + MAX_MESSAGE_LENGTH]
                await message.answer(part, parse_mode="Markdown")
        else:
            await message.answer(info, parse_mode="Markdown")
    else:
        await message.answer("❌ У вас нет доступа к этому боту.")

# 🚨 Обработчик команды /stop для остановки бота
@dp.message(Command("stop"))
async def stop(message: Message):
    if message.from_user.id == YOUR_TELEGRAM_ID:
        global periodic_task
        if periodic_task is not None:
            periodic_task.cancel()
            await message.answer("✅ Бот остановлен.")
        else:
            await message.answer("❌ Нет активной задачи для остановки.")
    else:
        await message.answer("❌ У вас нет доступа к этой команде.")

# 🕒 Функция для автоматического запроса каждые 60 сек
async def periodic_request():
    while True:
        await asyncio.sleep(60)  # Ждём 60 сек       
        info = await get_bus_info()

        # Разбиваем сообщение на части, если оно слишком длинное
        if len(info) > MAX_MESSAGE_LENGTH:
            for i in range(0, len(info), MAX_MESSAGE_LENGTH):
                part = info[i:i + MAX_MESSAGE_LENGTH]
                await bot.send_message(YOUR_TELEGRAM_ID, part, parse_mode="Markdown")
        else:
            await bot.send_message(YOUR_TELEGRAM_ID, info, parse_mode="Markdown")

# 🚀 Запуск бота
async def main():
    global periodic_task
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск периодической задачи в фоновом режиме
    periodic_task = asyncio.create_task(periodic_request())
    
    await dp.start_polling(bot)  # Передаём бота сюда

if __name__ == "__main__":
    asyncio.run(main())
