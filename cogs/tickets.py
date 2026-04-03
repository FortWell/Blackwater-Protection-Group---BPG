from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from bot.embed_utils import apply_embed_template
from bot.branding import (
    BRANDING_FOOTER_ICON_URL,
    BRANDING_FOOTER_TEXT,
    BRANDING_IMAGE_URL,
    BRANDING_THUMBNAIL_URL,
)
FOOTER_ICON_URL = BRANDING_FOOTER_ICON_URL
APPLICATION_REVIEW_ROLE_ID = 1400844188811006029
SECURITY_TICKET_BLOCKED_ROLE_ID = 1428795023968829562


def _apply_branding(embed: discord.Embed) -> discord.Embed:
    embed.set_thumbnail(url=BRANDING_THUMBNAIL_URL)
    embed.set_image(url=BRANDING_IMAGE_URL)
    embed.set_footer(text=BRANDING_FOOTER_TEXT, icon_url=FOOTER_ICON_URL)
    return embed


@dataclass(slots=True)
class TicketType:
    key: str
    label: str
    category_id: int
    support_role_id: int
    button_style: discord.ButtonStyle
    open_role_id: int = 0


def _ticket_types(cfg) -> dict[str, TicketType]:
    return {
        "management": TicketType(
            key="management",
            label="Management",
            category_id=cfg.ticket_management_category_id,
            support_role_id=cfg.ticket_management_support_role_id,
            button_style=discord.ButtonStyle.danger,
        ),
        "security": TicketType(
            key="security",
            label="Security",
            category_id=cfg.ticket_security_category_id,
            support_role_id=cfg.ticket_security_support_role_id,
            button_style=discord.ButtonStyle.primary,
        ),
        "general": TicketType(
            key="general",
            label="Executive Support",
            category_id=cfg.ticket_general_category_id,
            support_role_id=cfg.ticket_general_support_role_id,
            button_style=discord.ButtonStyle.success,
        ),
        "priority": TicketType(
            key="priority",
            label="Priority Support",
            category_id=cfg.ticket_priority_category_id,
            support_role_id=cfg.ticket_priority_support_role_id,
            button_style=discord.ButtonStyle.secondary,
            open_role_id=cfg.ticket_priority_open_role_id,
        ),
    }


def _topic_dict(topic: str | None) -> dict[str, str]:
    if not topic:
        return {}
    out: dict[str, str] = {}
    for part in topic.split(";"):
        item = part.strip()
        if ":" in item:
            k, v = item.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _topic_value_int(data: dict[str, str], key: str) -> int | None:
    raw = data.get(key, "")
    return int(raw) if raw.isdigit() else None


def _build_topic(owner_id: int, ticket_type: str, ticket_id: int, claimed_by: int | None = None) -> str:
    base = f"ticket-owner:{owner_id};ticket-type:{ticket_type};ticket-id:{ticket_id}"
    if claimed_by:
        return f"{base};claimed-by:{claimed_by}"
    return base


def _support_role_for_type(cfg, ticket_type_key: str) -> int:
    if ticket_type_key == "management":
        return cfg.ticket_management_support_role_id
    if ticket_type_key == "security":
        return cfg.ticket_security_support_role_id
    if ticket_type_key == "general":
        return cfg.ticket_general_support_role_id
    if ticket_type_key == "priority":
        return cfg.ticket_priority_support_role_id
    return 0


def _application_owner_for_topic(data: dict[str, str]) -> int | None:
    return _topic_value_int(data, "application-ticket")


async def _fetch_roblox_user(username: str) -> tuple[dict[str, Any] | None, str | None]:
    if not username.strip():
        return None, "Not provided"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username.strip()], "excludeBannedUsers": False},
            ) as resp:
                if resp.status != 200:
                    return None, "Lookup failed"
                payload = await resp.json()
                data = payload.get("data", [])
                if not data:
                    return None, "Username not found"
                user_row = data[0]
                user_id = user_row.get("id")
                if not user_id:
                    return None, "Username not found"

            async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as resp:
                if resp.status != 200:
                    return {
                        "username": user_row.get("name", username.strip()),
                        "id": user_id,
                        "profile_url": f"https://www.roblox.com/users/{user_id}/profile",
                        "created": "Unknown",
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
                    "username": detail.get("name", user_row.get("name", username.strip())),
                    "id": detail.get("id", user_id),
                    "profile_url": f"https://www.roblox.com/users/{user_id}/profile",
                    "created": created_fmt,
                }, None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None, "Lookup failed"


