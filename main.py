from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message, WebAppInfo
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from game import Room

BASE = Path(__file__).parent
TOKEN = os.getenv("BOT_TOKEN", "")
APP_SHORT_NAME = os.getenv("APP_SHORT_NAME", "durak")
BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"

ROOMS: dict[str, Room] = {}
SOCKETS: dict[str, set[WebSocket]] = {}
ROOM_LOCK = asyncio.Lock()
BOT_TASK: asyncio.Task | None = None
BOT_INSTANCE: Bot | None = None


def validate_init_data(raw: str) -> dict[str, str]:
    if DEV_MODE and raw.startswith("dev:"):
        parts = raw.split(":", 3)
        if len(parts) < 3:
            raise HTTPException(401, "Invalid dev initData")
        return {"user": json.dumps({"id": parts[1], "first_name": parts[2]}), "chat_instance": "dev-room", "auth_date": str(int(time.time()))}
    if not TOKEN:
        raise HTTPException(500, "BOT_TOKEN not configured")
    if not raw:
        raise HTTPException(401, "Mini App opened outside Telegram")
    parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
    data_hash = parsed.get("hash", [""])[0]
    if not data_hash:
        raise HTTPException(401, "Telegram hash missing")
    pairs = []
    for key, values in parsed.items():
        if key == "hash" or not values:
            continue
        pairs.append(f"{key}={values[0]}")
    check = "\n".join(sorted(pairs))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, data_hash):
        raise HTTPException(401, "Invalid Telegram initData")
    auth_date = int(parsed.get("auth_date", ["0"])[0] or 0)
    if not auth_date or time.time() - auth_date > 86400:
        raise HTTPException(401, "Telegram session expired")
    return {key: values[0] for key, values in parsed.items() if values}


def parse_user(data: dict[str, str]) -> tuple[str, str, str | None, str, str | None]:
    try:
        u = json.loads(data.get("user", "{}"))
    except json.JSONDecodeError:
        u = {}
    uid = str(u.get("id") or "")
    if not uid:
        raise HTTPException(401, "Telegram user missing")
    name = (u.get("first_name") or u.get("username") or "Игрок").strip()[:32]
    photo = u.get("photo_url")
    chat_instance = data.get("chat_instance")
    start_param = data.get("start_param")
    return uid, name, photo, chat_instance or "", start_param


async def bot_username() -> str:
    global BOT_USERNAME
    if BOT_USERNAME:
        return BOT_USERNAME
    if BOT_INSTANCE:
        me = await BOT_INSTANCE.get_me()
        BOT_USERNAME = me.username or ""
    return BOT_USERNAME


async def mini_link(room_id: str | None = None) -> str | None:
    username = await bot_username()
    if not username:
        return None
    base = f"https://t.me/{username}/{APP_SHORT_NAME}"
    return f"{base}?startapp={urllib.parse.quote(room_id)}" if room_id else base


def choose_room_key(chat_instance: str, start_param: str | None, uid: str) -> tuple[str, str]:
    if start_param and start_param.startswith("room_"):
        room_id = start_param.removeprefix("room_")[:32]
        return f"room:{room_id}", room_id
    if chat_instance:
        return f"chat:{chat_instance}", chat_instance[-10:]
    return f"user:{uid}", f"p{uid[-8:]}"


async def broadcast(room: Room) -> None:
    link = await mini_link(f"room_{room.room_id}")
    for ws in list(SOCKETS.get(room.room_id, set())):
        uid = getattr(ws, "durak_uid", "")
        if not uid:
            continue
        try:
            await ws.send_json({"type": "state", "state": room.public_state(uid, link)})
        except Exception:
            SOCKETS.get(room.room_id, set()).discard(ws)


async def send_error(ws: WebSocket, text: str) -> None:
    with contextlib.suppress(Exception):
        await ws.send_json({"type": "toast", "text": text})




