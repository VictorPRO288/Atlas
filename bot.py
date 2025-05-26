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

# Города и их ID
places = {
    "Novogrudok": "c624785",
    "Minsk": "c625144"
}

# Даты для маршрутов
date_novogrudok_minsk = "2025-06-01"
date_minsk_novogrudok = "2025-05-31"

# Глобальные переменные для хранения последних отправленных рейсов
last_sent_novogrudok_minsk = None
last_sent_minsk_novogrudok = None

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
    global selected_dates
    if message.from_user.id == YOUR_TELEGRAM_ID:
        try:
            new_dates = message.text.split()[1:]
            if new_dates:
                selected_dates = new_dates
                await message.answer(f"✅ Даты изменены: {', '.join(selected_dates)}")
            else:
                await message.answer("❌ Используйте: /setdate YYYY-MM-DD YYYY-MM-DD ...")
        except IndexError:
            await message.answer("❌ Используйте: /setdate YYYY-MM-DD YYYY-MM-DD ...")
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

# 🗓 Команда /setdates для установки дат в оба направления
@dp.message(Command("setdates"))
async def set_dates(message: Message):
    global date_novogrudok_minsk, date_minsk_novogrudok
    if message.from_user.id == YOUR_TELEGRAM_ID:
        try:
            _, date1, date2 = message.text.split()
            date_novogrudok_minsk = date1
            date_minsk_novogrudok = date2
            await message.answer(f"✅ Дата Новогрудок→Минск: {date1}\n✅ Дата Минск→Новогрудок: {date2}")
        except Exception:
            await message.answer("❌ Используйте: /setdates YYYY-MM-DD YYYY-MM-DD")
    else:
        await message.answer("❌ У вас нет доступа к этой команде.")

# 🚍 Обработчик команды /bus (исправленный)
@dp.message(Command("bus"))
async def send_bus_info(message: Message):
    if message.from_user.id != YOUR_TELEGRAM_ID:
        await message.answer("❌ У вас нет доступа к этому боту.")
        return

    # Новогрудок → Минск
    url1 = f'https://atlasbus.by/api/search?from_id={places["Novogrudok"]}&to_id={places["Minsk"]}&calendar_width=30&date={date_novogrudok_minsk}&passengers=1'
    response1 = requests.get(url1)
    msg1 = ""
    if response1.status_code == 200:
        data1 = response1.json().get('rides', [])
        available1 = [ride for ride in data1 if ride["freeSeats"] > 0]
        if available1:
            msg1 += f"✅ Билеты на {date_novogrudok_minsk} (Новогрудок → Минск):\n\n"
            for ride in available1:
                msg1 += f"🚏 *Маршрут:* {ride['name']}\n💰 *Цена:* {ride['onlinePrice']} BYN\n🎟 *Свободных мест:* {ride['freeSeats']}\n"
                if ride['pickupStops']:
                    stop = ride['pickupStops'][0]
                    msg1 += f"🚦 *Отправление:* {stop['datetime']}\n📍 *Место:* {stop['desc']}\n"
                msg1 += "🚏 *Прибытие:*\n"
                for stop in ride['dischargeStops']:
                    msg1 += f"   🕒 {stop['datetime']} - 📍 {stop['desc']}\n"
                msg1 += "\n" + "#" * 30 + "\n\n"

    # Минск → Новогрудок
    url2 = f'https://atlasbus.by/api/search?from_id={places["Minsk"]}&to_id={places["Novogrudok"]}&calendar_width=30&date={date_minsk_novogrudok}&passengers=1'
    response2 = requests.get(url2)
    msg2 = ""
    if response2.status_code == 200:
        data2 = response2.json().get('rides', [])
        available2 = [ride for ride in data2 if ride["freeSeats"] > 0]
        if available2:
            msg2 += f"✅ Билеты на {date_minsk_novogrudok} (Минск → Новогрудок):\n\n"
            for ride in available2:
                msg2 += f"🚏 *Маршрут:* {ride['name']}\n💰 *Цена:* {ride['onlinePrice']} BYN\n🎟 *Свободных мест:* {ride['freeSeats']}\n"
                if ride['pickupStops']:
                    stop = ride['pickupStops'][0]
                    msg2 += f"🚦 *Отправление:* {stop['datetime']}\n📍 *Место:* {stop['desc']}\n"
                msg2 += "🚏 *Прибытие:*\n"
                for stop in ride['dischargeStops']:
                    msg2 += f"   🕒 {stop['datetime']} - 📍 {stop['desc']}\n"
                msg2 += "\n" + "#" * 30 + "\n\n"

    sent = False
    if msg1:
        for part in split_message(msg1):
            await message.answer(part, parse_mode="Markdown")
        sent = True
    if msg2:
        for part in split_message(msg2):
            await message.answer(part, parse_mode="Markdown")
        sent = True
    if not sent:
        await message.answer("❌ Билетов нет в наличии на выбранные даты и направления.")

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

