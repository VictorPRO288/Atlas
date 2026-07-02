import os
import logging
from dotenv import load_dotenv
import requests
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")

load_dotenv()

TOKEN = os.getenv("token")
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

PLACES = {
    "Минск": "c625144",
    "Новогрудок": "c624785",
}
ROUTES = {
    "minsk-novogrudok": ("Минск", "Новогрудок"),
    "novogrudok-minsk": ("Новогрудок", "Минск"),
}
WEEKDAY = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

subscriptions: dict[int, list[dict]] = {}
prev_rides: dict[str, set] = {}


class SubForm(StatesGroup):
    choosing_route = State()
    choosing_date = State()
    choosing_time = State()


def build_route_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Минск -> Новогрудок", callback_data="sub_route_minsk-novogrudok")],
        [InlineKeyboardButton(text="Новогрудок -> Минск", callback_data="sub_route_novogrudok-minsk")],
    ])


def build_date_kb() -> InlineKeyboardMarkup:
    today = datetime.now()
    buttons = []
    for i in range(7):
        d = today + timedelta(days=i)
        wd = WEEKDAY[d.weekday()]
        label = f"{d.strftime('%d.%m')} ({wd})"
        if i == 0:
            label = "Сегодня, " + label
        elif i == 1:
            label = "Завтра, " + label
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"sub_date_{d.strftime('%Y-%m-%d')}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_date_short(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")


def booking_url(ride: dict, from_id: str, to_id: str, date: str) -> str:
    ride_id = ride.get("rideId", ride.get("id", ""))
    return (
        f"https://atlasbus.by/booking/{ride_id}"
        f"?passengers=1&from={from_id}&to={to_id}&date={date}&pickup=&discharge="
    )


def fetch_rides(from_id: str, to_id: str, date: str) -> list | None:
    url = (
        f"https://atlasbus.by/api/search"
        f"?from_id={from_id}&to_id={to_id}"
        f"&calendar_width=30&date={date}&passengers=1"
    )
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json().get("rides", [])
    except Exception as e:
        log.error(f"fetch_rides error: {e}")
    return None


def filter_rides(rides: list, time_from: str, time_to: str) -> list:
    result = []
    for ride in rides:
        if ride.get("freeSeats", 0) <= 0:
            continue
        if not ride.get("pickupStops"):
            continue
        dep = datetime.fromisoformat(ride["pickupStops"][0]["datetime"]).strftime("%H:%M")
        if time_from <= dep <= time_to:
            result.append(ride)
    return result


def short_ride(ride: dict, from_id: str, to_id: str, date: str) -> str:
    dep = datetime.fromisoformat(ride["pickupStops"][0]["datetime"]).strftime("%H:%M")
    arr = datetime.fromisoformat(ride["arrival"]).strftime("%H:%M")
    seats = ride.get("freeSeats", 0)
    url = booking_url(ride, from_id, to_id, date)
    return f"  {dep} - {arr} | Мест: {seats}\n  [Забронировать]({url})"


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    log.info(f"/start from {message.from_user.id}")
    await state.clear()
    await message.answer(
        "Привет!\n\n"
        "Я помогу отслеживать свободные места на автобусы AtlasBus.\n\n"
        "/subscribe - создать подписку\n"
        "/unsubscribe - отменить подписки\n"
        "/list - мои подписки"
    )


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message, state: FSMContext):
    log.info(f"/subscribe from {message.from_user.id}")
    await state.clear()
    await state.set_state(SubForm.choosing_route)
    await message.answer("Выберите маршрут:", reply_markup=build_route_kb())


