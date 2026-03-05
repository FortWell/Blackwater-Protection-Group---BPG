from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timezone
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

APPLICATION_REVIEW_ROLE_ID = 1478727168887623791
LEGACY_ACCEPT_DENY_LOCK_ROLE_ID = 1477383882973249616
APPLICATION_RESULTS_CHANNEL_ID = 1478729054092918784
APPLICATION_CATEGORY_ID = 1479196602701975603
APPLICATION_TICKET_CATEGORY_ID = 1479201857439273010
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
AI_TEST_LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1417875005387309137/"
    "1475920409709772951/Logo.png"
    "?ex=69aa723e&is=69a920be&hm=46119514c342478ed9e77aeb4df04f2134bf821c3c4976c7c8b7c37e1d1db47d&"
)
AI_TEST_IMAGE_URL = ACCEPT_BANNER_URL
MAX_AI_WARNING_STRIKES = 3

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
ACCEPT_REASON_VALUES = [choice.value for choice in ACCEPT_REASON_CHOICES]
DENY_REASON_VALUES = [choice.value for choice in DENY_REASON_CHOICES]


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


async def _fetch_roblox_user(username: str) -> tuple[dict[str, Any] | None, str | None]:
    raw = username.strip()
    if not raw:
        return None, "Not provided"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [raw], "excludeBannedUsers": False},
            ) as resp:
                if resp.status != 200:
                    return None, "Lookup failed"
                payload = await resp.json()
                rows = payload.get("data", [])
                if not rows:
                    return None, "Username not found"
                row = rows[0]
                user_id = row.get("id")
                if not user_id:
                    return None, "Username not found"
            async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as resp:
                if resp.status != 200:
                    return {
                        "username": row.get("name", raw),
                        "id": user_id,
                        "created": "Unknown",
                        "profile_url": f"https://www.roblox.com/users/{user_id}/profile",
                    }, None
                detail = await resp.json()
                created = detail.get("created", "")
                created_fmt = "Unknown"
                if created:
                    try:
                        created_fmt = datetime.fromisoformat(created.replace("Z", "+00:00")).strftime("%d/%m/%Y")
                    except ValueError:
                        created_fmt = created
                return {
                    "username": detail.get("name", row.get("name", raw)),
                    "id": detail.get("id", user_id),
                    "created": created_fmt,
                    "profile_url": f"https://www.roblox.com/users/{user_id}/profile",
                }, None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None, "Lookup failed"


