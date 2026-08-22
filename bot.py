from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import random
import re
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


SLOT_MACHINE_EMOJI = "🎰"
SLOT_MACHINE_JACKPOT_VALUE = 64

DEFAULT_CASE_COUNT = 25
DEFAULT_NFT_CHANCE_DENOMINATOR = 25
DEFAULT_SPIN_PRICE_STARS = 2.0
DEFAULT_SERVICE_COMMISSION_PERCENT = 15.0
AUTO_STATS_SECONDS = 6 * 3600
AUTO_STATS_SPIN_INTERVAL = 200

SETTING_CASE_COUNT = "case_count"
SETTING_NFT_CHANCE_DENOMINATOR = "nft_chance_denominator"
SETTING_SPIN_PRICE_STARS = "spin_price_stars"
SETTING_SERVICE_COMMISSION_PERCENT = "service_commission_percent"

STATS_PERIODS = {
    "1h": ("за последний час", timedelta(hours=1)),
    "6h": ("за последние 6 часов", timedelta(hours=6)),
    "24h": ("за последние 24 часа", timedelta(hours=24)),
    "7d": ("за последнюю неделю", timedelta(days=7)),
    "all": ("за все время", None),
}

TEMPLATE_DEFAULTS = {
    "jackpot_start": (
        "<b>{username}</b>, вы выбили <b>777</b>.\n\n"
        "Откройте одну клетку на поле <b>{case_count}</b> кейсов.\n"
        "Шанс NFT: <b>{nft_chance}</b>."
    ),
    "nft_win": (
        "💎 <b>{username}, поздравляем!</b> 💎\n"
        "Вы выиграли NFT: <b>{gift_title}</b>\n"
        "{gift_url}\n\n"
        "Администратор получил уведомление о выдаче."
    ),
    "gift_win": (
        "💎 <b>{username}, поздравляем!</b> 💎\n"
        "Вы выиграли подарок: <b>{gift_title}</b>\n\n"
        "Администратор получил уведомление о выдаче."
    ),
    "empty_win": (
        "<b>{username}</b>, в этой клетке пусто.\n"
        "Попробуйте выбить 777 еще раз."
    ),
    "stats": (
        "<b>Статистика лудки {period_title}</b>\n\n"
        "Всего прокрутов: <b>{total_spins}</b>\n"
        "777: <b>{jackpots}</b>\n"
        "Открыто кейсов: <b>{opened_cases}</b>\n"
        "NFT: <b>{nft_wins}</b>\n"
        "Подарки: <b>{gift_wins}</b>\n"
        "Пусто: <b>{empty_wins}</b>\n"
        "Оборот: <b>{gross_stars}</b>⭐\n"
        "После комиссии: <b>{net_stars}</b>⭐\n\n"
        "<b>Топ по прокрутам:</b>\n{top5}"
    ),
    "top5_empty": "Пока нет прокрутов.",
    "top5_row": "{place}. <b>{username}</b> - {total_spins} прокрутов, 777: {jackpots}, NFT: {nft_wins}",
    "mystats": (
        "<b>{username}</b>\n\n"
        "Прокруты: <b>{total_spins}</b>\n"
        "777: <b>{jackpots}</b>\n"
        "Открыто кейсов: <b>{opened_cases}</b>\n"
        "NFT: <b>{nft_wins}</b>\n"
        "Подарки: <b>{gift_wins}</b>\n"
        "Пусто: <b>{empty_wins}</b>"
    ),
    "owner_payout": (
        "<b>Новый выигрыш</b>\n\n"
        "Игрок: <b>{username}</b>\n"
        "Тип: <b>{result_type}</b>\n"
        "Подарок: <b>{gift_title}</b>\n"
        "{gift_url}"
    ),
    "payout_done": "Выдача подарка <b>{gift_title}</b> отмечена завершенной.",
    "owner_panel": (
        "<b>Owner-панель</b>\n\n"
        "Кейсов: <b>{case_count}</b>\n"
        "Цена прокрута: <b>{spin_price}⭐</b>\n"
        "Шанс NFT: <b>{nft_chance}</b>\n"
        "Комиссия сервиса: <b>{commission_percent}%</b>"
    ),
    "stats_choose_period": "<b>Выберите период статистики</b>",
    "game_settings": (
        "<b>Настройки игры</b>\n\n"
        "Кейсов: <b>{case_count}</b>\n"
        "Шанс NFT: <b>{nft_chance}</b>\n"
        "Цена прокрута: <b>{spin_price}⭐</b>\n"
        "Комиссия: <b>{commission_percent}%</b>\n\n"
        "Команды:\n"
        "<code>/game count 25</code>\n"
        "<code>/game chance 1/25</code>\n"
        "<code>/game price 2</code>\n"
        "<code>/game commission 15</code>"
    ),
    "game_saved": (
        "Готово.\n\n"
        "<b>Настройки игры</b>\n\n"
        "Кейсов: <b>{case_count}</b>\n"
        "Шанс NFT: <b>{nft_chance}</b>\n"
        "Цена прокрута: <b>{spin_price}⭐</b>\n"
        "Комиссия: <b>{commission_percent}%</b>"
    ),
    "validation_error": "{error}",
    "gift_added": "Добавил подарок #{gift_id}.",
    "action_done": "Готово.",
    "settext_empty": "Напишите текст после ключа или ответьте командой на готовое сообщение.",
    "settext_saved": "Шаблон {template_key} сохранен.",
    "allowed_chats_empty": "ALLOWED_CHAT_IDS пустой.",
    "no_allowed_chat": "Нет разрешенного чата.",
    "mystats_empty": "У вас пока нет прокрутов.",
    "reset_wrong_chat": "Команду нужно писать в разрешенном игровом чате.",
    "reset_done": "Статистика этого чата обнулена.",
    "case_not_found": "Раунд не найден.",
    "case_wrong_user": "Это не ваш кейс.",
    "case_already_opened": "Кейс уже открыт.",
    "case_opened_alert": "Кейс открыт.",
    "payout_done_alert": "Выдача отмечена завершенной.",
    "paid_already_alert": "Уже завершено.",
    "chat_id": "chat_id: {chat_id}\nuser_id: {user_id}",
    "giftbank": (
        "<b>Gifts owner</b>\n"
        "{owner_gifts}\n\n"
        "{blocked_gifts}\n\n"
        "{fallback_gifts}\n\n"
        "Команды:\n"
        "<code>/giftbank</code>\n"
        "<code>/gift block https://t.me/nft/name</code>\n"
        "<code>/gift unblock https://t.me/nft/name</code>\n"
        "<code>/gift add nft https://t.me/nft/name Название</code> - fallback\n"
        "<code>/gift fallbackblock 1</code>\n"
        "<code>/gift fallbackunblock 1</code>\n"
        "<code>/gift fallbackremove 1</code>"
    ),
    "giftbank_empty": "Активных gifts owner не найдено.",
    "giftbank_fetch_error": "Не удалось получить gifts owner: <code>{error}</code>",
    "giftbank_blocked_header": "<b>Заблокировано:</b>\n{blocked_list}",
    "giftbank_fallback_header": "<b>Fallback-подарки, если Telegram API не вернул owner gifts:</b>\n{fallback_list}",
    "texts_panel": (
        "<b>Настройка текстов</b>\n\n"
        "Ключи:\n{template_keys}\n\n"
        "Placeholders:\n{placeholders}"
    ),
    "template_detail": (
        "<b>Шаблон: {template_key}</b>\n\n"
        "{template_text}\n\n"
        "Изменить:\n"
        "<code>/settext {template_key} новый текст</code>\n\n"
        "Для custom emoji/жирного текста отправьте готовое сообщение "
        "и ответьте на него <code>/settext {template_key}</code>."
    ),
    "help": (
        "<b>Как играть</b>\n\n"
        "Отправляйте официальный Telegram-слот 🎰 в игровом чате.\n"
        "Если выпадает 777, бот открывает поле кейсов.\n"
        "Откройте одну клетку и заберите результат.\n\n"
        "/stats - статистика чата\n"
        "/mystats - ваша статистика"
    ),
}


