from __future__ import annotations

import json
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


def _as_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_value(name: str) -> str | None:
    if name in os.environ:
        return os.environ.get(name)
    return None


def _as_color(name: str, default: int | None = None) -> int | None:
    raw = _env_value(name)
    if raw is None:
        return default
    text = raw.strip()
    if not text:
        return 0
    base = text.lower()
    if base.startswith("#"):
        base = base[1:]
    if base.startswith("0x"):
        base = base[2:]
    try:
        if base.isdigit():
            return int(base)
        return int(base, 16)
    except ValueError:
        return default


def _as_json(name: str) -> object | None:
    raw = _env_value(name)
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _load_embed_template(prefix: str) -> dict[str, object]:
    template: dict[str, object] = {}

    def set_if_present(key: str, value: object | None) -> None:
        if value is not None:
            template[key] = value

    raw_title = _env_value(f"{prefix}_TITLE")
    if raw_title is not None:
        template["title"] = raw_title

    raw_description = _env_value(f"{prefix}_DESCRIPTION")
    if raw_description is not None:
        template["description"] = raw_description

    set_if_present("color", _as_color(f"{prefix}_COLOR"))

    raw_author_text = _env_value(f"{prefix}_AUTHOR_TEXT")
    if raw_author_text is not None:
        template["author_text"] = raw_author_text

    raw_author_url = _env_value(f"{prefix}_AUTHOR_URL")
    if raw_author_url is not None:
        template["author_url"] = raw_author_url

    raw_author_icon = _env_value(f"{prefix}_AUTHOR_ICON_URL")
    if raw_author_icon is not None:
        template["author_icon_url"] = raw_author_icon

    raw_thumbnail = _env_value(f"{prefix}_THUMBNAIL_URL")
    if raw_thumbnail is not None:
        template["thumbnail_url"] = raw_thumbnail

    raw_image = _env_value(f"{prefix}_IMAGE_URL")
    if raw_image is not None:
        template["image_url"] = raw_image

    raw_footer_text = _env_value(f"{prefix}_FOOTER_TEXT")
    if raw_footer_text is not None:
        template["footer_text"] = raw_footer_text

    raw_footer_url = _env_value(f"{prefix}_FOOTER_URL")
    if raw_footer_url is not None:
        template["footer_icon_url"] = raw_footer_url

    fields = _as_json(f"{prefix}_FIELDS_JSON")
    if fields is not None:
        template["fields"] = fields

    raw_replace = _env_value(f"{prefix}_FIELDS_REPLACE")
    if raw_replace is not None:
        template["replace_fields"] = raw_replace.strip().lower() in {"1", "true", "yes", "on"}

    raw_channel = _env_value(f"{prefix}_CHANNEL_ID")
    if raw_channel is not None:
        template["channel_id"] = int(raw_channel) if raw_channel.strip().isdigit() else 0

    return template


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
    ticket_management_category_id: int
    ticket_management_support_role_id: int
    ticket_security_category_id: int
    ticket_security_support_role_id: int
    ticket_general_category_id: int
    ticket_general_support_role_id: int
    application_review_channel_id: int
    apply_min_ai_score: float
    application_questions: list[str]
    ai_provider: str
    groq_api_key: str
    groq_model: str
    cloudflare_api_token: str
    cloudflare_account_id: str
    cloudflare_model: str
    ai_error_webhook_url: str
    ai_request_timeout_seconds: float
    asset_logo_url: str
    embed_templates: dict[str, dict[str, object]]

    @classmethod
    def from_env(cls) -> "BotConfig":
        load_dotenv()

        db_default = str(Path("data") / "bot.db")
        raw_questions = os.getenv("APPLICATION_QUESTIONS", "").strip()
        questions = [q.strip() for q in raw_questions.split("||") if q.strip()]
        if not questions:
            questions = [
                "Why do you want to join Blackwater Protection Group staff?",
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
            ticket_management_category_id=_as_int("TICKET_MANAGEMENT_CATEGORY_ID"),
            ticket_management_support_role_id=_as_int("TICKET_MANAGEMENT_SUPPORT_ROLE_ID"),
            ticket_security_category_id=_as_int("TICKET_SECURITY_CATEGORY_ID"),
            ticket_security_support_role_id=_as_int("TICKET_SECURITY_SUPPORT_ROLE_ID"),
            ticket_general_category_id=_as_int("TICKET_GENERAL_CATEGORY_ID"),
            ticket_general_support_role_id=_as_int("TICKET_GENERAL_SUPPORT_ROLE_ID"),
            application_review_channel_id=_as_int("APPLICATION_REVIEW_CHANNEL_ID"),
            apply_min_ai_score=ai_threshold,
            application_questions=questions,
            ai_provider=os.getenv("AI_PROVIDER", "heuristic").strip().lower(),
            groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
            groq_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip() or "llama-3.1-8b-instant",
            cloudflare_api_token=os.getenv("CLOUDFLARE_API_TOKEN", "").strip(),
            cloudflare_account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip(),
            cloudflare_model=os.getenv("CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct").strip() or "@cf/meta/llama-3.1-8b-instruct",
            ai_error_webhook_url=os.getenv("AI_ERROR_WEBHOOK_URL", "").strip(),
            ai_request_timeout_seconds=_as_float("AI_REQUEST_TIMEOUT_SECONDS", 12.0),
            asset_logo_url=os.getenv("ASSET_LOGO_URL", "").strip(),
            embed_templates={
                "app_ai_hold": _load_embed_template("APP_AI_HOLD_EMBED"),
                "app_ai_completed": _load_embed_template("APP_AI_COMPLETED_EMBED"),
                "app_ai_warning": _load_embed_template("APP_AI_WARNING_EMBED"),
                "app_ai_error": _load_embed_template("APP_AI_ERROR_EMBED"),
                "app_canceled": _load_embed_template("APP_CANCELED_EMBED"),
                "app_closed_strike": _load_embed_template("APP_CLOSED_STRIKE_EMBED"),
                "app_review_submitted": _load_embed_template("APP_REVIEW_SUBMITTED_EMBED"),
                "app_results": _load_embed_template("APP_RESULTS_EMBED"),
                "app_search_results": _load_embed_template("APP_SEARCH_RESULTS_EMBED"),
                "ticket_info": _load_embed_template("TICKET_INFO_EMBED"),
                "ticket_reason": _load_embed_template("TICKET_REASON_EMBED"),
                "ticket_panel": _load_embed_template("TICKET_PANEL_EMBED"),
            },
        )
