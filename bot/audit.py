from __future__ import annotations

import time
import logging
from typing import Any

import aiohttp
import discord

log = logging.getLogger("bpg-bot.audit")


class AuditLogger:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url.strip()
        self._session: aiohttp.ClientSession | None = None
        self._webhook: discord.Webhook | None = None

    async def start(self) -> None:
        if not self.webhook_url:
            return
        if self._session is None:
            self._session = aiohttp.ClientSession()
        if self._webhook is None:
            self._webhook = discord.Webhook.from_url(self.webhook_url, session=self._session)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
            self._webhook = None

    async def send(self, title: str, description: str, color: int = 0x2B2D31, fields: list[tuple[str, str]] | None = None) -> None:
        if not self.webhook_url:
            return
        await self.start()
        if self._webhook is None:
            return
        unix = int(time.time())
        ts_line = f"Timestamp: <t:{unix}:F> (<t:{unix}:R>)"
        final_desc = f"{description}\n\n{ts_line}" if description else ts_line
        embed = discord.Embed(title=title[:256], description=final_desc[:4000], color=color)
        if fields:
            for k, v in fields[:20]:
                embed.add_field(name=k[:256], value=(v or ".")[:1024], inline=False)
        try:
            await self._webhook.send(embed=embed, username="Blackwater Protection Group Audit", wait=False)
        except Exception:
            log.exception("Failed to send audit webhook message.")


def format_interaction_context(interaction: discord.Interaction) -> list[tuple[str, str]]:
    guild = interaction.guild
    channel = interaction.channel
    user = interaction.user
    return [
        ("User", f"{user} ({user.id})" if user else "Unknown"),
        ("Guild", f"{guild.name} ({guild.id})" if guild else "DM"),
        ("Channel", f"{getattr(channel, 'id', 'N/A')}"),
    ]
