from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web
from dotenv import dotenv_values, load_dotenv
import psutil

from bot.branding import (
    BRANDING_NAME,
    BRANDING_FOOTER_ICON_URL,
    BRANDING_FOOTER_TEXT,
    BRANDING_THUMBNAIL_URL,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("oci-dashboard")

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
SLOT_CONFIG_PATH = ROOT / "dashboard_slots.json"


@dataclass(slots=True)
class SlotDefinition:
    id: str
    name: str
    env_file: str
    status_port: int
    database_path: str
    supports_lockdown: bool = True
    entrypoint: str = "main.py"

    @property
    def dom_id(self) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", self.id.strip().lower())
        return slug.strip("-") or "slot"

    @property
    def env_path(self) -> Path:
        path = Path(self.env_file)
        return path if path.is_absolute() else ROOT / path

    @property
    def entrypoint_path(self) -> Path:
        path = Path(self.entrypoint)
        return path if path.is_absolute() else ROOT / path

    @property
    def stdout_log(self) -> Path:
        return LOG_DIR / f"dashboard-{self.dom_id}-stdout.log"

    @property
    def stderr_log(self) -> Path:
        return LOG_DIR / f"dashboard-{self.dom_id}-stderr.log"


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = dotenv_values(path)
    return {key: str(value) for key, value in values.items() if value is not None}


def _format_path(value: str) -> str:
    return value.replace("\\", "/")


def load_slot_definitions() -> list[SlotDefinition]:
    defaults = [
        SlotDefinition(
            id="primary",
            name="Primary Bot",
            env_file=".env",
            status_port=_env_int("BOT_STATUS_PORT_PRIMARY", 8091),
            database_path="data/bot.db",
            supports_lockdown=True,
        ),
        SlotDefinition(
            id="secondary",
            name="Secondary Bot",
            env_file=".env.secondary",
            status_port=_env_int("BOT_STATUS_PORT_SECONDARY", 8092),
            database_path="data/bot-2.db",
            supports_lockdown=False,
            entrypoint="secondary_bot/main.py",
        ),
    ]

    if not SLOT_CONFIG_PATH.exists():
        return defaults

    try:
        raw = json.loads(SLOT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.exception("Unable to parse dashboard_slots.json; falling back to defaults.")
        return defaults

    if isinstance(raw, dict):
        items = raw.get("slots", [])
    elif isinstance(raw, list):
        items = raw
    else:
        log.warning("dashboard_slots.json must be a list or object; falling back to defaults.")
        return defaults

    parsed: list[SlotDefinition] = []
    for index, item in enumerate(items, start=1):
        fallback = SlotDefinition(
            id=f"slot-{index}",
            name=f"Slot {index}",
            env_file=".env",
            status_port=8100 + index,
            database_path=f"data/slot-{index}.db",
            entrypoint="secondary_bot/main.py" if index == 2 else "main.py",
        )
        if not isinstance(item, dict):
            parsed.append(fallback)
            continue

        raw_id = str(item.get("id", fallback.id)).strip()
        raw_name = str(item.get("name", fallback.name)).strip()
        raw_env = str(item.get("env_file", fallback.env_file)).strip()
        raw_db = str(item.get("database_path", fallback.database_path)).strip()
        raw_supports_lockdown = _coerce_bool(item.get("supports_lockdown", fallback.supports_lockdown), fallback.supports_lockdown)
        raw_entrypoint = str(item.get("entrypoint", fallback.entrypoint)).strip()
        try:
            raw_port = int(item.get("status_port", fallback.status_port))
        except (TypeError, ValueError):
            raw_port = fallback.status_port
        parsed.append(
            SlotDefinition(
                id=re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_id).strip("-") or fallback.id,
                name=raw_name or fallback.name,
                env_file=raw_env or fallback.env_file,
                status_port=raw_port,
                database_path=raw_db or fallback.database_path,
                supports_lockdown=raw_supports_lockdown,
                entrypoint=raw_entrypoint or fallback.entrypoint,
            )
        )

    result: list[SlotDefinition] = []
    seen_ids: set[str] = set()
    seen_ports: set[int] = set()
    for slot in parsed:
        if slot.id in seen_ids:
            log.warning("Skipping duplicate slot id %s.", slot.id)
            continue
        if slot.status_port in seen_ports:
            log.warning("Skipping duplicate status port %s.", slot.status_port)
            continue
        seen_ids.add(slot.id)
        seen_ports.add(slot.status_port)
        result.append(slot)

    return result or defaults


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _resolve_python_executable() -> Path:
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def _tail_file(path: Path, line_count: int = 32) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = deque(handle, maxlen=line_count)
    return "".join(lines).rstrip()


BOT_ENV_KEYS = {
    "DISCORD_TOKEN",
    "DATABASE_PATH",
    "DEV_GUILD_ID",
    "ENABLE_MEMBERS_INTENT",
    "ENABLE_MESSAGE_CONTENT_INTENT",
    "ROLE_ID_SEND",
    "STAFF_MANAGEMENT_ROLE_ID",
    "STAFF_MANAGEMENT_ROLE_IDS",
    "GLOBAL_BAN_ROLE_ID",
    "BOT_LOG_CHANNEL_ID",
    "STAFF_LOG_CHANNEL_ID",
    "BOT_AUDIT_WEBHOOK_URL",
    "STAFF_PROMOTION_CHANNEL_ID",
    "STAFF_INFRACTION_CHANNEL_ID",
    "TICKET_MANAGEMENT_CATEGORY_ID",
    "TICKET_MANAGEMENT_SUPPORT_ROLE_ID",
    "TICKET_SECURITY_CATEGORY_ID",
    "TICKET_SECURITY_SUPPORT_ROLE_ID",
    "TICKET_GENERAL_CATEGORY_ID",
    "TICKET_GENERAL_SUPPORT_ROLE_ID",
    "TICKET_PRIORITY_CATEGORY_ID",
    "TICKET_PRIORITY_SUPPORT_ROLE_ID",
    "TICKET_PRIORITY_OPEN_ROLE_ID",
    "APPLICATION_REVIEW_CHANNEL_ID",
    "APPLICATION_QUESTIONS",
    "APPLICATION_AI_FLAG_THRESHOLD",
    "AI_PROVIDER",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_MODEL",
    "AI_ERROR_WEBHOOK_URL",
    "AI_REQUEST_TIMEOUT_SECONDS",
    "ASSET_LOGO_URL",
}

BOT_ENV_PREFIXES = (
    "APP_AI_HOLD_EMBED_",
    "APP_AI_COMPLETED_EMBED_",
    "APP_AI_WARNING_EMBED_",
    "APP_AI_ERROR_EMBED_",
    "APP_CANCELED_EMBED_",
    "APP_CLOSED_STRIKE_EMBED_",
    "APP_REVIEW_SUBMITTED_EMBED_",
    "APP_RESULTS_EMBED_",
    "APP_SEARCH_RESULTS_EMBED_",
    "TICKET_INFO_EMBED_",
    "TICKET_REASON_EMBED_",
    "TICKET_PANEL_EMBED_",
)


def _build_slot_environment(slot: SlotDefinition) -> dict[str, str]:
    env = os.environ.copy()
    for key in BOT_ENV_KEYS:
        env.pop(key, None)
    for key in list(env):
        if key.startswith(BOT_ENV_PREFIXES):
            env.pop(key, None)

    slot_env = _read_dotenv(slot.env_path)
    env.update(slot_env)
    env["PORT"] = str(slot.status_port)
    env["DATABASE_PATH"] = slot.database_path
    env["BOT_INSTANCE_NAME"] = slot.id
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _render_slot_overview_cards(
    slots: list[SlotDefinition],
    selected_slot_id: str,
    slot_snapshots: dict[str, dict[str, Any] | None],
) -> str:
    cards: list[str] = []
    for slot in slots:
        snapshot = slot_snapshots.get(slot.id)
        slot_meta = snapshot.get("slot") if snapshot else {}
        running = bool(snapshot.get("running")) if snapshot else False
        managed = bool(snapshot.get("managed")) if snapshot else False
        env_exists = bool(slot_meta.get("env_exists", True)) if snapshot else True
        entrypoint_exists = bool(slot_meta.get("entrypoint_exists", True)) if snapshot else True
        if not entrypoint_exists:
            status_label = "Entrypoint missing"
            status_class = "offline"
        elif not env_exists:
            status_label = "Env missing"
            status_class = "warn"
        elif snapshot is None:
            status_label = "Unavailable"
            status_class = "offline"
        elif running and managed:
            status_label = "Running"
            status_class = "ok"
        elif running:
            status_label = "Reachable"
            status_class = "warn"
        else:
            status_label = "Stopped"
            status_class = "offline"

        selected_class = " selected" if slot.id == selected_slot_id else ""
        selected_badge = '<span class="slot-badge">Selected</span>' if slot.id == selected_slot_id else ""
        link = f"/?slot={html.escape(slot.id)}"
        cards.append(
            f"""
          <article class="slot-card{selected_class}">
            <div class="slot-card-head">
              <div>
                <div class="slot-name">{html.escape(slot.name)}</div>
                <div class="slot-id">{html.escape(slot.id)}</div>
              </div>
              {selected_badge}
            </div>
            <div class="slot-status {status_class}">{status_label}</div>
            <div class="slot-meta">
              <div><span>Port</span><strong>{slot.status_port}</strong></div>
              <div><span>Database</span><strong>{html.escape(_format_path(slot.database_path))}</strong></div>
              <div><span>Env</span><strong>{html.escape(_format_path(str(slot.env_path)))}</strong></div>
            </div>
            <div class="slot-actions">
              <a class="slot-link" href="{link}">Open slot</a>
            </div>
          </article>
        """
        )
    return "".join(cards)


def _render_dashboard_html(
    host: str,
    dashboard_port: int,
    slots: list[SlotDefinition],
    selected_slot: SlotDefinition,
    slot_snapshots: dict[str, dict[str, Any] | None],
) -> str:
    # Determine display name based on selected slot
    if selected_slot.id == "primary":
        display_branding_name = "Blackwater Protection Group"
    elif selected_slot.id == "secondary":
        display_branding_name = "Office of Community Investigations - OCI"
    else:
        display_branding_name = BRANDING_NAME
    
    brand_thumb = html.escape(BRANDING_THUMBNAIL_URL)
    footer_text = html.escape(BRANDING_FOOTER_TEXT)
    footer_icon = html.escape(BRANDING_FOOTER_ICON_URL)
    host_display = html.escape(host)
    selected_slot_name = html.escape(selected_slot.name)
    selected_slot_id = html.escape(selected_slot.id)
    selected_status_port = html.escape(f"127.0.0.1:{selected_slot.status_port}")
    selected_supports_lockdown = selected_slot.supports_lockdown
    lockdown_setting_style = "" if selected_supports_lockdown else ' style="display:none;"'
    lockdown_stat_style = "" if selected_supports_lockdown else ' style="display:none;"'
    slot_cards_html = _render_slot_overview_cards(slots, selected_slot.id, slot_snapshots)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#08111f">
  <title>Office of Community Investigations - OCI Dashboard</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #07111e;
      --bg-2: #0b1526;
      --card: rgba(13, 23, 39, 0.88);
      --line: rgba(255, 255, 255, 0.09);
      --text: #eff6ff;
      --muted: #9fb2cb;
      --accent: #84b3ff;
      --accent-2: #32d3a7;
      --danger: #ef6a6a;
      --warn: #f1c96b;
      --shadow: 0 24px 80px rgba(0, 0, 0, 0.45);
      --radius: 24px;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(81, 136, 255, 0.18), transparent 30%),
        radial-gradient(circle at top right, rgba(50, 211, 167, 0.12), transparent 26%),
        linear-gradient(180deg, var(--bg), var(--bg-2));
      color: var(--text);
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
    }}

    .wrap {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}

    .hero {{
      display: grid;
      grid-template-columns: 96px 1fr auto;
      gap: 18px;
      align-items: center;
      padding: 22px 24px;
      background: linear-gradient(135deg, rgba(11, 21, 38, 0.94), rgba(9, 17, 30, 0.82));
      border: 1px solid var(--line);
      border-radius: calc(var(--radius) + 4px);
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }}

    .brand-mark {{
      width: 96px;
      height: 96px;
      border-radius: 22px;
      object-fit: cover;
      border: 1px solid rgba(255, 255, 255, 0.14);
      box-shadow: 0 16px 40px rgba(0, 0, 0, 0.4);
      background: rgba(255, 255, 255, 0.04);
    }}

    .eyebrow {{
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.24em;
      color: var(--accent);
      margin-bottom: 8px;
    }}

    h1 {{
      margin: 0;
      font-size: clamp(1.8rem, 4vw, 3rem);
      line-height: 1.05;
    }}

    .subtitle {{
      margin-top: 10px;
      color: var(--muted);
      max-width: 64ch;
      line-height: 1.55;
    }}

    .status-pill {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.04);
      color: var(--text);
      font-size: 0.92rem;
      white-space: nowrap;
    }}

    .status-dot {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--warn);
      box-shadow: 0 0 18px currentColor;
      flex: 0 0 auto;
    }}

    .status-pill.ok .status-dot {{
      background: var(--accent-2);
    }}

    .status-pill.offline .status-dot {{
      background: var(--danger);
    }}

    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-end;
    }}

    button {{
      appearance: none;
      border: 0;
      border-radius: 14px;
      padding: 12px 18px;
      font: inherit;
      font-weight: 700;
      color: #fff;
      cursor: pointer;
      transition: transform 0.16s ease, filter 0.16s ease, opacity 0.16s ease;
      box-shadow: 0 14px 28px rgba(0, 0, 0, 0.25);
    }}

    button:hover {{
      transform: translateY(-1px);
      filter: brightness(1.05);
    }}

    button:disabled {{
      opacity: 0.58;
      cursor: not-allowed;
      transform: none;
    }}

    .start {{
      background: linear-gradient(135deg, #22c55e, #16a34a);
    }}

    .stop {{
      background: linear-gradient(135deg, #ef4444, #b91c1c);
    }}

    .restart {{
      background: linear-gradient(135deg, #f59e0b, #d97706);
    }}

    .grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
      margin-top: 18px;
    }}

    .ops-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}

    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
      padding: 20px;
    }}

    .card h2 {{
      margin: 0 0 14px;
      font-size: 1.05rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: #d8e7ff;
    }}

    .stats {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}

    .stat {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.06);
    }}

    .stat .label {{
      color: var(--muted);
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      margin-bottom: 8px;
    }}

    .stat .value {{
      font-size: 1rem;
      word-break: break-word;
      line-height: 1.45;
    }}

    .split {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}

    .dashboard-shell {{
      display: grid;
      grid-template-columns: minmax(280px, 340px) minmax(0, 1fr);
      gap: 18px;
      margin-top: 18px;
      align-items: start;
    }}

    .sidebar {{
      display: grid;
      gap: 18px;
      align-content: start;
    }}

    .content-stack {{
      display: grid;
      gap: 18px;
      min-width: 0;
    }}

    .settings-grid {{
      display: grid;
      gap: 12px;
    }}

    .setting-block {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.06);
      display: grid;
      gap: 10px;
    }}

    .setting-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }}

    .setting-title {{
      font-size: 1rem;
      font-weight: 700;
    }}

    .setting-desc {{
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.45;
    }}

    .setting-status {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.06);
      font-size: 0.84rem;
      font-weight: 700;
      width: fit-content;
    }}

    .setting-status::before {{
      content: "";
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--warn);
      box-shadow: 0 0 12px currentColor;
    }}

    .setting-status.ok {{
      color: var(--accent-2);
    }}

    .setting-status.ok::before {{
      background: var(--accent-2);
    }}

    .setting-status.offline {{
      color: var(--danger);
    }}

    .setting-status.offline::before {{
      background: var(--danger);
    }}

    .setting-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}

    .setting-actions button {{
      padding: 10px 14px;
      border-radius: 12px;
      box-shadow: none;
    }}

    .kv-list {{
      display: grid;
      gap: 10px;
    }}

    .kv-list div {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      color: var(--muted);
      font-size: 0.86rem;
    }}

    .kv-list span {{
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .kv-list strong {{
      color: var(--text);
      font-weight: 600;
      text-align: right;
      overflow-wrap: anywhere;
    }}

    .slot-overview {{
      margin-top: 18px;
    }}

    .slot-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}

    .slot-card {{
      border-radius: 18px;
      padding: 16px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.06);
    }}

    .slot-card.selected {{
      border-color: rgba(132, 179, 255, 0.48);
      box-shadow: 0 0 0 1px rgba(132, 179, 255, 0.16), 0 16px 40px rgba(0, 0, 0, 0.18);
    }}

    .slot-card-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}

    .slot-name {{
      font-weight: 700;
      font-size: 1rem;
      line-height: 1.35;
    }}

    .slot-id {{
      color: var(--muted);
      font-size: 0.82rem;
      margin-top: 2px;
    }}

    .slot-status {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 12px;
      font-size: 0.88rem;
      font-weight: 700;
    }}

    .slot-status::before {{
      content: "";
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--warn);
      box-shadow: 0 0 12px currentColor;
    }}

    .slot-status.ok {{
      color: var(--accent-2);
    }}

    .slot-status.ok::before {{
      background: var(--accent-2);
    }}

    .slot-status.warn {{
      color: var(--warn);
    }}

    .slot-status.warn::before {{
      background: var(--warn);
    }}

    .slot-status.offline {{
      color: var(--danger);
    }}

    .slot-status.offline::before {{
      background: var(--danger);
    }}

    .slot-meta {{
      display: grid;
      gap: 10px;
      margin-bottom: 14px;
    }}

    .slot-meta div {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      color: var(--muted);
      font-size: 0.86rem;
    }}

    .slot-meta span {{
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .slot-meta strong {{
      color: var(--text);
      font-weight: 600;
      text-align: right;
    }}

    .slot-actions {{
      display: flex;
      justify-content: flex-end;
    }}

    .slot-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 12px;
      background: linear-gradient(135deg, #84b3ff, #5f8cff);
      color: #08111f;
      font-weight: 800;
    }}

    .slot-badge {{
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 0.75rem;
      color: #08111f;
      background: var(--accent-2);
      font-weight: 800;
      white-space: nowrap;
    }}

    .log-box {{
      margin-top: 14px;
      border-radius: 18px;
      background: #040a13;
      border: 1px solid rgba(255, 255, 255, 0.08);
      padding: 14px;
      min-height: 200px;
      overflow: auto;
    }}

    .log-box pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      color: #d8e7ff;
      font-family: "Cascadia Mono", "Consolas", monospace;
      font-size: 0.86rem;
      line-height: 1.5;
    }}

    .log-title {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }}

    .tag {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.05);
      color: var(--muted);
      border: 1px solid rgba(255, 255, 255, 0.06);
      font-size: 0.82rem;
    }}

    .footer {{
      margin-top: 18px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      color: var(--muted);
      font-size: 0.9rem;
    }}

    .footer img {{
      width: 18px;
      height: 18px;
      border-radius: 5px;
      vertical-align: -4px;
      margin-right: 8px;
    }}

    .muted {{
      color: var(--muted);
    }}

    @media (max-width: 980px) {{
      .hero {{
        grid-template-columns: 72px 1fr;
      }}

      .toolbar {{
        grid-column: 1 / -1;
        justify-content: flex-start;
      }}

      .dashboard-shell,
      .grid,
      .split,
      .stats,
      .slot-grid,
      .ops-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <img class="brand-mark" src="{brand_thumb}" alt="{display_branding_name} logo">
      <div>
        <div class="eyebrow">Local control panel</div>
        <h1>{display_branding_name}</h1>
        <div class="subtitle">
          Multi-slot dashboard for starting, stopping, and restarting local bot processes.
          This view is focused on <span class="muted">{selected_slot_name}</span>, whose status endpoint is exposed on
          <span class="muted">{selected_status_port}</span>, while this dashboard is served on
          <span class="muted">{host_display}:{dashboard_port}</span>.
          Use the slot cards below to switch between bot instances.
        </div>
        <div class="status-pill offline" style="margin-top: 14px;">
          <span class="status-dot"></span>
          <span class="status-text">Waiting for status</span>
        </div>
      </div>
      <div class="toolbar">
        <button class="start" data-action="start">Start Bot</button>
        <button class="restart" data-action="restart">Restart Bot</button>
        <button class="stop" data-action="stop">Stop Bot</button>
      </div>
    </section>

    <section class="card slot-overview">
      <div class="log-title">
        <h2 style="margin: 0;">Managed Slots</h2>
        <div class="tag">{len(slots)} total</div>
      </div>
      <div class="slot-grid">
        {slot_cards_html}
      </div>
    </section>

    <section class="dashboard-shell">
      <aside class="sidebar">
        <article class="card">
          <h2>Bot Settings</h2>
          <div class="settings-grid">
            <div class="setting-block" id="lockdown_setting_block"{lockdown_setting_style}>
              <div class="setting-head">
                <div>
                  <div class="setting-title">Lockdown</div>
                  <div class="setting-desc">Enable or disable command access for the selected bot slot.</div>
                </div>
                <div class="setting-status offline" id="lockdown_state">Loading...</div>
              </div>
              <div class="setting-actions">
                <button class="start" data-setting-action="lockdown-on">Enable</button>
                <button class="stop" data-setting-action="lockdown-off">Disable</button>
              </div>
            </div>
            <div class="setting-block">
              <div class="setting-head">
                <div>
                  <div class="setting-title">Selected Slot</div>
                  <div class="setting-desc">Live configuration for the bot process under this panel.</div>
                </div>
              </div>
              <div class="kv-list">
                <div><span>Name</span><strong id="selected_slot_name">{selected_slot_name}</strong></div>
                <div><span>Status Port</span><strong id="selected_status_port">{selected_status_port}</strong></div>
                <div><span>Database</span><strong id="selected_database_path">—</strong></div>
                <div><span>Env File</span><strong id="selected_env_file">—</strong></div>
              </div>
            </div>
          </div>
        </article>
      </aside>

      <div class="content-stack">
    <section class="grid">
      <article class="card">
        <h2>Bot Status</h2>
        <div class="stats">
          <div class="stat">
            <div class="label">Process State</div>
            <div class="value" id="process_state">Loading...</div>
          </div>
          <div class="stat">
            <div class="label">Bot Reachability</div>
            <div class="value" id="bot_state">Loading...</div>
          </div>
          <div class="stat">
            <div class="label">PID</div>
            <div class="value" id="pid">&mdash;</div>
          </div>
          <div class="stat">
            <div class="label">Return Code</div>
            <div class="value" id="return_code">&mdash;</div>
          </div>
          <div class="stat">
            <div class="label">Bot User</div>
            <div class="value" id="bot_user">&mdash;</div>
          </div>
          <div class="stat">
            <div class="label">Guild Count</div>
            <div class="value" id="guild_count">&mdash;</div>
          </div>
          <div class="stat">
            <div class="label">Latency</div>
            <div class="value" id="latency">&mdash;</div>
          </div>
          <div class="stat">
            <div class="label">Uptime</div>
            <div class="value" id="uptime">&mdash;</div>
          </div>
        </div>
      </article>

      <article class="card">
        <h2>Operations</h2>
        <div class="ops-grid">
          <div class="stat">
            <div class="label">Home Guild</div>
            <div class="value" id="home_guild">&mdash;</div>
          </div>
          <div class="stat" id="lockdown_stat"{lockdown_stat_style}>
            <div class="label">Lockdown</div>
            <div class="value" id="lockdown">&mdash;</div>
          </div>
          <div class="stat">
            <div class="label">Active Global Bans</div>
            <div class="value" id="global_ban_count">&mdash;</div>
          </div>
        </div>
        <div class="footer">
          <div class="tag">
            <span>Branding</span>
            <span>•</span>
            <span>{footer_text}</span>
          </div>
          <div>
            <img src="{footer_icon}" alt="Footer icon">
            <span>Property Of {display_branding_name}</span>
          </div>
        </div>
      </article>
    </section>

    <section class="grid">
      <article class="card">
        <div class="log-title">
          <h2 style="margin: 0;">Bot Stdout</h2>
          <div class="tag">Live tail</div>
        </div>
        <div class="log-box"><pre id="stdout_log">Waiting for data...</pre></div>
      </article>
      <article class="card">
        <div class="log-title">
          <h2 style="margin: 0;">Bot Stderr</h2>
          <div class="tag">Errors only</div>
        </div>
        <div class="log-box"><pre id="stderr_log">Waiting for data...</pre></div>
      </article>
    </section>
      </div>
    </section>
  </main>

  <script>
    const buttons = Array.from(document.querySelectorAll("button[data-action]"));
    const settingButtons = Array.from(document.querySelectorAll("button[data-setting-action]"));
    const selectedSlotId = "{selected_slot_id}";
    const selectedSupportsLockdown = {str(selected_supports_lockdown).lower()};
    const statusFields = {{
      process_state: document.getElementById("process_state"),
      bot_state: document.getElementById("bot_state"),
      pid: document.getElementById("pid"),
      return_code: document.getElementById("return_code"),
      bot_user: document.getElementById("bot_user"),
      guild_count: document.getElementById("guild_count"),
      latency: document.getElementById("latency"),
      uptime: document.getElementById("uptime"),
      home_guild: document.getElementById("home_guild"),
      lockdown: document.getElementById("lockdown"),
      global_ban_count: document.getElementById("global_ban_count"),
      lockdown_setting_block: document.getElementById("lockdown_setting_block"),
      lockdown_stat: document.getElementById("lockdown_stat"),
      lockdown_state: document.getElementById("lockdown_state"),
      selected_slot_name: document.getElementById("selected_slot_name"),
      selected_status_port: document.getElementById("selected_status_port"),
      selected_database_path: document.getElementById("selected_database_path"),
      selected_env_file: document.getElementById("selected_env_file"),
      stdout_log: document.getElementById("stdout_log"),
      stderr_log: document.getElementById("stderr_log"),
    }};

    function setStatusPill(kind, text) {{
      const pill = document.querySelector(".status-pill");
      if (!pill) {{
        return;
      }}
      pill.classList.remove("ok", "offline");
      if (kind) {{
        pill.classList.add(kind);
      }}
      pill.querySelector(".status-text").textContent = text;
    }}

    function formatBool(value) {{
      return value ? "Enabled" : "Disabled";
    }}

    function formatUser(user) {{
      if (!user) {{
        return "—";
      }}
      return `${{user.tag}} (${{user.id}})`;
    }}

    async function postAction(action) {{
      const response = await fetch(`/api/slots/${{selectedSlotId}}/${{action}}`, {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
        }},
      }});
      const data = await response.json();
      // Wait a bit for the bot to start/stop before refreshing
      await new Promise(resolve => setTimeout(resolve, 1500));
      await refresh();
      alert(data.message || "Action completed.");
    }}

    async function postLockdown(enabled) {{
      const response = await fetch(`/api/slots/${{selectedSlotId}}/lockdown`, {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
        }},
        body: JSON.stringify({{
          enabled,
        }}),
      }});
      const data = await response.json();
      await refresh();
      alert(data.message || "Lockdown updated.");
    }}

    async function refresh() {{
      try {{
        const response = await fetch(`/api/slots/${{selectedSlotId}}/status`, {{ cache: "no-store" }});
        const data = await response.json();

        const running = Boolean(data.running);
        const managed = Boolean(data.managed);
        const botStatus = data.bot_status || null;
        const slotInfo = data.slot || {{}};
        const slotIssue = !slotInfo.entrypoint_exists
          ? "Entrypoint missing"
          : !slotInfo.env_exists
            ? "Env missing"
            : null;
        const lockdownSupported = Boolean(botStatus?.lockdown_supported ?? slotInfo.supports_lockdown ?? selectedSupportsLockdown);
        const lockdownEnabled = Boolean(botStatus?.lockdown_enabled);

        if (running) {{
          setStatusPill("ok", managed ? "Bot running" : "Bot reachable");
        }} else {{
          setStatusPill("offline", "Bot stopped");
        }}

        statusFields.process_state.textContent = slotIssue || (managed ? "Managed by dashboard" : (running ? "External or detached" : "Stopped"));
        statusFields.bot_state.textContent = botStatus ? (botStatus.ready ? "Online" : "Starting") : "Offline";
        statusFields.pid.textContent = data.pid ?? botStatus?.pid ?? "—";
        statusFields.return_code.textContent = data.returncode ?? "—";
        statusFields.bot_user.textContent = formatUser(botStatus?.user ?? null);
        statusFields.guild_count.textContent = botStatus?.guild_count ?? "—";
        statusFields.latency.textContent = botStatus?.latency_ms !== null && botStatus?.latency_ms !== undefined
          ? `${{botStatus.latency_ms}} ms`
          : "—";
        statusFields.uptime.textContent = botStatus?.uptime_seconds !== null && botStatus?.uptime_seconds !== undefined
          ? botStatus.uptime_seconds + " seconds"
          : "—";
        statusFields.home_guild.textContent = botStatus?.home_guild_name
          ? `${{botStatus.home_guild_name}} (${{botStatus.home_guild_id}})`
          : "—";
        if (statusFields.lockdown_setting_block) {{
          statusFields.lockdown_setting_block.style.display = lockdownSupported ? "" : "none";
        }}
        if (statusFields.lockdown_stat) {{
          statusFields.lockdown_stat.style.display = lockdownSupported ? "" : "none";
        }}
        statusFields.lockdown.textContent = lockdownSupported ? formatBool(lockdownEnabled) : "N/A";
        statusFields.lockdown_state.textContent = lockdownSupported ? (lockdownEnabled ? "Enabled" : "Disabled") : "Not available";
        statusFields.lockdown_state.classList.toggle("ok", lockdownSupported && lockdownEnabled);
        statusFields.lockdown_state.classList.toggle("offline", !lockdownSupported || !lockdownEnabled);
        statusFields.selected_slot_name.textContent = slotInfo.name || selectedSlotId;
        statusFields.selected_status_port.textContent = slotInfo.status_port
          ? `127.0.0.1:${{slotInfo.status_port}}`
          : "—";
        statusFields.selected_database_path.textContent = slotInfo.database_path || "—";
        statusFields.selected_env_file.textContent = slotInfo.env_file || "—";
        statusFields.global_ban_count.textContent = botStatus?.global_ban_count ?? "—";
        statusFields.stdout_log.textContent = data.stdout_tail || "No stdout captured yet.";
        statusFields.stderr_log.textContent = data.stderr_tail || "No stderr captured yet.";

        buttons.forEach((button) => {{
          if (button.dataset.action === "start") {{
            button.disabled = running || Boolean(slotIssue);
          }}
          if (button.dataset.action === "stop") {{
            button.disabled = !running;
          }}
          if (button.dataset.action === "restart") {{
            button.disabled = !running;
          }}
        }});

        settingButtons.forEach((button) => {{
          if (button.dataset.settingAction === "lockdown-on") {{
            button.disabled = !lockdownSupported || lockdownEnabled;
          }}
          if (button.dataset.settingAction === "lockdown-off") {{
            button.disabled = !lockdownSupported || !lockdownEnabled;
          }}
        }});
      }} catch (error) {{
        setStatusPill("offline", "Dashboard disconnected");
        statusFields.process_state.textContent = "Unavailable";
        statusFields.bot_state.textContent = "Unavailable";
        statusFields.stdout_log.textContent = "Unable to load dashboard status.";
        statusFields.stderr_log.textContent = String(error);
      }}
    }}

    buttons.forEach((button) => {{
      button.addEventListener("click", async () => {{
        button.disabled = true;
        try {{
          await postAction(button.dataset.action);
          // Add extra refresh after action completes to ensure status is updated
          await new Promise(resolve => setTimeout(resolve, 500));
          await refresh();
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});

    settingButtons.forEach((button) => {{
      button.addEventListener("click", async () => {{
        button.disabled = true;
        try {{
          if (button.dataset.settingAction === "lockdown-on") {{
            await postLockdown(true);
          }} else if (button.dataset.settingAction === "lockdown-off") {{
            await postLockdown(false);
          }}
          // Add delay to ensure refresh after setting change
          await new Promise(resolve => setTimeout(resolve, 500));
          await refresh();
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});

    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>"""


class BotProcessController:
    def __init__(self, slot: SlotDefinition) -> None:
        self.slot = slot
        self.process: asyncio.subprocess.Process | None = None
        self._stdout_handle: Any | None = None
        self._stderr_handle: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def python_executable(self) -> Path:
        return _resolve_python_executable()

    @property
    def stdout_log(self) -> Path:
        return self.slot.stdout_log

    @property
    def stderr_log(self) -> Path:
        return self.slot.stderr_log

    @property
    def status_port(self) -> int:
        return self.slot.status_port

    @property
    def entrypoint(self) -> Path:
        return self.slot.entrypoint_path

    def _is_managed_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def _close_log_handles(self) -> None:
        for handle in (self._stdout_handle, self._stderr_handle):
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass
        self._stdout_handle = None
        self._stderr_handle = None

    async def _prune_finished_process(self) -> None:
        if self.process is None:
            return
        if self.process.returncode is None:
            return
        self._close_log_handles()
        self.process = None

    async def _fetch_bot_status(self) -> dict[str, Any] | None:
        timeout = ClientTimeout(total=3)
        url = f"http://127.0.0.1:{self.status_port}/status"
        try:
            async with ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    return await response.json()
        except Exception:
            return None

    def _bot_status_pid(self, bot_status: dict[str, Any] | None) -> int | None:
        if not bot_status:
            return None
        raw_pid = bot_status.get("pid")
        try:
            return int(raw_pid)
        except (TypeError, ValueError):
            return None

    async def _wait_for_shutdown(self, timeout_seconds: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if await self._fetch_bot_status() is None:
                return True
            await asyncio.sleep(0.5)
        return await self._fetch_bot_status() is None

    async def _terminate_external_process(self, pid: int) -> tuple[bool, str]:
        def _terminate_tree() -> tuple[bool, str]:
            try:
                proc = psutil.Process(pid)
            except psutil.NoSuchProcess:
                return True, f"{self.slot.name} is already stopped."
            except psutil.Error as exc:
                return False, f"Unable to access {self.slot.name} process {pid}: {exc}"

            targets = [proc]
            try:
                targets.extend(proc.children(recursive=True))
            except psutil.Error:
                pass

            for target in targets:
                try:
                    target.terminate()
                except psutil.Error:
                    pass

            try:
                _, alive = psutil.wait_procs(targets, timeout=10)
            except psutil.Error as exc:
                return False, f"Failed waiting for {self.slot.name} to exit: {exc}"

            for target in alive:
                try:
                    target.kill()
                except psutil.Error:
                    pass

            if alive:
                try:
                    psutil.wait_procs(alive, timeout=5)
                except psutil.Error:
                    pass

            return True, f"Stopped {self.slot.name} process {pid}."

        return await asyncio.to_thread(_terminate_tree)

    async def set_lockdown(self, enabled: bool) -> tuple[bool, str]:
        async with self._lock:
            if not self.slot.supports_lockdown:
                return False, f"Lockdown is not available for {self.slot.name}."
            bot_status = await self._fetch_bot_status()
            if bot_status is None:
                return False, f"{self.slot.name} is not reachable."

        timeout = ClientTimeout(total=5)
        url = f"http://127.0.0.1:{self.status_port}/control/lockdown"
        try:
            async with ClientSession(timeout=timeout) as session:
                async with session.post(url, json={"enabled": enabled}) as response:
                    data = await response.json(content_type=None)
                    if response.status != 200 or not data.get("ok"):
                        return False, data.get("message", "Failed to update lockdown.")
                    state_text = "enabled" if data.get("lockdown_enabled") else "disabled"
                    return True, f"Lockdown {state_text} for {self.slot.name}."
        except Exception:
            return False, f"Failed to update lockdown for {self.slot.name}."

    async def start(self) -> tuple[bool, str]:
        async with self._lock:
            await self._prune_finished_process()
            if self._is_managed_running():
                assert self.process is not None
                return False, f"{self.slot.name} is already running under the dashboard (PID {self.process.pid})."

            if await self._fetch_bot_status() is not None:
                return False, f"{self.slot.name} is already reachable on 127.0.0.1:{self.status_port}."

            if not self.entrypoint.exists():
                return False, f"Entrypoint not found: {_format_path(str(self.entrypoint))}."

            if not self.slot.env_path.exists():
                return False, f"Env file not found for {self.slot.name}: {_format_path(str(self.slot.env_path))}."

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.stdout_log.touch(exist_ok=True)
            self.stderr_log.touch(exist_ok=True)

            env = _build_slot_environment(self.slot)

            self._stdout_handle = self.stdout_log.open("ab", buffering=0)
            self._stderr_handle = self.stderr_log.open("ab", buffering=0)
            try:
                self.process = await asyncio.create_subprocess_exec(
                    str(self.python_executable),
                    str(self.entrypoint),
                    cwd=str(ROOT),
                    env=env,
                    stdout=self._stdout_handle,
                    stderr=self._stderr_handle,
                )
            except Exception:
                self._close_log_handles()
                self.process = None
                raise

            return True, f"Started {self.slot.name} process with PID {self.process.pid}."

    async def stop(self) -> tuple[bool, str]:
        async with self._lock:
            await self._prune_finished_process()
            if not self._is_managed_running():
                bot_status = await self._fetch_bot_status()
                if bot_status is None:
                    return False, f"{self.slot.name} is not running."

                external_pid = self._bot_status_pid(bot_status)
                if external_pid is None:
                    return False, f"{self.slot.name} is reachable on the status port, but its process id is unavailable."

                stopped, message = await self._terminate_external_process(external_pid)
                if not stopped:
                    return False, message
                if not await self._wait_for_shutdown():
                    return False, f"Stopped {self.slot.name}, but it is still reachable on the status port."
                return True, message

            assert self.process is not None
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=15)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

            pid = self.process.pid
            self._close_log_handles()
            self.process = None
            if not await self._wait_for_shutdown():
                return False, f"Stopped {self.slot.name} process {pid}, but it is still reachable on the status port."
            return True, f"Stopped {self.slot.name} process {pid}."

    async def restart(self) -> tuple[bool, str]:
        async with self._lock:
            await self._prune_finished_process()
            managed_running = self._is_managed_running()

        if not managed_running:
            bot_status = await self._fetch_bot_status()
            if bot_status is not None:
                stopped, stop_message = await self.stop()
                if not stopped:
                    return False, stop_message
                started, start_message = await self.start()
                if started:
                    return True, f"{stop_message} {start_message}"
                return False, start_message
            started, start_message = await self.start()
            return started, start_message

        stopped, stop_message = await self.stop()
        if not stopped:
            return False, stop_message
        started, start_message = await self.start()
        if started:
            return True, f"{stop_message} {start_message}"
        return False, start_message

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            await self._prune_finished_process()
            managed_running = self._is_managed_running()
            process = self.process

        bot_status = await self._fetch_bot_status()
        running = managed_running or bot_status is not None
        bot_status_pid = self._bot_status_pid(bot_status)
        return {
            "slot": {
                "id": self.slot.id,
                "name": self.slot.name,
                "status_port": self.slot.status_port,
                "database_path": self.slot.database_path,
                "supports_lockdown": self.slot.supports_lockdown,
                "entrypoint": _format_path(str(self.slot.entrypoint_path)),
                "env_file": _format_path(str(self.slot.env_path)),
                "env_exists": self.slot.env_path.exists(),
                "entrypoint_exists": self.slot.entrypoint_path.exists(),
            },
            "running": running,
            "managed": managed_running,
            "pid": process.pid if managed_running and process is not None else bot_status_pid,
            "returncode": process.returncode if process is not None else None,
            "bot_status": bot_status,
            "stdout_tail": _tail_file(self.stdout_log),
            "stderr_tail": _tail_file(self.stderr_log),
            "status_port": self.status_port,
        }


class DashboardManager:
    def __init__(self, slots: list[SlotDefinition]) -> None:
        if not slots:
            raise ValueError("At least one slot definition is required.")
        self.slots = slots
        self.default_slot_id = slots[0].id
        self._slot_map = {slot.id: slot for slot in slots}
        self._controllers = {slot.id: BotProcessController(slot) for slot in slots}

    def resolve_slot(self, slot_id: str | None) -> SlotDefinition:
        if slot_id and slot_id in self._slot_map:
            return self._slot_map[slot_id]
        return self._slot_map[self.default_slot_id]

    def controller_for(self, slot_id: str | None) -> BotProcessController:
        slot = self.resolve_slot(slot_id)
        return self._controllers[slot.id]

    async def snapshot(self, slot_id: str | None) -> dict[str, Any]:
        return await self.controller_for(slot_id).snapshot()

    async def snapshot_all(self) -> list[dict[str, Any]]:
        return await asyncio.gather(*(self._controllers[slot.id].snapshot() for slot in self.slots))

    async def start(self, slot_id: str | None) -> tuple[bool, str]:
        return await self.controller_for(slot_id).start()

    async def stop(self, slot_id: str | None) -> tuple[bool, str]:
        return await self.controller_for(slot_id).stop()

    async def restart(self, slot_id: str | None) -> tuple[bool, str]:
        return await self.controller_for(slot_id).restart()

    async def set_lockdown(self, slot_id: str | None, enabled: bool) -> tuple[bool, str]:
        return await self.controller_for(slot_id).set_lockdown(enabled)


def create_app() -> web.Application:
    load_dotenv()

    dashboard_host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    dashboard_port = _env_int("DASHBOARD_PORT", 8080)
    manager = DashboardManager(load_slot_definitions())

    def _slot_id_from_request(request: web.Request) -> str | None:
        return request.match_info.get("slot_id") or request.query.get("slot")

    async def dashboard(request: web.Request) -> web.Response:
        selected_slot = manager.resolve_slot(_slot_id_from_request(request))
        snapshots = await manager.snapshot_all()
        snapshot_map: dict[str, dict[str, Any] | None] = {
            snapshot["slot"]["id"]: snapshot for snapshot in snapshots
        }
        html_text = _render_dashboard_html(
            dashboard_host,
            dashboard_port,
            manager.slots,
            selected_slot,
            snapshot_map,
        )
        return web.Response(text=html_text, content_type="text/html")

    async def slot_page(request: web.Request) -> web.Response:
        slot = manager.resolve_slot(request.match_info.get("slot_id"))
        raise web.HTTPFound(f"/?slot={slot.id}")

    async def api_slots(_: web.Request) -> web.Response:
        snapshots = await manager.snapshot_all()
        return web.json_response(
            {
                "ok": True,
                "default_slot_id": manager.default_slot_id,
                "slots": snapshots,
            }
        )

    async def api_status(request: web.Request) -> web.Response:
        return web.json_response(await manager.snapshot(_slot_id_from_request(request)))

    async def api_start(request: web.Request) -> web.Response:
        ok, message = await manager.start(_slot_id_from_request(request))
        return web.json_response({"ok": ok, "message": message})

    async def api_stop(request: web.Request) -> web.Response:
        ok, message = await manager.stop(_slot_id_from_request(request))
        return web.json_response({"ok": ok, "message": message})

    async def api_restart(request: web.Request) -> web.Response:
        ok, message = await manager.restart(_slot_id_from_request(request))
        return web.json_response({"ok": ok, "message": message})

    async def api_lockdown(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        enabled_value = payload.get("enabled") if isinstance(payload, dict) else None
        if isinstance(enabled_value, str):
            enabled = enabled_value.strip().lower() in {"1", "true", "yes", "on"}
        elif isinstance(enabled_value, bool):
            enabled = enabled_value
        elif isinstance(enabled_value, int):
            enabled = enabled_value != 0
        else:
            return web.json_response({"ok": False, "message": "Missing enabled value."}, status=400)

        ok, message = await manager.set_lockdown(_slot_id_from_request(request), enabled)
        return web.json_response({"ok": ok, "message": message, "lockdown_enabled": enabled})

    app = web.Application()
    app["manager"] = manager
    app.router.add_get("/", dashboard)
    app.router.add_get("/slot/{slot_id}", slot_page)
    app.router.add_get("/api/slots", api_slots)
    app.router.add_get("/api/slots/{slot_id}/status", api_status)
    app.router.add_post("/api/slots/{slot_id}/start", api_start)
    app.router.add_post("/api/slots/{slot_id}/stop", api_stop)
    app.router.add_post("/api/slots/{slot_id}/restart", api_restart)
    app.router.add_post("/api/slots/{slot_id}/lockdown", api_lockdown)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/start", api_start)
    app.router.add_post("/api/stop", api_stop)
    app.router.add_post("/api/restart", api_restart)
    app.router.add_post("/api/lockdown", api_lockdown)
    return app


async def main() -> None:
    app = create_app()
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _env_int("DASHBOARD_PORT", 8080)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    log.info("Dashboard listening on http://%s:%s", host, port)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
