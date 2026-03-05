from __future__ import annotations

import asyncio
import json

import discord
from discord import app_commands
from discord.ext import commands

APPLICATION_REVIEW_ROLE_ID = 1478727168887623791
APPLICATION_RESULTS_CHANNEL_ID = 1478729054092918784
ACCEPT_BANNER_URL = (
    "https://cdn.discordapp.com/attachments/1417875005387309137/"
    "1469109135932133419/New_York_City_Roleplay_20250930_123917_0000.png"
    "?ex=69a965c0&is=69a81440&hm=cc059403c9fadbfef382c7e294c040d3586680090260fd7d3f6e12e21f24e21d&"
)
APPLICATION_LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1417875005387309137/"
    "1475920409709772951/Logo.png"
    "?ex=69a920be&is=69a7cf3e&hm=469a475936121c8397acdd13b932e90ef83bea8f7f47feeea704340c2f2ad82f&"
)

ACCEPT_STATUS_CHOICES = [
    app_commands.Choice(name="Accepted", value="Accepted"),
]

ACCEPT_REASON_CHOICES = [
    app_commands.Choice(
        name="Accepted. Application meets all requirements and shows strong understanding and effort.",
        value="Accepted. Application meets all requirements and shows strong understanding and effort.",
    ),
    app_commands.Choice(
        name="Accepted. Clear, professional, and well-written responses. All requirements met.",
        value="Accepted. Clear, professional, and well-written responses. All requirements met.",
    ),
    app_commands.Choice(
        name="Accepted. Good reasoning, maturity, and effort shown. Approved.",
        value="Accepted. Good reasoning, maturity, and effort shown. Approved.",
    ),
]

DENY_STATUS_CHOICES = [
    app_commands.Choice(name="Denied", value="Denied"),
]

DENY_REASON_CHOICES = [
    app_commands.Choice(
        name="Denied. One or more responses were flagged as AI-generated. Zero-tolerance policy applies.",
        value="Denied. One or more responses were flagged as AI-generated. Zero-tolerance policy applies.",
    ),
    app_commands.Choice(
        name="Denied. Answers lacked detail or effort and do not meet Red Rock standards.",
        value="Denied. Answers lacked detail or effort and do not meet Red Rock standards.",
    ),
    app_commands.Choice(
        name="Denied. Application contained unprofessional or inappropriate content.",
        value="Denied. Application contained unprofessional or inappropriate content.",
    ),
]


def estimate_ai_likelihood(text: str) -> float:
    """
    Placeholder detector for MVP:
    - Repeated template-heavy phrasing and very long sentences push score up.
    Replace with an external detector provider in production.
    """
    lowered = text.lower()
    score = 0.0
    if len(text) > 700:
        score += 0.20
    if lowered.count("furthermore") or lowered.count("in conclusion"):
        score += 0.15
    if lowered.count("however") >= 2:
        score += 0.10
    if text.count(",") > 10:
        score += 0.10
    unique_words = len(set(lowered.split()))
    total_words = max(len(lowered.split()), 1)
    if unique_words / total_words < 0.45:
        score += 0.20
    return min(score, 0.99)


class ApplicationsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _can_manage_applications(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        return bool(interaction.user.get_role(APPLICATION_REVIEW_ROLE_ID))

    async def _send_application_decision_embed(
        self,
        interaction: discord.Interaction,
        *,
        user: discord.Member,
        status: str,
        reason: str,
        notes: str,
        color: int,
        title: str,
    ) -> bool:
        if interaction.guild is None:
            return False

        target_channel = interaction.guild.get_channel(APPLICATION_RESULTS_CHANNEL_ID)
        if not isinstance(target_channel, discord.TextChannel):
            try:
                fetched = await interaction.guild.fetch_channel(APPLICATION_RESULTS_CHANNEL_ID)
            except discord.HTTPException:
                return False
            if not isinstance(fetched, discord.TextChannel):
                return False
            target_channel = fetched

        moderator = interaction.user
        moderator_text = f"{moderator} ({moderator.id})" if moderator else "Unknown"
        is_denied = status == "Denied"
        if is_denied:
            verdict_line = (
                f"Unfortunately, your application has been {status}\n"
                "You may be able to reapply in the future depending on department policy."
            )
        else:
            verdict_line = (
                f"Congratulations, your application has been {status}\n"
                "Please make sure to review any onboarding information channels."
            )
        body = (
            f"Your application {user.mention}\n"
            f"Because of > {reason}\n"
            f"Notes > {notes[:900]}\n\n"
            f"{verdict_line}"
        )
        embed = discord.Embed(title="Application Results", description=body, color=color)
        embed.set_image(url=ACCEPT_BANNER_URL)
        embed.set_thumbnail(url=APPLICATION_LOGO_URL)
        embed.set_footer(text="Federal Reserve Management", icon_url=APPLICATION_LOGO_URL)
        await target_channel.send(content=user.mention, embed=embed)
        return True

    async def _send_review_embed(
        self,
        guild: discord.Guild,
        applicant: discord.Member,
        answers: list[dict[str, str | float]],
        status: str,
        max_score: float,
    ) -> None:
        review_channel_id = self.bot.config.application_review_channel_id
        if not review_channel_id:
            return
        channel = guild.get_channel(review_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=f"Application: {status}",
            description=f"Applicant: {applicant.mention} (`{applicant.id}`)",
            color=0xD63324 if status == "DENIED_AI_FLAG" else 0x0B3D0B,
        )
        embed.add_field(name="Max AI score", value=f"{max_score:.0%}", inline=False)
        for idx, item in enumerate(answers, start=1):
            q = item["question"]
            a = item["answer"]
            s = item["ai_score"]
            value = f"AI score: {s:.0%}\n{str(a)[:900]}"
            embed.add_field(name=f"Q{idx}: {q}", value=value, inline=False)
        await channel.send(embed=embed)

    @app_commands.command(name="apply", description="Start the staff application in DMs.")
    async def apply(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        user = interaction.user
        questions = self.bot.config.application_questions
        answers: list[dict[str, str | float]] = []
        max_ai_score = 0.0

        try:
            dm = await user.create_dm()
            await dm.send(
                "Application started. Reply to each question in this DM.\n"
                "If an answer appears AI-generated at or above 50%, the application is auto-denied."
            )
        except discord.HTTPException:
            await interaction.followup.send("I could not DM you. Please enable DMs and try again.", ephemeral=True)
            return

        def check(msg: discord.Message) -> bool:
            return msg.author.id == user.id and msg.channel.id == dm.id

        for idx, question in enumerate(questions, start=1):
            await dm.send(f"Question {idx}/{len(questions)}: {question}")
            try:
                reply = await self.bot.wait_for("message", check=check, timeout=600)
            except asyncio.TimeoutError:
                await dm.send("Application timed out after 10 minutes without response.")
                await interaction.followup.send("Application timed out in DMs.", ephemeral=True)
                return

            score = estimate_ai_likelihood(reply.content)
            max_ai_score = max(max_ai_score, score)
            answers.append(
                {
                    "question": question,
                    "answer": reply.content,
                    "ai_score": score,
                }
            )

            if score >= self.bot.config.apply_min_ai_score:
                await dm.send(
                    f"Your application was denied automatically. AI-likelihood score was {score:.0%} "
                    f"(threshold: {self.bot.config.apply_min_ai_score:.0%})."
                )
                await self.bot.db.execute(
                    """
                    INSERT INTO applications (guild_id, user_id, status, ai_flagged, max_ai_score, answers_json)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (
                        interaction.guild.id,
                        user.id,
                        "DENIED_AI_FLAG",
                        score,
                        json.dumps(answers),
                    ),
                )
                await self._send_review_embed(
                    interaction.guild, user, answers, "DENIED_AI_FLAG", max_score=score
                )
                await interaction.followup.send(
                    "Application started and auto-denied due to AI score threshold. Check DMs.",
                    ephemeral=True,
                )
                return

        await self.bot.db.execute(
            """
            INSERT INTO applications (guild_id, user_id, status, ai_flagged, max_ai_score, answers_json)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (
                interaction.guild.id,
                user.id,
                "PENDING_REVIEW",
                max_ai_score,
                json.dumps(answers),
            ),
        )
        await self._send_review_embed(
            interaction.guild, user, answers, "PENDING_REVIEW", max_score=max_ai_score
        )
        await dm.send("Application submitted successfully and is now pending staff review.")
        await interaction.followup.send("Application completed in DMs.", ephemeral=True)

    @app_commands.command(name="accept", description="Accept or deny an application with professional review output.")
    @app_commands.describe(
        user="Applicant to review",
        status="Application status",
        reason="Decision reason",
        notes="Moderator notes",
    )
    @app_commands.choices(status=ACCEPT_STATUS_CHOICES, reason=ACCEPT_REASON_CHOICES)
    async def accept(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        status: app_commands.Choice[str],
        reason: app_commands.Choice[str],
        notes: str,
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not self._can_manage_applications(interaction):
            await interaction.response.send_message(
                f"You need <@&{APPLICATION_REVIEW_ROLE_ID}> to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        sent = await self._send_application_decision_embed(
            interaction,
            user=user,
            status=status.value,
            reason=reason.value,
            notes=notes,
            color=0x1F8B4C,
            title="Application Review Result",
        )
        if not sent:
            await interaction.followup.send(
                f"Could not send result: channel `{APPLICATION_RESULTS_CHANNEL_ID}` not found or inaccessible.",
                ephemeral=True,
            )
            return
        await interaction.followup.send("Application decision posted.", ephemeral=True)

    @app_commands.command(name="deny", description="Deny or accept an application with professional review output.")
    @app_commands.describe(
        user="Applicant to review",
        status="Application status",
        reason="Decision reason",
        notes="Moderator notes",
    )
    @app_commands.choices(status=DENY_STATUS_CHOICES, reason=DENY_REASON_CHOICES)
    async def deny(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        status: app_commands.Choice[str],
        reason: app_commands.Choice[str],
        notes: str,
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not self._can_manage_applications(interaction):
            await interaction.response.send_message(
                f"You need <@&{APPLICATION_REVIEW_ROLE_ID}> to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        sent = await self._send_application_decision_embed(
            interaction,
            user=user,
            status=status.value,
            reason=reason.value,
            notes=notes,
            color=0xB32020,
            title="Application Review Result",
        )
        if not sent:
            await interaction.followup.send(
                f"Could not send result: channel `{APPLICATION_RESULTS_CHANNEL_ID}` not found or inaccessible.",
                ephemeral=True,
            )
            return
        await interaction.followup.send("Application decision posted.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ApplicationsCog(bot))
