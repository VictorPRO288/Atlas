import os
import json
import re
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.backoff import BackoffConfig

# ─── Configuration & Logging ──────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("atlas_bot")

load_dotenv()

TOKEN = os.getenv("TOKEN") or os.getenv("token") or os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("Telegram Bot TOKEN is not set! Please specify TOKEN in environment variables.")

PORT = int(os.getenv("PORT", "10000"))
DATA_FILE = Path("subscriptions.json")

# Часовой пояс Минска (UTC+3)
MSK = timezone(timedelta(hours=3))

def get_now() -> datetime:
    return datetime.now(MSK)

# Маршруты и пункты назначения
ROUTES = {
    "minsk-novogrudok": {
        "title": "Минск ➔ Новогрудок",
        "short_title": "Минск → Нов.",
        "from_name": "Минск",
        "to_name": "Новогрудок",
        "from_id": "c625144",
        "to_id": "c624785",
    },
    "novogrudok-minsk": {
        "title": "Новогрудок ➔ Минск",
        "short_title": "Нов. → Минск",
        "from_name": "Новогрудок",
        "to_name": "Минск",
        "from_id": "c624785",
        "to_id": "c625144",
    },
}

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# ─── Persistence (Подписки) ───────────────────────────────────────────────────

# Структура: { user_id (int): [ { "route": ..., "date": ..., "time_from": ..., "time_to": ..., ... } ] }
subscriptions: dict[int, list[dict]] = {}
# Кэш состояния свободных мест для мониторинга: { sub_key: set("rideId_freeSeats") }
prev_rides_state: dict[str, set[str]] = {}

def load_subscriptions():
    global subscriptions
    if DATA_FILE.exists():
        try:
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            subscriptions = {int(uid): subs for uid, subs in raw.items()}
            total_subs = sum(len(s) for s in subscriptions.values())
            log.info(f"Loaded {total_subs} subscriptions for {len(subscriptions)} users")
        except Exception as e:
            log.error(f"Error loading subscriptions.json: {e}")
            subscriptions = {}

def save_subscriptions():
    try:
        raw = {str(uid): subs for uid, subs in subscriptions.items() if subs}
        DATA_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Error saving subscriptions.json: {e}")

# ─── FSM States ───────────────────────────────────────────────────────────────

class BotFlow(StatesGroup):
    choosing_route = State()
    choosing_date = State()
    waiting_custom_date = State()
    choosing_time = State()
    waiting_custom_time = State()

# ─── Date & Time Parsers ──────────────────────────────────────────────────────

