import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
import requests
import asyncio
from datetime import datetime, timedelta
from aiohttp import web
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PORT = int(os.getenv("PORT", "8080"))
DATA_FILE = Path("subscriptions.json")

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

PLACES = {
    "\u041c\u0438\u043d\u0441\u043a": "c625144",
    "\u041d\u043e\u0432\u043e\u0433\u0440\u0443\u0434\u043e\u043a": "c624785",
}
ROUTES = {
    "minsk-novogrudok": ("\u041c\u0438\u043d\u0441\u043a", "\u041d\u043e\u0432\u043e\u0433\u0440\u0443\u0434\u043e\u043a"),
    "novogrudok-minsk": ("\u041d\u043e\u0432\u043e\u0433\u0440\u0443\u0434\u043e\u043a", "\u041c\u0438\u043d\u0441\u043a"),
}
WEEKDAY = ["\u041f\u043d", "\u0412\u0442", "\u0421\u0440", "\u0427\u0442", "\u041f\u0442", "\u0421\u0431", "\u0412\u0441"]

subscriptions: dict[int, list[dict]] = {}
prev_rides: dict[str, set] = {}


# --- Persistence ---

def load_subscriptions():
    if DATA_FILE.exists():
        try:
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            for uid_str, subs in raw.items():
                subscriptions[int(uid_str)] = subs
            log.info(f"Loaded {sum(len(s) for s in subscriptions.values())} subscriptions for {len(subscriptions)} users")
        except Exception as e:
            log.error(f"Failed to load subscriptions: {e}")


def save_subscriptions():
    try:
        raw = {str(uid): subs for uid, subs in subscriptions.items()}
        DATA_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to save subscriptions: {e}")


# --- FSM ---

class SubForm(StatesGroup):
    choosing_route = State()
    choosing_date = State()
    choosing_time = State()


# --- Keyboards ---

def build_route_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u041c\u0438\u043d\u0441\u043a -> \u041d\u043e\u0432\u043e\u0433\u0440\u0443\u0434\u043e\u043a", callback_data="sub_route_minsk-novogrudok")],
        [InlineKeyboardButton(text="\u041d\u043e\u0432\u043e\u0433\u0440\u0443\u0434\u043e\u043a -> \u041c\u0438\u043d\u0441\u043a", callback_data="sub_route_novogrudok-minsk")],
    ])


def build_date_kb() -> InlineKeyboardMarkup:
    today = datetime.now()
    buttons = []
    for i in range(7):
        d = today + timedelta(days=i)
        wd = WEEKDAY[d.weekday()]
        label = f"{d.strftime('%d.%m')} ({wd})"
        if i == 0:
            label = "\u0421\u0435\u0433\u043e\u0434\u043d\u044f, " + label
        elif i == 1:
            label = "\u0417\u0430\u0432\u0442\u0440\u0430, " + label
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"sub_date_{d.strftime('%Y-%m-%d')}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- Helpers ---

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
    return f"  {dep} - {arr} | \u041c\u0435\u0441\u0442: {seats}\n  [\u0417\u0430\u0431\u0440\u043e\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c]({url})"


# --- Commands ---

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    log.info(f"/start from {message.from_user.id}")
    await state.clear()
    await message.answer(
        "\u041f\u0440\u0438\u0432\u0435\u0442!\n\n"
        "\u042f \u043f\u043e\u043c\u043e\u0433\u0443 \u043e\u0442\u0441\u043b\u0435\u0436\u0438\u0432\u0430\u0442\u044c \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0435 \u043c\u0435\u0441\u0442\u0430 \u043d\u0430 \u0430\u0432\u0442\u043e\u0431\u0443\u0441\u044b AtlasBus.\n\n"
        "/subscribe - \u0441\u043e\u0437\u0434\u0430\u0442\u044c \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0443\n"
        "/unsubscribe - \u043e\u0442\u043c\u0435\u043d\u0438\u0442\u044c \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0438\n"
        "/list - \u043c\u043e\u0438 \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0438"
    )


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message, state: FSMContext):
    log.info(f"/subscribe from {message.from_user.id}")
    await state.clear()
    await state.set_state(SubForm.choosing_route)
    await message.answer("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043c\u0430\u0440\u0448\u0440\u0443\u0442:", reply_markup=build_route_kb())


