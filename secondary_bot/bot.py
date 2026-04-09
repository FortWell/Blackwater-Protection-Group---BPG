from __future__ import annotations

# bot.py
# pip install -U discord.py
import asyncio
import datetime
import json
import random
import sqlite3
import time
import os
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web
from dotenv import load_dotenv
import psutil
import bot.branding as shared_branding
from cogs.tickets import _create_ticket_from_modal, _ticket_types


ROOT = Path(__file__).resolve().parents[1]
SECONDARY_ENV = ROOT / ".env.secondary"
load_dotenv(SECONDARY_ENV, override=True)

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SECONDARY_GUILD_ID_RAW = os.getenv("SECONDARY_GUILD_ID", "").strip()

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN in .env.secondary")

SECONDARY_GUILD_ID = int(SECONDARY_GUILD_ID_RAW) if SECONDARY_GUILD_ID_RAW.isdigit() else None

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
bot.started_at_monotonic = time.monotonic()
_web_runner: web.AppRunner | None = None

COMMAND_GUILD = discord.Object(id=SECONDARY_GUILD_ID) if SECONDARY_GUILD_ID is not None else None
OCI_INVESTIGATOR_ROLE_ID = 1320364221707321394
PRIORITY_SUPPORT_OPEN_ROLE_ID = 1428712941749796954

OCI_TICKET_THUMBNAIL_URL = (
    "https://cdn.discordapp.com/attachments/1327068360319565876/"
    "1489758499134242877/Office_of_Community_Investigations_logo.png?ex=69d23df8&is=69d0ec78&hm=43e393aa18c72a4df4de014674a718f1304409e805a9b8f1caf0fda37382f3ad&"
)
OCI_TICKET_IMAGE_URL = (
    "https://cdn.discordapp.com/attachments/1327068360319565876/"
    "1489758898670796881/IMG_20260404_014939.png?ex=69d23e57&is=69d0ecd7&hm=1adb98f95efefd15c2b44990f7f0fae4189a6895baef5708ef99d4d0bd5a2a3f&"
)
OCI_TICKET_FOOTER_ICON_URL = (
    "https://cdn.discordapp.com/attachments/1327068360319565876/"
    "1489758499134242877/Office_of_Community_Investigations_logo.png?ex=69d23df8&is=69d0ec78&hm=43e393aa18c72a4df4de014674a718f1304409e805a9b8f1caf0fda37382f3ad&"
)
OCI_TICKET_FOOTER_TEXT = "OCI Ticket System"