@dp.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, state: FSMContext):
    log.info(f"/unsubscribe from {message.from_user.id}")
    await state.clear()
    user_id = message.from_user.id
    subs = subscriptions.get(user_id, [])
    if not subs:
        await message.answer("У вас нет подписок.")
        return
    buttons = []
    for i, sub in enumerate(subs):
        date_short = format_date_short(sub["date"])
        label = f"{sub['from_city']} -> {sub['to_city']}  {date_short}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"unsub_{i}")])
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="unsub_cancel")])
    await message.answer("Выберите подписку для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.message(Command("list"))
async def cmd_list(message: Message, state: FSMContext):
    log.info(f"/list from {message.from_user.id}")
    await state.clear()
    user_id = message.from_user.id
    subs = subscriptions.get(user_id, [])
    if not subs:
        await message.answer("У вас нет подписок.")
        return
    lines = ["Ваши подписки:\n"]
    for i, sub in enumerate(subs, 1):
        date_short = format_date_short(sub["date"])
        time_range = sub["time_from"] if sub["time_from"] == sub["time_to"] else f"{sub['time_from']}-{sub['time_to']}"
        lines.append(f"{i}. {sub['from_city']} -> {sub['to_city']}")
        lines.append(f"   {date_short}  {time_range}")
    await message.answer("\n".join(lines))


@dp.callback_query(F.data.startswith("sub_route_"))
async def cb_sub_route(callback: types.CallbackQuery, state: FSMContext):
    route = callback.data.replace("sub_route_", "")
    log.info(f"route chosen: {route} by {callback.from_user.id}")
    await state.update_data(route=route)
    await state.set_state(SubForm.choosing_date)
    await callback.message.edit_text("Выберите дату:", reply_markup=build_date_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("sub_date_"))
async def cb_sub_date(callback: types.CallbackQuery, state: FSMContext):
    date_str = callback.data.replace("sub_date_", "")
    log.info(f"date chosen: {date_str} by {callback.from_user.id}")
    await state.update_data(date=date_str)
    await state.set_state(SubForm.choosing_time)
    await callback.message.edit_text(
        "Введите время (ЧЧ:ММ или ЧЧ:ММ-ЧЧ:ММ)\n"
        "Например: 14:00 или 14:00-18:00"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("unsub_"))
async def cb_unsubscribe(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    data = callback.data.replace("unsub_", "")
    if data == "cancel":
        await callback.message.edit_text("Отмена.")
        await callback.answer()
        return
    try:
        idx = int(data)
    except ValueError:
        await callback.answer()
        return
    subs = subscriptions.get(user_id, [])
    if 0 <= idx < len(subs):
        removed = subs.pop(idx)
        date_short = format_date_short(removed["date"])
        await callback.message.edit_text(
            f"Удалена подписка:\n  {removed['from_city']} -> {removed['to_city']}  {date_short}"
        )
    else:
        await callback.message.edit_text("Подписка не найдена.")
    await callback.answer()


@dp.message(SubForm.choosing_time)
async def cb_sub_time(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""
    log.info(f"time input: '{text}' from {user_id}, state=choosing_time")

    if not text:
        await message.answer("Введите время текстом (ЧЧ:ММ или ЧЧ:ММ-ЧЧ:ММ):")
        return

    time_from, time_to = "00:00", "23:59"
    if "-" in text:
        parts = text.split("-")
        if len(parts) == 2:
            time_from, time_to = parts[0].strip(), parts[1].strip()
    else:
        time_from = text
        time_to = text

    for t in (time_from, time_to):
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError:
            await message.answer("Неверный формат. Введите ЧЧ:ММ или ЧЧ:ММ-ЧЧ:ММ:")
            return

    data = await state.get_data()
    log.info(f"state data: {data}")

    route = data.get("route")
    date = data.get("date")
    if not route or not date:
        await message.answer("Ошибка состояния. Начните заново: /subscribe")
        await state.clear()
        return

    from_city, to_city = ROUTES[route]
    from_id, to_id = PLACES[from_city], PLACES[to_city]

    if user_id not in subscriptions:
        subscriptions[user_id] = []

    sub = {
        "route": route,
        "date": date,
        "time_from": time_from,
        "time_to": time_to,
        "from_id": from_id,
        "to_id": to_id,
        "from_city": from_city,
        "to_city": to_city,
    }
    subscriptions[user_id].append(sub)
    log.info(f"subscription created: {sub}")

    date_short = format_date_short(date)
    time_range = time_from if time_from == time_to else f"{time_from}-{time_to}"
    await state.clear()

    await message.answer(
        f"Подписка создана!\n\n"
        f"  {from_city} -> {to_city}\n"
        f"  {date_short} с {time_range}\n\n"
        "Поиск рейсов..."
    )

    rides = fetch_rides(from_id, to_id, date)
    if rides is None:
        await message.answer("Ошибка сервера. Попробуйте позже.")
        return

    filtered = filter_rides(rides, time_from, time_to)
    if filtered:
        lines = [f"  {from_city} -> {to_city}", f"  {date_short}"]
        for ride in filtered:
            lines.append(short_ride(ride, from_id, to_id, date))
        await message.answer("\n".join(lines), parse_mode="Markdown")
    else:
        await message.answer("Свободных рейсов пока нет. Уведомлю, когда появятся.")

    key = f"{user_id}:{route}:{date}:{time_from}:{time_to}"
    prev_rides[key] = set(
        f"{r.get('rideId', r.get('id'))}_{r['freeSeats']}" for r in filtered
    )


async def monitor():
    log.info("Monitor started")
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        for user_id, subs in list(subscriptions.items()):
            for sub in list(subs):
                sub_date = datetime.strptime(sub["date"], "%Y-%m-%d")
                if sub_date.date() < now.date():
                    old_date = sub["date"]
                    sub["date"] = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                    log.info(f"date auto-updated: {old_date} -> {sub['date']} for user {user_id}")

                rides = fetch_rides(sub["from_id"], sub["to_id"], sub["date"])
                if rides is None:
                    continue

                filtered = filter_rides(rides, sub["time_from"], sub["time_to"])
                key = f"{user_id}:{sub['route']}:{sub['date']}:{sub['time_from']}:{sub['time_to']}"
                current = set(
                    f"{r.get('rideId', r.get('id'))}_{r['freeSeats']}" for r in filtered
                )

                old = prev_rides.get(key)
                if old is None:
                    prev_rides[key] = current
                    continue

                if current == old:
                    continue

                new_rides = [
                    r for r in filtered
                    if f"{r.get('rideId', r.get('id'))}_{r['freeSeats']}" not in old
                ]

                if not new_rides:
                    prev_rides[key] = current
                    continue

                date_short = format_date_short(sub["date"])
                time_range = sub["time_from"] if sub["time_from"] == sub["time_to"] else f"{sub['time_from']}-{sub['time_to']}"

                lines = [
                    "Изменения!",
                    f"  {sub['from_city']} -> {sub['to_city']}",
                    f"  {date_short} ({time_range})\n",
                ]
                for ride in new_rides:
                    dep = datetime.fromisoformat(ride["pickupStops"][0]["datetime"]).strftime("%H:%M")
                    arr = datetime.fromisoformat(ride["arrival"]).strftime("%H:%M")
                    seats = ride.get("freeSeats", 0)
                    url = booking_url(ride, sub["from_id"], sub["to_id"], sub["date"])
                    lines.append("Появился рейс:")
                    lines.append(f"  {dep} - {arr} | Мест: {seats}")
                    lines.append(f"  [Забронировать]({url})")
                    lines.append("")

                prev_rides[key] = current
                log.info(f"notification sent to {user_id}: {len(new_rides)} new rides")

                try:
                    await bot.send_message(user_id, "\n".join(lines), parse_mode="Markdown")
                except Exception as e:
                    log.error(f"send_message failed: {e}")


@dp.startup()
async def on_startup(dispatcher):
    asyncio.create_task(monitor())


async def main():
    log.info("Bot starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Polling started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