@dp.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, state: FSMContext):
    log.info(f"/unsubscribe from {message.from_user.id}")
    await state.clear()
    user_id = message.from_user.id
    subs = subscriptions.get(user_id, [])
    if not subs:
        await message.answer("\u0423 \u0432\u0430\u0441 \u043d\u0435\u0442 \u043f\u043e\u0434\u043f\u0438\u0441\u043e\u043a.")
        return
    buttons = []
    for i, sub in enumerate(subs):
        date_short = format_date_short(sub["date"])
        label = f"{sub['from_city']} -> {sub['to_city']}  {date_short}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"unsub_{i}")])
    buttons.append([InlineKeyboardButton(text="\u041e\u0442\u043c\u0435\u043d\u0430", callback_data="unsub_cancel")])
    await message.answer("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0443 \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.message(Command("list"))
async def cmd_list(message: Message, state: FSMContext):
    log.info(f"/list from {message.from_user.id}")
    await state.clear()
    user_id = message.from_user.id
    subs = subscriptions.get(user_id, [])
    if not subs:
        await message.answer("\u0423 \u0432\u0430\u0441 \u043d\u0435\u0442 \u043f\u043e\u0434\u043f\u0438\u0441\u043e\u043a.")
        return
    lines = ["\u0412\u0430\u0448\u0438 \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0438:\n"]
    for i, sub in enumerate(subs, 1):
        date_short = format_date_short(sub["date"])
        time_range = sub["time_from"] if sub["time_from"] == sub["time_to"] else f"{sub['time_from']}-{sub['time_to']}"
        lines.append(f"{i}. {sub['from_city']} -> {sub['to_city']}")
        lines.append(f"   {date_short}  {time_range}")
    await message.answer("\n".join(lines))


# --- Callbacks ---

@dp.callback_query(F.data.startswith("sub_route_"))
async def cb_sub_route(callback: types.CallbackQuery, state: FSMContext):
    route = callback.data.replace("sub_route_", "")
    log.info(f"route chosen: {route} by {callback.from_user.id}")
    await state.update_data(route=route)
    await state.set_state(SubForm.choosing_date)
    await callback.message.edit_text("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0430\u0442\u0443:", reply_markup=build_date_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("sub_date_"))
async def cb_sub_date(callback: types.CallbackQuery, state: FSMContext):
    date_str = callback.data.replace("sub_date_", "")
    log.info(f"date chosen: {date_str} by {callback.from_user.id}")
    await state.update_data(date=date_str)
    await state.set_state(SubForm.choosing_time)
    await callback.message.edit_text(
        "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0432\u0440\u0435\u043c\u044f (\u0427\u0427:\u041c\u041c \u0438\u043b\u0438 \u0427\u0427:\u041c\u041c-\u0427\u0427:\u041c\u041c)\n"
        "\u041d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: 14:00 \u0438\u043b\u0438 14:00-18:00"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("unsub_"))
async def cb_unsubscribe(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    data = callback.data.replace("unsub_", "")
    if data == "cancel":
        await callback.message.edit_text("\u041e\u0442\u043c\u0435\u043d\u0430.")
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
        save_subscriptions()
        date_short = format_date_short(removed["date"])
        await callback.message.edit_text(
            f"\u0423\u0434\u0430\u043b\u0435\u043d\u0430 \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0430:\n  {removed['from_city']} -> {removed['to_city']}  {date_short}"
        )
    else:
        await callback.message.edit_text("\u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430.")
    await callback.answer()


# --- FSM: time input ---

@dp.message(SubForm.choosing_time)
async def cb_sub_time(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""
    log.info(f"time input: '{text}' from {user_id}, state=choosing_time")

    if not text:
        await message.answer("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0432\u0440\u0435\u043c\u044f \u0442\u0435\u043a\u0441\u0442\u043e\u043c (\u0427\u0427:\u041c\u041c \u0438\u043b\u0438 \u0427\u0427:\u041c\u041c-\u0427\u0427:\u041c\u041c):")
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
            await message.answer("\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442. \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0427\u0427:\u041c\u041c \u0438\u043b\u0438 \u0427\u0427:\u041c\u041c-\u0427\u0427:\u041c\u041c:")
            return

    data = await state.get_data()
    log.info(f"state data: {data}")

    route = data.get("route")
    date = data.get("date")
    if not route or not date:
        await message.answer("\u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f. \u041d\u0430\u0447\u043d\u0438\u0442\u0435 \u0437\u0430\u043d\u043e\u0432\u043e: /subscribe")
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
    save_subscriptions()
    log.info(f"subscription created: {sub}")

    date_short = format_date_short(date)
    time_range = time_from if time_from == time_to else f"{time_from}-{time_to}"
    await state.clear()

    await message.answer(
        f"\u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0430!\n\n"
        f"  {from_city} -> {to_city}\n"
        f"  {date_short} \u0441 {time_range}\n\n"
        "\u041f\u043e\u0438\u0441\u043a \u0440\u0435\u0439\u0441\u043e\u0432..."
    )

    rides = fetch_rides(from_id, to_id, date)
    if rides is None:
        await message.answer("\u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.")
        return

    filtered = filter_rides(rides, time_from, time_to)
    if filtered:
        lines = [f"  {from_city} -> {to_city}", f"  {date_short}"]
        for ride in filtered:
            lines.append(short_ride(ride, from_id, to_id, date))
        await message.answer("\n".join(lines), parse_mode="Markdown")
    else:
        await message.answer("\u0421\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0445 \u0440\u0435\u0439\u0441\u043e\u0432 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442. \u0423\u0432\u0435\u0434\u043e\u043c\u043b\u044e, \u043a\u043e\u0433\u0434\u0430 \u043f\u043e\u044f\u0432\u044f\u0442\u0441\u044f.")

    key = f"{user_id}:{route}:{date}:{time_from}:{time_to}"
    prev_rides[key] = set(
        f"{r.get('rideId', r.get('id'))}_{r['freeSeats']}" for r in filtered
    )


# --- Monitor ---

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
                    save_subscriptions()
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
                    "\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f!",
                    f"  {sub['from_city']} -> {sub['to_city']}",
                    f"  {date_short} ({time_range})\n",
                ]
                for ride in new_rides:
                    dep = datetime.fromisoformat(ride["pickupStops"][0]["datetime"]).strftime("%H:%M")
                    arr = datetime.fromisoformat(ride["arrival"]).strftime("%H:%M")
                    seats = ride.get("freeSeats", 0)
                    url = booking_url(ride, sub["from_id"], sub["to_id"], sub["date"])
                    lines.append("\u041f\u043e\u044f\u0432\u0438\u043b\u0441\u044f \u0440\u0435\u0439\u0441:")
                    lines.append(f"  {dep} - {arr} | \u041c\u0435\u0441\u0442: {seats}")
                    lines.append(f"  [\u0417\u0430\u0431\u0440\u043e\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c]({url})")
                    lines.append("")

                prev_rides[key] = current
                log.info(f"notification sent to {user_id}: {len(new_rides)} new rides")

                try:
                    await bot.send_message(user_id, "\n".join(lines), parse_mode="Markdown")
                except Exception as e:
                    log.error(f"send_message failed: {e}")


# --- Web server (healthcheck + webhook) ---

async def handle_health(request):
    return web.Response(text="OK")


async def on_startup(app):
    load_subscriptions()
    if WEBHOOK_URL:
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
        log.info(f"Webhook set: {WEBHOOK_URL}/webhook")
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("No WEBHOOK_URL, webhook deleted")
    asyncio.create_task(monitor())


async def on_cleanup(app):
    await bot.session.close()


def create_app():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/webhook", dp.webhook_handler)
    return app


async def main():
    log.info("Bot starting...")
    if WEBHOOK_URL:
        app = create_app()
        log.info(f"Starting webhook server on port {PORT}")
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        load_subscriptions()
        asyncio.create_task(monitor())
        log.info("Starting polling")
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