def format_date_display(date_str: str) -> str:
    """Форматирует '2026-11-05' в '05.11 (Чт)'"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        wd = WEEKDAYS_RU[dt.weekday()]
        return f"{dt.strftime('%d.%m')} ({wd})"
    except Exception:
        return date_str

def parse_user_date(text: str) -> str | None:
    """Парсит введенную пользователем дату (ДД.ММ, ДД.ММ.ГГГГ, ГГГГ-ММ-ДД)"""
    text = text.strip()
    now = get_now()
    
    # 2026-11-07
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        try:
            dt = datetime.strptime(text, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 07.11.2026
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dt = datetime(year, month, day)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 07.11 или 7.11
    m = re.match(r"^(\d{1,2})\.(\d{1,2})$", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = now.year
        try:
            dt = datetime(year, month, day)
            # Если месяц уже прошел в этом году, относим к следующему году
            if dt.date() < now.date() and month < now.month:
                dt = datetime(year + 1, month, day)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None

def parse_time_range(text: str) -> tuple[str, str] | None:
    """Парсит диапазон времени (14:00-18:00, 14-18, после 16:00, до 12:00, 15:30)"""
    text = text.strip().lower()
    
    # 14:00-18:00 или 14-18
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*[-–—]\s*(\d{1,2})(?::(\d{2}))?$", text)
    if m:
        h1, m1 = int(m.group(1)), int(m.group(2) or 0)
        h2, m2 = int(m.group(3)), int(m.group(4) or 0)
        if 0 <= h1 <= 23 and 0 <= m1 <= 59 and 0 <= h2 <= 23 and 0 <= m2 <= 59:
            return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"

    # после 16:00 или с 16
    m = re.match(r"^(?:после|с)\s*(\d{1,2})(?::(\d{2}))?$", text)
    if m:
        h, mn = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{h:02d}:{mn:02d}", "23:59"

    # до 18:00
    m = re.match(r"^до\s*(\d{1,2})(?::(\d{2}))?$", text)
    if m:
        h, mn = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return "00:00", f"{h:02d}:{mn:02d}"

    # одиночное время 15:00
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?$", text)
    if m:
        h, mn = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{h:02d}:{mn:02d}", "23:59"

    return None

# ─── API Helper ───────────────────────────────────────────────────────────────

async def fetch_rides(session: aiohttp.ClientSession, from_id: str, to_id: str, date: str) -> list | None:
    """Асинхронный запрос к API AtlasBus с полными заголовками браузера и устойчивостью к сбоям"""
    url = (
        f"https://atlasbus.by/api/search"
        f"?from_id={from_id}&to_id={to_id}"
        f"&calendar_width=30&date={date}&passengers=1"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://atlasbus.by/",
        "Origin": "https://atlasbus.by",
    }
    timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
    try:
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("rides", [])
            elif resp.status in (400, 429, 502, 503):
                err_text = await resp.text()
                log.warning(f"AtlasBus API returned HTTP {resp.status} for {date}: {err_text[:120]}")
            else:
                log.warning(f"AtlasBus API returned HTTP {resp.status} for {date}")
    except asyncio.TimeoutError:
        log.warning(f"AtlasBus API timeout for {date} (>15s)")
    except aiohttp.ClientError as e:
        log.warning(f"AtlasBus API network error for {date}: {type(e).__name__} {e}")
    except Exception as e:
        log.error(f"Error fetching rides for {date}: {type(e).__name__} {e}")
    return None

def filter_rides_by_time(rides: list, time_from: str, time_to: str) -> list:
    """Фильтрует рейсы по времени отправления первой остановки и наличию свободных мест"""
    result = []
    for r in rides:
        if r.get("freeSeats", 0) <= 0:
            continue
        if not r.get("pickupStops"):
            continue
        try:
            dep_time = datetime.fromisoformat(r["pickupStops"][0]["datetime"]).strftime("%H:%M")
            if time_from <= dep_time <= time_to:
                result.append(r)
        except Exception:
            continue
    return result

def format_ride_card(ride: dict, from_id: str, to_id: str, date: str) -> str:
    """Красивое форматирование одного рейса в Markdown"""
    pickup_time = "??:??"
    pickup_desc = "Остановка отправления"
    if ride.get("pickupStops"):
        p = ride["pickupStops"][0]
        pickup_time = datetime.fromisoformat(p["datetime"]).strftime("%H:%M")
        pickup_desc = p.get("desc", pickup_desc)

    arrival_time = "??:??"
    discharge_desc = "Остановка прибытия"
    if ride.get("dischargeStops"):
        d = ride["dischargeStops"][0]
        arrival_time = datetime.fromisoformat(d["datetime"]).strftime("%H:%M")
        discharge_desc = d.get("desc", discharge_desc)
    elif ride.get("arrival"):
        arrival_time = datetime.fromisoformat(ride["arrival"]).strftime("%H:%M")

    seats = ride.get("freeSeats", 0)
    price = ride.get("onlinePrice", ride.get("price", 22))
    currency = ride.get("currency", "BYN")
    
    bus_name = ""
    if ride.get("bus"):
        b = ride["bus"]
        bus_name = f" _{b.get('mark', '')} {b.get('model', '')}_".strip()

    ride_id = ride.get("rideId") or ride.get("id", "")
    book_url = f"https://atlasbus.by/booking/{ride_id}?passengers=1&from={from_id}&to={to_id}&date={date}&pickup=&discharge="

    seats_text = f"🔥 *{seats} мест*" if seats > 1 else "⚡️ *1 МЕСТО!*"
    return (
        f"🕒 *{pickup_time} ➔ {arrival_time}*{bus_name}\n"
        f"📍 Посадка: {pickup_desc}\n"
        f"🏁 Высадка: {discharge_desc}\n"
        f"🎟 Мест: {seats_text} | 💰 *{price} {currency}*\n"
        f"👉 [Забронировать на AtlasBus]({book_url})\n"
    )

# ─── Inline Keyboards ─────────────────────────────────────────────────────────

def build_main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Найти билеты сейчас", callback_data="act:search")],
        [InlineKeyboardButton(text="🔔 Отслеживать свободные места", callback_data="act:sub")],
        [InlineKeyboardButton(text="📋 Мои активные подписки", callback_data="act:my_subs")],
        [InlineKeyboardButton(text="ℹ️ Справка и помощь", callback_data="act:help")],
    ])

def build_route_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🚌 Минск ➔ Новогрудок", callback_data="set_route:minsk-novogrudok")],
        [InlineKeyboardButton(text="🚌 Новогрудок ➔ Минск", callback_data="set_route:novogrudok-minsk")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="nav:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_date_kb() -> InlineKeyboardMarkup:
    """Генерирует аккуратную сетку дат на 8 дней вперед, начиная с сегодняшнего дня"""
    now = get_now()
    rows = []
    
    # 1 ряд: Сегодня и Завтра
    row_today = []
    for i in range(2):
        d = now + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        wd = WEEKDAYS_RU[d.weekday()]
        label = f"Сегодня ({d.strftime('%d.%m')})" if i == 0 else f"Завтра ({d.strftime('%d.%m')})"
        row_today.append(InlineKeyboardButton(text=label, callback_data=f"set_date:{d_str}"))
    rows.append(row_today)

    # Следующие 6 дней по 2 кнопки в ряд
    current_pair = []
    for i in range(2, 8):
        d = now + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        wd = WEEKDAYS_RU[d.weekday()]
        label = f"{d.strftime('%d.%m')} ({wd})"
        current_pair.append(InlineKeyboardButton(text=label, callback_data=f"set_date:{d_str}"))
        if len(current_pair) == 2:
            rows.append(current_pair)
            current_pair = []
    if current_pair:
        rows.append(current_pair)

    # Кнопки ввода вручную и навигации
    rows.append([InlineKeyboardButton(text="📅 Ввести другую дату вручную", callback_data="set_date:custom")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к выбору маршрута", callback_data="nav:route")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_time_kb() -> InlineKeyboardMarkup:
    """Оптимизированное меню выбора времени"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ Весь день (любое время)", callback_data="set_time:00:00-23:59")],
        [
            InlineKeyboardButton(text="🌅 Утро (06:00 – 12:00)", callback_data="set_time:06:00-12:00"),
            InlineKeyboardButton(text="☀️ День (12:00 – 18:00)", callback_data="set_time:12:00-18:00"),
        ],
        [InlineKeyboardButton(text="🌙 Вечер (18:00 – 23:59)", callback_data="set_time:18:00-23:59")],
        [InlineKeyboardButton(text="✍️ Ввести свой интервал времени", callback_data="set_time:custom")],
        [InlineKeyboardButton(text="⬅️ Назад к выбору даты", callback_data="nav:date")],
    ])