def _ticket_info_embed(
    cfg,
    member: discord.Member,
    ticket_type: TicketType,
    ticket_id: int,
    roblox_info: dict[str, Any] | None,
    roblox_error: str | None,
    extra_fields: list[tuple[str, str]] | None = None,
) -> discord.Embed:
    created_date = member.created_at.astimezone(timezone.utc).strftime("%d/%m/%Y")
    embed = discord.Embed(
        title=f"{ticket_type.label} support",
        description=(
            f"Thank you for creating a {ticket_type.label.lower()} ticket. "
            "Our team will be with you shortly. In the meantime, ensure you've supplied "
            "our team with the essential items to assist you furthermore. Please patiently "
            "wait while our team gets back to your inquiry."
        ),
        color=0x2B2D31,
    )
    embed.add_field(
        name="Discord Information",
        value=(
            f"- **Discord Username:** {member.name}\n"
            f"- **Discord ID:** {member.id}\n"
            f"- **Discord account creation date:** `{created_date}`\n"
            f"- **Ticket ID:** {ticket_id}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Roblox Information",
        value=(
            f"- **Roblox Username:** {roblox_info['username'] if roblox_info else 'Not provided'}\n"
            f"- **Roblox ID:** {roblox_info['id'] if roblox_info else 'Not provided'}\n"
            f"- **Roblox Profile:** "
            f"{roblox_info['profile_url'] if roblox_info else (roblox_error or 'Not provided')}\n"
            f"- **Creation Date:** {roblox_info['created'] if roblox_info else (roblox_error or 'Not provided')}"
        ),
        inline=False,
    )
    if extra_fields:
        for name, value in extra_fields:
            if name and value:
                embed.add_field(name=name, value=value, inline=False)
    apply_embed_template(
        embed,
        cfg.embed_templates.get("ticket_info"),
        context={
            "ticket_type": ticket_type.label,
            "ticket_id": ticket_id,
            "user": member.mention,
            "user_id": member.id,
        },
    )
    return _apply_branding(embed)


def _ticket_reason_embed(cfg, reason: str, *, title: str = "What is the reason for the ticket?") -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=reason,
        color=0x2B2D31,
    )
    apply_embed_template(
        embed,
        cfg.embed_templates.get("ticket_reason"),
        context={
            "reason": reason,
        },
    )
    return _apply_branding(embed)


def _can_manage_ticket(interaction: discord.Interaction) -> tuple[bool, int | None]:
    bot: commands.Bot = interaction.client
    cfg = bot.config
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        return False, None
    if not isinstance(interaction.user, discord.Member):
        return False, None
    if interaction.user.guild_permissions.administrator:
        data = _topic_dict(interaction.channel.topic)
        owner_id = _application_owner_for_topic(data) or _topic_value_int(data, "ticket-owner")
        return True, owner_id

    data = _topic_dict(interaction.channel.topic)
    app_owner_id = _application_owner_for_topic(data)
    if app_owner_id is not None:
        is_owner = interaction.user.id == app_owner_id
        is_app_staff = bool(interaction.user.get_role(APPLICATION_REVIEW_ROLE_ID))
        return is_owner or is_app_staff, app_owner_id

    owner_id = _topic_value_int(data, "ticket-owner")
    ticket_type = data.get("ticket-type", "")
    support_role_id = _support_role_for_type(cfg, ticket_type)
    is_owner = owner_id is not None and interaction.user.id == owner_id
    is_staff = bool(support_role_id and interaction.user.get_role(support_role_id))
    return is_owner or is_staff, owner_id


