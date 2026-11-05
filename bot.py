import os
from dotenv import load_dotenv
import requests
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

load_dotenv()
# 🔹 Ваш токен бота
TOKEN = os.getenv("token") or os.getenv("TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Города и их ID
places = {
    "Novogrudok": "c624785",
    "Minsk": "c625144"
}

# Даты для маршрутов
date_novogrudok_minsk = "2025-11-08"
date_minsk_novogrudok = "2025-11-06"

# Глобальные переменные для хранения последних отправленных рейсов
last_sent_novogrudok_minsk = None
last_sent_minsk_novogrudok = None
# Режим мониторинга маршрутов (True - оба маршрута, False - один маршрут)
monitor_both_routes = False
# Текущий выбранный маршрут для одиночного режима
current_route = "novogrudok-minsk"
# Переменные для состояния
last_chosen_direction = None
periodic_task = None

# Список всех пользователей, которым отправлять уведомления
subscribers = set()

# Клавиатура для выбора направления
choose_direction_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новогрудок → Минск")],
        [KeyboardButton(text="Минск → Новогрудок")]
    ],
    resize_keyboard=True
)

# Клавиатура для подтверждения/отмены
confirm_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Подтвердить"), KeyboardButton(text="Отмена")]
    ],
    resize_keyboard=True
)

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

# 📩 Команда /start (добавляет пользователя в список)
@dp.message(Command("start"))
async def start(message: Message):
    subscribers.add(message.chat.id)
    await message.answer("Привет! Отправь /bus, чтобы получить информацию о рейсах.\n"
                         "Используй /setdate YYYY-MM-DD для выбора даты.\n"
                         "Используй /setroute novogrudok-minsk или minsk-novogrudok для выбора маршрута.")

# 📍 Команда /select_route для выбора маршрута с помощью inline-клавиатуры
@dp.message(Command("select_route"))
async def cmd_select_route(message: Message):
    """Send inline keyboard with route options"""
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="Маршрут 1", callback_data="route_1"),
            types.InlineKeyboardButton(text="Маршрут 2", callback_data="route_2")
        ],
        [
            types.InlineKeyboardButton(text="Оба маршрута", callback_data="both_routes")
        ]
    ])
    
    await message.answer("Пожалуйста, выберите маршрут:", reply_markup=keyboard)

# 📍 Обработчик выбора маршрута
@dp.callback_query(lambda c: c.data in ['route_1', 'route_2', 'both_routes'])
async def process_route_selection(callback_query: types.CallbackQuery):
    """Process route selection"""
    global current_route, monitor_both_routes
    
    if callback_query.data == "route_1":
        current_route = "novogrudok-minsk"  # or "minsk-novogrudok" depending on your preference
        monitor_both_routes = False
        await callback_query.message.edit_text("Вы выбрали: Маршрут 1 (Новогрудок → Минск)")
        await callback_query.answer("Выбран маршрут 1")
        
    elif callback_query.data == "route_2":
        current_route = "minsk-novogrudok"  # or another route of your choice
        monitor_both_routes = False
        await callback_query.message.edit_text("Вы выбрали: Маршрут 2 (Минск → Новогрудок)")
        await callback_query.answer("Выбран маршрут 2")
        
    elif callback_query.data == "both_routes":
        monitor_both_routes = True
        await callback_query.message.edit_text("Вы выбрали: Оба маршрута")
        await callback_query.answer("Выбраны оба маршрута")

# 🗓 Команда /setdates для установки дат в оба направления
@dp.message(Command("setdates"))
async def set_dates(message: Message):
    global date_novogrudok_minsk, date_minsk_novogrudok
    try:
        _, date1, date2 = message.text.split()
        date_novogrudok_minsk = date1
        date_minsk_novogrudok = date2
        await message.answer(f"✅ Дата Новогрудок→Минск: {date1}\n✅ Дата Минск→Новогрудок: {date2}")
    except Exception:
        await message.answer("❌ Используйте: /setdates YYYY-MM-DD YYYY-MM-DD")

