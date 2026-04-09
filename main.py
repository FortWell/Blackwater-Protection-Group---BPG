from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import ctypes
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web

from bot.audit import AuditLogger, format_interaction_context
from bot.config import BotConfig
from bot.db import Database
from bot.branding import BRANDING_NAME


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("oci-bot")
BOT_LOCKDOWN_ROLE_ID = 1400844188840497171


class BPGCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        checker = getattr(self.client, "is_command_allowed", None)
        if checker is None:
            return True
        return await checker(interaction)


def _acquire_single_instance_lock() -> int | None:
    # Windows-only named mutex to prevent multiple bot instances.
    if os.name != "nt":
        return None
    # Use a config-derived name so separate bot slots can run together.
    instance_name = os.getenv("BOT_INSTANCE_NAME", "").strip()
    if not instance_name:
        db_path = os.getenv("DATABASE_PATH", "").strip() or "data/bot.db"
        port = os.getenv("PORT", "").strip() or "0"
        instance_name = f"{db_path}|{port}"
    digest = hashlib.sha1(instance_name.encode("utf-8")).hexdigest()
    mutex_name = f"Local\\OCI_Office_of_Community_Investigations_{digest}"
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        return None
    err = ctypes.windll.kernel32.GetLastError()
    if err == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(handle)
        return None
    return handle