@dataclass(frozen=True)
class BotConfig:
    token: str
    db_path: Path
    allowed_chat_ids: set[int]
    owner_user_ids: set[int]


def parse_ids(value: str | None) -> set[int]:
    if not value:
        return set()

    result: set[int] = set()
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if item.startswith("="):
            item = item[1:].strip()
        result.add(int(item))
    return result


def read_config() -> BotConfig:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN.")

    data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    default_db = data_dir / "twist_ludka.sqlite3"
    db_path = Path(os.getenv("DATABASE_PATH", str(default_db)))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    allowed_chat_ids = parse_ids(os.getenv("ALLOWED_CHAT_IDS"))
    owner_user_ids = parse_ids(os.getenv("OWNER_USER_IDS"))

    if not allowed_chat_ids:
        logging.warning("ALLOWED_CHAT_IDS пустой. Бот не будет реагировать в группах.")
    if not owner_user_ids:
        logging.warning("OWNER_USER_IDS пустой. Owner-команды будут недоступны.")

    return BotConfig(
        token=token,
        db_path=db_path,
        allowed_chat_ids=allowed_chat_ids,
        owner_user_ids=owner_user_ids,
    )


class Database:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                private_started INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS processed_messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS slot_stats (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                total_spins INTEGER NOT NULL DEFAULT 0,
                jackpots INTEGER NOT NULL DEFAULT 0,
                opened_cases INTEGER NOT NULL DEFAULT 0,
                nft_wins INTEGER NOT NULL DEFAULT 0,
                gift_wins INTEGER NOT NULL DEFAULT 0,
                empty_wins INTEGER NOT NULL DEFAULT 0,
                gross_stars REAL NOT NULL DEFAULT 0,
                net_stars REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS case_rounds (
                round_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_id INTEGER,
                case_count INTEGER NOT NULL,
                selected_position INTEGER,
                result_type TEXT,
                gift_id INTEGER,
                gift_title TEXT,
                gift_url TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                opened_at TEXT
            );

            CREATE TABLE IF NOT EXISTS gifts (
                gift_id INTEGER PRIMARY KEY AUTOINCREMENT,
                gift_type TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS payouts (
                payout_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                round_id INTEGER,
                payout_type TEXT NOT NULL,
                gift_title TEXT NOT NULL,
                gift_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS game_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                result_type TEXT NOT NULL DEFAULT '',
                gross_stars REAL NOT NULL DEFAULT 0,
                net_stars REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS gift_blocks (
                gift_key TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS templates (
                key TEXT PRIMARY KEY,
                text TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def remember_user(self, user: User, private_started: bool = False) -> None:
        self.connection.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, private_started)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                private_started = CASE
                    WHEN excluded.private_started = 1 THEN 1
                    ELSE users.private_started
                END,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
                1 if private_started else 0,
            ),
        )
        self.connection.commit()

    def remember_chat(self, chat_id: int, title: str | None) -> None:
        self.connection.execute(
            """
            INSERT INTO chats (chat_id, title)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (chat_id, title or ""),
        )
        self.connection.commit()

    def mark_message_processed(self, chat_id: int, message_id: int) -> bool:
        try:
            self.connection.execute(
                "INSERT INTO processed_messages (chat_id, message_id) VALUES (?, ?)",
                (chat_id, message_id),
            )
        except sqlite3.IntegrityError:
            return False
        self.connection.commit()
        return True

    def get_setting(self, key: str, default: object) -> object:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return default
        return row["value"]

    def set_setting(self, key: str, value: object) -> None:
        self.connection.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
        self.connection.commit()

    def get_template(self, key: str) -> str:
        row = self.connection.execute(
            "SELECT text FROM templates WHERE key = ?",
            (key,),
        ).fetchone()
        return row["text"] if row else TEMPLATE_DEFAULTS[key]

    def set_template(self, key: str, text: str) -> None:
        self.connection.execute(
            """
            INSERT INTO templates (key, text)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET text = excluded.text
            """,
            (key, text),
        )
        self.connection.commit()

    def ensure_stat_row(self, chat_id: int, user_id: int) -> None:
        self.connection.execute(
            """
            INSERT INTO slot_stats (chat_id, user_id)
            VALUES (?, ?)
            ON CONFLICT(chat_id, user_id) DO NOTHING
            """,
            (chat_id, user_id),
        )

    def record_spin(self, chat_id: int, user_id: int, price: float, net_price: float) -> None:
        self.ensure_stat_row(chat_id, user_id)
        self.connection.execute(
            """
            UPDATE slot_stats
            SET total_spins = total_spins + 1,
                gross_stars = gross_stars + ?,
                net_stars = net_stars + ?
            WHERE chat_id = ? AND user_id = ?
            """,
            (price, net_price, chat_id, user_id),
        )
        self.record_event_no_commit(chat_id, user_id, "spin", "", price, net_price)
        self.connection.commit()

    def record_jackpot(self, chat_id: int, user_id: int) -> None:
        self.ensure_stat_row(chat_id, user_id)
        self.connection.execute(
            """
            UPDATE slot_stats
            SET jackpots = jackpots + 1
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        self.record_event_no_commit(chat_id, user_id, "jackpot")
        self.connection.commit()

    def create_case_round(self, chat_id: int, user_id: int, case_count: int) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO case_rounds (chat_id, user_id, case_count)
            VALUES (?, ?, ?)
            """,
            (chat_id, user_id, case_count),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def set_case_round_message(self, round_id: int, message_id: int) -> None:
        self.connection.execute(
            "UPDATE case_rounds SET message_id = ? WHERE round_id = ?",
            (message_id, round_id),
        )
        self.connection.commit()

    def get_case_round(self, round_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM case_rounds WHERE round_id = ?",
            (round_id,),
        ).fetchone()

    def close_case_round(
        self,
        round_id: int,
        selected_position: int,
        result_type: str,
        gift: dict[str, object] | sqlite3.Row | None,
    ) -> None:
        gift_id = None
        gift_title = ""
        gift_url = ""
        if gift is not None:
            if isinstance(gift, sqlite3.Row):
                gift_id = gift["gift_id"]
                gift_title = gift["title"]
                gift_url = gift["url"]
            else:
                gift_title = str(gift.get("title", ""))
                gift_url = str(gift.get("url", ""))
        self.connection.execute(
            """
            UPDATE case_rounds
            SET selected_position = ?,
                result_type = ?,
                gift_id = ?,
                gift_title = ?,
                gift_url = ?,
                status = 'opened',
                opened_at = CURRENT_TIMESTAMP
            WHERE round_id = ?
            """,
            (
                selected_position,
                result_type,
                gift_id,
                gift_title,
                gift_url,
                round_id,
            ),
        )
        self.connection.commit()

    def record_case_result(self, chat_id: int, user_id: int, result_type: str) -> None:
        self.ensure_stat_row(chat_id, user_id)
        column = {
            "nft": "nft_wins",
            "ordinary": "gift_wins",
            "deleted": "gift_wins",
            "empty": "empty_wins",
        }[result_type]
        self.connection.execute(
            f"""
            UPDATE slot_stats
            SET opened_cases = opened_cases + 1,
                {column} = {column} + 1
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        event_type = {
            "nft": "case_nft",
            "ordinary": "case_gift",
            "deleted": "case_gift",
            "empty": "case_empty",
        }[result_type]
        self.record_event_no_commit(chat_id, user_id, event_type, result_type)
        self.connection.commit()

    def record_event_no_commit(
        self,
        chat_id: int,
        user_id: int,
        event_type: str,
        result_type: str = "",
        gross_stars: float = 0,
        net_stars: float = 0,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO game_events (chat_id, user_id, event_type, result_type, gross_stars, net_stars)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, user_id, event_type, result_type, gross_stars, net_stars),
        )

    def add_gift(self, gift_type: str, title: str, url: str = "") -> int:
        cursor = self.connection.execute(
            "INSERT INTO gifts (gift_type, title, url) VALUES (?, ?, ?)",
            (gift_type, title, url),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def set_gift_blocked(self, gift_id: int, blocked: bool) -> None:
        self.connection.execute(
            "UPDATE gifts SET is_blocked = ? WHERE gift_id = ?",
            (1 if blocked else 0, gift_id),
        )
        self.connection.commit()

    def remove_gift(self, gift_id: int) -> None:
        self.connection.execute("DELETE FROM gifts WHERE gift_id = ?", (gift_id,))
        self.connection.commit()

    def list_gifts(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT *
                FROM gifts
                ORDER BY is_blocked ASC, gift_type ASC, gift_id ASC
                """
            )
        )

    def choose_gift(self, gift_type: str | None = None) -> sqlite3.Row | None:
        params: list[object] = []
        query = "SELECT * FROM gifts WHERE is_blocked = 0"
        if gift_type:
            query += " AND gift_type = ?"
            params.append(gift_type)
        rows = list(self.connection.execute(query, params))
        return random.choice(rows) if rows else None

    def block_owner_gift(self, gift_key: str, title: str = "") -> None:
        self.connection.execute(
            """
            INSERT INTO gift_blocks (gift_key, title)
            VALUES (?, ?)
            ON CONFLICT(gift_key) DO UPDATE SET title = excluded.title
            """,
            (gift_key, title),
        )
        self.connection.commit()

    def unblock_owner_gift(self, gift_key: str) -> None:
        self.connection.execute("DELETE FROM gift_blocks WHERE gift_key = ?", (gift_key,))
        self.connection.commit()

    def is_owner_gift_blocked(self, gift_key: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM gift_blocks WHERE gift_key = ?",
            (gift_key,),
        ).fetchone()
        return row is not None

    def list_gift_blocks(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM gift_blocks ORDER BY created_at DESC"
            )
        )

    def create_payout(
        self,
        chat_id: int,
        user_id: int,
        round_id: int,
        payout_type: str,
        gift_title: str,
        gift_url: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO payouts (chat_id, user_id, round_id, payout_type, gift_title, gift_url)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, user_id, round_id, payout_type, gift_title, gift_url),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def complete_payout(self, payout_id: int) -> sqlite3.Row | None:
        self.connection.execute(
            """
            UPDATE payouts
            SET status = 'paid', completed_at = CURRENT_TIMESTAMP
            WHERE payout_id = ?
            """,
            (payout_id,),
        )
        self.connection.commit()
        return self.connection.execute(
            "SELECT * FROM payouts WHERE payout_id = ?",
            (payout_id,),
        ).fetchone()

    def get_totals(self, chat_id: int) -> sqlite3.Row:
        row = self.connection.execute(
            """
            SELECT
                COALESCE(SUM(total_spins), 0) AS total_spins,
                COALESCE(SUM(jackpots), 0) AS jackpots,
                COALESCE(SUM(opened_cases), 0) AS opened_cases,
                COALESCE(SUM(nft_wins), 0) AS nft_wins,
                COALESCE(SUM(gift_wins), 0) AS gift_wins,
                COALESCE(SUM(empty_wins), 0) AS empty_wins,
                COALESCE(SUM(gross_stars), 0) AS gross_stars,
                COALESCE(SUM(net_stars), 0) AS net_stars
            FROM slot_stats
            WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Stats query failed.")
        return row

    def get_event_totals(self, chat_id: int, since: datetime | None = None) -> sqlite3.Row:
        where = "WHERE chat_id = ?"
        params: list[object] = [chat_id]
        if since is not None:
            where += " AND created_at >= ?"
            params.append(since.strftime("%Y-%m-%d %H:%M:%S"))

        row = self.connection.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN event_type = 'spin' THEN 1 ELSE 0 END), 0) AS total_spins,
                COALESCE(SUM(CASE WHEN event_type = 'jackpot' THEN 1 ELSE 0 END), 0) AS jackpots,
                COALESCE(SUM(CASE WHEN event_type IN ('case_nft', 'case_gift', 'case_empty') THEN 1 ELSE 0 END), 0) AS opened_cases,
                COALESCE(SUM(CASE WHEN event_type = 'case_nft' THEN 1 ELSE 0 END), 0) AS nft_wins,
                COALESCE(SUM(CASE WHEN event_type = 'case_gift' THEN 1 ELSE 0 END), 0) AS gift_wins,
                COALESCE(SUM(CASE WHEN event_type = 'case_empty' THEN 1 ELSE 0 END), 0) AS empty_wins,
                COALESCE(SUM(gross_stars), 0) AS gross_stars,
                COALESCE(SUM(net_stars), 0) AS net_stars
            FROM game_events
            {where}
            """,
            params,
        ).fetchone()
        if row is None:
            raise RuntimeError("Event stats query failed.")
        return row

    def get_top_rows(self, chat_id: int, limit: int = 5) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT s.*, u.username, u.first_name, u.last_name
                FROM slot_stats s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.chat_id = ?
                ORDER BY s.total_spins DESC, s.jackpots DESC, s.nft_wins DESC
                LIMIT ?
                """,
                (chat_id, limit),
            )
        )

    def get_event_top_rows(
        self,
        chat_id: int,
        since: datetime | None = None,
        limit: int = 5,
    ) -> list[sqlite3.Row]:
        where = "WHERE e.chat_id = ?"
        params: list[object] = [chat_id]
        if since is not None:
            where += " AND e.created_at >= ?"
            params.append(since.strftime("%Y-%m-%d %H:%M:%S"))
        params.append(limit)

        return list(
            self.connection.execute(
                f"""
                SELECT
                    e.user_id,
                    COALESCE(SUM(CASE WHEN e.event_type = 'spin' THEN 1 ELSE 0 END), 0) AS total_spins,
                    COALESCE(SUM(CASE WHEN e.event_type = 'jackpot' THEN 1 ELSE 0 END), 0) AS jackpots,
                    COALESCE(SUM(CASE WHEN e.event_type = 'case_nft' THEN 1 ELSE 0 END), 0) AS nft_wins,
                    u.username,
                    u.first_name,
                    u.last_name
                FROM game_events e
                JOIN users u ON u.user_id = e.user_id
                {where}
                GROUP BY e.user_id
                HAVING total_spins > 0
                ORDER BY total_spins DESC, jackpots DESC, nft_wins DESC
                LIMIT ?
                """,
                params,
            )
        )

    def get_user_stats(self, chat_id: int, user_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM slot_stats WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()

    def reset_stats(self, chat_id: int) -> None:
        self.connection.execute("DELETE FROM slot_stats WHERE chat_id = ?", (chat_id,))
        self.connection.execute("DELETE FROM processed_messages WHERE chat_id = ?", (chat_id,))
        self.connection.execute("DELETE FROM case_rounds WHERE chat_id = ?", (chat_id,))
        self.connection.execute("DELETE FROM game_events WHERE chat_id = ?", (chat_id,))
        self.connection.commit()


def db_from_context(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def config_from_context(context: ContextTypes.DEFAULT_TYPE) -> BotConfig:
    return context.application.bot_data["config"]


def is_allowed_chat(config: BotConfig, chat_id: int | None) -> bool:
    return chat_id is not None and chat_id in config.allowed_chat_ids


def is_owner(config: BotConfig, user_id: int | None) -> bool:
    return user_id is not None and user_id in config.owner_user_ids


def chat_title(update: Update) -> str:
    chat = update.effective_chat
    if not chat:
        return ""
    return chat.title or chat.username or str(chat.id)


def display_name_from_user(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part)
    return full_name or str(user.id)


def display_name_from_row(row: sqlite3.Row) -> str:
    if row["username"]:
        return f"@{row['username']}"
    full_name = " ".join(part for part in [row["first_name"], row["last_name"]] if part)
    return full_name or str(row["user_id"])


def html_escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def telegram_api_call(token: str, method: str, payload: dict[str, object]) -> dict[str, object]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not decoded.get("ok"):
        description = decoded.get("description", "unknown Telegram API error")
        raise RuntimeError(str(description))
    result = decoded.get("result")
    return result if isinstance(result, dict) else {"result": result}


def unique_gift_url(name: str) -> str:
    return f"https://t.me/nft/{urllib.parse.quote(name, safe='')}" if name else ""


def gift_key(gift: dict[str, object]) -> str:
    if gift.get("owned_gift_id"):
        return str(gift["owned_gift_id"])
    if gift.get("url"):
        return str(gift["url"]).casefold()
    return f"{gift.get('type', '')}:{gift.get('title', '')}".casefold()


def parse_owned_gift(raw: dict[str, object]) -> dict[str, object] | None:
    gift_type = raw.get("type")
    gift_data = raw.get("gift")
    if not isinstance(gift_data, dict):
        return None

    if gift_type == "unique":
        name = str(gift_data.get("name") or "")
        title = str(
            gift_data.get("title")
            or gift_data.get("base_name")
            or name
            or "NFT"
        )
        number = gift_data.get("number")
        if number:
            title = f"{title} #{number}"
        return {
            "type": "nft",
            "title": title,
            "url": unique_gift_url(name),
            "owned_gift_id": str(raw.get("owned_gift_id") or name or title),
            "raw": raw,
        }

    if gift_type == "regular":
        title = str(gift_data.get("title") or gift_data.get("id") or "Gift")
        return {
            "type": "ordinary",
            "title": title,
            "url": "",
            "owned_gift_id": str(raw.get("owned_gift_id") or gift_data.get("id") or title),
            "raw": raw,
        }

    return None


async def fetch_owner_gifts(
    config: BotConfig,
    db: Database,
    gift_type: str | None = None,
) -> list[dict[str, object]]:
    gifts: list[dict[str, object]] = []
    for owner_id in config.owner_user_ids:
        offset = ""
        while True:
            payload: dict[str, object] = {
                "user_id": owner_id,
                "limit": 100,
                "offset": offset,
            }
            result = await asyncio.to_thread(
                telegram_api_call,
                config.token,
                "getUserGifts",
                payload,
            )
            raw_gifts = result.get("gifts", [])
            if not isinstance(raw_gifts, list):
                break
            for raw_gift in raw_gifts:
                if not isinstance(raw_gift, dict):
                    continue
                parsed = parse_owned_gift(raw_gift)
                if parsed is None:
                    continue
                if gift_type and parsed["type"] != gift_type:
                    continue
                block_keys = {
                    gift_key(parsed),
                    str(parsed.get("url", "")).casefold(),
                    str(parsed.get("title", "")).casefold(),
                }
                if any(key and db.is_owner_gift_blocked(key) for key in block_keys):
                    continue
                gifts.append(parsed)
            next_offset = result.get("next_offset")
            if not next_offset:
                break
            offset = str(next_offset)
    return gifts


async def choose_owner_gift(
    config: BotConfig,
    db: Database,
    gift_type: str | None = None,
) -> dict[str, object] | None:
    try:
        gifts = await fetch_owner_gifts(config, db, gift_type)
    except Exception as error:
        logging.warning("Cannot fetch owner gifts: %s", error)
        gifts = []

    if gifts:
        return random.choice(gifts)

    fallback = db.choose_gift(gift_type)
    if fallback is None:
        return None
    return {
        "type": fallback["gift_type"],
        "title": fallback["title"],
        "url": fallback["url"],
        "owned_gift_id": str(fallback["gift_id"]),
    }


def get_case_count(db: Database) -> int:
    try:
        value = int(db.get_setting(SETTING_CASE_COUNT, DEFAULT_CASE_COUNT))
    except (TypeError, ValueError):
        return DEFAULT_CASE_COUNT
    return max(1, min(value, 100))


def get_nft_chance_denominator(db: Database) -> int:
    try:
        value = int(db.get_setting(SETTING_NFT_CHANCE_DENOMINATOR, DEFAULT_NFT_CHANCE_DENOMINATOR))
    except (TypeError, ValueError):
        return DEFAULT_NFT_CHANCE_DENOMINATOR
    return max(1, value)


def get_spin_price(db: Database) -> float:
    try:
        value = float(db.get_setting(SETTING_SPIN_PRICE_STARS, DEFAULT_SPIN_PRICE_STARS))
    except (TypeError, ValueError):
        return DEFAULT_SPIN_PRICE_STARS
    return max(0, value)


def get_commission_percent(db: Database) -> float:
    try:
        value = float(
            db.get_setting(
                SETTING_SERVICE_COMMISSION_PERCENT,
                DEFAULT_SERVICE_COMMISSION_PERCENT,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_SERVICE_COMMISSION_PERCENT
    return min(max(0, value), 100)


def format_stars(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def render_template(db: Database, key: str, values: dict[str, object]) -> str:
    template = db.get_template(key)
    safe_values = {name: html_escape(value) for name, value in values.items()}
    try:
        return template.format(**safe_values)
    except KeyError as error:
        missing = error.args[0]
        return f"В шаблоне <b>{html_escape(key)}</b> не найден placeholder: <b>{html_escape(missing)}</b>"


def render_template_with_raw(
    db: Database,
    key: str,
    values: dict[str, object],
    raw_keys: set[str],
) -> str:
    template = db.get_template(key)
    safe_values = {
        name: str(value) if name in raw_keys else html_escape(value)
        for name, value in values.items()
    }
    try:
        return template.format(**safe_values)
    except KeyError as error:
        missing = error.args[0]
        return f"В шаблоне <b>{html_escape(key)}</b> не найден placeholder: <b>{html_escape(missing)}</b>"


def strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def template_text(
    db: Database,
    key: str,
    user: User | None = None,
    chat_id: int | None = None,
    period_key: str = "all",
    extra: dict[str, object] | None = None,
) -> str:
    return render_template(
        db,
        key,
        build_template_values(db, user, chat_id, period_key, extra),
    )


def template_alert(
    db: Database,
    key: str,
    extra: dict[str, object] | None = None,
) -> str:
    return strip_html_tags(template_text(db, key, extra=extra))


def case_keyboard(round_id: int, case_count: int, selected_position: int | None = None, label: str = "") -> InlineKeyboardMarkup:
    side = max(1, round(case_count**0.5))
    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []

    for position in range(1, case_count + 1):
        text = "□"
        if selected_position == position:
            text = label or "✓"
        current_row.append(
            InlineKeyboardButton(text, callback_data=f"case:{round_id}:{position}")
        )
        if len(current_row) >= side:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    return InlineKeyboardMarkup(rows)


def owner_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Статистика", callback_data="panel:stats"),
                InlineKeyboardButton("Игра", callback_data="panel:game"),
            ],
            [
                InlineKeyboardButton("Подарки", callback_data="panel:gifts"),
                InlineKeyboardButton("Тексты", callback_data="panel:texts"),
            ],
        ]
    )


def stats_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1 час", callback_data="panel:stats:1h"),
                InlineKeyboardButton("6 часов", callback_data="panel:stats:6h"),
            ],
            [
                InlineKeyboardButton("24 часа", callback_data="panel:stats:24h"),
                InlineKeyboardButton("Неделя", callback_data="panel:stats:7d"),
            ],
            [InlineKeyboardButton("Все время", callback_data="panel:stats:all")],
        ]
    )


