from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from bot.db import Database


class AutoRoleCog(commands.Cog):
    """Manage automatic role restoration when users rejoin."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db

    async def _init_auto_role_table(self) -> None:
        """Initialize the auto_role_associations table."""
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_role_associations (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, role_id)
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_role_config (
                guild_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 1
            )
            """
        )

    async def cog_load(self) -> None:
        """Initialize tables on cog load."""
        await self._init_auto_role_table()

    @app_commands.command(name="auto-role-add", description="Add a role to auto-restore for users")
    @app_commands.describe(role="The role to automatically restore when users rejoin")
    async def auto_role_add(self, interaction: discord.Interaction, role: discord.Role) -> None:
        """Add a role to the auto-role system."""
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Permission denied.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You need manage roles permission.", ephemeral=True)
            return

        try:
            await self.db.execute(
                """
                INSERT OR IGNORE INTO auto_role_config (guild_id, enabled)
                VALUES (?, 1)
                """,
                (interaction.guild.id,),
            )

            await self.db.execute(
                """
                INSERT OR IGNORE INTO auto_role_associations (guild_id, user_id, role_id)
                VALUES (?, ?, ?)
                """,
                (interaction.guild.id, interaction.user.id, role.id),
            )

            embed = discord.Embed(
                title="Auto-Role Added",
                description=f"Role {role.mention} will be automatically restored for you when you rejoin.",
                color=0x0B1E3D,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to add role: {str(e)}", ephemeral=True)

    @app_commands.command(name="remove-auto-role", description="Remove a role from auto-restore")
    @app_commands.describe(role="The role to remove from auto-restore")
    async def remove_auto_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        """Remove a role from the auto-role system."""
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Permission denied.", ephemeral=True)
            return

        try:
            await self.db.execute(
                """
                DELETE FROM auto_role_associations
                WHERE guild_id = ? AND user_id = ? AND role_id = ?
                """,
                (interaction.guild.id, interaction.user.id, role.id),
            )

            embed = discord.Embed(
                title="Auto-Role Removed",
                description=f"Role {role.mention} will no longer be automatically restored.",
                color=0x0B1E3D,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to remove role: {str(e)}", ephemeral=True)

    @app_commands.command(name="auto-role-list", description="List all auto-restore roles for your profile")
    async def auto_role_list(self, interaction: discord.Interaction) -> None:
        """List all auto-role associations for the user."""
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        try:
            rows = await self.db.fetchall(
                """
                SELECT role_id FROM auto_role_associations
                WHERE guild_id = ? AND user_id = ?
                """,
                (interaction.guild.id, interaction.user.id),
            )

            if not rows:
                embed = discord.Embed(
                    title="Auto-Roles",
                    description="You have no auto-restore roles configured.",
                    color=0x0B1E3D,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            role_mentions = []
            for (role_id,) in rows:
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
        except Exception as e:
            await interaction.response.send_message(f"Failed to retrieve roles: {str(e)}", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Restore auto-roles when a member rejoins."""
        try:
            rows = await self.db.fetchall(
                """
                SELECT role_id FROM auto_role_associations
                WHERE guild_id = ? AND user_id = ?
                """,
                (member.guild.id, member.id),
            )

            for (role_id,) in rows:
                role = member.guild.get_role(role_id)
                if role and not member.get_role(role_id):
                    try:
                        await member.add_roles(role, reason="Auto-role restoration")
                    except discord.HTTPException:
                        pass
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    cog = AutoRoleCog(bot)
    await cog.cog_load()
    await bot.add_cog(cog)