@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
async def health():
    return {"ok": True, "rooms": len(ROOMS)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    room_id = None
    room = None
    uid = None
    try:
        raw = ws.query_params.get("initData", "")
        data = validate_init_data(raw)
        uid, name, photo, chat_instance, start_param = parse_user(data)
        key, room_id = choose_room_key(chat_instance, start_param, uid)
        async with ROOM_LOCK:
            room = ROOMS.get(key)
            if not room:
                room = Room(room_id=room_id, context=key)
                ROOMS[key] = room
        existing = room.player(uid)
        if existing:
            existing.name = name
            existing.photo_url = photo or existing.photo_url
            existing.connections += 1
        else:
            if room.phase != "lobby" or len(room.players) >= 3:
                await send_error(ws, "Стол уже заполнен. Создай новую игру через ссылку в боте.")
                await ws.close()
                return
            p = room.player(uid)
            if p is None:
                from game import Player
                room.players.append(Player(uid, name, photo_url=photo, connections=1))
                if not room.host_id:
                    room.host_id = uid
                    room.emit("join", f"{name} создал стол. Ждём игроков…")
                else:
                    room.emit("join", f"{name} сел за стол. {len(room.players)}/3 игроков.")
        SOCKETS.setdefault(room_id, set()).add(ws)
        setattr(ws, "durak_uid", uid)
        await broadcast(room)
        while True:
            msg = await ws.receive_json()
            action = msg.get("action")
            ok = False
            detail = ""
            if action == "start":
                ok, detail = room.start(uid)
            elif action == "attack":
                ok, detail = room.attack(uid, str(msg.get("card", "")))
            elif action == "defend":
                try:
                    target = int(msg.get("target"))
                except (TypeError, ValueError):
                    target = -1
                ok, detail = room.defend(uid, str(msg.get("card", "")), target)
            elif action == "take":
                ok, detail = room.take(uid)
            elif action == "finish":
                ok, detail = room.finish_round(uid)
            elif action == "reset":
                if uid != room.host_id or room.phase != "finished":
                    ok, detail = False, "Только создатель стола может начать новую партию."
                else:
                    room.reset_lobby()
                    ok, detail = True, "Новый стол готов."
            elif action == "ping":
                ok, detail = True, "pong"
            else:
                detail = "Неизвестное действие."
            if not ok:
                await send_error(ws, detail)
            await broadcast(room)
    except WebSocketDisconnect:
        pass
    except HTTPException as exc:
        await send_error(ws, str(exc.detail))
        with contextlib.suppress(Exception):
            await ws.close()
    except Exception as exc:
        print("websocket error:", repr(exc))
        await send_error(ws, "Ошибка соединения. Попробуй открыть игру ещё раз.")
    finally:
        if room_id:
            SOCKETS.get(room_id, set()).discard(ws)
            if room and uid:
                p = room.player(uid)
                if p:
                    p.connections = max(0, p.connections - 1)


router = Router()


@router.message(Command("start"))
async def start_cmd(message: Message, bot: Bot):
    me = await bot.get_me()
    url = f"https://t.me/{me.username}/{APP_SHORT_NAME}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🃏 Открыть Дурака", url=url)]])
    await message.answer("🃏 <b>ДУРАК</b>\n\nНастоящий стол на 2–3 игроков прямо в Telegram. Нажми кнопку и зови друзей.", reply_markup=kb, parse_mode="HTML")


@router.message(Command("durak"))
async def durak_cmd(message: Message, bot: Bot):
    await start_cmd(message, bot)


async def run_bot() -> None:
    global BOT_INSTANCE
    if not TOKEN:
        print("BOT_TOKEN is missing; web app is still served.")
        return
    BOT_INSTANCE = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    with contextlib.suppress(Exception):
        me = await BOT_INSTANCE.get_me()
        global BOT_USERNAME
        BOT_USERNAME = me.username or BOT_USERNAME
        await BOT_INSTANCE.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🃏 Дурак", web_app=WebAppInfo(url=f"https://t.me/{BOT_USERNAME}/{APP_SHORT_NAME}")))
    try:
        await dp.start_polling(BOT_INSTANCE, allowed_updates=["message"])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print("telegram polling stopped:", repr(exc))
    finally:
        with contextlib.suppress(Exception):
            await BOT_INSTANCE.session.close()
        BOT_INSTANCE = None


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    global BOT_TASK
    BOT_TASK = asyncio.create_task(run_bot())
    yield
    if BOT_TASK:
        BOT_TASK.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await BOT_TASK




if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
