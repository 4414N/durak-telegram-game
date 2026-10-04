import asyncio
import hashlib
import hmac
import json
import os
import random
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

BASE = Path(__file__).parent
TOKEN = os.getenv("BOT_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
APP_SHORT_NAME = os.getenv("APP_SHORT_NAME", "durak")

RANKS = [6, 7, 8, 9, 10, 11, 12, 13, 14]  # J,Q,K,A = 11..14
SUITS = ["♠", "♥", "♦", "♣"]
SUIT_NAMES = {"♠": "spades", "♥": "hearts", "♦": "diamonds", "♣": "clubs"}
RANK_NAMES = {6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K", 14: "A"}


def card_id(c: dict) -> str:
    return f"{c['rank']}{c['suit']}"


def make_deck() -> list[dict]:
    return [{"rank": r, "suit": s} for s in SUITS for r in RANKS]


def rank_name(r: int) -> str:
    return RANK_NAMES[r]


def is_red(suit: str) -> bool:
    return suit in ("♥", "♦")


def beats(a: dict, b: dict, trump: str) -> bool:
    """Can card a beat card b?"""
    if a["suit"] == b["suit"]:
        return a["rank"] > b["rank"]
    return a["suit"] == trump and b["suit"] != trump


@dataclass
class Player:
    user_id: str
    name: str
    hand: list[dict] = field(default_factory=list)
    connected: int = 0
    score: int = 0


@dataclass
class Room:
    key: str
    players: list[Player] = field(default_factory=list)
    deck: list[dict] = field(default_factory=list)
    trump: str | None = None
    discard: list[dict] = field(default_factory=list)
    table: list[dict] = field(default_factory=list)  # {attack, defense}
    attacker: int = 0
    defender: int = 1
    phase: str = "lobby"
    turn: int | None = None
    winner: str | None = None
    message: str = "Создайте игру и пригласите до двух друзей."
    last_event: str = ""
    created_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def player(self, user_id: str) -> Player | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def refill(self) -> None:
        # Classic draw order: attacker first, then around the table, defender last.
        order = []
        for offset in range(len(self.players)):
            order.append((self.attacker + offset) % len(self.players))
        for idx in order:
            while len(self.players[idx].hand) < 6 and self.deck:
                self.players[idx].hand.append(self.deck.pop())

    def all_ranks_on_table(self) -> set[int]:
        ranks = set()
        for pair in self.table:
            ranks.add(pair["attack"]["rank"])
            if pair.get("defense"):
                ranks.add(pair["defense"]["rank"])
        return ranks

    def available_attack_card(self, card: dict) -> bool:
        if len(self.table) >= 6:
            return False
        return card["rank"] in self.all_ranks_on_table() or not self.table

    def can_defend(self, card: dict, attack_card: dict) -> bool:
        return beats(card, attack_card, self.trump or "♠")

    def open_attacks_count(self) -> int:
        return sum(1 for x in self.table if x.get("defense") is None)

    def winner_if_done(self) -> str | None:
        if self.phase != "playing":
            return self.winner
        # A player who has no cards is out only after deck is empty and table resolved.
        if self.deck or self.open_attacks_count():
            return None
        alive = [p for p in self.players if p.hand]
        if len(alive) <= 1:
            return alive[0].user_id if alive else None
        return None

    def public_state(self, viewer_id: str) -> dict[str, Any]:
        players = []
        for i, p in enumerate(self.players):
            players.append({
                "id": p.user_id,
                "name": p.name,
                "count": len(p.hand),
                "connected": bool(p.connected),
                "role": "attacker" if i == self.attacker else ("defender" if i == self.defender else "waiting"),
            })
        viewer = self.player(viewer_id)
        can_attack = self.phase == "playing" and viewer_id != self.players[self.defender].user_id
        can_defend = self.phase == "playing" and viewer_id == self.players[self.defender].user_id
        return {
            "room": self.key,
            "phase": self.phase,
            "me": viewer_id,
            "players": players,
            "hand": viewer.hand if viewer else [],
            "trump": self.trump,
            "deck_count": len(self.deck),
            "table": self.table,
            "attacker": self.players[self.attacker].name if self.players else None,
            "defender": self.players[self.defender].name if self.players else None,
            "can_attack": can_attack,
            "can_defend": can_defend,
            "turn": self.turn,
            "winner": self.winner,
            "message": self.message,
            "last_event": self.last_event,
            "open_attacks": self.open_attacks_count(),
        }

    async def join(self, user_id: str, name: str) -> tuple[bool, str]:
        async with self.lock:
            existing = self.player(user_id)
            if existing:
                existing.name = name
                existing.connected += 1
                return True, "Вы уже в игре."
            if len(self.players) >= 3 or self.phase != "lobby":
                return False, "В этой игре уже нет свободного места."
            self.players.append(Player(user_id, name, connected=1))
            self.message = f"{name} присоединился. Игроков: {len(self.players)}/3."
            self.last_event = f"{name} влетает за стол 🃏"
            return True, "Добро пожаловать!"

    async def start(self) -> tuple[bool, str]:
        async with self.lock:
            if self.phase != "lobby":
                return False, "Игра уже началась."
            if len(self.players) < 2:
                return False, "Нужно минимум 2 игрока."
            self.deck = make_deck()
            random.shuffle(self.deck)
            self.discard = []
            self.table = []
            for p in self.players:
                p.hand = [self.deck.pop() for _ in range(6)]
            self.trump = self.deck[-1]["suit"] if self.deck else random.choice(SUITS)
            # Lowest trump starts.
            trump_cards = [(min((c for c in p.hand if c["suit"] == self.trump), default=None, key=lambda c: c["rank"]), i) for i, p in enumerate(self.players)]
            trump_cards = [(c, i) for c, i in trump_cards if c]
            self.attacker = min(trump_cards, key=lambda x: x[0]["rank"])[1] if trump_cards else random.randrange(len(self.players))
            self.defender = (self.attacker + 1) % len(self.players)
            self.turn = self.attacker
            self.phase = "playing"
            self.message = f"Игра началась. Ходит {self.players[self.attacker].name}. Козырь — {self.trump}"
            self.last_event = f"Козырь: {self.trump}"
            return True, "Старт!"

    def attack(self, user_id: str, cid: str) -> tuple[bool, str]:
        if self.phase != "playing":
            return False, "Игра ещё не идёт."
        if user_id == self.players[self.defender].user_id:
            return False, "Защищающийся не атакует."
        p = self.player(user_id)
        if not p:
            return False, "Вы не за этим столом."
        c = next((c for c in p.hand if card_id(c) == cid), None)
        if not c:
            return False, "Такой карты у вас нет."
        if not self.available_attack_card(c):
            return False, "Можно подкидывать только карты уже присутствующего достоинства."
        if self.open_attacks_count() >= min(6, len(self.players[self.defender].hand)):
            return False, "Больше шести карт на отбив дать нельзя."
        p.hand.remove(c)
        self.table.append({"attack": c, "defense": None, "by": p.name})
        self.last_event = self._attack_flavor(c, p.name)
        self.message = f"{p.name} подкинул {rank_name(c['rank'])}{c['suit']}."
        self.turn = self.defender
        return True, "Атака принята."

    def defend(self, user_id: str, cid: str, target_index: int) -> tuple[bool, str]:
        if user_id != self.players[self.defender].user_id or self.phase != "playing":
            return False, "Сейчас не ваш ход защиты."
        if not (0 <= target_index < len(self.table)):
            return False, "Неверная карта атаки."
        target = self.table[target_index]
        if target.get("defense") is not None:
            return False, "Эта карта уже отбита."
        p = self.player(user_id)
        c = next((c for c in p.hand if card_id(c) == cid), None)
        if not c:
            return False, "Такой карты у вас нет."
        if not self.can_defend(c, target["attack"]):
            return False, "Этой картой отбиться нельзя."
        p.hand.remove(c)
        target["defense"] = c
        self.last_event = self._defense_flavor(c, p.name)
        self.message = f"{p.name} отбился картой {rank_name(c['rank'])}{c['suit']}."
        # keep turn on defender until all attacks are covered or another attack arrives
        if self.open_attacks_count() == 0:
            self.turn = self.defender
        return True, "Отбито!"

    def take(self, user_id: str) -> tuple[bool, str]:
        if user_id != self.players[self.defender].user_id or self.phase != "playing":
            return False, "Забирать можно только во время своей защиты."
        p = self.player(user_id)
        cards = []
        for pair in self.table:
            cards.append(pair["attack"])
            if pair.get("defense"):
                cards.append(pair["defense"])
        p.hand.extend(cards)
        self.table.clear()
        self.last_event = f"{p.name} забирает стол. Сильно. 😈"
        self.message = f"{p.name} забирает все карты со стола."
        self.attacker = (self.defender + 1) % len(self.players)
        self.defender = (self.attacker + 1) % len(self.players)
        self.refill()
        self.turn = self.attacker
        self._finish_if_needed()
        return True, "Стол забран."

    def pass_attack(self, user_id: str) -> tuple[bool, str]:
        if self.phase != "playing" or not self.table or user_id == self.players[self.defender].user_id:
            return False, "Сейчас нельзя завершить атаку."
        if self.open_attacks_count() > 0:
            return False, "Сначала защитник должен отбиться или забрать."
        self.discard.extend([x["attack"] for x in self.table])
        self.discard.extend([x["defense"] for x in self.table if x.get("defense")])
        self.table.clear()
        old_defender = self.defender
        self.attacker = old_defender
        self.defender = (old_defender + 1) % len(self.players)
        self.refill()
        self.turn = self.attacker
        self.last_event = f"{self.players[self.attacker].name} начинает новый заход."
        self.message = f"Отбой. Теперь атакует {self.players[self.attacker].name}."
        self._finish_if_needed()
        return True, "Отбой."

    def _finish_if_needed(self) -> None:
        if self.deck:
            return
        # After deck empties, player with empty hand wins; if only one player has cards, they're the Durak.
        zero = [p for p in self.players if len(p.hand) == 0]
        if zero and all(len(p.hand) > 0 for p in self.players if p.user_id != zero[0].user_id):
            self.winner = zero[0].user_id
            self.phase = "finished"
            self.message = f"🏆 {zero[0].name} победил и больше не дурак."
            self.last_event = "Финальный удар! 🏆"

    @staticmethod
    def _attack_flavor(c: dict, name: str) -> str:
        if c["rank"] == 14:
            return f"{name} достал ТУЗА. Слишком самоуверенно. 👑"
        if c["rank"] == 7:
            return f"{name}: Семёрка судьбы. Ну-ну… 🕯️"
        return random.choice([
            f"{name} идёт в атаку!",
            f"{name} красиво подкинул карту.",
            f"Кто-то решил, что вам мало проблем. 😏",
        ])

    @staticmethod
    def _defense_flavor(c: dict, name: str) -> str:
        if c["suit"] in ("♥", "♦") and c["rank"] == 14:
            return f"{name} отбился красным тузом. Вот это характер! 🔥"
        return random.choice([
            f"{name} отбился. Красиво.",
            f"Отбой принят. 🛡️",
            f"{name} поймал атаку на лету.",
        ])


ROOMS: dict[str, Room] = {}
SOCKETS: dict[str, set[WebSocket]] = {}


def validate_init_data(init_data: str) -> dict[str, str]:
    if not TOKEN:
        raise HTTPException(500, "BOT_TOKEN не задан на сервере")
    parsed = urllib.parse.parse_qs(init_data, strict_parsing=False)
    data_hash = parsed.get("hash", [None])[0]
    if not data_hash:
        raise HTTPException(401, "Telegram initData hash missing")
    pairs = []
    for k, v in parsed.items():
        if k == "hash":
            continue
        if not v:
            continue
        pairs.append(f"{k}={v[0]}")
    data_check_string = "\n".join(sorted(pairs))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, data_hash):
        raise HTTPException(401, "Invalid Telegram initData")
    return {k: v[0] for k, v in parsed.items() if v}


def parse_telegram_user(data: dict[str, str]) -> tuple[str, str, str]:
    user = json.loads(data.get("user", "{}"))
    uid = str(user.get("id"))
    name = user.get("first_name") or user.get("username") or "Игрок"
    room_key = data.get("chat_instance") or data.get("start_param") or uid
    return uid, name[:32], room_key


async def broadcast(room: Room) -> None:
    sockets = list(SOCKETS.get(room.key, set()))
    for ws in sockets:
        try:
            # find viewer id attached to websocket
            uid = getattr(ws, "durak_uid", None)
            await ws.send_json(room.public_state(uid) if uid else {"error": "unauthorized"})
        except Exception:
            SOCKETS.get(room.key, set()).discard(ws)


app = FastAPI(title="Durak Telegram Mini App")


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
async def health():
    return {"ok": True, "rooms": len(ROOMS)}


@app.get("/api/me")
async def api_me(initData: str):
    data = validate_init_data(initData)
    uid, name, room = parse_telegram_user(data)
    return {"id": uid, "name": name, "room": room}


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    try:
        init_data = ws.query_params.get("initData", "")
        data = validate_init_data(init_data)
        uid, name, room_key = parse_telegram_user(data)
        room = ROOMS.setdefault(room_key, Room(room_key))
        # avoid stale rooms for broken deep links, but retain game data during the process
        SOCKETS.setdefault(room_key, set()).add(ws)
        setattr(ws, "durak_uid", uid)
        ok, msg = await room.join(uid, name)
        if not ok:
            await ws.send_json({"error": msg})
            await ws.close()
            return
        await broadcast(room)
        while True:
            raw = await ws.receive_json()
            action = raw.get("action")
            cid = raw.get("card")
            target = raw.get("target")
            result = (False, "Неизвестное действие.")
            if action == "start":
                result = await room.start()
            elif action == "attack":
                result = (room.attack(uid, cid or ""))
            elif action == "defend":
                result = (room.defend(uid, cid or "", int(target)))
            elif action == "take":
                result = (room.take(uid))
            elif action == "pass":
                result = (room.pass_attack(uid))
            elif action == "ping":
                result = (True, "pong")
            room.message = result[1] if not result[0] else room.message
            await broadcast(room)
    except WebSocketDisconnect:
        pass
    except HTTPException as e:
        try:
            await ws.send_json({"error": e.detail})
            await ws.close()
        except Exception:
            pass
    finally:
        if 'room_key' in locals():
            SOCKETS.get(room_key, set()).discard(ws)
            if 'room' in locals():
                p = room.player(uid)
                if p:
                    p.connected = max(0, p.connected - 1)
                await broadcast(room)


router = Router()


def mini_app_url() -> str:
    # Direct-link Mini Apps use t.me/botusername/appname. Username is fetched at runtime.
    return "__MINI_APP_URL__"


@router.message(Command("start"))
async def start_cmd(message: Message, bot: Bot):
    me = await bot.get_me()
    url = f"https://t.me/{me.username}/{APP_SHORT_NAME}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🃏 Открыть Дурака", url=url)]])
    await message.answer("🎴 <b>Дурак</b>\n\nОткрывай игру и зови друзей за стол — максимум 3 игрока.", reply_markup=kb, parse_mode="HTML")


@router.message(Command("durak"))
async def durak_cmd(message: Message, bot: Bot):
    await start_cmd(message, bot)


async def bot_main():
    if not TOKEN:
        print("BOT_TOKEN is not set; web app still starts, but Telegram auth will fail.")
        return
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


async def main():
    import uvicorn
    # Run web server + Telegram polling together.
    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), bot_main())


if __name__ == "__main__":
    asyncio.run(main())