def templates_keyboard() -> InlineKeyboardMarkup:
    rows = []
    keys = list(TEMPLATE_DEFAULTS)
    for index in range(0, len(keys), 2):
        rows.append(
            [
                InlineKeyboardButton(key, callback_data=f"panel:text:{key}")
                for key in keys[index : index + 2]
            ]
        )
    return InlineKeyboardMarkup(rows)


def build_template_values(
    db: Database,
    user: User | None = None,
    chat_id: int | None = None,
    period_key: str = "all",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "username": display_name_from_user(user) if user else "",
        "case_count": get_case_count(db),
        "nft_chance": f"1/{get_nft_chance_denominator(db)}",
        "spin_price": format_stars(get_spin_price(db)),
        "commission_percent": format_stars(get_commission_percent(db)),
    }
    if chat_id is not None:
        period_title, delta = STATS_PERIODS.get(period_key, STATS_PERIODS["all"])
        since = datetime.utcnow() - delta if delta else None
        totals = db.get_event_totals(chat_id, since) if delta else db.get_totals(chat_id)
        values.update(
            {
                "period": period_key,
                "period_title": period_title,
                "total_spins": totals["total_spins"],
                "jackpots": totals["jackpots"],
                "opened_cases": totals["opened_cases"],
                "nft_wins": totals["nft_wins"],
                "gift_wins": totals["gift_wins"],
                "empty_wins": totals["empty_wins"],
                "gross_stars": format_stars(float(totals["gross_stars"])),
                "net_stars": format_stars(float(totals["net_stars"])),
                "top5": format_top5(db, chat_id, period_key),
            }
        )
    else:
        values["period"] = period_key
        values["period_title"] = STATS_PERIODS.get(period_key, STATS_PERIODS["all"])[0]
    if extra:
        values.update(extra)
    return values