class SecondaryTicketConfig:
    def __init__(self, bot_ref: commands.Bot):
        self._bot = bot_ref
        self.bot_audit_webhook_url = os.getenv("BOT_AUDIT_WEBHOOK_URL", "").strip()
        self.embed_templates: dict[str, dict[str, object]] = {}
        self.ticket_panel_mode = "case_report"

    def _guild(self) -> discord.Guild | None:
        if SECONDARY_GUILD_ID is not None:
            guild = self._bot.get_guild(SECONDARY_GUILD_ID)
            if guild is not None:
                return guild
        if len(self._bot.guilds) == 1:
            return self._bot.guilds[0]
        return None

    @staticmethod
    def _env_int(name: str, default: int = 0) -> int:
        raw = os.getenv(name, "").strip()
        return int(raw) if raw.isdigit() else default

    def _resolve_category_id(self, env_name: str, candidate_names: list[str]) -> int:
        value = self._env_int(env_name, 0)
        if value:
            return value
        guild = self._guild()
        if guild is None:
            return 0
        candidates = [name.casefold().strip() for name in candidate_names if name.strip()]
        for category in guild.categories:
            normalized = category.name.casefold().strip()
            if normalized in candidates:
                return category.id
        for category in guild.categories:
            normalized = category.name.casefold().strip()
            if any(candidate in normalized for candidate in candidates):
                return category.id
        return 0

    def _resolve_role_id(self, env_name: str, candidate_names: list[str], default: int = 0) -> int:
        value = self._env_int(env_name, default)
        if value:
            return value
        guild = self._guild()
        if guild is None:
            return default
        candidates = [name.casefold().strip() for name in candidate_names if name.strip()]
        for role in guild.roles:
            normalized = role.name.casefold().strip()
            if normalized in candidates:
                return role.id
        for role in guild.roles:
            normalized = role.name.casefold().strip()
            if any(candidate in normalized for candidate in candidates):
                return role.id
        return default

    @property
    def ticket_management_category_id(self) -> int:
        return self._resolve_category_id(
            "TICKET_MANAGEMENT_CATEGORY_ID",
            ["management support", "management tickets", "management", "management support tickets"],
        )

    @property
    def ticket_management_support_role_id(self) -> int:
        return self._resolve_role_id(
            "TICKET_MANAGEMENT_SUPPORT_ROLE_ID",
            ["management support", "management", "management staff", "support staff"],
        )

    @property
    def ticket_security_category_id(self) -> int:
        return self._resolve_category_id(
            "TICKET_SECURITY_CATEGORY_ID",
            ["order security tickets", "security tickets", "security", "order security"],
        )

    @property
    def ticket_security_support_role_id(self) -> int:
        return self._resolve_role_id(
            "TICKET_SECURITY_SUPPORT_ROLE_ID",
            ["security support", "security", "order security", "support staff"],
        )

    @property
    def ticket_general_category_id(self) -> int:
        return self._resolve_category_id(
            "TICKET_GENERAL_CATEGORY_ID",
            ["executive support", "general support", "support"],
        )

    @property
    def ticket_general_support_role_id(self) -> int:
        return self._resolve_role_id(
            "TICKET_GENERAL_SUPPORT_ROLE_ID",
            ["executive support", "general support", "support staff"],
        )

    @property
    def ticket_priority_category_id(self) -> int:
        return self._resolve_category_id(
            "TICKET_PRIORITY_CATEGORY_ID",
            ["priority support", "priority tickets", "priority"],
        )

    @property
    def ticket_priority_support_role_id(self) -> int:
        return self._resolve_role_id(
            "TICKET_PRIORITY_SUPPORT_ROLE_ID",
            ["priority support", "priority", "priority staff"],
        )

    @property
    def ticket_priority_open_role_id(self) -> int:
        return self._env_int("TICKET_PRIORITY_OPEN_ROLE_ID", PRIORITY_SUPPORT_OPEN_ROLE_ID)

    @property
    def ticket_role_perms_id(self) -> int:
        return self._env_int("TICKET_ROLE_PERMS_ID")

    @property
    def active_cases_channel_id(self) -> int:
        return self._env_int("ACTIVE_CASES_CHANNEL")

    @property
    def blacklist_channel_id(self) -> int:
        return self._env_int("BLACKLIST_CHANNEL")

    @property
    def logs_channel_id(self) -> int:
        return self._env_int("LOGS_CHANNEL")


bot.config = SecondaryTicketConfig(bot)


def _command(**kwargs: Any):
    if COMMAND_GUILD is not None:
        kwargs["guild"] = COMMAND_GUILD
    return bot.tree.command(**kwargs)


def _scope_commands_to_guild(guild_id: int) -> None:
    for command in bot.tree.walk_commands():
        command.guild_ids = [guild_id]