# 🚍 Обработчик команды /bus (исправленный)
@dp.message(Command("bus"))
async def send_bus_info(message: Message):
    global current_route, monitor_both_routes
    sent = False

    # Новогрудок → Минск
    if monitor_both_routes or current_route == "novogrudok-minsk":
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
        if msg1:
            for part in split_message(msg1):
                await message.answer(part, parse_mode="Markdown")
            sent = True

    # Минск → Новогрудок
    if monitor_both_routes or current_route == "minsk-novogrudok":
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
        if msg2:
            for part in split_message(msg2):
                await message.answer(part, parse_mode="Markdown")
            sent = True

    if not sent:
        await message.answer("❌ Билетов нет в наличии на выбранные даты и направления.")

# 🚨 Команда /stop для остановки бота
@dp.message(Command("stop"))
async def stop(message: Message):
    global periodic_task
    if periodic_task is not None:
        periodic_task.cancel()
        periodic_task = None
        await message.answer("✅ Бот остановлен.")
    else:
        await message.answer("❌ Нет активной задачи для остановки.")

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

        # Проверяем маршрут Новогрудок → Минск
        if monitor_both_routes or current_route == "novogrudok-minsk":
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

            if last_sent_novogrudok_minsk is None:
                last_sent_novogrudok_minsk = set1
            elif set1 != last_sent_novogrudok_minsk:
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
                        # Отправляем всем подписчикам
                        for chat_id in subscribers:
                            await bot.send_message(chat_id, part, parse_mode="Markdown")
                last_sent_novogrudok_minsk = set1

        # Проверяем маршрут Минск → Новогрудок
        if monitor_both_routes or current_route == "minsk-novogrudok":
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
                        for chat_id in subscribers:
                            await bot.send_message(chat_id, part, parse_mode="Markdown")
                last_sent_minsk_novogrudok = set2

# Запуск фоновой задачи при старте бота
@dp.startup()
async def on_startup(dispatcher):
    asyncio.create_task(periodic_request())

# Команда для вызова меню выбора направления
@dp.message(Command("menu"))
async def menu(message: Message):
    await message.answer(
        "Выберите направление для установки даты:",
        reply_markup=choose_direction_kb
    )

# Обработчик выбора направления
@dp.message(lambda m: m.text in ["Новогрудок → Минск", "Минск → Новогрудок"])
async def ask_date(message: Message):
    global last_chosen_direction
    direction = message.text
    last_chosen_direction = direction
    await message.answer(
        f"Введите дату для направления {direction} в формате YYYY-MM-DD:",
        reply_markup=ReplyKeyboardRemove()
    )
    # Сохраняем выбранное направление в state (если используете FSM) или глобально

# Обработчик ввода даты (пример без FSM)
@dp.message(lambda m: m.text and len(m.text) == 10 and m.text[4] == '-' and m.text[7] == '-')
async def set_direction_date(message: Message):
    # Здесь нужно знать, для какого направления вводится дата (например, через глобальную переменную или FSM)
    # Пример для глобальной переменной:
    global last_chosen_direction, date_novogrudok_minsk, date_minsk_novogrudok
    date = message.text
    if last_chosen_direction == "Новогрудок → Минск":
        date_novogrudok_minsk = date
        await message.answer(f"✅ Дата для Новогрудок → Минск установлена: {date}", reply_markup=confirm_kb)
    elif last_chosen_direction == "Минск → Новогрудок":
        date_minsk_novogrudok = date
        await message.answer(f"✅ Дата для Минск → Новогрудок установлена: {date}", reply_markup=confirm_kb)
    else:
        await message.answer("Сначала выберите направление через /menu", reply_markup=choose_direction_kb)

# Обработчик подтверждения
@dp.message(lambda m: m.text == "Подтвердить")
async def confirm(message: Message):
    await message.answer("Даты успешно сохранены! Для просмотра билетов используйте /bus", reply_markup=ReplyKeyboardRemove())

# Обработчик отмены
@dp.message(lambda m: m.text == "Отмена")
async def cancel(message: Message):
    await message.answer("Операция отменена.", reply_markup=ReplyKeyboardRemove())

# 🚀 Запуск бота
async def main():
    global periodic_task
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск периодической проверки билетов
    periodic_task = asyncio.create_task(periodic_request())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