def format_top5(db: Database, chat_id: int, period_key: str = "all") -> str:
    _, delta = STATS_PERIODS.get(period_key, STATS_PERIODS["all"])
    since = datetime.utcnow() - delta if delta else None
    rows = db.get_event_top_rows(chat_id, since) if delta else db.get_top_rows(chat_id)
    if not rows:
        return template_text(db, "top5_empty")
    lines = []
    for index, row in enumerate(rows, start=1):
        lines.append(
            template_text(
                db,
                "top5_row",
                extra={
                    "place": index,
                    "username": display_name_from_row(row),
                    "total_spins": row["total_spins"],
                    "jackpots": row["jackpots"],
                    "nft_wins": row["nft_wins"],
                },
            )
        )
    return "\n".join(lines)


async def send_html(message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )


async def notify_owners(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    config = config_from_context(context)
    for owner_id in config.owner_user_ids:
        try:
            await context.bot.send_message(
                owner_id,
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
        except TelegramError as error:
            logging.warning("Cannot notify owner %s: %s", owner_id, error)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    db = db_from_context(context)
    db.remember_user(update.effective_user, private_started=True)
    text = render_template(
        db,
        "help",
        build_template_values(db, update.effective_user),
    )
    await send_html(update.message, text)


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    db = db_from_context(context)
    db.remember_user(update.effective_user, update.effective_chat.type == "private")
    text = render_template(db, "help", build_template_values(db, update.effective_user))
    await send_html(update.message, text)


async def show_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.message:
        return

    db = db_from_context(context)
    await send_html(
        update.message,
        template_text(
            db,
            "chat_id",
            update.effective_user,
            extra={
                "chat_id": update.effective_chat.id,
                "user_id": update.effective_user.id,
            },
        ),
    )


async def show_owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    config = config_from_context(context)
    if not is_owner(config, update.effective_user.id):
        return

    db = db_from_context(context)
    text = template_text(db, "owner_panel", update.effective_user)
    await send_html(update.message, text, owner_panel_keyboard())


async def handle_owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return

    config = config_from_context(context)
    if not is_owner(config, query.from_user.id):
        await query.answer()
        return

    db = db_from_context(context)
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]

    if action == "game":
        text = game_settings_text(db)
        reply_markup = None
    elif action == "gifts":
        text = await gifts_text(config, db)
        reply_markup = None
    elif action == "texts":
        if len(parts) >= 3:
            key = parts[2]
            text = template_detail_text(db, key)
            reply_markup = templates_keyboard()
        else:
            text = templates_text(db)
            reply_markup = templates_keyboard()
    elif action == "stats" and len(parts) >= 3:
        chat_id = next(iter(config.allowed_chat_ids), None)
        text = (
            stats_text(db, chat_id, parts[2])
            if chat_id is not None
            else template_text(db, "allowed_chats_empty", query.from_user)
        )
        reply_markup = stats_period_keyboard()
    elif action == "stats":
        text = template_text(db, "stats_choose_period", query.from_user)
        reply_markup = stats_period_keyboard()
    else:
        chat_id = next(iter(config.allowed_chat_ids), None)
        text = (
            stats_text(db, chat_id)
            if chat_id is not None
            else template_text(db, "allowed_chats_empty", query.from_user)
        )
        reply_markup = None

    await query.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )


def game_settings_text(db: Database) -> str:
    return template_text(db, "game_settings")


async def manage_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    config = config_from_context(context)
    if not is_owner(config, update.effective_user.id):
        return

    db = db_from_context(context)
    if not context.args:
        await send_html(update.message, game_settings_text(db))
        return

    action = context.args[0].lower()
    value = " ".join(context.args[1:]).strip()
    try:
        if action in {"count", "cases", "cells", "ячейки", "кейсы"}:
            count = int(value)
            if count < 1 or count > 100:
                raise ValueError("Количество кейсов должно быть от 1 до 100.")
            db.set_setting(SETTING_CASE_COUNT, count)
        elif action in {"chance", "nftchance", "nft", "шанс"}:
            denominator = parse_chance(value)
            db.set_setting(SETTING_NFT_CHANCE_DENOMINATOR, denominator)
        elif action in {"price", "stars", "цена"}:
            price = float(value.replace(",", "."))
            if price < 0:
                raise ValueError("Цена не может быть отрицательной.")
            db.set_setting(SETTING_SPIN_PRICE_STARS, price)
        elif action in {"commission", "fee", "комиссия"}:
            percent = float(value.replace(",", "."))
            if percent < 0 or percent > 100:
                raise ValueError("Комиссия должна быть от 0 до 100.")
            db.set_setting(SETTING_SERVICE_COMMISSION_PERCENT, percent)
        else:
            await send_html(update.message, game_settings_text(db))
            return
    except ValueError as error:
        await send_html(update.message, template_text(db, "validation_error", extra={"error": error}))
        return

    await send_html(update.message, template_text(db, "game_saved"))


