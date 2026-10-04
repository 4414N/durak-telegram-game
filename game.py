from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

RANKS = [6, 7, 8, 9, 10, 11, 12, 13, 14]
SUITS = ["♠", "♥", "♦", "♣"]
RANK_NAMES = {11: "J", 12: "Q", 13: "K", 14: "A"}
SUIT_NAMES = {"♠": "spades", "♥": "hearts", "♦": "diamonds", "♣": "clubs"}
RED = {"♥", "♦"}


def make_deck() -> list[dict[str, Any]]:
    return [{"rank": rank, "suit": suit} for suit in SUITS for rank in RANKS]


def card_id(card: dict[str, Any]) -> str:
    return f"{card['rank']}{card['suit']}"


def beats(defense: dict[str, Any], attack: dict[str, Any], trump: str) -> bool:
    if defense["suit"] == attack["suit"]:
        return defense["rank"] > attack["rank"]
    return defense["suit"] == trump and attack["suit"] != trump


def rank_label(rank: int) -> str:
    return RANK_NAMES.get(rank, str(rank))


def card_label(card: dict[str, Any]) -> str:
    return f"{rank_label(card['rank'])}{card['suit']}"


@dataclass
class Player:
    user_id: str
    name: str
    photo_url: str | None = None
    hand: list[dict[str, Any]] = field(default_factory=list)
    connections: int = 0
    rounds_won: int = 0
    takes: int = 0
    attacks: int = 0
    defenses: int = 0


