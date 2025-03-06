import requests
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import os

# 🔹 Замените на ваш токен бота
TOKEN = "7973682034:AAF1hOAXBuWX5ylEjhMcSmDDGeJhnFb26qs"
# 🔹 Укажите свой Telegram ID, чтобы получать сообщения только вы
YOUR_TELEGRAM_ID = 978523669  

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Глобальные переменные
periodic_task = None
selected_date = "2025-03-09"  # Дата по умолчанию

# 🔍 Функция запроса к сайту
async def get_bus_info():
    global selected_date
    places = {
        "Novogrudok": "c624785",
        "Minsk": "c625144"
    }

    url = f'https://atlasbus.by/api/search?from_id={places["Novogrudok"]}&to_id={places["Minsk"]}&calendar_width=30&date={selected_date}&passengers=1'
    response = requests.get(url)
    
    if response.status_code != 200:
        return None  

    data = response.json().get('rides', [])

    available_rides = [ride for ride in data if ride["freeSeats"] > 0]

    if not available_rides:
        return None  

    message = f"✅ Билеты есть на {selected_date}\n\n🚌 **Доступные рейсы:**\n\n"

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
        await message.answer("Привет! Отправь /bus, чтобы получить информацию о рейсах.\n"
                             "Используй /setdate YYYY-MM-DD для выбора даты.")
    else:
        await message.answer("❌ У вас нет доступа к этому боту.")

# 📅 Обработчик команды /setdate YYYY-MM-DD
@dp.message(Command("setdate"))
async def set_date(message: Message):
    global selected_date
    if message.from_user.id == YOUR_TELEGRAM_ID:
        try:
            new_date = message.text.split()[1]
            selected_date = new_date
            await message.answer(f"✅ Дата изменена на {selected_date}")
        except IndexError:
            await message.answer("❌ Используйте формат: /setdate YYYY-MM-DD")
    else:
        await message.answer("❌ У вас нет доступа к этой команде.")

# 🚍 Обработчик команды /bus
@dp.message(Command("bus"))
async def send_bus_info(message: Message):
    if message.from_user.id == YOUR_TELEGRAM_ID:
        info = await get_bus_info()
        if info:
            await message.answer(info, parse_mode="Markdown")
        else:
            await message.answer("❌ Билетов нет в наличии на выбранную дату.")
    else:
        await message.answer("❌ У вас нет доступа к этому боту.")

# 🚨 Обработчик команды /stop для остановки бота
@dp.message(Command("stop"))
async def stop(message: Message):
    if message.from_user.id == YOUR_TELEGRAM_ID:
        global periodic_task
        if periodic_task is not None:
            periodic_task.cancel()
            periodic_task = None
            await message.answer("✅ Бот остановлен.")
        else:
            await message.answer("❌ Нет активной задачи для остановки.")
    else:
        await message.answer("❌ У вас нет доступа к этой команде.")

# 🕒 Фоновая проверка билетов
async def periodic_request():
    while True:
        await asyncio.sleep(300)  # Проверка каждые 5 минут  
        info = await get_bus_info()
        if info:
            await bot.send_message(YOUR_TELEGRAM_ID, info, parse_mode="Markdown")

# 🚀 Запуск бота
async def main():
    global periodic_task
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск периодической задачи
    periodic_task = asyncio.create_task(periodic_request())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