def _format_uptime(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, min_ = divmod(minutes, 60)
    days, hour = divmod(hours, 24)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hour or parts:
        parts.append(f"{hour}h")
    if min_ or parts:
        parts.append(f"{min_}m")
    parts.append(f"{sec}s")
    return " ".join(parts)


def _is_oci_staff(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    return any(role.id == OCI_INVESTIGATOR_ROLE_ID for role in member.roles)


async def _require_oci_staff(interaction: discord.Interaction) -> bool:
    if _is_oci_staff(interaction):
        return True

    message = "You are not authorized."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return False


async def _resolve_text_channel(interaction: discord.Interaction, channel_id: int) -> discord.TextChannel | None:
    if not channel_id:
        return None
    channel = interaction.client.get_channel(channel_id)
    if channel is None and interaction.guild is not None:
        try:
            channel = await interaction.guild.fetch_channel(channel_id)
        except discord.HTTPException:
            channel = None
    return channel if isinstance(channel, discord.TextChannel) else None


def _build_status_payload() -> dict[str, object]:
    user = bot.user
    latency_ms = round(bot.latency * 1000, 2) if bot.latency is not None else None
    uptime_seconds = int(time.monotonic() - getattr(bot, "started_at_monotonic", time.monotonic()))
    return {
        "ok": True,
        "service": "bpg-secondary",
        "ready": bot.is_ready(),
        "logged_in": user is not None,
        "pid": os.getpid(),
        "user": {
            "id": user.id,
            "tag": str(user),
        }
        if user is not None
        else None,
        "guild_count": len(bot.guilds),
        "latency_ms": latency_ms,
        "uptime_seconds": uptime_seconds,
        "home_guild_id": SECONDARY_GUILD_ID,
        "home_guild_name": None,
        "lockdown_supported": False,
    }


def _dashboard_url() -> str:
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.getenv("DASHBOARD_PORT", "8080").strip() or "8080"
    return f"http://{host}:{port}"


def _case_db_path() -> Path:
    raw = os.getenv("DATABASE_PATH", "data/bot-2.db").strip() or "data/bot-2.db"
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def _init_case_db() -> None:
    path = _case_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oci_cases (
                case_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_by_id INTEGER NOT NULL,
                created_by_tag TEXT NOT NULL,
                created_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.commit()


def _store_case_record(case_id: str, subject: str, reason: str, created_by_id: int, created_by_tag: str) -> None:
    path = _case_db_path()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO oci_cases (
                case_id, subject, reason, created_by_id, created_by_tag, created_at, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, '[]')
            ON CONFLICT(case_id) DO UPDATE SET
                subject = excluded.subject,
                reason = excluded.reason,
                created_by_id = excluded.created_by_id,
                created_by_tag = excluded.created_by_tag,
                created_at = excluded.created_at
            """,
            (
                case_id,
                subject,
                reason,
                created_by_id,
                created_by_tag,
                datetime.datetime.now(datetime.UTC).isoformat(),
            ),
        )
        conn.commit()


def _fetch_case_exists(case_id: str) -> bool:
    path = _case_db_path()
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT 1 FROM oci_cases WHERE case_id = ? LIMIT 1", (case_id,)).fetchone()
    return row is not None


def _append_case_evidence(case_id: str, evidence_entry: dict[str, Any]) -> bool:
    path = _case_db_path()
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT evidence_json FROM oci_cases WHERE case_id = ? LIMIT 1", (case_id,)).fetchone()
        if row is None:
            return False
        try:
            evidence_list = json.loads(row[0]) if row[0] else []
        except json.JSONDecodeError:
            evidence_list = []
        if not isinstance(evidence_list, list):
            evidence_list = []
        evidence_list.append(evidence_entry)
        conn.execute(
            "UPDATE oci_cases SET evidence_json = ? WHERE case_id = ?",
            (json.dumps(evidence_list), case_id),
        )
        conn.commit()
    return True


async def _rehydrate_case_from_channel(channel: discord.TextChannel, case_id: str) -> bool:
    target_title = f"Case File: {case_id}"
    async for message in channel.history(limit=500, oldest_first=False):
        if not message.embeds:
            continue
        embed = message.embeds[0]
        if (embed.title or "").strip() != target_title:
            continue

        subject = "Unknown"
        reason = "Unknown"
        created_by_tag = "Unknown"
        for field in embed.fields:
            name = field.name.casefold().strip()
            value = str(field.value).strip() or "Unknown"
            if name == "subject":
                subject = value
            elif name == "reason":
                reason = value
            elif name == "investigator":
                created_by_tag = value

        await asyncio.to_thread(_store_case_record, case_id, subject, reason, 0, created_by_tag)
        return True
    return False


async def _rehydrate_case_record(interaction: discord.Interaction, case_id: str) -> bool:
    candidate_ids: list[int] = []
    cfg = interaction.client.config
    for channel_id in (cfg.active_cases_channel_id, cfg.logs_channel_id):
        if channel_id and channel_id not in candidate_ids:
            candidate_ids.append(channel_id)
    if isinstance(interaction.channel, discord.TextChannel) and interaction.channel.id not in candidate_ids:
        candidate_ids.append(interaction.channel.id)

    for channel_id in candidate_ids:
        channel = await _resolve_text_channel(interaction, channel_id)
        if channel is None:
            continue
        if await _rehydrate_case_from_channel(channel, case_id):
            return True
    return False


def _is_ticket_staff(member: discord.Member) -> bool:
    cfg = bot.config
    role_ids = {
        getattr(cfg, "ticket_management_support_role_id", 0),
        getattr(cfg, "ticket_security_support_role_id", 0),
        getattr(cfg, "ticket_general_support_role_id", 0),
        getattr(cfg, "ticket_priority_support_role_id", 0),
        getattr(cfg, "ticket_role_perms_id", 0),
    }
    return any(role_id and member.get_role(role_id) for role_id in role_ids)


# -------- Commands --------


@_command(name="ping", description="Show bot latency, CPU, RAM, and uptime.")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    process = psutil.Process()
    memory_info = process.memory_info()
    ram_mb = memory_info.rss / (1024 * 1024)
    cpu_percent = process.cpu_percent(interval=0.2)

    started_at = getattr(bot, "started_at_monotonic", time.monotonic())
    uptime_seconds = max(0, int(time.monotonic() - started_at))
    latency_ms = round(bot.latency * 1000, 1)

    embed = discord.Embed(
        title="Pong!",
        color=0x0B1E3D,
        timestamp=datetime.datetime.now(datetime.UTC),
    )
    embed.add_field(name="Latency", value=f"`{latency_ms} ms`", inline=True)
    embed.add_field(name="CPU Usage", value=f"`{cpu_percent:.1f}%`", inline=True)
    embed.add_field(name="RAM Usage", value=f"`{ram_mb:.1f} MB`", inline=True)
    embed.add_field(name="Uptime", value=f"`{_format_uptime(uptime_seconds)}`", inline=False)

    await interaction.followup.send(embed=embed)


@_command(
    name="dashboard",
    description="Show the local dashboard link.",
)
async def dashboard(interaction: discord.Interaction) -> None:
    dashboard_url = _dashboard_url()

    embed = discord.Embed(
        title="OCI Dashboard",
        description=(
            f"Open the local control panel here:\n`{dashboard_url}`\n\n"
            "If it is not already running, start it with `python dashboard.py`."
        ),
        color=0x0B1E3D,
        timestamp=datetime.datetime.now(datetime.UTC),
    )
    embed.set_thumbnail(url=shared_branding.BRANDING_THUMBNAIL_URL)
    embed.set_image(url=shared_branding.BRANDING_IMAGE_URL)
    embed.set_footer(
        text=shared_branding.BRANDING_FOOTER_TEXT,
        icon_url=shared_branding.BRANDING_FOOTER_ICON_URL,
    )

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Open Dashboard", url=dashboard_url))

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AppealTicketModal(discord.ui.Modal, title="Submit an Appeal"):
    case_id = discord.ui.TextInput(
        label="Case ID",
        placeholder="Enter the case ID",
        style=discord.TextStyle.short,
        required=True,
        max_length=100,
    )
    explanation = discord.ui.TextInput(
        label="Explanation",
        placeholder="Explain why you believe the decision was incorrect.",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=900,
    )
    evidence = discord.ui.TextInput(
        label="Any new evidence",
        placeholder="Add any screenshots, links, or context.",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=900,
    )

    def __init__(self, ticket_type: object):
        super().__init__(timeout=300)
        self.ticket_type = ticket_type

    async def on_submit(self, interaction: discord.Interaction) -> None:
        extra_fields = [
            ("Case ID", str(self.case_id)),
            ("Any new evidence", str(self.evidence)),
        ]
        await _create_ticket_from_modal(
            interaction,
            self.ticket_type,
            reason=str(self.explanation),
            reason_title="Explanation",
            extra_fields=extra_fields,
            include_roblox_info=False,
            title_override="Appeal",
            description_override=(
                "Thank you for submitting an appeal. "
                "Our team will review the appeal shortly."
            ),
            followup_message="Appeal created: {ticket_channel}",
        )


class AppealPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Appeal",
        style=discord.ButtonStyle.primary,
        emoji="📩",
        custom_id="ticket:create:appeal",
    )
    async def open_appeal(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        ticket_type = _ticket_types(bot.config).get("general")
        if ticket_type is None:
            await interaction.response.send_message("Appeal tickets are not configured.", ephemeral=True)
            return
        await interaction.response.send_modal(AppealTicketModal(ticket_type))


async def _send_appeal_panel(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return
    if not _is_ticket_staff(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this in a text channel.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Submit an Appeal",
        description=(
            "If you believe a decision was incorrect, you may submit an appeal.\n\n"
            "Include:\n"
            "• Case ID\n"
            "• Explanation\n"
            "• Any new evidence\n\n"
            "Abuse of appeals may result in denial."
        ),
        color=0x0B1E3D,
    )
    embed.set_thumbnail(url=shared_branding.BRANDING_THUMBNAIL_URL)
    embed.set_image(url=shared_branding.BRANDING_IMAGE_URL)
    embed.set_footer(
        text=shared_branding.BRANDING_FOOTER_TEXT,
        icon_url=shared_branding.BRANDING_FOOTER_ICON_URL,
    )

    async for msg in interaction.channel.history(limit=30):
        if msg.author.id != bot.user.id:
            continue
        if not msg.embeds:
            continue
        first = msg.embeds[0]
        if first.title == "Submit an Appeal" and msg.components:
            await interaction.response.send_message(
                f"Ticket panel already exists: {msg.jump_url}",
                ephemeral=True,
            )
            return

    await interaction.channel.send(embed=embed, view=AppealPanelView())
    await interaction.response.send_message("Ticket panel sent.", ephemeral=True)


@_command(name="ticketblacklist", description="Post the appeal ticket panel.")
async def ticketblacklist(interaction: discord.Interaction) -> None:
    await _send_appeal_panel(interaction)


@_command(name="ticketapeal", description="Post the appeal ticket panel.")
async def ticketapeal(interaction: discord.Interaction) -> None:
    await _send_appeal_panel(interaction)


@_command(name="case", description="Create a case")
@app_commands.describe(subject="User or server", reason="Reason")
async def case(interaction: discord.Interaction, subject: str, reason: str):
    if not await _require_oci_staff(interaction):
        return

    channel = await _resolve_text_channel(interaction, interaction.client.config.active_cases_channel_id)
    if channel is None:
        await interaction.response.send_message("Active cases channel is not configured.", ephemeral=True)
        return

    case_id = f"OCI-{datetime.datetime.now(datetime.UTC).year}-{random.randint(1000, 9999)}"
    try:
        await asyncio.to_thread(
            _store_case_record,
            case_id,
            subject,
            reason,
            interaction.user.id,
            str(interaction.user),
        )
    except Exception:
        await interaction.response.send_message("Failed to store the case.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"Case File: {case_id}",
        color=0x2b2d31,
    )
    embed.add_field(name="Status", value="🟡 Under Review", inline=False)
    embed.add_field(name="Subject", value=subject, inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Investigator", value=interaction.user.mention, inline=False)
    embed.set_footer(
        text=shared_branding.BRANDING_FOOTER_TEXT,
        icon_url=shared_branding.BRANDING_FOOTER_ICON_URL,
    )

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        await interaction.response.send_message("Failed to send the case log.", ephemeral=True)
        return

    await interaction.response.send_message(f"Case {case_id} created.", ephemeral=True)


@_command(name="evidence", description="Add evidence to a case")
@app_commands.describe(
    case_id="Case ID",
    description="What this evidence shows",
    link="Optional link (imgur, medal, etc.)",
    attachment="Optional attachment",
)
async def evidence(
    interaction: discord.Interaction,
    case_id: str,
    description: str,
    link: str | None = None,
    attachment: discord.Attachment | None = None,
):
    if not await _require_oci_staff(interaction):
        return

    case_exists = await asyncio.to_thread(_fetch_case_exists, case_id)
    if not case_exists:
        rehydrated = await _rehydrate_case_record(interaction, case_id)
        if not rehydrated:
            await interaction.response.send_message("Case not found.", ephemeral=True)
            return

    attachment_url = attachment.url if attachment is not None else None
    evidence_entry = {
        "description": description,
        "link": link,
        "attachment": attachment_url,
        "submitted_by": interaction.user.id,
    }
    try:
        saved = await asyncio.to_thread(_append_case_evidence, case_id, evidence_entry)
    except Exception:
        saved = False
    if not saved:
        await interaction.response.send_message("Failed to save evidence.", ephemeral=True)
        return

    target_channel = await _resolve_text_channel(interaction, interaction.client.config.logs_channel_id)
    if target_channel is None:
        target_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None

    embed = discord.Embed(
        title=f"Evidence Added: {case_id}",
        color=0x5865F2,
    )
    embed.add_field(name="Description", value=description, inline=False)
    if link:
        embed.add_field(name="Link", value=link, inline=False)
    if attachment_url:
        if attachment is not None and (attachment.content_type or "").startswith("image/"):
            embed.set_image(url=attachment_url)
        else:
            embed.add_field(name="Attachment", value=attachment_url, inline=False)
    embed.add_field(name="Submitted By", value=interaction.user.mention, inline=False)
    embed.set_footer(
        text=shared_branding.BRANDING_FOOTER_TEXT,
        icon_url=shared_branding.BRANDING_FOOTER_ICON_URL,
    )

    if target_channel is not None:
        try:
            await target_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    await interaction.response.send_message(
        f"Evidence added to case `{case_id}`.",
        ephemeral=True,
    )


@_command(name="blacklist", description="Blacklist a subject")
@app_commands.describe(subject="User or server", reason="Reason", notes="Optional notes")
async def blacklist(interaction: discord.Interaction, subject: str, reason: str, notes: str | None = None):
    if not await _require_oci_staff(interaction):
        return

    channel = await _resolve_text_channel(interaction, interaction.client.config.blacklist_channel_id)
    if channel is None:
        await interaction.response.send_message("Blacklist channel is not configured.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Blacklist Notice Issued",
        color=discord.Color.red(),
    )
    embed.add_field(name="Subject", value=subject, inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    if notes:
        embed.add_field(name="Notes", value=notes, inline=False)
    embed.add_field(name="Status", value="🔴 Closed", inline=False)
    embed.set_footer(
        text=shared_branding.BRANDING_FOOTER_TEXT,
        icon_url=shared_branding.BRANDING_FOOTER_ICON_URL,
    )

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        await interaction.response.send_message("Failed to send the blacklist log.", ephemeral=True)
        return

    await interaction.response.send_message("Blacklist recorded.", ephemeral=True)


@_command(name="closecase", description="Close a case")
@app_commands.describe(case_id="Case ID", outcome="Final decision")
async def closecase(interaction: discord.Interaction, case_id: str, outcome: str):
    if not await _require_oci_staff(interaction):
        return

    channel = await _resolve_text_channel(interaction, interaction.client.config.logs_channel_id)
    if channel is None:
        await interaction.response.send_message("Logs channel is not configured.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"Case Closed: {case_id}",
        color=discord.Color.green(),
    )
    embed.add_field(name="Outcome", value=outcome, inline=False)
    embed.add_field(name="Closed By", value=interaction.user.mention, inline=False)
    embed.set_footer(
        text=shared_branding.BRANDING_FOOTER_TEXT,
        icon_url=shared_branding.BRANDING_FOOTER_ICON_URL,
    )

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        await interaction.response.send_message("Failed to send the close log.", ephemeral=True)
        return

    await interaction.response.send_message("Case closed and logged.", ephemeral=True)


@_command(name="appealreview", description="Review an appeal")
@app_commands.describe(case_id="Case ID", decision="Approve/Deny", notes="Optional notes")
@app_commands.choices(decision=[
    app_commands.Choice(name="Approve", value="approve"),
    app_commands.Choice(name="Deny", value="deny"),
])
async def appealreview(interaction: discord.Interaction, case_id: str, decision: app_commands.Choice[str], notes: str = None):
    if not await _require_oci_staff(interaction):
        return

    channel = await _resolve_text_channel(interaction, interaction.client.config.logs_channel_id)
    if channel is None:
        await interaction.response.send_message("Logs channel is not configured.", ephemeral=True)
        return

    if decision.value == "approve":
        status = "🟢 Appeal Approved"
        color = discord.Color.green()
    else:
        status = "🔴 Appeal Denied"
        color = discord.Color.red()

    embed = discord.Embed(
        title=f"Appeal Review: {case_id}",
        color=color,
    )
    embed.add_field(name="Decision", value=status, inline=False)
    if notes:
        embed.add_field(name="Notes", value=notes, inline=False)
    embed.set_footer(
        text=shared_branding.BRANDING_FOOTER_TEXT,
        icon_url=shared_branding.BRANDING_FOOTER_ICON_URL,
    )

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        await interaction.response.send_message("Failed to send the appeal review log.", ephemeral=True)
        return

    await interaction.response.send_message("Appeal reviewed.", ephemeral=True)


@_command(name="assign", description="Assign an investigator to a case")
@app_commands.describe(case_id="Case ID", investigator="User to assign")
async def assign(interaction: discord.Interaction, case_id: str, investigator: discord.Member):
    if not await _require_oci_staff(interaction):
        return

    channel = await _resolve_text_channel(interaction, interaction.client.config.logs_channel_id)
    if channel is None:
        await interaction.response.send_message("Logs channel is not configured.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"Case Assignment: {case_id}",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Assigned To", value=investigator.mention, inline=False)
    embed.add_field(name="Assigned By", value=interaction.user.mention, inline=False)
    embed.set_footer(
        text=shared_branding.BRANDING_FOOTER_TEXT,
        icon_url=shared_branding.BRANDING_FOOTER_ICON_URL,
    )

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        await interaction.response.send_message("Failed to send the assignment log.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"{investigator.mention} has been assigned to case `{case_id}`.",
        ephemeral=True,
    )


# -------- Auto-Role Commands --------


def _init_auto_role_db() -> None:
    """Initialize auto-role tables in the database."""
    path = _case_db_path()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_role_associations (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, role_id)
            )
            """
        )
        conn.commit()


def _get_user_auto_roles(user_id: int, guild_id: int) -> list[int]:
    """Get all auto-role IDs for a user."""
    path = _case_db_path()
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT role_id FROM auto_role_associations WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchall()
    return [row[0] for row in rows]


def _add_user_auto_role(user_id: int, guild_id: int, role_id: int) -> bool:
    """Add an auto-role for a user."""
    path = _case_db_path()
    try:
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO auto_role_associations (guild_id, user_id, role_id) VALUES (?, ?, ?)",
                (guild_id, user_id, role_id),
            )
            conn.commit()
        return True
    except sqlite3.Error:
        return False


def _remove_user_auto_role(user_id: int, guild_id: int, role_id: int) -> bool:
    """Remove an auto-role for a user."""
    path = _case_db_path()
    try:
        with sqlite3.connect(path) as conn:
            conn.execute(
                "DELETE FROM auto_role_associations WHERE guild_id = ? AND user_id = ? AND role_id = ?",
                (guild_id, user_id, role_id),
            )
            conn.commit()
        return True
    except sqlite3.Error:
        return False


@_command(name="auto-role-add", description="Add a role to auto-restore for your profile")
@app_commands.describe(role="The role to automatically restore when you rejoin")
async def auto_role_add(interaction: discord.Interaction, role: discord.Role) -> None:
    """Add a role to the auto-role system."""
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return

    success = await asyncio.to_thread(
        _add_user_auto_role,
        interaction.user.id,
        interaction.guild.id,
        role.id,
    )

    if success:
        embed = discord.Embed(
            title="Auto-Role Added",
            description=f"Role {role.mention} will be automatically restored for you when you rejoin.",
            color=0x0B1E3D,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("Failed to add role.", ephemeral=True)


@_command(name="remove-auto-role", description="Remove a role from auto-restore")
@app_commands.describe(role="The role to remove from auto-restore")
async def remove_auto_role(interaction: discord.Interaction, role: discord.Role) -> None:
    """Remove a role from the auto-role system."""
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return

    success = await asyncio.to_thread(
        _remove_user_auto_role,
        interaction.user.id,
        interaction.guild.id,
        role.id,
    )

    if success:
        embed = discord.Embed(
            title="Auto-Role Removed",
            description=f"Role {role.mention} will no longer be automatically restored.",
            color=0x0B1E3D,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("Failed to remove role.", ephemeral=True)


@_command(name="auto-role-list", description="List all auto-restore roles for your profile")
async def auto_role_list(interaction: discord.Interaction) -> None:
    """List all auto-role associations for the user."""
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    role_ids = await asyncio.to_thread(
        _get_user_auto_roles,
        interaction.user.id,
        interaction.guild.id,
    )

    if not role_ids:
        embed = discord.Embed(
            title="Auto-Roles",
            description="You have no auto-restore roles configured.",
            color=0x0B1E3D,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    role_mentions = []
    for role_id in role_ids:
        role = interaction.guild.get_role(role_id)
        if role:
            role_mentions.append(role.mention)
        else:
            role_mentions.append(f"<@&{role_id}> (deleted)")

    embed = discord.Embed(
        title="Auto-Roles",
        description="Roles that will be automatically restored when you rejoin:\n" + "\n".join(role_mentions),
        color=0x0B1E3D,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------- Startup --------


@bot.event
async def setup_hook() -> None:
    shared_branding.BRANDING_THUMBNAIL_URL = OCI_TICKET_THUMBNAIL_URL
    shared_branding.BRANDING_IMAGE_URL = OCI_TICKET_IMAGE_URL
    shared_branding.BRANDING_FOOTER_ICON_URL = OCI_TICKET_FOOTER_ICON_URL
    shared_branding.BRANDING_FOOTER_TEXT = OCI_TICKET_FOOTER_TEXT
    await asyncio.to_thread(_init_case_db)
    await asyncio.to_thread(_init_auto_role_db)
    await bot.load_extension("cogs.tickets")
    if COMMAND_GUILD is not None:
        bot.tree.copy_global_to(guild=COMMAND_GUILD)
        bot.tree.clear_commands(guild=None)

    port_raw = os.getenv("PORT", "").strip()
    if port_raw.isdigit():
        port = int(port_raw)

        async def status(_: web.Request) -> web.Response:
            return web.json_response(_build_status_payload())

        global _web_runner
        app = web.Application()
        app.router.add_get("/", status)
        app.router.add_get("/healthz", status)
        app.router.add_get("/status", status)
        _web_runner = web.AppRunner(app)
        await _web_runner.setup()
        site = web.TCPSite(_web_runner, host="0.0.0.0", port=port)
        await site.start()


@bot.event
async def on_ready():
    if COMMAND_GUILD is not None:
        synced = await bot.tree.sync(guild=COMMAND_GUILD)
        print(f"Synced {len(synced)} commands to configured guild {COMMAND_GUILD.id}")
        cleared = await bot.tree.sync()
        print(f"Cleared {len(cleared)} global commands")
    elif len(bot.guilds) == 1:
        target_guild = bot.guilds[0]
        _scope_commands_to_guild(target_guild.id)
        synced = await bot.tree.sync(guild=target_guild)
        print(f"Synced {len(synced)} commands to detected guild {target_guild.id}")
    else:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global commands")
    print(f"Logged in as {bot.user}")


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """Restore auto-roles when a member rejoins."""
    try:
        role_ids = await asyncio.to_thread(
            _get_user_auto_roles,
            member.id,
            member.guild.id,
        )

        for role_id in role_ids:
            role = member.guild.get_role(role_id)
            if role and not member.get_role(role_id):
                try:
                    await member.add_roles(role, reason="Auto-role restoration")
                except discord.HTTPException:
                    pass
    except Exception:
        pass


def main() -> None:
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