class DecisionNotesModal(discord.ui.Modal, title="Application Decision Notes"):
    notes = discord.ui.TextInput(
        label="Notes",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
        placeholder="Enter staff notes...",
    )

    def __init__(self, cog: "ApplicationsCog", applicant_id: int, status: str, reason: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.applicant_id = applicant_id
        self.status = status
        self.reason = reason

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        if not self.cog._can_manage_applications(interaction):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        member = interaction.guild.get_member(self.applicant_id)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(self.applicant_id)
            except discord.HTTPException:
                member = None
        if member is None:
            await interaction.response.send_message("Applicant is no longer in this server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        sent = await self.cog._send_application_decision_embed(
            interaction,
            user=member,
            status=self.status,
            reason=self.reason,
            notes=str(self.notes),
            color=0x1F8B4C if self.status == "Accepted" else 0xB32020,
            title="Application Review Result",
        )
        if not sent:
            await interaction.followup.send("Failed to post decision result.", ephemeral=True)
            return
        await interaction.followup.send("Decision posted.", ephemeral=True)


class DecisionReasonSelect(discord.ui.Select):
    def __init__(self, cog: "ApplicationsCog", applicant_id: int, status: str, reasons: list[str]):
        options = [discord.SelectOption(label=r[:100], value=r) for r in reasons]
        super().__init__(placeholder="Reasson:", min_values=1, max_values=1, options=options)
        self.cog = cog
        self.applicant_id = applicant_id
        self.status = status

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.values:
            await interaction.response.send_message("Please select a reason.", ephemeral=True)
            return
        reason = self.values[0]
        await interaction.response.send_modal(
            DecisionNotesModal(
                cog=self.cog,
                applicant_id=self.applicant_id,
                status=self.status,
                reason=reason,
            )
        )


class DecisionReasonView(discord.ui.View):
    def __init__(self, cog: "ApplicationsCog", applicant_id: int, status: str, reasons: list[str]):
        super().__init__(timeout=900)
        self.add_item(DecisionReasonSelect(cog=cog, applicant_id=applicant_id, status=status, reasons=reasons))


class ApplicationReviewActionsView(discord.ui.View):
    def __init__(self, cog: "ApplicationsCog", applicant_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.applicant_id = applicant_id

    async def _require_staff(self, interaction: discord.Interaction) -> bool:
        if not self.cog._can_manage_applications(interaction):
            await interaction.response.send_message(
                f"You need <@&{APPLICATION_REVIEW_ROLE_ID}> to use this.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="\U0001F7E9Accept", style=discord.ButtonStyle.secondary, custom_id="app:review:accept")
    async def accept(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._require_staff(interaction):
            return
        await interaction.response.send_message(
            "Select accept reason and then submit notes:",
            view=DecisionReasonView(
                cog=self.cog,
                applicant_id=self.applicant_id,
                status="Accepted",
                reasons=ACCEPT_REASON_VALUES,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="\U0001F7E5Deny", style=discord.ButtonStyle.secondary, custom_id="app:review:deny")
    async def deny(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._require_staff(interaction):
            return
        await interaction.response.send_message(
            "Select deny reason and then submit notes:",
            view=DecisionReasonView(
                cog=self.cog,
                applicant_id=self.applicant_id,
                status="Denied",
                reasons=DENY_REASON_VALUES,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="\u2753Open Ticket", style=discord.ButtonStyle.secondary, custom_id="app:review:open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            return

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("This can only be used in a server.", ephemeral=True)
            return
        if not await self._require_staff(interaction):
            return
        applicant = interaction.guild.get_member(self.applicant_id)
        if applicant is None:
            try:
                applicant = await interaction.guild.fetch_member(self.applicant_id)
            except discord.HTTPException:
                applicant = None
        if applicant is None:
            await interaction.followup.send("Applicant is no longer in this server.", ephemeral=True)
            return

        category = interaction.guild.get_channel(APPLICATION_TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send("Application category is not configured/found.", ephemeral=True)
            return

        safe_name = "".join(ch for ch in applicant.display_name.lower().replace(" ", "-") if ch.isalnum() or ch == "-")
        safe_name = safe_name.strip("-") or f"user-{applicant.id}"
        ticket_name = f"app-ticket-{safe_name}"[:95]
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            applicant: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        review_role = interaction.guild.get_role(APPLICATION_REVIEW_ROLE_ID)
        if review_role is not None:
            overwrites[review_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        if interaction.guild.me is not None:
            overwrites[interaction.guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

        ticket_channel = await interaction.guild.create_text_channel(
            name=ticket_name,
            category=category,
            topic=f"application-ticket:{applicant.id}",
            overwrites=overwrites,
            reason=f"Application ticket opened by {interaction.user} for {applicant}",
        )
        await ticket_channel.send(
            f"{applicant.mention} Ticket opened for your application review.\n"
            f"Opened by: {interaction.user.mention}"
        )
        await interaction.followup.send(f"Ticket opened: {ticket_channel.mention}", ephemeral=True)


class ApplicationsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _can_manage_applications(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        return bool(interaction.user.get_role(APPLICATION_REVIEW_ROLE_ID))

    def _can_use_accept_deny_commands(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        return bool(interaction.user.get_role(LEGACY_ACCEPT_DENY_LOCK_ROLE_ID))

    def _application_flow_items(self) -> list[tuple[str, str]]:
        # kind: "info" means send text only, "question" means wait for an answer.
        return [
            (
                "info",
                "Thank you for your interest in joining the Federal Reserve.\n"
                "The Federal Reserve represents professionalism, structure, and responsibility.\n"
                "Answer all questions clearly and honestly. Low-effort or troll applications will be denied.",
            ),
            ("info", "**Applicant Information**"),
            ("question", "1. 👤 Roblox Username:"),
            ("question", "2. 💬 Discord Username & Tag:"),
            ("question", "3. 📅 Age:"),
            ("question", "4. 🌍 Time Zone:"),
            ("question", "5. 🎙️ Do you have a working microphone? (Yes/No)"),
            ("info", "**Availability & Commitment**"),
            ("question", "6. ⏰ How active are you? (Hours per day and per week)"),
            ("question", "7. 📆 Are you able to attend scheduled trainings, meetings, and Federal events consistently?"),
            ("question", "8. 📚 Are you familiar with the purpose and responsibilities of the Federal Reserve within the game? Explain your understanding."),
            ("info", "**Knowledge & Responsibility**"),
            ("question", "9. 🏛️ What do you believe is the primary mission of the Federal Reserve?"),
            ("question", "10. 📖 Why is professionalism important when representing a federal institution?"),
            ("question", "11. 🔐 How would you handle sensitive or confidential information within the Federal Reserve?"),
            ("info", "**Situational Questions**"),
            ("question", "12. ⚖️ A staff member is being disrespectful during a formal Federal event. What would you do?"),
            ("question", "13. 🧠 You are given an instruction by a superior that you disagree with but it follows policy. How do you respond?"),
            ("question", "14. 🕯️ A civilian is confused about Federal procedures and begins complaining publicly. How do you handle it?"),
            ("question", "15. 🤝 Two members are arguing during an operation. What steps would you take to resolve the conflict?"),
            ("info", "**Motivation & Character**"),
            ("question", "16. 🎯 Why do you want to join the Federal Reserve?"),
            ("question", "17. 💼 What skills or strengths do you bring to this position?"),
            ("question", "18. 📈 How do you handle constructive criticism?"),
            ("question", "19. 🧠 Describe a time you showed leadership or responsibility."),
            ("question", "20. 🕴️ How would you represent the Federal Reserve both in-game and in the community?"),
            ("info", "**Final Agreement**"),
            ("question", "21. Do you understand that misuse of authority, corruption, or unprofessional conduct may result in removal? (Yes/No)"),
            ("question", "22. Do you agree to follow the chain of command and maintain integrity at all times? (Yes/No)"),
            (
                "info",
                "📌 **Notice:**\n"
                "Once submitted, Federal Command will review your application.\n"
                "Do not DM staff members regarding your status.\n\n"
                "Federal Reserve • Personnel Division.",
            ),
        ]

    def _ai_hold_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001F7E8Please Hold!",
            description=(
                "Your answer to this question is now being tested with: Original AI. Please HOLD.\n"
                "And do NOT \U0001F7E5 type ANYTHING until you receive the message to start writing again."
            ),
            color=0xF2C94C,
        )
        embed.set_author(name="Artificial Intelligence Test")
        embed.set_thumbnail(url=AI_TEST_LOGO_URL)
        embed.set_image(url=AI_TEST_IMAGE_URL)
        embed.set_footer(text="Federal Reserve Management", icon_url=AI_TEST_LOGO_URL)
        return embed

    def _ai_completed_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001F7E9Completed!",
            description=(
                "Your answer for this question has been checked and logged!\n\n"
                "You may now continue writing!"
            ),
            color=0x6FCF97,
        )
        embed.set_author(name="Artificial Intelligence Test _ COMPLETED")
        embed.set_thumbnail(url=AI_TEST_LOGO_URL)
        embed.set_image(url=AI_TEST_IMAGE_URL)
        embed.set_footer(text="Federal Reserve Management", icon_url=AI_TEST_LOGO_URL)
        return embed

    def _ai_warning_embed(self, strikes: int) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001F7E5WARNING!",
            description=(
                "You have send an Message, This message will now be ignored and you are being warned.\n"
                f"You now have {strikes}/{MAX_AI_WARNING_STRIKES} Stricks this warning has been loged.\n"
                "If you get 2 more stricks your application will be closed and you will be reported to the Application Staff."
            ),
            color=0xD63324,
        )
        embed.set_author(name="Artificial Intelligence Test _ WARNING")
        embed.set_thumbnail(url=AI_TEST_LOGO_URL)
        embed.set_image(url=AI_TEST_IMAGE_URL)
        embed.set_footer(text="Federal Reserve Management", icon_url=AI_TEST_LOGO_URL)
        return embed

    async def _find_existing_application_channel(
        self, guild: discord.Guild, user_id: int
    ) -> discord.TextChannel | None:
        needle = f"application-owner:{user_id}"
        for channel in guild.text_channels:
            if channel.topic and needle in channel.topic:
                return channel
        return None

    async def _log_application_event(
        self,
        session_id: int,
        event_type: str,
        content: str | None = None,
        strike_count: int = 0,
    ) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO application_events (session_id, event_type, content, strike_count)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, event_type, content or "", strike_count),
        )

    async def _update_application_session(
        self,
        session_id: int,
        *,
        status: str | None = None,
        strike_count: int | None = None,
    ) -> None:
        current_status = status
        if current_status is None:
            current_status = await self.bot.db.fetch_value(
                "SELECT status FROM application_sessions WHERE id = ?",
                (session_id,),
            ) or "IN_PROGRESS"
        current_strikes = strike_count
        if current_strikes is None:
            raw = await self.bot.db.fetch_value(
                "SELECT strike_count FROM application_sessions WHERE id = ?",
                (session_id,),
            )
            current_strikes = int(raw) if raw and raw.isdigit() else 0
        await self.bot.db.execute(
            """
            UPDATE application_sessions
            SET status = ?, strike_count = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (current_status, current_strikes, session_id),
        )

    async def _build_application_transcript(self, channel: discord.TextChannel) -> str:
        lines = [
            "NYCRPP Application Transcript",
            f"Guild: {channel.guild.name} ({channel.guild.id})",
            f"Channel: #{channel.name} ({channel.id})",
            "-" * 72,
        ]
        async for msg in channel.history(limit=None, oldest_first=True):
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {msg.content or ''}")
        return "\n".join(lines)

    async def _send_canceled_transcript(
        self,
        log_channel: discord.TextChannel,
        applicant: discord.Member,
        transcript_text: str,
    ) -> None:
        embed = discord.Embed(
            title="Application Canceled",
            description=(
                f"Applicant: {applicant.mention} (`{applicant.id}`)\n"
                "This application was canceled by the applicant with `!cancel`."
            ),
            color=0xB32020,
        )
        embed.set_thumbnail(url=APPLICATION_LOGO_URL)
        embed.set_image(url=ACCEPT_BANNER_URL)
        embed.set_footer(text="Federal Reserve Management", icon_url=APPLICATION_LOGO_URL)
        transcript_file = discord.File(
            fp=io.BytesIO(transcript_text.encode("utf-8", errors="replace")),
            filename=f"application-canceled-{log_channel.id}.txt",
        )
        await log_channel.send(embed=embed, file=transcript_file)

    async def _monitor_hold_violations(
        self,
        channel: discord.abc.Messageable,
        applicant: discord.Member,
        session_id: int,
        log_channel: discord.TextChannel | None,
        strikes: int,
        hold_seconds: float = 2.0,
    ) -> tuple[int, bool]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + hold_seconds
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return strikes, False
            try:
                extra = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.channel.id == channel.id and m.author.id == applicant.id,
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                return strikes, False
            try:
                await extra.delete()
            except discord.HTTPException:
                pass
            strikes += 1
            await self._log_application_event(
                session_id,
                "HOLD_VIOLATION",
                content=extra.content,
                strike_count=strikes,
            )
            await self._update_application_session(session_id, strike_count=strikes)
            await channel.send(embed=self._ai_warning_embed(strikes))
            if log_channel is not None:
                await log_channel.send(
                    f"[HOLD VIOLATION] {applicant} ({applicant.id}) | strike {strikes}/{MAX_AI_WARNING_STRIKES}\n"
                    f"Content: {extra.content[:1500]}"
                )
            if strikes >= MAX_AI_WARNING_STRIKES:
                return strikes, True

    async def _report_application_lock(
        self,
        guild: discord.Guild,
        applicant: discord.Member,
        strikes: int,
    ) -> None:
        review_channel_id = self.bot.config.application_review_channel_id
        if not review_channel_id:
            return
        channel = guild.get_channel(review_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title="Application Closed (Strike Limit)",
            description=(
                f"Applicant: {applicant.mention} (`{applicant.id}`)\n"
                f"Reason: Sent messages during AI hold window.\n"
                f"Strikes: {strikes}/{MAX_AI_WARNING_STRIKES}"
            ),
            color=0xB32020,
        )
        embed.set_footer(text="Federal Reserve Management", icon_url=AI_TEST_LOGO_URL)
        await channel.send(content=f"<@&{APPLICATION_REVIEW_ROLE_ID}>", embed=embed)

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
        strike_count: int,
        log_channel: discord.TextChannel,
        transcript_text: str,
        roblox_info: dict[str, Any] | None = None,
        roblox_error: str | None = None,
    ) -> None:
        channel = log_channel

        embed = discord.Embed(
            title="Application Submitted",
            description=(
                f"Applicant: {applicant.mention} (`{applicant.id}`)\n"
                f"Status: {status}\n"
                f"Strikes: {strike_count}/{MAX_AI_WARNING_STRIKES}\n"
                f"Application Logs: {log_channel.mention}"
            ),
            color=0x0B3D0B,
        )
        embed.add_field(name="Max AI score", value=f"{max_score:.0%}", inline=False)
        if roblox_info is not None:
            embed.add_field(
                name="Roblox Info",
                value=(
                    f"Username: {roblox_info.get('username', 'Unknown')}\n"
                    f"ID: {roblox_info.get('id', 'Unknown')}\n"
                    f"Created: {roblox_info.get('created', 'Unknown')}\n"
                    f"Profile: {roblox_info.get('profile_url', 'Unknown')}"
                ),
                inline=False,
            )
        elif roblox_error:
            embed.add_field(name="Roblox Info", value=f"Lookup: {roblox_error}", inline=False)
        for idx, item in enumerate(answers, start=1):
            q = item["question"]
            a = item["answer"]
            s = item["ai_score"]
            value = f"AI score: {s:.0%}\n{str(a)[:900]}"
            embed.add_field(name=f"Q{idx}: {q}", value=value, inline=False)
        embed.set_thumbnail(url=APPLICATION_LOGO_URL)
        embed.set_image(url=ACCEPT_BANNER_URL)
        embed.set_footer(text="Federal Reserve Management", icon_url=APPLICATION_LOGO_URL)

        transcript_bytes = transcript_text.encode("utf-8", errors="replace")
        transcript_file = discord.File(
            fp=io.BytesIO(transcript_bytes),
            filename=f"application-transcript-{log_channel.id}.txt",
        )
        await channel.send(
            content=f"<@&{APPLICATION_REVIEW_ROLE_ID}>",
            embed=embed,
            file=transcript_file,
            view=ApplicationReviewActionsView(self, applicant.id),
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
        )

    @app_commands.command(name="apply", description="Start the staff application in DMs.")
    async def apply(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        user = interaction.user
        guild = interaction.guild
        existing_log_channel = await self._find_existing_application_channel(guild, user.id)
        if existing_log_channel is not None:
            await interaction.followup.send(
                "You already have an active application. Please complete it before starting another.",
                ephemeral=True,
            )
            return

        category = guild.get_channel(APPLICATION_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(
                f"Application category `{APPLICATION_CATEGORY_ID}` not found.",
                ephemeral=True,
            )
            return
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        review_role = guild.get_role(APPLICATION_REVIEW_ROLE_ID)
        if review_role is not None:
            overwrites[review_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

        safe_name = "".join(ch for ch in user.display_name.lower().replace(" ", "-") if ch.isalnum() or ch == "-")
        safe_name = safe_name.strip("-") or f"user-{user.id}"
        channel_name = f"{safe_name}-application-logs"
        log_channel = await guild.create_text_channel(
            name=channel_name[:95],
            category=category,
            topic=f"application-owner:{user.id}",
            overwrites=overwrites,
            reason=f"Application logs channel for {user}",
        )
        session_id = await self.bot.db.execute_insert(
            """
            INSERT INTO application_sessions (guild_id, user_id, channel_id, status, strike_count)
            VALUES (?, ?, ?, ?, 0)
            """,
            (guild.id, user.id, log_channel.id, "IN_PROGRESS"),
        )
        await self._log_application_event(session_id, "APPLICATION_STARTED", content="Application started in DM flow.")

        try:
            dm = await user.create_dm()
            # Intro/section content is sent from the configured flow below.
            await dm.send("To cancel this application at any time, type: `!cancel`")
        except discord.HTTPException:
            await self._update_application_session(session_id, status="DM_BLOCKED", strike_count=0)
            await self._log_application_event(session_id, "DM_BLOCKED", content="User has DMs disabled.")
            await interaction.followup.send(
                "I could not DM you. Enable DMs and use `/apply` again.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "Application started in your DMs. Please continue there.",
            ephemeral=True,
        )
        await log_channel.send(f"Application started for {user.mention} (`{user.id}`).")

        flow_items = self._application_flow_items()
        questions = [text for (kind, text) in flow_items if kind == "question"]
        answers: list[dict[str, str | float]] = []
        transcript_lines: list[str] = [
            "NYCRPP Application Transcript",
            f"Guild: {guild.name} ({guild.id})",
            f"Applicant: {user} ({user.id})",
            f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "-" * 72,
        ]
        max_ai_score = 0.0
        strikes = 0
        roblox_info: dict[str, Any] | None = None
        roblox_error: str | None = None

        def check(msg: discord.Message) -> bool:
            return msg.author.id == user.id and msg.channel.id == dm.id

        q_idx = 0
        for kind, text in flow_items:
            if kind != "question":
                await dm.send(text)
                await log_channel.send(f"[INFO] {text[:1800]}")
                await self._log_application_event(session_id, "INFO_SENT", content=text, strike_count=strikes)
                continue

            q_idx += 1
            question = text
            await dm.send(question)
            transcript_lines.append(f"[Q{q_idx}] {question}")
            await log_channel.send(f"[Q{q_idx}] {question}")
            await self._log_application_event(session_id, "QUESTION_SENT", content=question, strike_count=strikes)
            try:
                reply = await self.bot.wait_for("message", check=check, timeout=600)
            except asyncio.TimeoutError:
                await dm.send("Application timed out after 10 minutes without response.")
                await log_channel.send("Application timed out after 10 minutes without response.")
                await self._update_application_session(session_id, status="TIMED_OUT", strike_count=strikes)
                await self._log_application_event(session_id, "TIMEOUT", content="No response within 10 minutes.", strike_count=strikes)
                return

            if reply.content.strip().lower() == "!cancel":
                cancel_line = "[SYSTEM] Applicant canceled the application with !cancel."
                transcript_lines.append(cancel_line)
                transcript_lines.append(f"Canceled: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
                await dm.send("Application canceled successfully.")
                await log_channel.send(cancel_line)
                await self._update_application_session(session_id, status="CANCELED_BY_USER", strike_count=strikes)
                await self._log_application_event(
                    session_id,
                    "APPLICATION_CANCELED",
                    content="Canceled by user via !cancel",
                    strike_count=strikes,
                )
                await self.bot.db.execute(
                    """
                    INSERT INTO applications (guild_id, user_id, status, ai_flagged, max_ai_score, answers_json)
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (
                        guild.id,
                        user.id,
                        "CANCELED_BY_USER",
                        max_ai_score,
                        json.dumps(answers),
                    ),
                )
                await self._send_canceled_transcript(
                    log_channel=log_channel,
                    applicant=user,
                    transcript_text="\n".join(transcript_lines),
                )
                return

            transcript_lines.append(f"[A{q_idx}] {reply.content}")
            await log_channel.send(f"[A{q_idx}] {reply.content[:1800]}")
            await self._log_application_event(
                session_id,
                "ANSWER_RECEIVED",
                content=reply.content,
                strike_count=strikes,
            )
            if q_idx == 1:
                roblox_info, roblox_error = await _fetch_roblox_user(reply.content)
                if roblox_info is not None:
                    info_line = (
                        f"[ROBLOX LOOKUP] Username: {roblox_info['username']} | "
                        f"ID: {roblox_info['id']} | Created: {roblox_info['created']} | "
                        f"Profile: {roblox_info['profile_url']}"
                    )
                    await log_channel.send(info_line)
                    await self._log_application_event(
                        session_id,
                        "ROBLOX_LOOKUP",
                        content=info_line,
                        strike_count=strikes,
                    )
                    transcript_lines.append(info_line)
                elif roblox_error:
                    err_line = f"[ROBLOX LOOKUP] {roblox_error}"
                    await log_channel.send(err_line)
                    await self._log_application_event(
                        session_id,
                        "ROBLOX_LOOKUP",
                        content=err_line,
                        strike_count=strikes,
                    )
                    transcript_lines.append(err_line)
            if q_idx == 2:
                provided_tag = reply.content.strip()
                actual_tag = str(user)
                created_fmt = user.created_at.strftime("%d/%m/%Y")
                info_line = (
                    f"[DISCORD LOOKUP] Provided: {provided_tag} | "
                    f"Actual: {actual_tag} | ID: {user.id} | "
                    f"Created: {created_fmt}"
                )
                await log_channel.send(info_line)
                await self._log_application_event(
                    session_id,
                    "DISCORD_LOOKUP",
                    content=info_line,
                    strike_count=strikes,
                )
                transcript_lines.append(info_line)
            await dm.send(embed=self._ai_hold_embed())
            score = estimate_ai_likelihood(reply.content)
            max_ai_score = max(max_ai_score, score)
            answers.append(
                {
                    "question": question,
                    "answer": reply.content,
                    "ai_score": score,
                }
            )
            strikes, should_close = await self._monitor_hold_violations(dm, user, session_id, log_channel, strikes)
            if should_close:
                await dm.send("Application closed due to repeated warnings.")
                await log_channel.send("Application closed due to repeated warnings.")
                await self._report_application_lock(guild, user, strikes)
                await self._update_application_session(session_id, status="CLOSED_STRIKE_LIMIT", strike_count=strikes)
                await self._log_application_event(
                    session_id,
                    "APPLICATION_CLOSED",
                    content="Closed due to strike limit.",
                    strike_count=strikes,
                )
                await self.bot.db.execute(
                    """
                    INSERT INTO applications (guild_id, user_id, status, ai_flagged, max_ai_score, answers_json)
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (
                        guild.id,
                        user.id,
                        "CLOSED_STRIKE_LIMIT",
                        max_ai_score,
                        json.dumps(answers),
                    ),
                )
                transcript_lines.append("-" * 72)
                transcript_lines.append(
                    f"Closed (strike limit): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )
                transcript_text = "\n".join(transcript_lines)
                await self._send_review_embed(
                    guild,
                    user,
                    answers,
                    "CLOSED_STRIKE_LIMIT",
                    max_score=max_ai_score,
                    strike_count=strikes,
                    log_channel=log_channel,
                    transcript_text=transcript_text,
                    roblox_info=roblox_info,
                    roblox_error=roblox_error,
                )
                return
            await dm.send(embed=self._ai_completed_embed())
            await self._log_application_event(
                session_id,
                "AI_CHECK_COMPLETED",
                content=f"AI score: {score:.4f}",
                strike_count=strikes,
            )
            await log_channel.send(f"[AI CHECK] Q{q_idx} score: {score:.0%}")

        await self._update_application_session(session_id, status="PENDING_REVIEW", strike_count=strikes)
        await self._log_application_event(
            session_id,
            "APPLICATION_SUBMITTED",
            content="Application submitted for staff review.",
            strike_count=strikes,
        )
        await self.bot.db.execute(
            """
            INSERT INTO applications (guild_id, user_id, status, ai_flagged, max_ai_score, answers_json)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (
                guild.id,
                user.id,
                "PENDING_REVIEW",
                max_ai_score,
                json.dumps(answers),
            ),
        )
        transcript_lines.append("-" * 72)
        transcript_lines.append(f"Completed: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        transcript_text = "\n".join(transcript_lines)
        await self._send_review_embed(
            guild,
            user,
            answers,
            "PENDING_REVIEW",
            max_score=max_ai_score,
            strike_count=strikes,
            log_channel=log_channel,
            transcript_text=transcript_text,
            roblox_info=roblox_info,
            roblox_error=roblox_error,
        )
        await dm.send("Application submitted successfully and is now pending staff review.")
        await log_channel.send("Application submitted successfully and is now pending staff review.")

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
        if not self._can_use_accept_deny_commands(interaction):
            await interaction.response.send_message(
                "These commands are NOT in-use and should NOT be used. "
                "Open an General Support Ticket for any questions + DM Thunderbeast_044",
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
        if not self._can_use_accept_deny_commands(interaction):
            await interaction.response.send_message(
                "These commands are NOT in-use and should NOT be used. "
                "Open an General Support Ticket for any questions + DM Thunderbeast_044",
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

