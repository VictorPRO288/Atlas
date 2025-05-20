import requests
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

# 🔹 Ваш токен бота
TOKEN = "7973682034:AAF1hOAXBuWX5ylEjhMcSmDDGeJhnFb26qs"
# 🔹 Ваш Telegram ID
YOUR_TELEGRAM_ID = 978523669  

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Глобальные переменные
periodic_task = None
selected_date = "2025-05-25"  # Дата по умолчанию
selected_route = "novogrudok-minsk"  # Маршрут по умолчанию

# Города и их ID
places = {
    "Novogrudok": "c624785",
    "Minsk": "c625144"
}

# Глобальная переменная для хранения последних отправленных рейсов  
last_sent_rides = set()

# 🔍 Функция получения данных о билетах
async def get_bus_info():
    global selected_date, selected_route

    if selected_route == "novogrudok-minsk":
        from_city, to_city = places["Novogrudok"], places["Minsk"]
    else:
        from_city, to_city = places["Minsk"], places["Novogrudok"]

    url = f'https://atlasbus.by/api/search?from_id={from_city}&to_id={to_city}&calendar_width=30&date={selected_date}&passengers=1'
    response = requests.get(url)
    
    if response.status_code != 200:
        return None  

    data = response.json().get('rides', [])
    available_rides = [ride for ride in data if ride["freeSeats"] > 0]

    if not available_rides:
        return None  

    route_name = "Новогрудок → Минск" if selected_route == "novogrudok-minsk" else "Минск → Новогрудок"
    message = f"✅ Билеты есть на {selected_date} ({route_name})\n\n🚌 **Доступные рейсы:**\n\n"

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

# Модифицированная функция получения новых билетов
async def get_new_bus_info():
    global selected_date, selected_route, last_sent_rides

    if selected_route == "novogrudok-minsk":
        from_city, to_city = places["Novogrudok"], places["Minsk"]
    else:
        from_city, to_city = places["Minsk"], places["Novogrudok"]

    url = f'https://atlasbus.by/api/search?from_id={from_city}&to_id={to_city}&calendar_width=30&date={selected_date}&passengers=1'
    response = requests.get(url)
    
    if response.status_code != 200:
        return None

    data = response.json().get('rides', [])
    available_rides = [ride for ride in data if ride["freeSeats"] > 0]

    # Создаём уникальные идентификаторы для рейсов (например, по id и количеству мест)
    current_rides = set(f"{ride['id']}_{ride['freeSeats']}" for ride in available_rides)

    # Находим новые рейсы или те, где изменилось количество мест
    new_rides = [ride for ride in available_rides if f"{ride['id']}_{ride['freeSeats']}" not in last_sent_rides]

    if not new_rides:
        return None

    # Обновляем список отправленных рейсов
    last_sent_rides = current_rides

    route_name = "Новогрудок → Минск" if selected_route == "novogrudok-minsk" else "Минск → Новогрудок"
    message = f"✅ Новые билеты на {selected_date} ({route_name})\n\n🚌 **Доступные рейсы:**\n\n"

    for ride in new_rides:
        message += f"🚏 *Маршрут:* {ride['name']}\n💰 *Цена:* {ride['onlinePrice']} BYN\n🎟 *Свободных мест:* {ride['freeSeats']}\n"
        if ride['pickupStops']:
            stop = ride['pickupStops'][0]
            message += f"🚦 *Отправление:* {stop['datetime']}\n📍 *Место:* {stop['desc']}\n"
        message += "🚏 *Прибытие:*\n"
        for stop in ride['dischargeStops']:
            message += f"   🕒 {stop['datetime']} - 📍 {stop['desc']}\n"
        message += "\n" + "#" * 30 + "\n\n"

    return message

# 🔹 Функция для разбиения сообщений на части (если они слишком длинные)
def split_message(text, max_length=4000):
    parts = []
    while len(text) > max_length:
        split_index = text[:max_length].rfind("\n")  # Ищем последний перенос строки
        if split_index == -1:
            split_index = max_length
        parts.append(text[:split_index])
        text = text[split_index:]
    parts.append(text)  # Добавляем оставшуюся часть
    return parts

# 📩 Команда /start
@dp.message(Command("start"))
async def start(message: Message):
    if message.from_user.id == YOUR_TELEGRAM_ID:
        await message.answer("Привет! Отправь /bus, чтобы получить информацию о рейсах.\n"
                             "Используй /setdate YYYY-MM-DD для выбора даты.\n"
                             "Используй /setroute novogrudok-minsk или minsk-novogrudok для выбора маршрута.")
    else:
        await message.answer("❌ У вас нет доступа к этому боту.")

# 📅 Команда /setdate YYYY-MM-DD
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

# 🔄 Команда /setroute для смены маршрута
@dp.message(Command("setroute"))
async def set_route(message: Message):
    global selected_route
    if message.from_user.id == YOUR_TELEGRAM_ID:
        try:
            new_route = message.text.split()[1].lower()
            if new_route in ["novogrudok-minsk", "minsk-novogrudok"]:
                selected_route = new_route
                route_name = "Новогрудок → Минск" if new_route == "novogrudok-minsk" else "Минск → Новогрудок"
                await message.answer(f"✅ Маршрут изменён: {route_name}")
            else:
                await message.answer("❌ Неправильный формат. Используйте: /setroute novogrudok-minsk или /setroute minsk-novogrudok")
        except IndexError:
            await message.answer("❌ Используйте: /setroute novogrudok-minsk или /setroute minsk-novogrudok")
    else:
        await message.answer("❌ У вас нет доступа к этой команде.")

# 🚍 Обработчик команды /bus (исправленный)
@dp.message(Command("bus"))
async def send_bus_info(message: Message):
    if message.from_user.id == YOUR_TELEGRAM_ID:
        info = await get_bus_info()
        if info:
            messages = split_message(info)  # Разбиваем сообщение на части
            for part in messages:
                await message.answer(part, parse_mode="Markdown")
        else:
            await message.answer("❌ Билетов нет в наличии на выбранную дату.")
    else:
        await message.answer("❌ У вас нет доступа к этому боту.")
        
# 🚨 Команда /stop для остановки бота
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
        await asyncio.sleep(120)  # Проверка каждые 2 минуты
        info = await get_new_bus_info()
        if info:
            await bot.send_message(YOUR_TELEGRAM_ID, info, parse_mode="Markdown")

# 🚀 Запуск бота
async def main():
    global periodic_task
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск периодической проверки билетов
    periodic_task = asyncio.create_task(periodic_request())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