async def _close_ticket_channel(
    interaction: discord.Interaction,
    owner_id: int | None,
    close_reason: str | None,
) -> None:
    assert isinstance(interaction.channel, discord.TextChannel)
    channel = interaction.channel
    # Acknowledge first to avoid "Unknown interaction" on long transcript operations.
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        return

    await _send_ticket_transcript(interaction, channel, owner_id)
    if close_reason:
        await channel.send(f"Ticket closed by {interaction.user.mention}. Reason: {close_reason}")
    try:
        await interaction.followup.send("Ticket closed. Deleting channel in 5 seconds.", ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        pass
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {interaction.user} | reason: {close_reason or 'No reason'}")
    except discord.HTTPException:
        pass


def _message_to_text_line(msg: discord.Message) -> str:
    ts = msg.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    author = f"{msg.author} ({msg.author.id})"
    content = msg.content or ""
    if msg.attachments:
        att = " | ".join(a.url for a in msg.attachments)
        content = f"{content}\n[Attachments] {att}".strip()
    if msg.embeds:
        content = f"{content}\n[Embeds] {len(msg.embeds)} embed(s)".strip()
    return f"[{ts}] {author}: {content}"


async def _build_transcript_text(channel: discord.TextChannel) -> str:
    header = [
        "Blackwater Protection Group Ticket Transcript",
        f"Guild: {channel.guild.name} ({channel.guild.id})",
        f"Channel: #{channel.name} ({channel.id})",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "-" * 72,
    ]
    lines = list(header)
    async for msg in channel.history(limit=None, oldest_first=True):
        lines.append(_message_to_text_line(msg))
    return "\n".join(lines)


async def _send_ticket_transcript(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    owner_id: int | None,
) -> None:
    bot: commands.Bot = interaction.client
    webhook_url = bot.config.bot_audit_webhook_url.strip()
    transcript = await _build_transcript_text(channel)
    data_bytes = transcript.encode("utf-8", errors="replace")
    filename = f"transcript-{channel.id}.txt"

    # Send to webhook
    if webhook_url:
        try:
            async with aiohttp.ClientSession() as session:
                webhook = discord.Webhook.from_url(webhook_url, session=session)
                await webhook.send(
                    content=f"Ticket transcript for <#{channel.id}>",
                    file=discord.File(io.BytesIO(data_bytes), filename=filename),
                    username="Ticket Transcript",
                    wait=False,
                )
        except Exception:
            pass

    # Send to ticket owner DMs
    if owner_id and interaction.guild is not None:
        owner = interaction.guild.get_member(owner_id)
        if owner is None:
            try:
                owner = await interaction.guild.fetch_member(owner_id)
            except discord.HTTPException:
                owner = None
        if owner is not None:
            try:
                await owner.send(
                    "Here is your ticket transcript.",
                    file=discord.File(io.BytesIO(data_bytes), filename=filename),
                )
            except discord.HTTPException:
                pass


class CloseRequestDecisionView(discord.ui.View):
    def __init__(self, owner_id: int | None):
        super().__init__(timeout=900)
        self.owner_id = owner_id

    async def _only_owner(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is None:
            await interaction.response.send_message("Ticket owner was not found.", ephemeral=True)
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the ticket owner can respond to this request.", ephemeral=True)
            return False
        return True

    def _disable_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="ticket:close_request:accept")
    async def accept(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._only_owner(interaction):
            return
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This can only be used in a ticket channel.", ephemeral=True)
            return
        self._disable_buttons()
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        await _close_ticket_channel(interaction, self.owner_id, None)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="ticket:close_request:deny")
    async def deny(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._only_owner(interaction):
            return
        self._disable_buttons()
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        await interaction.response.send_message("Close request denied. Ticket will remain open.")


async def _create_ticket_from_modal(
    interaction: discord.Interaction,
    ticket_type: TicketType,
    *,
    reason: str,
    roblox_username: str = "",
    reason_title: str = "What is the reason for the ticket?",
    extra_fields: list[tuple[str, str]] | None = None,
) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        return

    bot: commands.Bot = interaction.client
    cfg = bot.config
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("Tickets can only be created in a server.", ephemeral=True)
        return

    if ticket_type.key == "security" and interaction.user.get_role(SECURITY_TICKET_BLOCKED_ROLE_ID):
        await interaction.followup.send(
            "You can't open this ticket. Go open an Executive Support Ticket, mate.",
            ephemeral=True,
        )
        return

    if ticket_type.open_role_id and not interaction.user.get_role(ticket_type.open_role_id):
        await interaction.followup.send(
            "You do not have the required role to open this ticket.",
            ephemeral=True,
        )
        return

    if not ticket_type.category_id:
        await interaction.followup.send(
            f"{ticket_type.label} ticket category is not configured.",
            ephemeral=True,
        )
        return

    category = guild.get_channel(ticket_type.category_id)
    if not isinstance(category, discord.CategoryChannel):
        await interaction.followup.send(f"{ticket_type.label} category was not found.", ephemeral=True)
        return

    for ch in category.channels:
        if not isinstance(ch, discord.TextChannel):
            continue
        data = _topic_dict(ch.topic)
        owner_id = _topic_value_int(data, "ticket-owner")
        existing_type = data.get("ticket-type")
        if owner_id == interaction.user.id and existing_type == ticket_type.key:
            await interaction.followup.send(
                f"You already have an open {ticket_type.label} ticket: {ch.mention}",
                ephemeral=True,
            )
            return

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    if ticket_type.support_role_id:
        support_role = guild.get_role(ticket_type.support_role_id)
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

    base_name = interaction.user.name.lower().replace(" ", "-")
    channel_name = f"{ticket_type.key}-{base_name}-{interaction.user.discriminator}"
    ticket_channel = await guild.create_text_channel(
        name=channel_name[:95],
        category=category,
        topic="creating",
        overwrites=overwrites,
        reason=f"{ticket_type.label} ticket opened by {interaction.user}",
    )
    ticket_id = ticket_channel.id
    await ticket_channel.edit(
        topic=_build_topic(
            owner_id=interaction.user.id,
            ticket_type=ticket_type.key,
            ticket_id=ticket_id,
        )
    )

    roblox_info, roblox_error = await _fetch_roblox_user(roblox_username)
    await ticket_channel.send(
        embed=_ticket_info_embed(
            cfg,
            interaction.user,
            ticket_type,
            ticket_id,
            roblox_info=roblox_info,
            roblox_error=roblox_error,
            extra_fields=extra_fields,
        )
    )
    await ticket_channel.send(embed=_ticket_reason_embed(cfg, reason, title=reason_title))
    await ticket_channel.send(view=TicketActionsView())
    await interaction.followup.send(
        f"{ticket_type.label} ticket created: {ticket_channel.mention}",
        ephemeral=True,
    )


class TicketReasonModal(discord.ui.Modal, title="Executive Support Ticket"):
    reason = discord.ui.TextInput(
        label="What is the reason for the ticket?",
        placeholder="Enter the full reason...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=900,
    )
    roblox_username = discord.ui.TextInput(
        label="Roblox Username",
        placeholder="Enter your Roblox username",
        required=False,
        max_length=40,
    )

    def __init__(self, ticket_type: TicketType):
        super().__init__(timeout=300)
        self.ticket_type = ticket_type

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _create_ticket_from_modal(
            interaction,
            self.ticket_type,
            reason=str(self.reason),
            roblox_username=str(self.roblox_username),
        )


class PriorityTicketReasonModal(discord.ui.Modal, title="Priority Support Ticket"):
    reason = discord.ui.TextInput(
        label="Why are you creating this Ticket?",
        placeholder="Please answer this so we can help you!",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=900,
    )
    hr = discord.ui.TextInput(
        label="Is this an Ticket for: HR?",
        placeholder="Answer with True or False Please",
        style=discord.TextStyle.short,
        required=True,
    )
    shr = discord.ui.TextInput(
        label="Is this an Ticket for: SHR?",
        placeholder="Answer this so we can help you!",
        style=discord.TextStyle.short,
        required=True,
    )

    def __init__(self, ticket_type: TicketType):
        super().__init__(timeout=300)
        self.ticket_type = ticket_type

    async def on_submit(self, interaction: discord.Interaction) -> None:
        extra_fields = [
            ("Is this an Ticket for: HR?", str(self.hr)),
            ("Is this an Ticket for: SHR?", str(self.shr)),
        ]
        await _create_ticket_from_modal(
            interaction,
            self.ticket_type,
            reason=str(self.reason),
            reason_title="Priority Support Request",
            extra_fields=extra_fields,
        )


async def _claim_ticket(interaction: discord.Interaction) -> None:
    bot: commands.Bot = interaction.client
    cfg = bot.config
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("This can only be used in a ticket channel.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return

    data = _topic_dict(interaction.channel.topic)
    app_owner_id = _application_owner_for_topic(data)
    if app_owner_id is not None:
        if (
            not interaction.user.guild_permissions.administrator
            and not interaction.user.get_role(APPLICATION_REVIEW_ROLE_ID)
        ):
            await interaction.response.send_message("Only application staff can claim this ticket.", ephemeral=True)
            return
        claimed_by = _topic_value_int(data, "claimed-by")
        if claimed_by and claimed_by != interaction.user.id:
            await interaction.response.send_message("This ticket is already claimed by another staff member.", ephemeral=True)
            return
        await interaction.channel.edit(
            topic=f"application-ticket:{app_owner_id};claimed-by:{interaction.user.id}"
        )
        await interaction.response.send_message(
            f"This application ticket has been claimed by: {interaction.user.display_name}."
        )
        return

    ticket_type = data.get("ticket-type", "")
    support_role_id = _support_role_for_type(cfg, ticket_type)
    if not support_role_id or not interaction.user.get_role(support_role_id):
        await interaction.response.send_message("Only support staff can claim this ticket.", ephemeral=True)
        return

    claimed_by = _topic_value_int(data, "claimed-by")
    if claimed_by and claimed_by != interaction.user.id:
        await interaction.response.send_message("This ticket is already claimed by another staff member.", ephemeral=True)
        return

    owner_id = _topic_value_int(data, "ticket-owner") or 0
    ticket_id = _topic_value_int(data, "ticket-id") or interaction.channel.id
    await interaction.channel.edit(
        topic=_build_topic(owner_id=owner_id, ticket_type=ticket_type, ticket_id=ticket_id, claimed_by=interaction.user.id)
    )
    await interaction.response.send_message(
        f"This ticket has been claimed by: {interaction.user.display_name}.\n"
        "Please do not ping them. For more ticket rules, refer to the Ticket Regulations."
    )


class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="❌", custom_id="ticket:close")
    async def close_ticket(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This can only be used in a ticket channel.", ephemeral=True)
            return
        allowed, owner_id = _can_manage_ticket(interaction)
        if not allowed:
            await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
            return
        await _close_ticket_channel(interaction, owner_id, None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.secondary, emoji="✋", custom_id="ticket:claim")
    async def claim_ticket(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await _claim_ticket(interaction)


class TicketCreateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _open_reason_modal(self, interaction: discord.Interaction, type_key: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        cfg = interaction.client.config
        ticket_type = _ticket_types(cfg).get(type_key)
        if ticket_type is None:
            await interaction.response.send_message("Invalid ticket type.", ephemeral=True)
            return

        if ticket_type.key == "security":
            is_blocked = any(role.id == SECURITY_TICKET_BLOCKED_ROLE_ID for role in interaction.user.roles)
            if not is_blocked:
                # Fetch fresh member roles in case cache is stale.
                try:
                    fresh = await interaction.guild.fetch_member(interaction.user.id)
                    is_blocked = any(role.id == SECURITY_TICKET_BLOCKED_ROLE_ID for role in fresh.roles)
                except discord.HTTPException:
                    is_blocked = False
            if is_blocked:
                await interaction.response.send_message(
                    "You can't open this ticket. Go open an Executive Support Ticket, mate.",
                    ephemeral=True,
                )
                return

        if ticket_type.key == "priority" and ticket_type.open_role_id:
            has_access = bool(interaction.user.get_role(ticket_type.open_role_id))
            if not has_access:
                try:
                    fresh = await interaction.guild.fetch_member(interaction.user.id)
                    has_access = any(role.id == ticket_type.open_role_id for role in fresh.roles)
                except discord.HTTPException:
                    has_access = False
            if not has_access:
                await interaction.response.send_message(
                    "You do not have the required role to open this ticket.",
                    ephemeral=True,
                )
                return

        if ticket_type.key == "priority":
            await interaction.response.send_modal(PriorityTicketReasonModal(ticket_type))
            return

        await interaction.response.send_modal(TicketReasonModal(ticket_type))

    @discord.ui.button(label="Management", style=discord.ButtonStyle.danger, custom_id="ticket:create:management")
    async def management(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._open_reason_modal(interaction, "management")

    @discord.ui.button(label="Security", style=discord.ButtonStyle.primary, custom_id="ticket:create:security")
    async def security(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        if interaction.user.get_role(SECURITY_TICKET_BLOCKED_ROLE_ID):
            await interaction.response.send_message(
                "You can't open this ticket. Go open an Executive Support Ticket, mate.",
                ephemeral=True,
            )
            return
        await self._open_reason_modal(interaction, "security")

    @discord.ui.button(label="Executive Support", style=discord.ButtonStyle.success, custom_id="ticket:create:general")
    async def general(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._open_reason_modal(interaction, "general")

    @discord.ui.button(label="Priority Support", style=discord.ButtonStyle.secondary, custom_id="ticket:create:priority")
    async def priority(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._open_reason_modal(interaction, "priority")


class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(TicketCreateView())
        self.bot.add_view(TicketActionsView())

    def _is_ticket_staff(self, member: discord.Member) -> bool:
        cfg = self.bot.config
        role_ids = {
            cfg.ticket_management_support_role_id,
            cfg.ticket_security_support_role_id,
            cfg.ticket_general_support_role_id,
            cfg.ticket_priority_support_role_id,
        }
        role_ids = {rid for rid in role_ids if rid}
        if not role_ids:
            return False
        return any(member.get_role(rid) for rid in role_ids)

    @app_commands.command(name="ticket-panel", description="Post the ticket creation panel.")
    async def ticket_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Permission denied.", ephemeral=True)
            return
        if not self._is_ticket_staff(interaction.user):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this in a text channel.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Support Tickets",
            description=(
                "Open a ticket by selecting a category below:\n"
                "• Management Ticket\n"
                "• Security Ticket\n"
                "• Executive Support Ticket\n"
                "• Priority Support Ticket"
            ),
            color=0x0B1E3D,
        )
        apply_embed_template(
            embed,
            self.bot.config.embed_templates.get("ticket_panel"),
        )
        _apply_branding(embed)

        async for msg in interaction.channel.history(limit=30):
            if msg.author.id != self.bot.user.id:
                continue
            if not msg.embeds:
                continue
            first = msg.embeds[0]
            if first.title == "Support Tickets" and msg.components:
                await interaction.response.send_message(
                    f"Ticket panel already exists: {msg.jump_url}",
                    ephemeral=True,
                )
                return

        await interaction.channel.send(embed=embed, view=TicketCreateView())
        await interaction.response.send_message("Ticket panel sent.", ephemeral=True)

    @app_commands.command(name="close-request", description="Request owner approval to close this ticket.")
    async def close_request(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
            return
        allowed, owner_id = _can_manage_ticket(interaction)
        if not allowed:
            await interaction.response.send_message("You cannot manage this ticket.", ephemeral=True)
            return
        if owner_id is None:
            await interaction.response.send_message("Ticket owner could not be resolved.", ephemeral=True)
            return
        owner = interaction.guild.get_member(owner_id)
        if owner is None:
            try:
                owner = await interaction.guild.fetch_member(owner_id)
            except discord.HTTPException:
                owner = None
        if owner is None:
            await interaction.response.send_message("Ticket owner is no longer in this server.", ephemeral=True)
            return

        view = CloseRequestDecisionView(owner_id=owner_id)
        await interaction.response.send_message(
            f"{owner.mention}, {interaction.user.display_name} is asking to close this ticket.\n"
            "Select Accept or Deny below.",
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

    @app_commands.command(name="close", description="Close this ticket.")
    async def close(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
            return
        allowed, owner_id = _can_manage_ticket(interaction)
        if not allowed:
            await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
            return
        await _close_ticket_channel(interaction, owner_id, None)

    @app_commands.command(name="claim", description="Claim this ticket.")
    async def claim(self, interaction: discord.Interaction) -> None:
        await _claim_ticket(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketsCog(bot))