class BPGBot(commands.Bot):
    def __init__(self, config: BotConfig):
        intents = discord.Intents.default()
        intents.members = config.enable_members_intent
        intents.message_content = config.enable_message_content_intent
        super().__init__(command_prefix="!", intents=intents, tree_cls=BPGCommandTree)
        self.config = config
        self.db = Database(config.database_path)
        self.audit = AuditLogger(config.bot_audit_webhook_url)
        self._web_runner: web.AppRunner | None = None
        self.started_at_monotonic = time.monotonic()
        self.bot_lockdown_enabled = False
        self.bot_lockdown_role_id = BOT_LOCKDOWN_ROLE_ID
        self.home_guild_id = config.dev_guild_id

    async def setup_hook(self) -> None:
        await self.db.init()
        self.bot_lockdown_enabled = (
            await self.db.get_setting("bot_lockdown_enabled", "0")
        ) == "1"
        await self.audit.start()
        await self._start_health_server_if_needed()
        for extension in (
            "cogs.embeds",
            "cogs.tickets",
            "cogs.applications",
            "cogs.global_bans",
            "cogs.staff",
            "cogs.utility",
        ):
            await self.load_extension(extension)
            log.info("Loaded extension: %s", extension)

        self._scope_commands_to_home_guild()

        async def safe_sync_global() -> None:
            try:
                synced_global = await asyncio.wait_for(self.tree.sync(), timeout=20)
                log.info("Synced %s global commands", len(synced_global))
            except asyncio.TimeoutError:
                log.warning("Global command sync timed out after 20s; continuing startup.")
            except Exception:
                log.exception("Global command sync failed; continuing startup.")

        if self.home_guild_id:
            guild_obj = discord.Object(id=self.home_guild_id)
            await safe_sync_global()
            synced_ok = False
            for attempt in range(1, 4):
                try:
                    synced = await asyncio.wait_for(self.tree.sync(guild=guild_obj), timeout=30)
                    log.info(
                        "Synced %s commands to home guild %s (attempt %s)",
                        len(synced),
                        self.home_guild_id,
                        attempt,
                    )
                    synced_ok = True
                    break
                except asyncio.TimeoutError:
                    log.warning("Home guild sync timed out on attempt %s.", attempt)
                except Exception:
                    log.exception("Home guild sync failed on attempt %s.", attempt)
            if not synced_ok:
                log.warning("Home guild sync failed after 3 attempts; continuing startup.")
            for guild in self.guilds:
                if guild.id == self.home_guild_id:
                    continue
                try:
                    synced = await asyncio.wait_for(self.tree.sync(guild=guild), timeout=20)
                    log.info("Synced %s commands to guild %s (%s)", len(synced), guild.name, guild.id)
                except asyncio.TimeoutError:
                    log.warning("Guild sync timed out for %s (%s); continuing.", guild.name, guild.id)
                except Exception:
                    log.exception("Guild sync failed for %s (%s); continuing.", guild.name, guild.id)
        else:
            await safe_sync_global()
            for guild in self.guilds:
                try:
                    synced = await asyncio.wait_for(self.tree.sync(guild=guild), timeout=20)
                    log.info("Synced %s commands to guild %s (%s)", len(synced), guild.name, guild.id)
                except asyncio.TimeoutError:
                    log.warning("Guild sync timed out for %s (%s); continuing.", guild.name, guild.id)
                except Exception:
                    log.exception("Guild sync failed for %s (%s); continuing.", guild.name, guild.id)

    def _scope_commands_to_home_guild(self) -> None:
        if not self.home_guild_id:
            return
        for command in self.tree.walk_commands():
            if command.name == "ping":
                command.guild_ids = None
                continue
            command.guild_ids = [self.home_guild_id]

    async def is_command_allowed(self, interaction: discord.Interaction) -> bool:
        command_name = interaction.command.qualified_name if interaction.command else ""
        if command_name == "ping":
            return True

        if self.home_guild_id and interaction.guild_id not in (None, self.home_guild_id):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"This bot only accepts commands in the {BRANDING_NAME} server. "
                        "Use `/ping` in other servers.",
                        ephemeral=True,
                    )
            except Exception:
                pass
            return False

        if interaction.guild_id is not None and isinstance(interaction.user, discord.Member):
            if await self.is_user_globally_banned(interaction.user.id):
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "You are globally banned from using this bot.",
                            ephemeral=True,
                        )
                except Exception:
                    pass
                return False

        if not self.bot_lockdown_enabled:
            return True
        if not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.get_role(self.bot_lockdown_role_id):
            return True
        return False

    async def is_user_globally_banned(self, user_id: int) -> bool:
        value = await self.db.fetch_value(
            "SELECT 1 FROM global_bans WHERE user_id = ? AND is_active = 1 LIMIT 1",
            (user_id,),
        )
        return value is not None

    async def set_lockdown_enabled(self, enabled: bool) -> None:
        self.bot_lockdown_enabled = enabled
        await self.db.set_setting("bot_lockdown_enabled", "1" if enabled else "0")

    def build_status_payload(self, *, global_ban_count: int | None = None) -> dict[str, Any]:
        user = self.user
        home_guild = self.get_guild(self.home_guild_id) if self.home_guild_id else None
        latency_ms = round(self.latency * 1000, 2) if self.latency is not None else None
        uptime_seconds = int(time.monotonic() - self.started_at_monotonic)
        return {
            "ok": True,
            "service": "bpg-bot",
            "ready": self.is_ready(),
            "logged_in": user is not None,
            "pid": os.getpid(),
            "user": {
                "id": user.id,
                "tag": str(user),
            }
            if user is not None
            else None,
            "guild_count": len(self.guilds),
            "latency_ms": latency_ms,
            "uptime_seconds": uptime_seconds,
            "home_guild_id": self.home_guild_id,
            "home_guild_name": home_guild.name if home_guild is not None else None,
            "lockdown_supported": True,
            "lockdown_enabled": self.bot_lockdown_enabled,
            "global_ban_count": global_ban_count,
        }

    async def on_ready(self) -> None:
        if self.user is None:
            return
        log.info("Bot online as %s (%s)", self.user, self.user.id)
        await self.audit.send(
            "Bot Online",
            f"Connected as {self.user} ({self.user.id})",
            color=0x1F8B4C,
        )

    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command[Any, Any, Any] | app_commands.ContextMenu,
    ) -> None:
        now = int(time.time())
        fields = format_interaction_context(interaction)
        fields.append(("Command", f"/{command.qualified_name}"))
        fields.append(("Executed At", f"<t:{now}:F> (<t:{now}:R>)"))
        await self.audit.send(
            "Command Executed",
            "A slash command completed successfully.",
            color=0x0B1E3D,
            fields=fields,
        )

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        cmd_name = interaction.command.qualified_name if interaction.command else "unknown"
        now = int(time.time())
        fields = format_interaction_context(interaction)
        fields.append(("Command", f"/{cmd_name}"))
        fields.append(("Executed At", f"<t:{now}:F> (<t:{now}:R>)"))
        fields.append(("Error", str(error)))
        await self.audit.send(
            "Command Error",
            "A slash command raised an error.",
            color=0xB32020,
            fields=fields,
        )
        # Always send a user-visible error so interactions do not appear as "did not respond".
        msg = "Command failed. Please try again."
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                return
            if self.bot_lockdown_enabled:
                msg = f"Bot is in lockdown. Only <@&{self.bot_lockdown_role_id}> can use bot commands."
            else:
                msg = str(error) if str(error) else "You do not have permission to use this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = "This command is on cooldown. Try again shortly."
        elif isinstance(error, app_commands.CommandInvokeError):
            msg = f"Command failed: {error.original}"

        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            pass

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type not in (discord.InteractionType.component, discord.InteractionType.modal_submit):
            return
        data = interaction.data or {}
        custom_id = str(data.get("custom_id", "unknown"))
        fields = format_interaction_context(interaction)
        fields.append(("Custom ID", custom_id))
        await self.audit.send(
            "Interaction Used",
            "A button/modal interaction was used.",
            color=0x2B2D31,
            fields=fields,
        )

    async def close(self) -> None:
        if self._web_runner is not None:
            await self._web_runner.cleanup()
            self._web_runner = None
        await self.audit.close()
        await self.db.close()
        await super().close()

    async def _start_health_server_if_needed(self) -> None:
        port_raw = os.getenv("PORT", "").strip()
        if not port_raw.isdigit():
            return
        port = int(port_raw)

        async def status(_: web.Request) -> web.Response:
            global_ban_count_raw = await self.db.fetch_value(
                "SELECT COUNT(*) FROM global_bans WHERE is_active = 1",
            )
            global_ban_count = int(global_ban_count_raw) if global_ban_count_raw is not None else 0
            return web.json_response(self.build_status_payload(global_ban_count=global_ban_count))

        async def control_lockdown(request: web.Request) -> web.Response:
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

            await self.set_lockdown_enabled(enabled)
            return web.json_response(
                {
                    "ok": True,
                    "message": "Lockdown updated.",
                    "lockdown_enabled": self.bot_lockdown_enabled,
                }
            )

        app = web.Application()
        app.router.add_get("/", status)
        app.router.add_get("/healthz", status)
        app.router.add_get("/status", status)
        app.router.add_post("/control/lockdown", control_lockdown)

        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, host="0.0.0.0", port=port)
        await site.start()
        log.info("Health server listening on 0.0.0.0:%s", port)


async def main() -> None:
    lock_handle = _acquire_single_instance_lock()
    if os.name == "nt" and lock_handle is None:
        log.error("Another bot instance is already running. Exiting this instance.")
        return

    config = BotConfig.from_env()
    token = config.token
    if not token:
        raise RuntimeError("Missing DISCORD_TOKEN in environment.")

    bot = BPGBot(config)
    async with bot:
        try:
            await bot.start(token)
        finally:
            if lock_handle is not None:
                ctypes.windll.kernel32.CloseHandle(lock_handle)


if __name__ == "__main__":
    asyncio.run(main())