def build_my_subs_kb(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура со списком подписок пользователя и кнопками удаления"""
    user_subs = subscriptions.get(user_id, [])
    buttons = []
    for idx, sub in enumerate(user_subs, 1):
        r_info = ROUTES.get(sub["route"], {})
        s_title = r_info.get("short_title", sub["route"])
        d_str = format_date_display(sub["date"])
        t_str = "весь день" if sub["time_from"] == "00:00" and sub["time_to"] == "23:59" else f"{sub['time_from']}-{sub['time_to']}"
        label = f"❌ Удалить #{idx}: {s_title} ({d_str}, {t_str})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"del_sub:{idx-1}")])
    
    if user_subs:
        buttons.append([InlineKeyboardButton(text="🗑 Удалить ВСЕ подписки", callback_data="del_all_subs")])
    buttons.append([InlineKeyboardButton(text="➕ Создать новую подписку", callback_data="act:sub")])
    buttons.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="nav:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ─── Bot Initialization ───────────────────────────────────────────────────────

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ─── Command Handlers ─────────────────────────────────────────────────────────

@dp.message(Command("start", "menu"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "👋 *Добро пожаловать в Atlas Ticket Bot!*\n\n"
        "Я помогу найти билеты на рейсы *AtlasBus* между Минском и Новогрудком, "
        "а также автоматически оповещу вас, как только освободится место.\n\n"
        "Выберите нужное действие ниже:"
    )
    await message.answer(text, reply_markup=build_main_menu_kb(), parse_mode="Markdown")

@dp.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(action="search")
    await state.set_state(BotFlow.choosing_route)
    await message.answer("🔍 *Поиск билетов*\n\nВыберите направление:", reply_markup=build_route_kb(), parse_mode="Markdown")

@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(action="subscribe")
    await state.set_state(BotFlow.choosing_route)
    await message.answer("🔔 *Отслеживание мест*\n\nВыберите направление:", reply_markup=build_route_kb(), parse_mode="Markdown")

@dp.message(Command("subs", "list"))
async def cmd_subs(message: Message, state: FSMContext):
    await state.clear()
    await show_user_subscriptions(message.from_user.id, message)

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    help_text = (
        "ℹ️ *Справка по боту AtlasBus*\n\n"
        "📌 *Команды:*\n"
        "• /menu — Главное меню\n"
        "• /search — Быстрый поиск свободных мест\n"
        "• /subscribe — Включить фоновое отслеживание билетов\n"
        "• /subs — Список активных подписок и их отмена\n\n"
        "💡 *Как работает отслеживание?*\n"
        "Бот каждые 30 секунд проверяет сайт AtlasBus. Как только кто-то сдает билет "
        "или перевозчик открывает дополнительный рейс на выбранную дату и время, "
        "бот сразу присылает вам уведомление с кнопкой бронирования!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Главное меню", callback_data="nav:main")]])
    await message.answer(help_text, reply_markup=kb, parse_mode="Markdown")

# ─── Navigation & Flow Callbacks ──────────────────────────────────────────────

@dp.callback_query(F.data.startswith("act:"))
async def handle_action(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    
    if action == "search":
        await state.clear()
        await state.update_data(action="search")
        await state.set_state(BotFlow.choosing_route)
        await callback.message.edit_text(
            "🔍 *Поиск билетов*\n\nВыберите направление поездки:",
            reply_markup=build_route_kb(),
            parse_mode="Markdown",
        )
    elif action == "sub":
        await state.clear()
        await state.update_data(action="subscribe")
        await state.set_state(BotFlow.choosing_route)
        await callback.message.edit_text(
            "🔔 *Отслеживание мест*\n\nВыберите направление поездки:",
            reply_markup=build_route_kb(),
            parse_mode="Markdown",
        )
    elif action == "my_subs":
        await state.clear()
        await show_user_subscriptions(callback.from_user.id, callback.message, edit=True)
    elif action == "help":
        help_text = (
            "ℹ️ *Справка по боту AtlasBus*\n\n"
            "• *Поиск:* мгновенно показывает все рейсы со свободными местами.\n"
            "• *Отслеживание:* каждые 30 секунд мониторит сайт и присылает сообщение со ссылкой, как только появится место.\n"
            "• *Подписки:* можно в любой момент посмотреть и отменить в меню «Мои активные подписки»."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Главное меню", callback_data="nav:main")]])
        await callback.message.edit_text(help_text, reply_markup=kb, parse_mode="Markdown")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("nav:"))
async def handle_navigation(callback: CallbackQuery, state: FSMContext):
    dest = callback.data.split(":")[1]
    
    if dest == "main":
        await state.clear()
        text = (
            "👋 *Главное меню*\n\n"
            "Выберите необходимое действие:"
        )
        await callback.message.edit_text(text, reply_markup=build_main_menu_kb(), parse_mode="Markdown")
    elif dest == "route":
        await state.set_state(BotFlow.choosing_route)
        data = await state.get_data()
        act_title = "Поиск билетов" if data.get("action") == "search" else "Отслеживание мест"
        await callback.message.edit_text(
            f"*{act_title}*\n\nВыберите направление поездки:",
            reply_markup=build_route_kb(),
            parse_mode="Markdown",
        )
    elif dest == "date":
        data = await state.get_data()
        route_key = data.get("route", "minsk-novogrudok")
        route_info = ROUTES[route_key]
        await state.set_state(BotFlow.choosing_date)
        await callback.message.edit_text(
            f"📍 Направление: *{route_info['title']}*\n\n"
            "📅 Выберите дату отправления:",
            reply_markup=build_date_kb(),
            parse_mode="Markdown",
        )
    elif dest == "time":
        await state.set_state(BotFlow.choosing_time)
        data = await state.get_data()
        route_info = ROUTES.get(data.get("route", "minsk-novogrudok"), {})
        d_display = format_date_display(data.get("date", get_now().strftime("%Y-%m-%d")))
        await callback.message.edit_text(
            f"📍 *{route_info.get('title', '')}*\n"
            f"📅 Дата: *{d_display}*\n\n"
            "⏰ Выберите желаемое время отправления:",
            reply_markup=build_time_kb(),
            parse_mode="Markdown",
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("set_route:"))
async def handle_set_route(callback: CallbackQuery, state: FSMContext):
    route_key = callback.data.split(":")[1]
    if route_key not in ROUTES:
        await callback.answer("Неверный маршрут", show_alert=True)
        return

    await state.update_data(route=route_key)
    await state.set_state(BotFlow.choosing_date)
    
    route_info = ROUTES[route_key]
    await callback.message.edit_text(
        f"📍 Направление: *{route_info['title']}*\n\n"
        "📅 Выберите дату отправления из предложенных вариантов:",
        reply_markup=build_date_kb(),
        parse_mode="Markdown",
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("set_date:"))
async def handle_set_date(callback: CallbackQuery, state: FSMContext):
    date_val = callback.data.split(":")[1]
    
    if date_val == "custom":
        await state.set_state(BotFlow.waiting_custom_date)
        await callback.message.edit_text(
            "📅 *Ввод даты вручную*\n\n"
            "Отправьте дату сообщением в чат.\n"
            "Примеры форматов:\n"
            "• `12.11` (12 ноября)\n"
            "• `15.11.2026`\n"
            "• `2026-11-20`\n\n"
            "Или вернитесь назад:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад к выбору даты", callback_data="nav:date")]]),
            parse_mode="Markdown",
        )
        await callback.answer()
        return

    await state.update_data(date=date_val)
    await state.set_state(BotFlow.choosing_time)
    
    data = await state.get_data()
    route_info = ROUTES.get(data.get("route"), {})
    d_display = format_date_display(date_val)
    
    await callback.message.edit_text(
        f"📍 *{route_info.get('title', '')}*\n"
        f"📅 Дата: *{d_display}*\n\n"
        "⏰ Выберите желаемое время отправления:",
        reply_markup=build_time_kb(),
        parse_mode="Markdown",
    )
    await callback.answer()

@dp.message(BotFlow.waiting_custom_date)
async def handle_custom_date_input(message: Message, state: FSMContext):
    parsed = parse_user_date(message.text)
    now = get_now()
    
    if not parsed:
        await message.answer(
            "❌ Не удалось распознать дату.\n"
            "Пожалуйста, введите дату в виде `ДД.ММ` (например, `14.11`) или `ГГГГ-ММ-ДД`:",
            parse_mode="Markdown",
        )
        return

    if parsed < now.strftime("%Y-%m-%d"):
        await message.answer("❌ Указанная дата уже прошла! Пожалуйста, введите сегодняшнюю или будущую дату:")
        return

    await state.update_data(date=parsed)
    await state.set_state(BotFlow.choosing_time)
    
    data = await state.get_data()
    route_info = ROUTES.get(data.get("route"), {})
    d_display = format_date_display(parsed)

    await message.answer(
        f"📍 *{route_info.get('title', '')}*\n"
        f"📅 Дата: *{d_display}*\n\n"
        "⏰ Выберите желаемое время отправления:",
        reply_markup=build_time_kb(),
        parse_mode="Markdown",
    )

@dp.callback_query(F.data.startswith("set_time:"))
async def handle_set_time(callback: CallbackQuery, state: FSMContext):
    time_val = callback.data.removeprefix("set_time:")
    
    if time_val == "custom":
        await state.set_state(BotFlow.waiting_custom_time)
        await callback.message.edit_text(
            "✍️ *Ввод времени вручную*\n\n"
            "Отправьте сообщением желаемое время или диапазон, например:\n"
            "• `14:00-19:00`\n"
            "• `после 17:00`\n"
            "• `до 12:00`\n"
            "• `15:30`\n\n"
            "Или вернитесь назад:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад к выбору времени", callback_data="nav:time")]]),
            parse_mode="Markdown",
        )
        await callback.answer()
        return

    parts = time_val.split("-")
    time_from, time_to = parts[0], parts[1]
    await finalize_selection(callback.from_user.id, callback.message, state, time_from, time_to)
    await callback.answer()

@dp.message(BotFlow.waiting_custom_time)
async def handle_custom_time_input(message: Message, state: FSMContext):
    res = parse_time_range(message.text)
    if not res:
        await message.answer(
            "❌ Неверный формат времени.\n"
            "Пожалуйста, введите диапазон вида `14:00-18:00`, `после 16:00` или `до 12:00`:",
            parse_mode="Markdown",
        )
        return

    time_from, time_to = res
    await finalize_selection(message.from_user.id, message, state, time_from, time_to)

# ─── Finalize Search / Subscription ───────────────────────────────────────────

async def finalize_selection(user_id: int, target_msg: Message, state: FSMContext, time_from: str, time_to: str):
    data = await state.get_data()
    action = data.get("action", "search")
    route_key = data.get("route", "minsk-novogrudok")
    date_val = data.get("date", get_now().strftime("%Y-%m-%d"))
    route_info = ROUTES[route_key]

    await state.clear()

    if action == "search":
        # Мгновенный поиск
        wait_text = (
            f"🔎 Ищу свободные места...\n"
            f"📍 {route_info['title']}\n"
            f"📅 {format_date_display(date_val)} (с {time_from} по {time_to})"
        )
        if target_msg.from_user and target_msg.from_user.is_bot:
            try:
                await target_msg.edit_text(wait_text)
            except Exception:
                await target_msg.answer(wait_text)
        else:
            await target_msg.answer(wait_text)

        async with aiohttp.ClientSession() as session:
            rides = await fetch_rides(session, route_info["from_id"], route_info["to_id"], date_val)

        if rides is None:
            err_text = "❌ Ошибка соединения с сервером AtlasBus. Попробуйте повторить запрос чуть позже."
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Повторить поиск", callback_data="act:search")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="nav:main")],
            ])
            await target_msg.answer(err_text, reply_markup=kb)
            return

        filtered = filter_rides_by_time(rides, time_from, time_to)
        d_display = format_date_display(date_val)
        time_display = "весь день" if time_from == "00:00" and time_to == "23:59" else f"{time_from} – {time_to}"

        if filtered:
            header = (
                f"✅ *Найдено рейсов:* {len(filtered)}\n"
                f"📍 *{route_info['title']}*\n"
                f"📅 *{d_display}* | ⏰ *{time_display}*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            )
            # Формируем блоки по рейсам
            ride_texts = [format_ride_card(r, route_info["from_id"], route_info["to_id"], date_val) for r in filtered]
            
            # Отправка сообщений (с учетом лимита длины Telegram в 4096 символов)
            full_msg = header + "\n".join(ride_texts)
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔔 Отслеживать изменения мест", callback_data=f"fast_sub|{route_key}|{date_val}|{time_from}|{time_to}")],
                [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="act:search")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="nav:main")],
            ])
            
            if len(full_msg) <= 4000:
                await target_msg.answer(full_msg, reply_markup=kb, parse_mode="Markdown", disable_web_page_preview=True)
            else:
                chunks = []
                curr = header
                for rt in ride_texts:
                    if len(curr) + len(rt) > 3800:
                        chunks.append(curr)
                        curr = rt + "\n"
                    else:
                        curr += rt + "\n"
                if curr:
                    chunks.append(curr)
                
                for idx, chunk in enumerate(chunks):
                    if idx == len(chunks) - 1:
                        await target_msg.answer(chunk, reply_markup=kb, parse_mode="Markdown", disable_web_page_preview=True)
                    else:
                        await target_msg.answer(chunk, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            # Рейсов нет
            no_rides_text = (
                f"❌ На выбранную дату и время свободных мест нет.\n\n"
                f"📍 *{route_info['title']}*\n"
                f"📅 *{d_display}* | ⏰ *{time_display}*\n\n"
                "💡 *Хотите включить отслеживание?*\n"
                "Как только кто-то сдаст билет или откроется рейс, бот пришлет вам уведомление со ссылкой!"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔔 Включить отслеживание на это время", callback_data=f"fast_sub|{route_key}|{date_val}|{time_from}|{time_to}")],
                [InlineKeyboardButton(text="🔍 Выбрать другое время", callback_data="act:search")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="nav:main")],
            ])
            await target_msg.answer(no_rides_text, reply_markup=kb, parse_mode="Markdown")

    else:
        # Создание подписки
        create_subscription(user_id, route_key, date_val, time_from, time_to)
        d_display = format_date_display(date_val)
        time_display = "Весь день" if time_from == "00:00" and time_to == "23:59" else f"{time_from} – {time_to}"

        success_text = (
            "✅ *Подписка успешно оформлена!*\n\n"
            f"📍 Направление: *{route_info['title']}*\n"
            f"📅 Дата: *{d_display}*\n"
            f"⏰ Время: *{time_display}*\n\n"
            "🤖 Я буду проверять наличие свободных мест каждые 30 секунд.\n"
            "Как только появится хотя бы одно место — вы получите мгновенное оповещение со ссылкой на бронирование! 🚀"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои подписки", callback_data="act:my_subs")],
            [InlineKeyboardButton(text="➕ Создать ещё одну", callback_data="act:sub")],
            [InlineKeyboardButton(text="🔍 Найти билеты", callback_data="act:search")],
        ])
        await target_msg.answer(success_text, reply_markup=kb, parse_mode="Markdown")

def create_subscription(user_id: int, route_key: str, date: str, time_from: str, time_to: str):
    """Добавляет подписку и сохраняет в JSON"""
    route_info = ROUTES[route_key]
    if user_id not in subscriptions:
        subscriptions[user_id] = []
        
    # Проверяем, нет ли уже точно такой же подписки
    for s in subscriptions[user_id]:
        if s["route"] == route_key and s["date"] == date and s["time_from"] == time_from and s["time_to"] == time_to:
            return
            
    sub = {
        "route": route_key,
        "date": date,
        "time_from": time_from,
        "time_to": time_to,
        "from_id": route_info["from_id"],
        "to_id": route_info["to_id"],
        "from_name": route_info["from_name"],
        "to_name": route_info["to_name"],
        "created_at": get_now().isoformat(),
    }
    subscriptions[user_id].append(sub)
    save_subscriptions()
    log.info(f"New subscription for user {user_id}: {route_key} on {date} ({time_from}-{time_to})")

@dp.callback_query(F.data.startswith("fast_sub|"))
async def handle_fast_sub(callback: CallbackQuery):
    parts = callback.data.split("|")
    route_key, date_val, time_from, time_to = parts[1], parts[2], parts[3], parts[4]
    create_subscription(callback.from_user.id, route_key, date_val, time_from, time_to)
    
    route_info = ROUTES.get(route_key, {})
    d_display = format_date_display(date_val)
    time_display = "Весь день" if time_from == "00:00" and time_to == "23:59" else f"{time_from} – {time_to}"
    
    await callback.message.answer(
        f"✅ *Отслеживание включено!*\n\n"
        f"📍 *{route_info.get('title', '')}*\n"
        f"📅 Дата: *{d_display}*\n"
        f"⏰ Время: *{time_display}*\n\n"
        "Я сообщу, как только билеты появятся в наличии!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои подписки", callback_data="act:my_subs")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="nav:main")],
        ]),
        parse_mode="Markdown",
    )
    await callback.answer("Подписка создана!")

# ─── Subscription Management ──────────────────────────────────────────────────

async def show_user_subscriptions(user_id: int, message: Message, edit: bool = False):
    user_subs = subscriptions.get(user_id, [])
    if not user_subs:
        text = (
            "📋 *У вас пока нет активных подписок.*\n\n"
            "Вы можете включить отслеживание билетов на нужный маршрут и дату, "
            "и бот напишет вам сразу, как появятся свободные места."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать подписку", callback_data="act:sub")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="nav:main")],
        ])
    else:
        text = f"📋 *Ваши активные подписки ({len(user_subs)}):*\n\n"
        for idx, s in enumerate(user_subs, 1):
            r_info = ROUTES.get(s["route"], {})
            d_display = format_date_display(s["date"])
            t_display = "весь день" if s["time_from"] == "00:00" and s["time_to"] == "23:59" else f"{s['time_from']} – {s['time_to']}"
            text += f"{idx}️⃣ *{r_info.get('title', s['route'])}*\n"
            text += f"   📅 {d_display} | ⏰ {t_display}\n\n"
        text += "Чтобы удалить подписку, нажмите на соответствующую кнопку ниже:"
        kb = build_my_subs_kb(user_id)

    if edit and hasattr(message, "edit_text"):
        await message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("del_sub:"))
async def handle_del_sub(callback: CallbackQuery):
    idx = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    user_subs = subscriptions.get(user_id, [])
    
    if 0 <= idx < len(user_subs):
        removed = user_subs.pop(idx)
        if not user_subs:
            subscriptions.pop(user_id, None)
        save_subscriptions()
        log.info(f"Removed subscription #{idx} for user {user_id}: {removed}")
        await callback.answer("Подписка удалена")
    else:
        await callback.answer("Подписка уже не существует")

    await show_user_subscriptions(user_id, callback.message, edit=True)

@dp.callback_query(F.data == "del_all_subs")
async def handle_del_all_subs(callback: CallbackQuery):
    user_id = callback.from_user.id
    subscriptions.pop(user_id, None)
    save_subscriptions()
    await callback.answer("Все подписки удалены")
    await show_user_subscriptions(user_id, callback.message, edit=True)

# ─── Background Monitor Task ──────────────────────────────────────────────────

async def monitor_subscriptions():
    """Фоновая задача: проверяет рейсы каждые 45 секунд для всех подписчиков с защитой от перегрузки соединений"""
    log.info("Background subscription monitor started")
    
    connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            await asyncio.sleep(45)
            
            if not subscriptions:
                continue

            now = get_now()
            today_str = now.strftime("%Y-%m-%d")

            # 1. Очистка устаревших подписок (прошедшие даты)
            for uid, subs in list(subscriptions.items()):
                valid_subs = [s for s in subs if s["date"] >= today_str]
                if len(valid_subs) != len(subs):
                    if valid_subs:
                        subscriptions[uid] = valid_subs
                    else:
                        subscriptions.pop(uid, None)
                    save_subscriptions()

            # 2. Группируем запросы по (from_id, to_id, date), чтобы не спамить API дубликатами
            queries = set()
            for subs in subscriptions.values():
                for s in subs:
                    queries.add((s["from_id"], s["to_id"], s["date"]))

            # 3. Делаем запросы к API с паузой между запросами
            fetched_results = {}
            for from_id, to_id, date_str in queries:
                rides = await fetch_rides(session, from_id, to_id, date_str)
                fetched_results[(from_id, to_id, date_str)] = rides or []
                await asyncio.sleep(1.0)  # Пауза 1 секунда между запросами к AtlasBus

            # 4. Проверяем каждую подписку
            for user_id, subs in list(subscriptions.items()):
                for sub in list(subs):
                    query_key = (sub["from_id"], sub["to_id"], sub["date"])
                    rides = fetched_results.get(query_key, [])
                    filtered = filter_rides_by_time(rides, sub["time_from"], sub["time_to"])

                    sub_key = f"{user_id}:{sub['route']}:{sub['date']}:{sub['time_from']}:{sub['time_to']}"
                    
                    # Идентификаторы доступных рейсов и количество мест: set("rideId_freeSeats")
                    current_state = set(f"{r.get('rideId', r.get('id'))}_{r.get('freeSeats', 0)}" for r in filtered)
                    old_state = prev_rides_state.get(sub_key)

                    # Первый раз — инициализируем состояние
                    if old_state is None:
                        prev_rides_state[sub_key] = current_state
                        continue

                    # Если состояние не изменилось — пропускаем
                    if current_state == old_state:
                        continue

                    # Находим новые или освободившиеся рейсы
                    new_available = [
                        r for r in filtered
                        if f"{r.get('rideId', r.get('id'))}_{r.get('freeSeats', 0)}" not in old_state
                    ]

                    prev_rides_state[sub_key] = current_state

                    if not new_available:
                        continue

                    # Отправляем уведомление пользователю
                    route_info = ROUTES.get(sub["route"], {})
                    d_display = format_date_display(sub["date"])
                    
                    lines = [
                        "⚡️ *Появились свободные места на рейс!*\n",
                        f"📍 *{route_info.get('title', '')}*",
                        f"📅 *{d_display}*\n",
                    ]
                    for r in new_available:
                        lines.append(format_ride_card(r, sub["from_id"], sub["to_id"], sub["date"]))

                    notif_text = "\n".join(lines)
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📋 Мои подписки", callback_data="act:my_subs")],
                    ])

                    try:
                        await bot.send_message(user_id, notif_text, reply_markup=kb, parse_mode="Markdown", disable_web_page_preview=True)
                        log.info(f"Notification sent to user {user_id} ({len(new_available)} new rides)")
                    except Exception as e:
                        log.error(f"Failed to send notification to {user_id}: {e}")

# ─── Railway Embedded Healthcheck Server ──────────────────────────────────────

async def start_healthcheck_server():
    """Встроенный HTTP-сервер для Railway healthcheck (чтобы не было 502 Bad Gateway)"""
    app = web.Application()

    async def handle_ping(request):
        return web.Response(text="AtlasBus Bot is running OK 🚀", content_type="text/plain")

    async def handle_status(request):
        total_users = len(subscriptions)
        total_subs = sum(len(s) for s in subscriptions.values())
        return web.json_response({
            "status": "ok",
            "users": total_users,
            "subscriptions": total_subs,
            "time": get_now().isoformat(),
        })

    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    app.router.add_get("/status", handle_status)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"Healthcheck HTTP server successfully bound to 0.0.0.0:{PORT}")
    return runner

# ─── Main Entry Point ─────────────────────────────────────────────────────────

async def main():
    log.info("Starting AtlasBus Telegram Bot...")
    load_subscriptions()

    # Сбрасываем старый вебхук перед началом polling
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Telegram webhook removed, starting Long Polling")

    # Запускаем встроенный HTTP-сервер для Railway
    web_runner = await start_healthcheck_server()

    # Запускаем фоновый мониторинг
    asyncio.create_task(monitor_subscriptions())

    backoff = BackoffConfig(
        min_delay=5.0,    # При сбоях Telegram API ждем минимум 5 секунд
        max_delay=60.0,   # Максимальная задержка до 60 секунд
        factor=2.0,       # Экспоненциальное увеличение задержки
        jitter=0.5,
    )

    try:
        log.info("Bot is active and ready to handle updates!")
        await dp.start_polling(
            bot,
            polling_timeout=30,  # Стандартный таймаут Long Polling 30 сек
            backoff_config=backoff,
            allowed_updates=["message", "callback_query"],
        )
    finally:
        await web_runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