# Глобальная переменная для хранения последних отправленных рейсов
last_sent_rides = None

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

    # Первый запуск — просто сохраняем состояние, ничего не отправляем
    if last_sent_rides is None:
        last_sent_rides = current_rides
        return None

    # Находим новые рейсы или те, где изменилось количество мест
    new_rides = [ride for ride in available_rides if f"{ride['id']}_{ride['freeSeats']}" not in last_sent_rides]

    # Если изменений нет — ничего не отправляем
    if not new_rides and current_rides == last_sent_rides:
        return None

    # Обновляем список отправленных рейсов
    last_sent_rides = current_rides

    route_name = "Новогрудок → Минск" if selected_route == "novogrudok-minsk" else "Минск → Новогрудок"
    message = f"✅ Обновление по билетам на {selected_date} ({route_name})\n\n🚌 **Доступные рейсы:**\n\n"

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

# 🕒 Фоновая проверка билетов
async def periodic_request():
    global last_sent_novogrudok_minsk, last_sent_minsk_novogrudok
    while True:
        await asyncio.sleep(60)  # Проверка каждую минуту

        # --- Новогрудок → Минск ---
        url1 = f'https://atlasbus.by/api/search?from_id={places["Novogrudok"]}&to_id={places["Minsk"]}&calendar_width=30&date={date_novogrudok_minsk}&passengers=1'
        response1 = requests.get(url1)
        rides1 = []
        if response1.status_code == 200:
            data1 = response1.json().get('rides', [])
            rides1 = [
                (ride['id'], ride['freeSeats'])
                for ride in data1 if ride["freeSeats"] > 0
            ]
        set1 = set(rides1)

        # Первый запуск — просто сохраняем состояние, ничего не отправляем
        if last_sent_novogrudok_minsk is None:
            last_sent_novogrudok_minsk = set1
        elif set1 != last_sent_novogrudok_minsk:
            # Есть изменения — отправляем сообщение
            msg = ""
            if rides1:
                msg += f"✅ Обновление на {date_novogrudok_minsk} (Новогрудок → Минск):\n\n"
                for ride in data1:
                    if ride["freeSeats"] > 0:
                        msg += f"🚏 *Маршрут:* {ride['name']}\n💰 *Цена:* {ride['onlinePrice']} BYN\n🎟 *Свободных мест:* {ride['freeSeats']}\n"
                        if ride['pickupStops']:
                            stop = ride['pickupStops'][0]
                            msg += f"🚦 *Отправление:* {stop['datetime']}\n📍 *Место:* {stop['desc']}\n"
                        msg += "🚏 *Прибытие:*\n"
                        for stop in ride['dischargeStops']:
                            msg += f"   🕒 {stop['datetime']} - 📍 {stop['desc']}\n"
                        msg += "\n" + "#" * 30 + "\n\n"
                for part in split_message(msg):
                    await bot.send_message(YOUR_TELEGRAM_ID, part, parse_mode="Markdown")
            last_sent_novogrudok_minsk = set1

        # --- Минск → Новогрудок ---
        url2 = f'https://atlasbus.by/api/search?from_id={places["Minsk"]}&to_id={places["Novogrudok"]}&calendar_width=30&date={date_minsk_novogrudok}&passengers=1'
        response2 = requests.get(url2)
        rides2 = []
        if response2.status_code == 200:
            data2 = response2.json().get('rides', [])
            rides2 = [
                (ride['id'], ride['freeSeats'])
                for ride in data2 if ride["freeSeats"] > 0
            ]
        set2 = set(rides2)

        if last_sent_minsk_novogrudok is None:
            last_sent_minsk_novogrudok = set2
        elif set2 != last_sent_minsk_novogrudok:
            msg = ""
            if rides2:
                msg += f"✅ Обновление на {date_minsk_novogrudok} (Минск → Новогрудок):\n\n"
                for ride in data2:
                    if ride["freeSeats"] > 0:
                        msg += f"🚏 *Маршрут:* {ride['name']}\n💰 *Цена:* {ride['onlinePrice']} BYN\n🎟 *Свободных мест:* {ride['freeSeats']}\n"
                        if ride['pickupStops']:
                            stop = ride['pickupStops'][0]
                            msg += f"🚦 *Отправление:* {stop['datetime']}\n📍 *Место:* {stop['desc']}\n"
                        msg += "🚏 *Прибытие:*\n"
                        for stop in ride['dischargeStops']:
                            msg += f"   🕒 {stop['datetime']} - 📍 {stop['desc']}\n"
                        msg += "\n" + "#" * 30 + "\n\n"
                for part in split_message(msg):
                    await bot.send_message(YOUR_TELEGRAM_ID, part, parse_mode="Markdown")
            last_sent_minsk_novogrudok = set2

# Запуск фоновой задачи при старте бота
@dp.startup()
async def on_startup(dispatcher):
    asyncio.create_task(periodic_request())

# 🚀 Запуск бота
async def main():
    global periodic_task
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск периодической проверки билетов
    periodic_task = asyncio.create_task(periodic_request())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
