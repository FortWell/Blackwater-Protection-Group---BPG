from __future__ import annotations

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from bot.branding import (
    BRANDING_FOOTER_ICON_URL,
    BRANDING_FOOTER_TEXT,
    BRANDING_IMAGE_URL,
    BRANDING_THUMBNAIL_URL,
)


def _trim(value: str, limit: int) -> str:
    return value[:limit]


async def _format_servers(guilds: list[discord.Guild], user_id: int) -> str:
    names: list[str] = []
    for guild in guilds:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                member = None
        if member is not None:
            names.append(f"{guild.name} (`{guild.id}`)")
    if not names:
        return "None detected"
    return ", ".join(names)


def _chunk_blocks(blocks: list[str], limit: int = 3800) -> list[str]:
    pages: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else f"{current}\n\n{block}"
        if current and len(candidate) > limit:
            pages.append(current)
            current = block
            continue
        current = candidate
    if current:
        pages.append(current)
    return pages


class GlobalBanCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _has_access(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        role_id = self.bot.config.global_ban_role_id
        return bool(role_id and interaction.user.get_role(role_id))

    def _home_guild_id(self) -> int:
        return int(self.bot.config.dev_guild_id or 0)

    def _home_guild_name(self) -> str:
        guild = self.bot.get_guild(self._home_guild_id())
        return guild.name if guild is not None else "This server"

    def _build_embed(self, title: str, description: str, *, color: int = 0x0B1E3D) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_thumbnail(url=BRANDING_THUMBNAIL_URL)
        embed.set_image(url=BRANDING_IMAGE_URL)
        embed.set_footer(text=BRANDING_FOOTER_TEXT, icon_url=BRANDING_FOOTER_ICON_URL)
        return embed

    async def _send_audit(
        self,
        title: str,
        description: str,
        *,
        color: int,
        fields: list[tuple[str, str]] | None = None,
    ) -> None:
        audit = getattr(self.bot, "audit", None)
        if audit is None:
            return
        await audit.send(title, description, color=color, fields=fields or [])

    async def _ensure_home_guild(self, interaction: discord.Interaction) -> bool:
        home_guild_id = self._home_guild_id()
        if home_guild_id and interaction.guild_id != home_guild_id:
            await interaction.response.send_message(
                f"This command is only available in {self._home_guild_name()}.",
                ephemeral=True,
            )
            return False
        return True

    @app_commands.command(name="global-ban", description="Globally ban a user from bot commands.")
    @app_commands.describe(user="User to globally ban", reason="Ban reason", notes="Staff notes")
    async def global_ban(self, interaction: discord.Interaction, user: discord.User, reason: str, notes: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not self._has_access(interaction):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        if not await self._ensure_home_guild(interaction):
            return

        existing = await self.bot.db.fetch_global_ban(user.id)
        await self.bot.db.upsert_global_ban(
            user_id=user.id,
            user_tag=str(user),
            reason=reason,
            notes=notes,
            banned_by_id=interaction.user.id,
            banned_by_tag=str(interaction.user),
            banned_guild_id=interaction.guild.id,
            banned_guild_name=interaction.guild.name,
        )

        current_servers = await _format_servers(self.bot.guilds, user.id)
        audit_fields = [
            ("Target", f"{user} (`{user.id}`)"),
            ("Reason", _trim(reason, 900)),
            ("Notes", _trim(notes, 900)),
            ("Recorded In", f"{interaction.guild.name} (`{interaction.guild.id}`)"),
            ("Current Servers", _trim(current_servers, 900)),
        ]
        await self._send_audit(
            "Global Ban Updated" if existing and int(existing["is_active"]) == 1 else "Global Ban Added",
            f"Global ban stored for {user}.",
            color=0xD63324,
            fields=audit_fields,
        )

        embed = self._build_embed(
            "Global Ban Added" if not (existing and int(existing["is_active"]) == 1) else "Global Ban Updated",
            (
                f"Target: {user.mention} (`{user.id}`)\n"
                f"Reason: {_trim(reason, 900)}\n"
                f"Notes: {_trim(notes, 900)}\n"
                f"Recorded in: {interaction.guild.name}\n"
                f"Current servers: {_trim(current_servers, 900)}"
            ),
            color=0xD63324,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="global-unban", description="Remove a user from the global ban list.")
    @app_commands.describe(user="User to globally unban", reason="Unban reason", notes="Staff notes")
    async def global_unban(self, interaction: discord.Interaction, user: discord.User, reason: str, notes: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not self._has_access(interaction):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        if not await self._ensure_home_guild(interaction):
            return

        existing = await self.bot.db.fetch_global_ban(user.id)
        if existing is None or int(existing["is_active"]) != 1:
            await interaction.response.send_message("That user is not globally banned.", ephemeral=True)
            return

        await self.bot.db.set_global_unban(
            user_id=user.id,
            unbanned_by_id=interaction.user.id,
            unbanned_by_tag=str(interaction.user),
            reason=reason,
            notes=notes,
        )

        await self._send_audit(
            "Global Ban Removed",
            f"Global ban removed for {user}.",
            color=0x0B3D0B,
            fields=[
                ("Target", f"{user} (`{user.id}`)"),
                ("Reason", _trim(reason, 900)),
                ("Notes", _trim(notes, 900)),
                ("Removed By", f"{interaction.user} (`{interaction.user.id}`)"),
            ],
        )

        embed = self._build_embed(
            "Global Ban Removed",
            (
                f"Target: {user.mention} (`{user.id}`)\n"
                f"Reason: {_trim(reason, 900)}\n"
                f"Notes: {_trim(notes, 900)}\n"
                f"Removed by: {interaction.user.mention}"
            ),
            color=0x0B3D0B,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="global-ban-list", description="Show all active global bans.")
    async def global_ban_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not self._has_access(interaction):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        if not await self._ensure_home_guild(interaction):
            return

        rows = await self.bot.db.fetch_active_global_bans()
        if not rows:
            embed = self._build_embed(
                "Global Ban List",
                "There are no active global bans right now.",
                color=0x0B3D0B,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        blocks: list[str] = []
        for row in rows:
            user_id = int(row["user_id"])
            current_servers = await _format_servers(self.bot.guilds, user_id)
            block = (
                f"**{row['user_tag']}** (`{user_id}`)\n"
                f"Reason: {_trim(str(row['reason']), 700)}\n"
                f"Notes: {_trim(str(row['notes']), 700)}\n"
                f"Recorded in: {row['banned_guild_name']} (`{row['banned_guild_id']}`)\n"
                f"Current servers: {_trim(current_servers, 700)}\n"
                f"Banned at: {row['banned_at']}"
            )
            blocks.append(block)

        pages = _chunk_blocks(blocks)
        embeds = []
        total_pages = len(pages)
        for index, page in enumerate(pages, start=1):
            title = "Global Ban List"
            if total_pages > 1:
                title = f"Global Ban List ({index}/{total_pages})"
            embed = self._build_embed(title, page, color=0xD63324 if rows else 0x0B3D0B)
            embeds.append(embed)

        await interaction.response.send_message(embed=embeds[0], ephemeral=True)
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GlobalBanCog(bot))