def parse_chance(value: str) -> int:
    value = value.strip().replace(" ", "")
    if "/" in value:
        left, right = value.split("/", 1)
        if left not in {"1", ""}:
            raise ValueError("Шанс задается как 1/N, например 1/25.")
        denominator = int(right)
    else:
        denominator = int(value)
    if denominator < 1:
        raise ValueError("Знаменатель шанса должен быть 1 или больше.")
    return denominator


async def manage_gifts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    config = config_from_context(context)
    if not is_owner(config, update.effective_user.id):
        return

    db = db_from_context(context)
    if not context.args or context.args[0].lower() in {"list", "show"}:
        await send_html(update.message, await gifts_text(config, db))
        return

    action = context.args[0].lower()
    try:
        if action == "add":
            if len(context.args) < 3:
                raise ValueError("Формат: /gift add nft Ссылка Название")
            gift_type = context.args[1].lower()
            if gift_type not in {"nft", "ordinary", "deleted"}:
                raise ValueError("Типы: nft, ordinary, deleted.")
            url = ""
            title_parts = context.args[2:]
            if title_parts and title_parts[0].startswith("http"):
                url = title_parts[0]
                title_parts = title_parts[1:]
            title = " ".join(title_parts).strip() or gift_type.upper()
            gift_id = db.add_gift(gift_type, title, url)
            await send_html(
                update.message,
                template_text(db, "gift_added", extra={"gift_id": gift_id}),
            )
        elif action in {"block", "unblock"}:
            if len(context.args) < 2:
                raise ValueError("Укажите ссылку, owned_gift_id или ключ подарка.")
            key = normalize_gift_block_key(" ".join(context.args[1:]))
            if action == "block":
                db.block_owner_gift(key)
            else:
                db.unblock_owner_gift(key)
            await send_html(update.message, template_text(db, "action_done"))
        elif action == "remove":
            if len(context.args) < 2:
                raise ValueError("Укажите ID локального fallback-подарка.")
            gift_id = int(context.args[1])
            db.remove_gift(gift_id)
            await send_html(update.message, template_text(db, "action_done"))
        elif action in {"fallbackblock", "fallbackunblock"}:
            if len(context.args) < 2:
                raise ValueError("Укажите ID локального fallback-подарка.")
            gift_id = int(context.args[1])
            if action == "fallbackblock":
                db.set_gift_blocked(gift_id, True)
            else:
                db.set_gift_blocked(gift_id, False)
            await send_html(update.message, template_text(db, "action_done"))
        elif action == "fallbackremove":
            if len(context.args) < 2:
                raise ValueError("Укажите ID локального fallback-подарка.")
            gift_id = int(context.args[1])
            db.remove_gift(gift_id)
            await send_html(update.message, template_text(db, "action_done"))
        else:
            await send_html(update.message, await gifts_text(config, db))
    except ValueError as error:
        await send_html(update.message, template_text(db, "validation_error", extra={"error": error}))


def normalize_gift_block_key(value: str) -> str:
    value = value.strip()
    if value.startswith("https://t.me/nft/") or value.startswith("http://t.me/nft/"):
        return value.casefold()
    return value.casefold()