@dataclass
class Room:
    room_id: str
    context: str
    players: list[Player] = field(default_factory=list)
    deck: list[dict[str, Any]] = field(default_factory=list)
    trump: str | None = None
    table: list[dict[str, Any]] = field(default_factory=list)
    discard: list[dict[str, Any]] = field(default_factory=list)
    attacker: int = 0
    defender: int = 1
    phase: str = "lobby"
    host_id: str | None = None
    message: str = "Создай стол и позови друзей."
    event: str = ""
    event_id: int = 0
    winner_ids: list[str] = field(default_factory=list)
    durak_id: str | None = None
    round_no: int = 0

    def player(self, uid: str) -> Player | None:
        return next((p for p in self.players if p.user_id == uid), None)

    def next_idx(self, idx: int) -> int:
        return (idx + 1) % len(self.players)

    def living_players(self) -> list[Player]:
        return [p for p in self.players if p.hand]

    def table_ranks(self) -> set[int]:
        ranks: set[int] = set()
        for pair in self.table:
            ranks.add(pair["attack"]["rank"])
            if pair.get("defense"):
                ranks.add(pair["defense"]["rank"])
        return ranks

    def open_attacks(self) -> int:
        return sum(1 for x in self.table if x.get("defense") is None)

    def max_attacks(self) -> int:
        if not self.players:
            return 0
        return min(6, len(self.players[self.defender].hand))

    def can_add_rank(self, card: dict[str, Any]) -> bool:
        if not self.table:
            return True
        return card["rank"] in self.table_ranks()

    def sort_hand(self, player: Player) -> None:
        trump = self.trump
        player.hand.sort(key=lambda c: (0 if c["suit"] == trump else 1, c["rank"], SUITS.index(c["suit"])))

    def refill(self) -> None:
        order = [(self.attacker + offset) % len(self.players) for offset in range(len(self.players))]
        for idx in order:
            p = self.players[idx]
            while len(p.hand) < 6 and self.deck:
                p.hand.append(self.deck.pop(0))
            self.sort_hand(p)

    def emit(self, event: str, message: str) -> None:
        self.event = event
        self.message = message
        self.event_id += 1

    def start(self, uid: str) -> tuple[bool, str]:
        if self.phase != "lobby":
            return False, "Партия уже идёт."
        if uid != self.host_id:
            return False, "Начать игру может создатель стола."
        if len(self.players) < 2:
            return False, "Нужен хотя бы ещё один игрок."
        self.deck = make_deck()
        random.shuffle(self.deck)
        self.table.clear()
        self.discard.clear()
        self.winner_ids.clear()
        self.durak_id = None
        self.round_no = 1
        for p in self.players:
            p.hand = []
        # Keep one bottom card visible as trump; draw from the front so it stays there until last.
        for _ in range(6):
            for p in self.players:
                if self.deck:
                    p.hand.append(self.deck.pop(0))
        self.trump = self.deck[-1]["suit"] if self.deck else random.choice(SUITS)
        for p in self.players:
            self.sort_hand(p)
        trumps = []
        for i, p in enumerate(self.players):
            cards = [c for c in p.hand if c["suit"] == self.trump]
            if cards:
                trumps.append((min(cards, key=lambda c: c["rank"])["rank"], i))
        self.attacker = min(trumps)[1] if trumps else random.randrange(len(self.players))
        self.defender = self.next_idx(self.attacker)
        a = self.players[self.attacker].name
        self.phase = "playing"
        self.emit("start", f"Раунд {self.round_no}. Первый ход — {a}.")
        return True, "Игра началась."

    def attack(self, uid: str, cid: str) -> tuple[bool, str]:
        if self.phase != "playing":
            return False, "Сейчас не время атаки."
        if uid == self.players[self.defender].user_id:
            return False, "Защищающийся не может подкидывать."
        p = self.player(uid)
        if not p:
            return False, "Вы не игрок этого стола."
        card = next((c for c in p.hand if card_id(c) == cid), None)
        if not card:
            return False, "Такой карты нет в руке."
        if not self.table and uid != self.players[self.attacker].user_id:
            return False, "Начать атаку может только атакующий."
        if self.table and self.open_attacks() == 0 and uid != self.players[self.attacker].user_id:
            return False, "Первый атакующий должен решить, продолжать ли заход."
        if len(self.table) >= self.max_attacks():
            return False, "Больше карт подкинуть нельзя."
        if not self.can_add_rank(card):
            return False, "Подкинуть можно только достоинство, которое уже есть на столе."
        p.hand.remove(card)
        p.attacks += 1
        self.table.append({"attack": card, "defense": None, "by": uid})
        label = card_label(card)
        if card["rank"] == 14:
            self.emit("ace", f"{p.name}: ТУЗ. Очень смело. 👑")
        elif card["rank"] == 7:
            self.emit("seven", f"{p.name} достаёт семёрку судьбы. 🕯️")
        else:
            self.emit("attack", f"{p.name} подкинул {label}.")
        return True, "Карта сыграна."

    def defend(self, uid: str, cid: str, target: int) -> tuple[bool, str]:
        if self.phase != "playing" or uid != self.players[self.defender].user_id:
            return False, "Сейчас защищается другой игрок."
        if target < 0 or target >= len(self.table):
            return False, "Выбери карту атаки на столе."
        pair = self.table[target]
        if pair.get("defense"):
            return False, "Эта карта уже отбита."
        p = self.player(uid)
        card = next((c for c in p.hand if card_id(c) == cid), None)
        if not card:
            return False, "Такой карты нет в руке."
        if not beats(card, pair["attack"], self.trump or "♠"):
            return False, "Этой картой не отбиться."
        p.hand.remove(card)
        p.defenses += 1
        pair["defense"] = card
        self.emit("defense", f"{p.name} отбился {card_label(card)}. 🛡️")
        return True, "Отбито."

    def take(self, uid: str) -> tuple[bool, str]:
        if self.phase != "playing" or uid != self.players[self.defender].user_id:
            return False, "Забрать стол может только защищающийся."
        p = self.player(uid)
        cards: list[dict[str, Any]] = []
        for pair in self.table:
            cards.append(pair["attack"])
            if pair.get("defense"):
                cards.append(pair["defense"])
        p.hand.extend(cards)
        p.takes += 1
        self.table.clear()
        self.attacker = self.defender
        self.defender = self.next_idx(self.attacker)
        self.refill()
        self.emit("take", f"{p.name} забирает стол. Это было больно. 😈")
        self._check_game_over()
        return True, "Стол забран."

    def finish_round(self, uid: str) -> tuple[bool, str]:
        if self.phase != "playing" or uid != self.players[self.attacker].user_id:
            return False, "Завершить заход может атакующий."
        if not self.table:
            return False, "Сначала нужно сыграть карту."
        if self.open_attacks():
            return False, "Защита ещё не закончена."
        attacker_name = self.players[self.attacker].name
        for pair in self.table:
            self.discard.append(pair["attack"])
            if pair.get("defense"):
                self.discard.append(pair["defense"])
        self.table.clear()
        self.attacker = self.defender
        self.defender = self.next_idx(self.attacker)
        self.refill()
        self._check_game_over()
        if self.phase == "playing":
            self.round_no += 1
            self.emit("clear", f"Отбой. Теперь атакует {self.players[self.attacker].name}.")
        else:
            self.event = f"{attacker_name} поставил точку."
            self.event_id += 1
        return True, "Отбой."

    def _check_game_over(self) -> None:
        if self.deck:
            return
        empty = [p for p in self.players if not p.hand]
        if not empty:
            return
        self.winner_ids = [p.user_id for p in empty]
        for p in empty:
            p.rounds_won += 1
        alive = [p for p in self.players if p.hand]
        self.durak_id = alive[0].user_id if len(alive) == 1 else None
        self.phase = "finished"
        winners = ", ".join(p.name for p in empty)
        if self.durak_id:
            loser = self.player(self.durak_id)
            self.emit("win", f"🏆 {winners} победил(и). Дурак — {loser.name}.")
        else:
            self.emit("win", f"🏆 {winners} закончил(и) игру одновременно.")

    def reset_lobby(self) -> None:
        self.deck.clear()
        self.table.clear()
        self.discard.clear()
        self.trump = None
        self.phase = "lobby"
        self.winner_ids.clear()
        self.durak_id = None
        self.round_no = 0
        for p in self.players:
            p.hand.clear()
        self.emit("lobby", "Стол снова открыт. Собираем игроков.")

    def public_state(self, viewer_id: str, mini_app_link: str | None = None) -> dict[str, Any]:
        viewer = self.player(viewer_id)
        me_index = next((i for i, p in enumerate(self.players) if p.user_id == viewer_id), -1)
        players = []
        for i, p in enumerate(self.players):
            players.append({
                "id": p.user_id,
                "name": p.name,
                "photo_url": p.photo_url,
                "count": len(p.hand),
                "connected": p.connections > 0,
                "is_me": p.user_id == viewer_id,
                "is_host": p.user_id == self.host_id,
                "role": "attacker" if self.phase == "playing" and i == self.attacker else "defender" if self.phase == "playing" and i == self.defender else "player",
                "rounds_won": p.rounds_won,
            })
        can_attack = self.phase == "playing" and me_index != self.defender and bool(viewer)
        can_start = self.phase == "lobby" and viewer_id == self.host_id and len(self.players) >= 2
        can_finish = self.phase == "playing" and me_index == self.attacker and bool(self.table) and self.open_attacks() == 0
        can_take = self.phase == "playing" and me_index == self.defender and bool(self.table)
        can_defend = self.phase == "playing" and me_index == self.defender and self.open_attacks() > 0
        return {
            "room_id": self.room_id,
            "phase": self.phase,
            "me": viewer_id,
            "host_id": self.host_id,
            "players": players,
            "hand": viewer.hand if viewer else [],
            "trump": self.trump,
            "deck_count": len(self.deck),
            "table": self.table,
            "attacker": self.players[self.attacker].user_id if self.players and self.phase == "playing" else None,
            "defender": self.players[self.defender].user_id if self.players and self.phase == "playing" else None,
            "round": self.round_no,
            "can_attack": can_attack,
            "can_start": can_start,
            "can_finish": can_finish,
            "can_take": can_take,
            "can_defend": can_defend,
            "can_reset": self.phase == "finished" and viewer_id == self.host_id,
            "open_attacks": self.open_attacks(),
            "max_attacks": self.max_attacks(),
            "message": self.message,
            "event": self.event,
            "event_id": self.event_id,
            "winner_ids": self.winner_ids,
            "durak_id": self.durak_id,
            "mini_app_link": mini_app_link,
        }
