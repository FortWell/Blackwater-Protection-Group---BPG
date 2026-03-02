from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_int(name: str, default: int = 0) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _as_int_list(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    values: list[int] = []
    for part in raw.split(","):
        piece = part.strip()
        if piece.isdigit():
            values.append(int(piece))
    return values


def _as_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class BotConfig:
    token: str
    database_path: str
    dev_guild_id: int
    enable_members_intent: bool
    enable_message_content_intent: bool
    role_id_send: int
    staff_management_role_id: int
    staff_management_role_ids: list[int]
    bot_log_channel_id: int
    bot_audit_webhook_url: str
    staff_promotion_channel_id: int
    staff_infraction_channel_id: int
    fps_promotion_channel_id: int
    fps_infraction_channel_id: int
    ticket_management_category_id: int
    ticket_management_support_role_id: int
    ticket_security_category_id: int
    ticket_security_support_role_id: int
    ticket_general_category_id: int
    ticket_general_support_role_id: int
    application_review_channel_id: int
    apply_min_ai_score: float
    application_questions: list[str]
    asset_logo_url: str

    @classmethod
    def from_env(cls) -> "BotConfig":
        load_dotenv()

        db_default = str(Path("data") / "bot.db")
        raw_questions = os.getenv("APPLICATION_QUESTIONS", "").strip()
        questions = [q.strip() for q in raw_questions.split("||") if q.strip()]
        if not questions:
            questions = [
                "Why do you want to join NYCRPP staff?",
                "How would you handle a difficult player situation?",
                "What timezone and hours can you be active?",
            ]

        ai_threshold_raw = os.getenv("APPLICATION_AI_FLAG_THRESHOLD", "0.50").strip()
        try:
            ai_threshold = float(ai_threshold_raw)
        except ValueError:
            ai_threshold = 0.50

        return cls(
            token=os.getenv("DISCORD_TOKEN", "").strip(),
            database_path=os.getenv("DATABASE_PATH", db_default).strip(),
            dev_guild_id=_as_int("DEV_GUILD_ID"),
            enable_members_intent=_as_bool("ENABLE_MEMBERS_INTENT", default=False),
            enable_message_content_intent=_as_bool("ENABLE_MESSAGE_CONTENT_INTENT", default=False),
            role_id_send=_as_int("ROLE_ID_SEND"),
            staff_management_role_id=_as_int("STAFF_MANAGEMENT_ROLE_ID"),
            staff_management_role_ids=_as_int_list("STAFF_MANAGEMENT_ROLE_IDS"),
            bot_log_channel_id=_as_int("BOT_LOG_CHANNEL_ID") or _as_int("STAFF_LOG_CHANNEL_ID"),
            bot_audit_webhook_url=os.getenv("BOT_AUDIT_WEBHOOK_URL", "").strip(),
            staff_promotion_channel_id=_as_int("STAFF_PROMOTION_CHANNEL_ID"),
            staff_infraction_channel_id=_as_int("STAFF_INFRACTION_CHANNEL_ID"),
            fps_promotion_channel_id=_as_int("FPS_PROMOTION_CHANNEL_ID"),
            fps_infraction_channel_id=_as_int("FPS_INFRACTION_CHANNEL_ID"),
            ticket_management_category_id=_as_int("TICKET_MANAGEMENT_CATEGORY_ID"),
            ticket_management_support_role_id=_as_int("TICKET_MANAGEMENT_SUPPORT_ROLE_ID"),
            ticket_security_category_id=_as_int("TICKET_SECURITY_CATEGORY_ID"),
            ticket_security_support_role_id=_as_int("TICKET_SECURITY_SUPPORT_ROLE_ID"),
            ticket_general_category_id=_as_int("TICKET_GENERAL_CATEGORY_ID"),
            ticket_general_support_role_id=_as_int("TICKET_GENERAL_SUPPORT_ROLE_ID"),
            application_review_channel_id=_as_int("APPLICATION_REVIEW_CHANNEL_ID"),
            apply_min_ai_score=ai_threshold,
            application_questions=questions,
            asset_logo_url=os.getenv("ASSET_LOGO_URL", "").strip(),
        )