async def gifts_text(config: BotConfig, db: Database) -> str:
    owner_lines: list[str] = []
    try:
        owner_gifts = await fetch_owner_gifts(config, db)
    except Exception as error:
        owner_gifts = []
        owner_lines.append(
            render_template(
                db,
                "giftbank_fetch_error",
                {"error": error},
            )
        )

    if owner_gifts:
        for index, gift in enumerate(owner_gifts[:50], start=1):
            key = gift_key(gift)
            url = f" {html_escape(gift.get('url', ''))}" if gift.get("url") else ""
            owner_lines.append(
                f"{index}. <b>{html_escape(gift['type'])}</b> "
                f"{html_escape(gift['title'])} "
                f"<code>{html_escape(key)}</code>{url}"
            )
        if len(owner_gifts) > 50:
            owner_lines.append(f"...и еще {len(owner_gifts) - 50}")
    elif not owner_lines:
        owner_lines.append(template_text(db, "giftbank_empty"))

    blocked_text = ""
    blocked_rows = db.list_gift_blocks()
    if blocked_rows:
        blocked_lines = []
        for row in blocked_rows:
            title = f" - {html_escape(row['title'])}" if row["title"] else ""
            blocked_lines.append(f"<code>{html_escape(row['gift_key'])}</code>{title}")
        blocked_text = render_template_with_raw(
            db,
            "giftbank_blocked_header",
            {"blocked_list": "\n".join(blocked_lines)},
            {"blocked_list"},
        )

    fallback_text = ""
    fallback_rows = db.list_gifts()
    if fallback_rows:
        fallback_lines = []
        for row in fallback_rows:
            status = "заблокирован" if row["is_blocked"] else "активен"
            url = f" {html_escape(row['url'])}" if row["url"] else ""
            fallback_lines.append(
                f"#{row['gift_id']} <b>{html_escape(row['gift_type'])}</b> "
                f"{html_escape(row['title'])} - {status}{url}"
            )
        fallback_text = render_template_with_raw(
            db,
            "giftbank_fallback_header",
            {"fallback_list": "\n".join(fallback_lines)},
            {"fallback_list"},
        )

    return render_template_with_raw(
        db,
        "giftbank",
        {
            "owner_gifts": "\n".join(owner_lines),
            "blocked_gifts": blocked_text,
            "fallback_gifts": fallback_text,
        },
        {"owner_gifts", "blocked_gifts", "fallback_gifts"},
    )


async def set_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    config = config_from_context(context)
    if not is_owner(config, update.effective_user.id):
        return

    db = db_from_context(context)
    if not context.args:
        await send_html(update.message, templates_text(db))
        return

    key = context.args[0].lower()
    if key not in TEMPLATE_DEFAULTS:
        await send_html(update.message, templates_text(db))
        return

    text = " ".join(context.args[1:]).strip()
    if update.message.reply_to_message and update.message.reply_to_message.text_html:
        text = update.message.reply_to_message.text_html

    if not text:
        await send_html(update.message, template_text(db, "settext_empty"))
        return

    db.set_template(key, text)
    await send_html(
        update.message,
        template_text(db, "settext_saved", extra={"template_key": key}),
    )


def templates_text(db: Database) -> str:
    template_keys = "\n".join(
        f"<code>/settext {html_escape(key)} текст</code>" for key in TEMPLATE_DEFAULTS
    )
    placeholders = (
        "<code>{username}</code>, <code>{case_count}</code>, <code>{nft_chance}</code>,\n"
        "<code>{spin_price}</code>, <code>{commission_percent}</code>, <code>{gift_title}</code>, <code>{gift_url}</code>,\n"
        "<code>{selected_case}</code>, <code>{total_spins}</code>, <code>{jackpots}</code>,\n"
        "<code>{opened_cases}</code>, <code>{nft_wins}</code>, <code>{gift_wins}</code>,\n"
        "<code>{empty_wins}</code>, <code>{gross_stars}</code>, <code>{net_stars}</code>, <code>{top5}</code>,\n"
        "<code>{period}</code>, <code>{period_title}</code>, <code>{result_type}</code>, <code>{template_key}</code>"
    )
    return render_template_with_raw(
        db,
        "texts_panel",
        {"template_keys": template_keys, "placeholders": placeholders},
        {"template_keys", "placeholders"},
    )


def template_detail_text(db: Database, key: str) -> str:
    if key not in TEMPLATE_DEFAULTS:
        return templates_text(db)
    current_text = db.get_template(key)
    return render_template_with_raw(
        db,
        "template_detail",
        {"template_key": key, "template_text": current_text},
        {"template_text"},
    )


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.message:
        return

    config = config_from_context(context)
    if not is_owner(config, update.effective_user.id):
        return
    if not is_allowed_chat(config, update.effective_chat.id):
        return

    db = db_from_context(context)
    await send_html(update.message, stats_text(db, update.effective_chat.id))


def stats_text(db: Database, chat_id: int | None, period_key: str = "all") -> str:
    if chat_id is None:
        return template_text(db, "no_allowed_chat")
    return render_template(
        db,
        "stats",
        build_template_values(db, chat_id=chat_id, period_key=period_key),
    )


async def show_my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.message:
        return

    config = config_from_context(context)
    db = db_from_context(context)
    db.remember_user(update.effective_user, update.effective_chat.type == "private")

    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        chat_id = next(iter(config.allowed_chat_ids), 0)
    if not chat_id:
        await send_html(update.message, template_text(db, "allowed_chats_empty"))
        return

    stats = db.get_user_stats(chat_id, update.effective_user.id)
    if stats is None:
        await send_html(update.message, template_text(db, "mystats_empty", update.effective_user))
        return

    text = template_text(
        db,
        "mystats",
        update.effective_user,
        extra={
            "total_spins": stats["total_spins"],
            "jackpots": stats["jackpots"],
            "opened_cases": stats["opened_cases"],
            "nft_wins": stats["nft_wins"],
            "gift_wins": stats["gift_wins"],
            "empty_wins": stats["empty_wins"],
            "gross_stars": format_stars(float(stats["gross_stars"])),
            "net_stars": format_stars(float(stats["net_stars"])),
        },
    )
    await send_html(update.message, text)


async def reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.message:
        return

    config = config_from_context(context)
    if not is_owner(config, update.effective_user.id):
        return
    if not is_allowed_chat(config, update.effective_chat.id):
        await send_html(update.message, template_text(db, "reset_wrong_chat", update.effective_user))
        return

    db = db_from_context(context)
    db.reset_stats(update.effective_chat.id)
    await send_html(update.message, template_text(db, "reset_done", update.effective_user))


