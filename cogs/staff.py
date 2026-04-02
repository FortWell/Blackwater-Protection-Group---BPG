from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


PUNISHMENTS = [
    ("Termination", "TERMINATION"),
    ("Warning 1", "WARNING_1"),
    ("Warning 2", "WARNING_2"),
    ("Warning 3", "WARNING_3"),
    ("Strike 1", "STRIKE_1"),
    ("Strike 2", "STRIKE_2"),
    ("Strike 3", "STRIKE_3"),
    ("Suspension", "SUSPENSION"),
    ("Notice", "NOTICE"),
]

PROMOTION_BANNER_URL = (
    "https://cdn.discordapp.com/attachments/1417875005387309137/"
    "1469298336095010931/banner_federal_1.png"
)
PROMOTION_THUMBNAIL_URL = (
    "https://cdn.discordapp.com/attachments/1417875005387309137/"
    "1475920409709772951/Logo.png"
)
CENTRAL_REQUIRED_ROLE_ID = 1477331892448526456
CENTRAL_TARGET_CHANNEL_ID = 1477320169914372168


class StaffCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _can_manage(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        allowed_ids = set(self.bot.config.staff_management_role_ids)
        if self.bot.config.staff_management_role_id:
            allowed_ids.add(self.bot.config.staff_management_role_id)
        if not allowed_ids:
            return False
        member_role_ids = {role.id for role in interaction.user.roles}
        return bool(member_role_ids.intersection(allowed_ids))

    async def _resolve_member(
        self,
        guild: discord.Guild,
        user: discord.Member | discord.User,
    ) -> discord.Member | None:
        if isinstance(user, discord.Member):
            return user
        member = guild.get_member(user.id)
        if member:
            return member
        try:
            member = await guild.fetch_member(user.id)
        except discord.HTTPException:
            return None
        return member

    async def _publish_panel(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        ping_user: discord.Member | None = None,
        preferred_channel_id: int = 0,
    ) -> None:
        content = ping_user.mention if ping_user is not None else None
        mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)

        if interaction.guild is None:
            await interaction.followup.send(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        channel_id = preferred_channel_id or self.bot.config.bot_log_channel_id
        channel: discord.TextChannel | None = None
        if channel_id:
            candidate = interaction.guild.get_channel(channel_id)
            if isinstance(candidate, discord.TextChannel):
                channel = candidate

        if channel is None and isinstance(interaction.channel, discord.TextChannel):
            channel = interaction.channel

        if channel is None:
            await interaction.followup.send(
                "Could not find a valid channel to post the panel.",
                ephemeral=True,
            )
            return

        try:
            await channel.send(
                embed=embed,
                content=content,
                allowed_mentions=mentions,
            )
        except discord.HTTPException:
            await interaction.followup.send(
                "Failed to send panel message.",
                ephemeral=True,
            )

    async def _safe_defer(self, interaction: discord.Interaction) -> bool:
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            return True
        except discord.NotFound:
            # Interaction token expired/invalid (often caused by duplicate bot instances).
            return False
        except discord.HTTPException:
            return False

    async def _run_promotion(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        new_rank: discord.Role,
        reason: str,
        *,
        panel_title: str,
        panel_desc: str,
        channel_id: int,
        require_manage_permission: bool = True,
    ) -> None:
        if not await self._safe_defer(interaction):
            return
        if interaction.guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        if require_manage_permission and not self._can_manage(interaction):
            await interaction.followup.send("You do not have permission.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("Permission denied.", ephemeral=True)
            return

        if new_rank.is_default():
            await interaction.followup.send("You cannot promote someone to @everyone.", ephemeral=True)
            return
        if new_rank.managed:
            await interaction.followup.send(
                "You cannot assign a managed/integration role.",
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        if me is None:
            await interaction.followup.send("Bot member not found in this server.", ephemeral=True)
            return
        if new_rank >= me.top_role:
            await interaction.followup.send(
                "I cannot assign that role because it is higher than or equal to my top role.",
                ephemeral=True,
            )
            return

        try:
            await user.add_roles(new_rank, reason=f"Promotion by {interaction.user} | {reason}")
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to manage that user's roles (check role hierarchy and bot permissions).",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send("Failed to update roles due to a Discord API error.", ephemeral=True)
            return

        embed = discord.Embed(
            title=panel_title,
            description=panel_desc,
            color=0x1F8B4C,
        )
        embed.add_field(name="User:", value=user.mention, inline=True)
        embed.add_field(name="New Rank:", value=new_rank.mention, inline=True)
        embed.add_field(name="Reason:", value=reason or ".", inline=True)
        embed.set_thumbnail(url=PROMOTION_THUMBNAIL_URL)
        embed.set_image(url=PROMOTION_BANNER_URL)
        embed.set_footer(text="Federal Reserve Management", icon_url=PROMOTION_THUMBNAIL_URL)

        await self._publish_panel(
            interaction,
            embed,
            ping_user=user,
            preferred_channel_id=channel_id,
        )
        await interaction.followup.send(
            f"Promoted {user.mention} to **{new_rank.name}**.",
            ephemeral=True,
        )

    async def _run_infraction(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        punishment: app_commands.Choice[str],
        reason: str,
        *,
        panel_title: str,
        panel_desc: str,
        channel_id: int,
        require_manage_permission: bool = True,
    ) -> None:
        if not await self._safe_defer(interaction):
            return
        if interaction.guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        if require_manage_permission and not self._can_manage(interaction):
            await interaction.followup.send("You do not have permission.", ephemeral=True)
            return

        await self.bot.db.execute(
            """
            INSERT INTO infractions (guild_id, user_id, staff_id, punishment, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                interaction.guild.id,
                user.id,
                interaction.user.id,
                punishment.value,
                reason,
            ),
        )

        embed = discord.Embed(
            title=panel_title,
            description=panel_desc,
            color=0xB32020,
        )
        embed.add_field(name="User:", value=user.mention, inline=True)
        embed.add_field(name="Punishment", value=punishment.name, inline=True)
        embed.add_field(name="Reason:", value=reason or ".", inline=True)
        embed.set_thumbnail(url=PROMOTION_THUMBNAIL_URL)
        embed.set_image(url=PROMOTION_BANNER_URL)
        embed.set_footer(text="Federal Reserve Management", icon_url=PROMOTION_THUMBNAIL_URL)
        await self._publish_panel(
            interaction,
            embed,
            ping_user=user,
            preferred_channel_id=channel_id,
        )
        await interaction.followup.send(
            f"Infraction recorded for {user.mention}: **{punishment.name}**.",
            ephemeral=True,
        )

    @app_commands.command(name="promote", description="Central promotion command.")
    @app_commands.describe(
        user="User to promote",
        new_rank="New rank role to assign",
        reason="Reason for promotion",
    )
    async def promote(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        new_rank: discord.Role,
        reason: str = ".",
    ) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Permission denied.", ephemeral=True)
            return
        if not interaction.user.get_role(CENTRAL_REQUIRED_ROLE_ID):
            await interaction.response.send_message("You do not have permission for this command.", ephemeral=True)
            return
        await self._run_promotion(
            interaction,
            user,
            new_rank,
            reason,
            panel_title="Central Promotion",
            panel_desc=(
                "Central leadership has reviewed your contributions.\n"
                "You have been issued a promotion. Congratulations!"
            ),
            channel_id=CENTRAL_TARGET_CHANNEL_ID,
            require_manage_permission=False,
        )

    @app_commands.command(name="infract", description="Central infraction command.")
    @app_commands.describe(
        user="User receiving the infraction",
        punishment="Punishment type",
        reason="Reason/details",
    )
    @app_commands.choices(
        punishment=[app_commands.Choice(name=label, value=value) for (label, value) in PUNISHMENTS]
    )
    async def infract(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        punishment: app_commands.Choice[str],
        reason: str = "No reason provided.",
    ) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Permission denied.", ephemeral=True)
            return
        if not interaction.user.get_role(CENTRAL_REQUIRED_ROLE_ID):
            await interaction.response.send_message("You do not have permission for this command.", ephemeral=True)
            return
        await self._run_infraction(
            interaction,
            user,
            punishment,
            reason,
            panel_title="Central Infraction",
            panel_desc=(
                "Central leadership has identified a policy violation.\n"
                "Appropriate actions are being applied to your account."
            ),
            channel_id=CENTRAL_TARGET_CHANNEL_ID,
            require_manage_permission=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffCog(bot))
