from __future__ import annotations

import datetime
import time

import discord
import psutil
from discord import app_commands
from discord.ext import commands

LOCKDOWN_ROLE_ID = 1471151522657079306


def _format_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m {secs}s"
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _has_lockdown_control(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        return bool(interaction.user.get_role(LOCKDOWN_ROLE_ID))

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

        await interaction.followup.send(embed=embed)

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilityCog(bot))
