from __future__ import annotations

import datetime
import os
import time

import aiohttp
import discord
import psutil
from discord import app_commands
from discord.ext import commands

from bot.branding import (
    BRANDING_NAME,
    BRANDING_FOOTER_ICON_URL,
    BRANDING_FOOTER_TEXT,
    BRANDING_IMAGE_URL,
    BRANDING_THUMBNAIL_URL,
)

HOME_GUILD_ID = int(os.getenv("DEV_GUILD_ID", "0").strip() or 0)
LOCKDOWN_ROLE_ID = 1400844188840497171
OPORATION_BLITZ_ROLE_ID = 1478860250869399733
OPORATION_BLITZ_WEBHOOK_URL = (
    "https://discord.com/api/webhooks/1479825942166769725/"
    "3KgwradbuA5g3s8ApPptFFFjjEvlF_FddA0dMGKAE8QzoByUythdrMdzpcsUQMXplIrW"
)


def _format_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m {secs}s"
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def _home_guild_only() -> callable:
    if HOME_GUILD_ID:
        return app_commands.guilds(HOME_GUILD_ID)
    return lambda obj: obj


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _dashboard_url(self) -> str:
        host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
        port = os.getenv("DASHBOARD_PORT", "8080").strip() or "8080"
        return f"http://{host}:{port}"

    def _has_lockdown_control(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        return bool(interaction.user.get_role(LOCKDOWN_ROLE_ID))

    def _has_oporation_blitz_access(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        return bool(interaction.user.get_role(OPORATION_BLITZ_ROLE_ID))

    @app_commands.command(name="ping", description="Show bot latency, CPU, RAM, and uptime.")
    async def ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        process = psutil.Process()
        memory_info = process.memory_info()
        ram_mb = memory_info.rss / (1024 * 1024)
        cpu_percent = process.cpu_percent(interval=0.2)

        started_at = getattr(self.bot, "started_at_monotonic", time.monotonic())
        uptime_seconds = max(0, int(time.monotonic() - started_at))
        latency_ms = round(self.bot.latency * 1000, 1)

        embed = discord.Embed(
            title="Pong!",
            color=0x0B1E3D,
            timestamp=datetime.datetime.now(datetime.UTC),
        )
        embed.add_field(name="Latency", value=f"`{latency_ms} ms`", inline=True)
        embed.add_field(name="CPU Usage", value=f"`{cpu_percent:.1f}%`", inline=True)
        embed.add_field(name="RAM Usage", value=f"`{ram_mb:.1f} MB`", inline=True)
        embed.add_field(name="Uptime", value=f"`{_format_uptime(uptime_seconds)}`", inline=False)
        embed.set_thumbnail(url=BRANDING_THUMBNAIL_URL)
        embed.set_image(url=BRANDING_IMAGE_URL)
        embed.set_footer(text=BRANDING_FOOTER_TEXT, icon_url=BRANDING_FOOTER_ICON_URL)

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="dashboard",
        description="Show the local Office of Community Investigations - OCI dashboard link.",
    )
    @_home_guild_only()
    async def dashboard(self, interaction: discord.Interaction) -> None:
        dashboard_url = self._dashboard_url()

        embed = discord.Embed(
            title=f"{BRANDING_NAME} Dashboard",
            description=(
                f"Open the local control panel here:\n`{dashboard_url}`\n\n"
                "If it is not already running, start it with `python dashboard.py`."
            ),
            color=0x0B1E3D,
            timestamp=datetime.datetime.now(datetime.UTC),
        )
        embed.set_thumbnail(url=BRANDING_THUMBNAIL_URL)
        embed.set_image(url=BRANDING_IMAGE_URL)
        embed.set_footer(text=BRANDING_FOOTER_TEXT, icon_url=BRANDING_FOOTER_ICON_URL)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Dashboard", url=dashboard_url))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="bot-lockdown",
        description="Lock all bot commands so only the lockdown role can use them.",
    )
    async def lockdown_enable(self, interaction: discord.Interaction) -> None:
        if not self._has_lockdown_control(interaction):
            await interaction.response.send_message(
                f"You need <@&{LOCKDOWN_ROLE_ID}> to use this command.",
                ephemeral=True,
            )
            return

        if getattr(self.bot, "bot_lockdown_enabled", False):
            await interaction.response.send_message("Bot lockdown is already enabled.", ephemeral=True)
            return

        self.bot.bot_lockdown_enabled = True
        await self.bot.db.set_setting("bot_lockdown_enabled", "1")
        await interaction.response.send_message(
            f"Bot lockdown enabled. Only <@&{LOCKDOWN_ROLE_ID}> can use bot commands now.",
            ephemeral=True,
        )

    @app_commands.command(
        name="bot-disable-lockdown",
        description="Disable bot lockdown and restore normal command permissions.",
    )
    async def lockdown_disable(self, interaction: discord.Interaction) -> None:
        if not self._has_lockdown_control(interaction):
            await interaction.response.send_message(
                f"You need <@&{LOCKDOWN_ROLE_ID}> to use this command.",
                ephemeral=True,
            )
            return

        if not getattr(self.bot, "bot_lockdown_enabled", False):
            await interaction.response.send_message("Bot lockdown is already disabled.", ephemeral=True)
            return

        self.bot.bot_lockdown_enabled = False
        await self.bot.db.set_setting("bot_lockdown_enabled", "0")
        await interaction.response.send_message(
            "Bot lockdown disabled. Commands are back to their original permissions.",
            ephemeral=True,
        )

    @app_commands.command(
        name="oporation-blitz",
        description="Delete all bot embed messages and send the clarification webhook.",
    )
    @app_commands.describe(reasson="Clarification reason used in the webhook message.")
    @app_commands.default_permissions(manage_guild=True)
    async def oporation_blitz(self, interaction: discord.Interaction, reasson: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not self._has_oporation_blitz_access(interaction):
            await interaction.response.send_message(
                f"You need <@&{OPORATION_BLITZ_ROLE_ID}> to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        me = interaction.guild.me
        if me is None or self.bot.user is None:
            await interaction.followup.send("Bot member is unavailable.", ephemeral=True)
            return

        deleted_messages = 0
        scanned_channels = 0
        failed_channels = 0

        for channel in interaction.guild.text_channels:
            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.read_message_history and perms.manage_messages):
                continue
            scanned_channels += 1
            try:
                async for message in channel.history(limit=None):
                    if message.author.id == self.bot.user.id and message.embeds:
                        try:
                            await message.delete()
                            deleted_messages += 1
                        except discord.HTTPException:
                            continue
            except discord.HTTPException:
                failed_channels += 1
                continue

        webhook_text = (
            "Hello @here,\n\n"
            "The server was not raided.\n"
            f"Clarification: The reason provided was: {reasson}\n\n"
            f"As stated in the {BRANDING_NAME} bot policy, all bot assets such as embeds and related content "
            f"are property of {BRANDING_NAME} only.\n"
            f"The {BRANDING_NAME} bot assets are only permitted to be used by authorized servers while "
            f"{BRANDING_NAME} consents and is present in the server using them.\n\n"
            f"For more information please contact {BRANDING_NAME} staff."
        )

        webhook_ok = True
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
                webhook = discord.Webhook.from_url(OPORATION_BLITZ_WEBHOOK_URL, session=session)
                await webhook.send(
                    content=webhook_text,
                    username=f"{BRANDING_NAME} Systems",
                    allowed_mentions=discord.AllowedMentions(everyone=True, users=False, roles=False),
                    wait=False,
                )
        except Exception:
            webhook_ok = False

        await interaction.followup.send(
            (
                f"Oporation Blitz finished.\n"
                f"Scanned channels: {scanned_channels}\n"
                f"Deleted bot embed messages: {deleted_messages}\n"
                f"Failed channels: {failed_channels}\n"
                f"Webhook sent: {'Yes' if webhook_ok else 'No'}"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilityCog(bot))
