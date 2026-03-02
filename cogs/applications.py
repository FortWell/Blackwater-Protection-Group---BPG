from __future__ import annotations

import asyncio
import json

import discord
from discord import app_commands
from discord.ext import commands


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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ApplicationsCog(bot))