async def send_stats_to_chat(
    application: Application,
    chat_id: int,
    period_key: str = "6h",
) -> None:
    db: Database = application.bot_data["db"]
    try:
        await application.bot.send_message(
            chat_id,
            stats_text(db, chat_id, period_key),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError as error:
        logging.warning("Cannot send stats to chat %s: %s", chat_id, error)


async def auto_stats_loop(application: Application) -> None:
    config: BotConfig = application.bot_data["config"]
    while True:
        await asyncio.sleep(AUTO_STATS_SECONDS)
        for chat_id in config.allowed_chat_ids:
            await send_stats_to_chat(application, chat_id, "6h")


async def on_startup(application: Application) -> None:
    application.bot_data["auto_stats_task"] = application.create_task(
        auto_stats_loop(application)
    )


async def react_to_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if not update.message.dice:
        return

    config = config_from_context(context)
    if not is_allowed_chat(config, update.effective_chat.id):
        return

    # Forwarded dice messages can replay old wins. Only original slot messages count.
    if getattr(update.message, "forward_origin", None) or getattr(update.message, "forward_date", None):
        return

    db = db_from_context(context)
    if not db.mark_message_processed(update.effective_chat.id, update.message.message_id):
        return

    db.remember_user(update.effective_user)
    db.remember_chat(update.effective_chat.id, chat_title(update))

    price = get_spin_price(db)
    commission = get_commission_percent(db)
    net_price = price * (100 - commission) / 100
    db.record_spin(update.effective_chat.id, update.effective_user.id, price, net_price)
    totals = db.get_totals(update.effective_chat.id)
    if totals["total_spins"] and totals["total_spins"] % AUTO_STATS_SPIN_INTERVAL == 0:
        await send_stats_to_chat(context.application, update.effective_chat.id, "all")

    dice = update.message.dice
    if dice.emoji != SLOT_MACHINE_EMOJI or dice.value != SLOT_MACHINE_JACKPOT_VALUE:
        return

    db.record_jackpot(update.effective_chat.id, update.effective_user.id)
    await send_case_challenge(update, context)


async def send_case_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    db = db_from_context(context)
    case_count = get_case_count(db)
    round_id = db.create_case_round(update.effective_chat.id, update.effective_user.id, case_count)
    text = render_template(
        db,
        "jackpot_start",
        build_template_values(db, update.effective_user),
    )
    sent_message = await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=case_keyboard(round_id, case_count),
    )
    db.set_case_round_message(round_id, sent_message.message_id)


async def handle_case_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user or not query.message:
        return

    db = db_from_context(context)
    _, round_id_text, position_text = query.data.split(":", 2)
    round_id = int(round_id_text)
    position = int(position_text)
    case_round = db.get_case_round(round_id)

    if case_round is None:
        await query.answer(template_alert(db, "case_not_found"), show_alert=True)
        return
    if case_round["user_id"] != query.from_user.id:
        await query.answer(template_alert(db, "case_wrong_user"), show_alert=True)
        return
    if case_round["status"] != "open":
        await query.answer(template_alert(db, "case_already_opened"), show_alert=True)
        return

    config = config_from_context(context)
    result_type, gift = await choose_case_result(config, db)
    db.close_case_round(round_id, position, result_type, gift)
    db.record_case_result(case_round["chat_id"], case_round["user_id"], result_type)

    label = result_button_label(result_type, gift)
    try:
        await query.edit_message_reply_markup(
            reply_markup=case_keyboard(round_id, case_round["case_count"], position, label)
        )
    except TelegramError:
        pass

    user = query.from_user
    extra = {
        "selected_case": position,
        "gift_title": gift["title"] if gift else "",
        "gift_url": gift["url"] if gift else "",
        "result_type": result_type,
    }
    template_key = "empty_win" if result_type == "empty" else ("nft_win" if result_type == "nft" else "gift_win")
    text = render_template(db, template_key, build_template_values(db, user, extra=extra))
    await query.answer(template_alert(db, "case_opened_alert"))
    await query.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    if result_type != "empty" and gift is not None:
        payout_id = db.create_payout(
            case_round["chat_id"],
            case_round["user_id"],
            round_id,
            result_type,
            gift["title"],
            gift["url"],
        )
        payout_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Выдача завершена", callback_data=f"payout:{payout_id}")]]
        )
        await notify_owners(
            context,
            (
                render_template(
                    db,
                    "owner_payout",
                    build_template_values(db, user, extra=extra),
                )
            ),
            payout_keyboard,
        )


async def choose_case_result(config: BotConfig, db: Database) -> tuple[str, dict[str, object] | None]:
    denominator = get_nft_chance_denominator(db)
    nft_gift = await choose_owner_gift(config, db, "nft")
    if nft_gift is not None and random.randint(1, denominator) == 1:
        return "nft", nft_gift

    gift = await choose_owner_gift(config, db, "ordinary")
    if gift is not None:
        return "ordinary", gift

    return "empty", None


def result_button_label(result_type: str, gift: dict[str, object] | sqlite3.Row | None) -> str:
    if result_type == "nft":
        return "NFT"
    if result_type in {"ordinary", "deleted"}:
        return str(gift["title"])[:12] if gift else "gift"
    return "пусто"


async def handle_payout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return

    config = config_from_context(context)
    if not is_owner(config, query.from_user.id):
        await query.answer()
        return

    db = db_from_context(context)
    payout_id = int(query.data.split(":", 1)[1])
    payout = db.complete_payout(payout_id)
    await query.answer(template_alert(db, "payout_done_alert"))
    if query.message:
        await query.edit_message_reply_markup(
            InlineKeyboardMarkup([[InlineKeyboardButton("Завершено", callback_data=f"paid:{payout_id}")]])
        )
    if payout:
        try:
            await context.bot.send_message(
                payout["user_id"],
                render_template(
                    db,
                    "payout_done",
                    {
                        "gift_title": payout["gift_title"],
                        "gift_url": payout["gift_url"],
                    },
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except TelegramError:
            pass


async def unknown_paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        db = db_from_context(context)
        await query.answer(template_alert(db, "paid_already_alert"))


async def on_shutdown(application: Application) -> None:
    task = application.bot_data.get("auto_stats_task")
    if task:
        task.cancel()
    db: Database = application.bot_data["db"]
    db.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    config = read_config()
    db = Database(config.db_path)

    application = (
        Application.builder()
        .token(config.token)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    application.bot_data["config"] = config
    application.bot_data["db"] = db

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", show_help))
    application.add_handler(CommandHandler("chatid", show_chat_id))
    application.add_handler(CommandHandler("owner", show_owner_panel))
    application.add_handler(CommandHandler("panel", show_owner_panel))
    application.add_handler(CommandHandler("game", manage_game))
    application.add_handler(CommandHandler("gift", manage_gifts))
    application.add_handler(CommandHandler("giftbank", manage_gifts))
    application.add_handler(CommandHandler("settext", set_text))
    application.add_handler(CommandHandler("texts", set_text))
    application.add_handler(CommandHandler("stats", show_stats))
    application.add_handler(CommandHandler("mystats", show_my_stats))
    application.add_handler(CommandHandler("resetstats", reset_stats))
    application.add_handler(CallbackQueryHandler(handle_owner_panel, pattern="^panel:"))
    application.add_handler(CallbackQueryHandler(handle_case_choice, pattern="^case:"))
    application.add_handler(CallbackQueryHandler(handle_payout, pattern="^payout:"))
    application.add_handler(CallbackQueryHandler(unknown_paid_callback, pattern="^paid:"))
    application.add_handler(MessageHandler(filters.ALL, react_to_message))

    logging.info("TwiST ludka bot started.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
