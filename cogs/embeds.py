from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from bot.branding import (
    BRANDING_FOOTER_ICON_URL,
    BRANDING_FOOTER_TEXT,
    BRANDING_IMAGE_URL,
    BRANDING_THUMBNAIL_URL,
)

log = logging.getLogger(__name__)

MAX_TITLE_LEN = 256
MAX_DESCRIPTION_LEN = 4096
MAX_FIELD_NAME_LEN = 256
MAX_FIELD_VALUE_LEN = 1024
MAX_FOOTER_TEXT_LEN = 2048
MAX_BUTTON_LABEL_LEN = 80
MAX_BUTTON_DESCRIPTION_LEN = 4096
MAX_BUTTONS = 2


@dataclass(slots=True)
class ButtonSpec:
    label: str
    response: str


def _trim_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


def _resolve_color(color: app_commands.Choice[str] | None, fallback: int = 0x000000) -> int:
    if color is None:
        return fallback
    if color.value == "dark_green":
        return 0x0B3D0B
    if color.value == "dark_blue":
        return 0x0B1E3D
    return 0x000000


def _apply_branding(embed: discord.Embed) -> discord.Embed:
    embed.set_thumbnail(url=BRANDING_THUMBNAIL_URL)
    embed.set_image(url=BRANDING_IMAGE_URL)
    embed.set_footer(text=BRANDING_FOOTER_TEXT, icon_url=BRANDING_FOOTER_ICON_URL)
    return embed


def _build_embed(
    *,
    title: str,
    description: str,
    color_value: int,
    image_url: str | None,
    thumbnail_url: str | None,
    footer_text: str | None,
    footer_icon_url: str | None,
    fields: list[tuple[str | None, str | None]],
) -> discord.Embed:
    embed = discord.Embed(
        title=_trim_text(title, MAX_TITLE_LEN),
        description=_trim_text(description, MAX_DESCRIPTION_LEN),
        color=color_value,
    )
    if image_url:
        embed.set_image(url=image_url)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    if footer_text or footer_icon_url:
        embed.set_footer(
            text=_trim_text(footer_text or "", MAX_FOOTER_TEXT_LEN) or "",
            icon_url=footer_icon_url or None,
        )
    for name, value in fields:
        if name and value:
            embed.add_field(
                name=_trim_text(name, MAX_FIELD_NAME_LEN) or "\u200b",
                value=_trim_text(value, MAX_FIELD_VALUE_LEN) or "\u200b",
                inline=False,
            )
    return embed


def _preserve_description_structure(text: str) -> str:
    # Allow users to type \n in slash command input and keep line structure in embeds.
    normalized = text.replace("\\n", "\n")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u2028", "\n").replace("\u2029", "\n")
    return normalized


async def _read_attachment_text(
    attachment: discord.Attachment,
    *,
    kind: str,
) -> tuple[str | None, str | None]:
    if attachment.size > 16_000:
        return None, f"{kind} file is too large. Keep it under 16 KB."

    try:
        raw = await attachment.read()
    except discord.HTTPException:
        return None, f"Failed to read the {kind.lower()} file."

    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding), None
        except UnicodeDecodeError:
            continue
    return None, f"Could not decode the {kind.lower()} file. Use a UTF-8 text file."


def _normalize_button_spec(label: str, response: str) -> ButtonSpec | None:
    label_final = _trim_text(label.strip(), MAX_BUTTON_LABEL_LEN)
    response_final = _trim_text(_preserve_description_structure(response).strip(), MAX_BUTTON_DESCRIPTION_LEN)
    if not label_final or not response_final:
        return None
    return ButtonSpec(label=label_final, response=response_final)


def _parse_button_spec_value(value: object) -> ButtonSpec | None:
    if isinstance(value, dict):
        label = value.get("label") or value.get("text") or value.get("name")
        response = value.get("response") or value.get("description") or value.get("content")
        if not isinstance(label, str) or not isinstance(response, str):
            return None
        return _normalize_button_spec(label, response)

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        label, response = value[0], value[1]
        if not isinstance(label, str) or not isinstance(response, str):
            return None
        return _normalize_button_spec(label, response)

    return None


def _parse_buttons_json(parsed: object) -> list[ButtonSpec] | None:
    items: list[object] = []
    if isinstance(parsed, list):
        items = list(parsed)
    elif isinstance(parsed, dict):
        buttons = parsed.get("buttons")
        if isinstance(buttons, list):
            items = list(buttons)
        else:
            for key in ("button1", "button2"):
                if key in parsed:
                    items.append(parsed[key])
            if not items:
                for index in (1, 2):
                    label = parsed.get(f"button{index}_label") or parsed.get(f"button{index}_text")
                    response = (
                        parsed.get(f"button{index}_response")
                        or parsed.get(f"button{index}_description")
                        or parsed.get(f"button{index}_content")
                    )
                    if isinstance(label, str) and isinstance(response, str):
                        items.append({"label": label, "response": response})
            if not items:
                label = parsed.get("label") or parsed.get("text") or parsed.get("name")
                response = parsed.get("response") or parsed.get("description") or parsed.get("content")
                if isinstance(label, str) and isinstance(response, str):
                    items.append({"label": label, "response": response})
    else:
        return None

    specs: list[ButtonSpec] = []
    for item in items:
        spec = _parse_button_spec_value(item)
        if spec is None:
            return None
        specs.append(spec)

    if len(specs) > MAX_BUTTONS:
        return None
    return specs


def _parse_buttons_text(text: str) -> list[ButtonSpec] | None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    if "---" in normalized:
        blocks = [block.strip() for block in normalized.split("---")]
        specs: list[ButtonSpec] = []
        for block in blocks:
            if not block:
                continue
            lines = [line.rstrip() for line in block.splitlines() if line.strip()]
            if len(lines) < 2:
                return None
            spec = _normalize_button_spec(lines[0], "\n".join(lines[1:]))
            if spec is None:
                return None
            specs.append(spec)
        if len(specs) > MAX_BUTTONS:
            return None
        return specs

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if lines and all("|" in line for line in lines):
        specs = []
        for line in lines:
            label, response = line.split("|", 1)
            spec = _normalize_button_spec(label, response)
            if spec is None:
                return None
            specs.append(spec)
        if len(specs) > MAX_BUTTONS:
            return None
        return specs

    if len(lines) >= 2:
        spec = _normalize_button_spec(lines[0], "\n".join(lines[1:]))
        if spec is not None:
            return [spec]

    return None


async def _resolve_buttons_input(
    buttons_file: discord.Attachment | None,
) -> tuple[list[ButtonSpec] | None, str | None]:
    if buttons_file is None:
        return None, None

    raw_text, error = await _read_attachment_text(buttons_file, kind="Buttons")
    if error:
        return None, error
    if raw_text is None:
        return None, "Failed to read the buttons file."

    filename = (buttons_file.filename or "").lower()
    if filename.endswith(".json"):
        try:
            parsed = json.loads(raw_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "Invalid JSON file. Use UTF-8 encoded valid JSON."
        buttons = _parse_buttons_json(parsed)
    else:
        buttons = _parse_buttons_text(raw_text)

    if buttons is None:
        return None, (
            "Buttons file must define up to 2 buttons.\n"
            "Use JSON with a `buttons` array or a text file with blocks separated by `---`."
        )
    return buttons, None


def _button_view_signature(buttons: list[ButtonSpec]) -> str:
    payload = json.dumps(
        [{"label": button.label, "response": button.response} for button in buttons],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


async def _resolve_description_input(
    description: str,
    description_file: discord.Attachment | None,
) -> tuple[str | None, str | None]:
    if description_file is None:
        return _preserve_description_structure(description), None

    raw_text, error = await _read_attachment_text(description_file, kind="Description")
    if error:
        return None, error
    if raw_text is None:
        return None, "Failed to read the description file."

    filename = (description_file.filename or "").lower()
    if filename.endswith(".json"):
        try:
            parsed = json.loads(raw_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "Invalid JSON file. Use UTF-8 encoded valid JSON."

        desc_value: str | None = None
        if isinstance(parsed, dict):
            if isinstance(parsed.get("description"), str):
                desc_value = parsed["description"]
            elif isinstance(parsed.get("embed"), dict) and isinstance(parsed["embed"].get("description"), str):
                desc_value = parsed["embed"]["description"]

        if not desc_value:
            return None, "JSON must include `description` or `embed.description` as a string."

        normalized = _preserve_description_structure(desc_value)
        return normalized[:MAX_DESCRIPTION_LEN], None

    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized[:MAX_DESCRIPTION_LEN], None


def _embed_fields_by_index(existing: discord.Embed, idx: int) -> tuple[str | None, str | None]:
    if idx < len(existing.fields):
        fld = existing.fields[idx]
        return fld.name, fld.value
    return None, None


class EmbedButtonItem(discord.ui.Button):
    def __init__(self, *, message_id: int, index: int, spec: ButtonSpec, signature: str):
        super().__init__(
            label=spec.label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"embedbtn:{message_id}:{signature}:{index}",
        )
        self.response_text = spec.response

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title=self.label or "Button response",
            description=_trim_text(self.response_text, MAX_BUTTON_DESCRIPTION_LEN),
            color=0x0B1E3D,
        )
        await interaction.response.send_message(embed=_apply_branding(embed), ephemeral=True)


class EmbedButtonView(discord.ui.View):
    def __init__(self, *, message_id: int, button_specs: list[ButtonSpec]):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.button_specs = button_specs
        self.signature = _button_view_signature(button_specs)
        for index, spec in enumerate(button_specs, start=1):
            self.add_item(
                EmbedButtonItem(
                    message_id=message_id,
                    index=index,
                    spec=spec,
                    signature=self.signature,
                )
            )


class EmbedsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            rows = await self.bot.db.fetch_embed_message_button_rows()
        except Exception:
            log.exception("Failed to load persisted embed buttons.")
            return

        for row in rows:
            try:
                message_id = int(row["message_id"])
                buttons_json = str(row["buttons_json"])
            except (KeyError, TypeError, ValueError):
                log.warning("Skipping malformed embed button row: %r", row)
                continue

            try:
                parsed = json.loads(buttons_json)
            except json.JSONDecodeError:
                log.warning("Skipping invalid button JSON for message %s.", message_id)
                continue

            button_specs = _parse_buttons_json(parsed)
            if not button_specs:
                log.warning("Skipping empty or invalid button spec set for message %s.", message_id)
                continue

            try:
                self.bot.add_view(
                    EmbedButtonView(message_id=message_id, button_specs=button_specs),
                    message_id=message_id,
                )
            except Exception:
                log.exception("Failed to register persistent buttons for message %s.", message_id)

    async def _get_channel_by_id(
        self, guild: discord.Guild, channel_id: int
    ) -> discord.abc.GuildChannel | None:
        channel = guild.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
        return channel

    def _has_send_permission(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        role_id = self.bot.config.role_id_send
        return bool(role_id and interaction.user.get_role(role_id))

    def _bot_can_send_embeds(
        self,
        guild: discord.Guild,
        channel: discord.abc.GuildChannel,
    ) -> tuple[bool, str | None]:
        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            return False, "That channel is not a valid text/announcement channel."
        me = guild.me
        if me is None:
            return False, "Bot member not found in this server."
        perms = channel.permissions_for(me)
        if not perms.send_messages:
            return False, "I do not have permission to send messages in that channel."
        if not perms.embed_links:
            return False, "I do not have permission to embed links in that channel."
        return True, None

    def _humanize_http_error(self, error: discord.HTTPException) -> str:
        details = getattr(error, "text", None) or str(error)
        return f"Failed to send the embed. Discord API error ({error.status}): {details}"

    @staticmethod
    def _serialize_button_specs(button_specs: list[ButtonSpec]) -> str:
        return json.dumps(
            [{"label": button.label, "response": button.response} for button in button_specs],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _make_button_view(self, message_id: int, button_specs: list[ButtonSpec]) -> EmbedButtonView:
        return EmbedButtonView(message_id=message_id, button_specs=button_specs)

    async def _persist_button_specs_for_message(
        self,
        *,
        message: discord.Message,
        button_specs: list[ButtonSpec],
    ) -> None:
        if message.guild is None:
            log.warning("Cannot persist button specs for message %s without a guild.", message.id)
            return
        await self.bot.db.upsert_embed_message_buttons(
            message_id=message.id,
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            buttons_json=self._serialize_button_specs(button_specs),
        )

    async def _clear_button_specs_for_message(self, message_id: int) -> None:
        await self.bot.db.delete_embed_message_buttons(message_id)

    def _build_send_embed(
        self,
        *,
        title: str,
        description: str,
        color: app_commands.Choice[str] | None,
        image_url: str | None,
        thumbnail_url: str | None,
        footer_text: str | None,
        footer_icon_url: str | None,
        fields: list[tuple[str | None, str | None]],
    ) -> discord.Embed:
        return _apply_branding(
            _build_embed(
                title=title,
                description=_preserve_description_structure(description),
                color_value=_resolve_color(color, fallback=0x000000),
                image_url=image_url,
                thumbnail_url=thumbnail_url,
                footer_text=footer_text,
                footer_icon_url=footer_icon_url,
                fields=fields,
            )
        )

    @app_commands.command(name="say", description="Send a plain message to a channel")
    @app_commands.describe(
        channel="Where to send the message (picker)",
        channel_id="Channel ID to send the message (paste ID)",
        message="Message content",
    )
    async def say(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        channel_id: str | None,
        message: str,
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if not self._has_send_permission(interaction):
            await interaction.followup.send(
                "You do NOT have permission to use this command.",
                ephemeral=True,
            )
            return
        if interaction.guild is None:
            await interaction.followup.send(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        target_channel: discord.abc.GuildChannel | None = channel
        if target_channel is None:
            if not channel_id or not channel_id.isdigit():
                await interaction.followup.send(
                    "Provide a channel or a numeric channel_id.",
                    ephemeral=True,
                )
                return
            target_channel = await self._get_channel_by_id(interaction.guild, int(channel_id))

        if not isinstance(target_channel, (discord.TextChannel, discord.ForumChannel)):
            await interaction.followup.send(
                "That channel is not a valid text/announcement channel.",
                ephemeral=True,
            )
            return

        try:
            await target_channel.send(content=message)
        except discord.HTTPException:
            await interaction.followup.send(
                "Failed to send the message.",
                ephemeral=True,
            )
            return

        await interaction.followup.send("Message sent.", ephemeral=True)

    @app_commands.command(name="restore", description="Edit a bot embed by message link")
    @app_commands.describe(
        message_link="Link to the message you want to edit",
        title="Embed title (optional; defaults to current embed title)",
        description="Embed description (optional; defaults to current embed description)",
        description_file="Optional .txt/.json file for description (overrides description text)",
        buttons_file="Optional .txt/.json file for up to 2 buttons",
        color="Embed color",
        image_url="Main image URL",
        thumbnail_url="Thumbnail image URL",
        footer_text="Footer text",
        footer_icon_url="Footer icon URL",
        field1_name="Field 1 name",
        field1_value="Field 1 value",
        field2_name="Field 2 name",
        field2_value="Field 2 value",
        field3_name="Field 3 name",
        field3_value="Field 3 value",
        field4_name="Field 4 name",
        field4_value="Field 4 value",
        field5_name="Field 5 name",
        field5_value="Field 5 value",
        field6_name="Field 6 name",
        field6_value="Field 6 value",
    )
    @app_commands.choices(
        color=[
            app_commands.Choice(name="Black", value="black"),
            app_commands.Choice(name="Dark Green", value="dark_green"),
            app_commands.Choice(name="Dark Blue", value="dark_blue"),
        ]
    )
    async def restore(
        self,
        interaction: discord.Interaction,
        message_link: str,
        title: str | None = None,
        description: str | None = None,
        description_file: discord.Attachment | None = None,
        buttons_file: discord.Attachment | None = None,
        color: app_commands.Choice[str] | None = None,
        image_url: str | None = None,
        thumbnail_url: str | None = None,
        footer_text: str | None = None,
        footer_icon_url: str | None = None,
        field1_name: str | None = None,
        field1_value: str | None = None,
        field2_name: str | None = None,
        field2_value: str | None = None,
        field3_name: str | None = None,
        field3_value: str | None = None,
        field4_name: str | None = None,
        field4_value: str | None = None,
        field5_name: str | None = None,
        field5_value: str | None = None,
        field6_name: str | None = None,
        field6_value: str | None = None,
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        match = re.search(r"/channels/(\d+)/(\d+)/(\d+)", message_link)
        if not match:
            await interaction.followup.send(
                "Invalid message link. Please paste the full Discord message link.",
                ephemeral=True,
            )
            return

        guild_id, channel_id, message_id = map(int, match.groups())
        if guild_id != interaction.guild.id:
            await interaction.followup.send(
                "That message link is from a different server.",
                ephemeral=True,
            )
            return

        channel = await self._get_channel_by_id(interaction.guild, channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "That message is not in a text channel I can access.",
                ephemeral=True,
            )
            return

        try:
            message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            await interaction.followup.send(
                "I couldn't find that message.",
                ephemeral=True,
            )
            return

        if message.author.id != self.bot.user.id:
            await interaction.followup.send(
                "I can only edit messages sent by this bot.",
                ephemeral=True,
            )
            return
        if not message.embeds:
            await interaction.followup.send(
                "That message has no embed to restore from.",
                ephemeral=True,
            )
            return

        current = message.embeds[0]

        title_final = title if title is not None else (current.title or "")
        description_final = description if description is not None else (current.description or "")
        image_final = image_url if image_url is not None else (current.image.url if current.image else None)
        thumb_final = (
            thumbnail_url if thumbnail_url is not None else (current.thumbnail.url if current.thumbnail else None)
        )
        footer_text_final = footer_text if footer_text is not None else (current.footer.text if current.footer else None)
        footer_icon_final = (
            footer_icon_url
            if footer_icon_url is not None
            else (current.footer.icon_url if current.footer else None)
        )
        description_input, description_error = await _resolve_description_input(
            description=description if description is not None else (current.description or ""),
            description_file=description_file,
        )
        if description_error:
            await interaction.followup.send(description_error, ephemeral=True)
            return

        button_specs, buttons_error = await _resolve_buttons_input(buttons_file)
        if buttons_error:
            await interaction.followup.send(buttons_error, ephemeral=True)
            return

        old1n, old1v = _embed_fields_by_index(current, 0)
        old2n, old2v = _embed_fields_by_index(current, 1)
        old3n, old3v = _embed_fields_by_index(current, 2)
        old4n, old4v = _embed_fields_by_index(current, 3)
        old5n, old5v = _embed_fields_by_index(current, 4)
        old6n, old6v = _embed_fields_by_index(current, 5)

        field1_name_final = field1_name if field1_name is not None else old1n
        field1_value_final = field1_value if field1_value is not None else old1v
        field2_name_final = field2_name if field2_name is not None else old2n
        field2_value_final = field2_value if field2_value is not None else old2v
        field3_name_final = field3_name if field3_name is not None else old3n
        field3_value_final = field3_value if field3_value is not None else old3v
        field4_name_final = field4_name if field4_name is not None else old4n
        field4_value_final = field4_value if field4_value is not None else old4v
        field5_name_final = field5_name if field5_name is not None else old5n
        field5_value_final = field5_value if field5_value is not None else old5v
        field6_name_final = field6_name if field6_name is not None else old6n
        field6_value_final = field6_value if field6_value is not None else old6v

        embed = self._build_send_embed(
            title=title_final,
            description=description_input or "",
            color=color,
            image_url=image_final,
            thumbnail_url=thumb_final,
            footer_text=footer_text_final,
            footer_icon_url=footer_icon_final,
            fields=[
                (field1_name_final, field1_value_final),
                (field2_name_final, field2_value_final),
                (field3_name_final, field3_value_final),
                (field4_name_final, field4_value_final),
                (field5_name_final, field5_value_final),
                (field6_name_final, field6_value_final),
            ],
        )

        try:
            if buttons_file is not None:
                if button_specs:
                    view = self._make_button_view(message.id, button_specs)
                    await message.edit(embed=embed, view=view)
                    try:
                        await self._persist_button_specs_for_message(message=message, button_specs=button_specs)
                    except Exception:
                        log.exception("Failed to persist button specs for message %s.", message.id)
                else:
                    await message.edit(embed=embed, view=None)
                    try:
                        await self._clear_button_specs_for_message(message.id)
                    except Exception:
                        log.exception("Failed to clear button specs for message %s.", message.id)
            else:
                await message.edit(embed=embed)
        except discord.HTTPException:
            await interaction.followup.send(
                "Failed to edit that message.",
                ephemeral=True,
            )
            return

        await interaction.followup.send("Message updated.", ephemeral=True)

    @app_commands.command(name="send-message", description="Send a custom embed to a channel")
    @app_commands.describe(
        channel="Where to send the embed (picker)",
        channel_id="Channel ID to send the embed (paste ID)",
        title="Embed title",
        description="Embed description",
        description_file="Optional .txt/.json file for description (overrides description text)",
        buttons_file="Optional .txt/.json file for up to 2 buttons",
        color="Embed color",
        image_url="Main image URL",
        thumbnail_url="Thumbnail image URL",
        footer_text="Footer text",
        footer_icon_url="Footer icon URL",
        field1_name="Field 1 name",
        field1_value="Field 1 value",
        field2_name="Field 2 name",
        field2_value="Field 2 value",
        field3_name="Field 3 name",
        field3_value="Field 3 value",
        field4_name="Field 4 name",
        field4_value="Field 4 value",
        field5_name="Field 5 name",
        field5_value="Field 5 value",
        field6_name="Field 6 name",
        field6_value="Field 6 value",
    )
    @app_commands.choices(
        color=[
            app_commands.Choice(name="Black", value="black"),
            app_commands.Choice(name="Dark Green", value="dark_green"),
            app_commands.Choice(name="Dark Blue", value="dark_blue"),
        ]
    )
    async def send_message(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        channel_id: str | None,
        title: str,
        description: str | None = None,
        description_file: discord.Attachment | None = None,
        buttons_file: discord.Attachment | None = None,
        color: app_commands.Choice[str] | None = None,
        image_url: str | None = None,
        thumbnail_url: str | None = None,
        footer_text: str | None = None,
        footer_icon_url: str | None = None,
        field1_name: str | None = None,
        field1_value: str | None = None,
        field2_name: str | None = None,
        field2_value: str | None = None,
        field3_name: str | None = None,
        field3_value: str | None = None,
        field4_name: str | None = None,
        field4_value: str | None = None,
        field5_name: str | None = None,
        field5_value: str | None = None,
        field6_name: str | None = None,
        field6_value: str | None = None,
    ) -> None:
        if not self._has_send_permission(interaction):
            no_perm = discord.Embed(
                title="No permission.",
                description=(
                    "You do NOT have permission to use this command.\n"
                    "Please open an Executive Support ticket."
                ),
                color=0xD63324,
            )
            await interaction.response.send_message(embed=_apply_branding(no_perm), ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        target_channel: discord.abc.GuildChannel | None = channel
        if target_channel is None:
            if not channel_id or not channel_id.isdigit():
                await interaction.response.send_message(
                    "Provide a channel or a numeric channel_id.",
                    ephemeral=True,
                )
                return
            target_channel = await self._get_channel_by_id(interaction.guild, int(channel_id))

        if not isinstance(target_channel, (discord.TextChannel, discord.ForumChannel)):
            await interaction.response.send_message(
                "That channel is not a valid text/announcement channel.",
                ephemeral=True,
            )
            return
        ok, reason = self._bot_can_send_embeds(interaction.guild, target_channel)
        if not ok:
            await interaction.response.send_message(reason or "Cannot send embed to that channel.", ephemeral=True)
            return

        fields = [
            (field1_name, field1_value),
            (field2_name, field2_value),
            (field3_name, field3_value),
            (field4_name, field4_value),
            (field5_name, field5_value),
            (field6_name, field6_value),
        ]

        button_specs, buttons_error = await _resolve_buttons_input(buttons_file)
        if buttons_error:
            await interaction.response.send_message(buttons_error, ephemeral=True)
            return

        if description is None and description_file is None:
            await interaction.response.send_modal(
                SendMessageDescriptionModal(
                    cog=self,
                    target_channel=target_channel,
                    title=title,
                    color=color,
                    image_url=image_url,
                    thumbnail_url=thumbnail_url,
                    footer_text=footer_text,
                    footer_icon_url=footer_icon_url,
                    fields=fields,
                    button_specs=button_specs,
                )
            )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        final_description, description_error = await _resolve_description_input(
            description=description or "",
            description_file=description_file,
        )
        if description_error:
            await interaction.followup.send(description_error, ephemeral=True)
            return

        embed = self._build_send_embed(
            title=title,
            description=final_description or "",
            color=color,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
            footer_text=footer_text,
            footer_icon_url=footer_icon_url,
            fields=fields,
        )
        try:
            message = await target_channel.send(embed=embed)
            if button_specs:
                view = self._make_button_view(message.id, button_specs)
                try:
                    await message.edit(view=view)
                except discord.HTTPException:
                    log.exception("Failed to attach buttons to sent embed message %s.", message.id)
                    await interaction.followup.send(
                        "Embed sent, but I couldn't attach the buttons.",
                        ephemeral=True,
                    )
                    return
                try:
                    await self._persist_button_specs_for_message(message=message, button_specs=button_specs)
                except Exception:
                    log.exception("Failed to persist button specs for message %s.", message.id)
        except discord.HTTPException as exc:
            await interaction.followup.send(
                self._humanize_http_error(exc),
                ephemeral=True,
            )
            return

        await interaction.followup.send("Embed sent.", ephemeral=True)


class SendMessageDescriptionModal(discord.ui.Modal, title="Embed Description"):
    description_input = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000,
        placeholder="Paste your multiline description here...",
    )

    def __init__(
        self,
        *,
        cog: EmbedsCog,
        target_channel: discord.abc.GuildChannel,
        title: str,
        color: app_commands.Choice[str] | None,
        image_url: str | None,
        thumbnail_url: str | None,
        footer_text: str | None,
        footer_icon_url: str | None,
        fields: list[tuple[str | None, str | None]],
        button_specs: list[ButtonSpec] | None,
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.target_channel = target_channel
        self.title_text = title
        self.color_choice = color
        self.image_url = image_url
        self.thumbnail_url = thumbnail_url
        self.footer_text = footer_text
        self.footer_icon_url = footer_icon_url
        self.fields = fields
        self.button_specs = button_specs or []

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if not isinstance(self.target_channel, (discord.TextChannel, discord.ForumChannel)):
            await interaction.followup.send("Target channel is no longer valid.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        ok, reason = self.cog._bot_can_send_embeds(interaction.guild, self.target_channel)
        if not ok:
            await interaction.followup.send(reason or "Cannot send embed to that channel.", ephemeral=True)
            return
        embed = self.cog._build_send_embed(
            title=self.title_text,
            description=str(self.description_input),
            color=self.color_choice,
            image_url=self.image_url,
            thumbnail_url=self.thumbnail_url,
            footer_text=self.footer_text,
            footer_icon_url=self.footer_icon_url,
            fields=self.fields,
        )
        try:
            message = await self.target_channel.send(embed=embed)
            if self.button_specs:
                view = self.cog._make_button_view(message.id, self.button_specs)
                try:
                    await message.edit(view=view)
                except discord.HTTPException:
                    log.exception("Failed to attach buttons to sent embed message %s.", message.id)
                    await interaction.followup.send(
                        "Embed sent, but I couldn't attach the buttons.",
                        ephemeral=True,
                    )
                    return
                try:
                    await self.cog._persist_button_specs_for_message(
                        message=message,
                        button_specs=self.button_specs,
                    )
                except Exception:
                    log.exception("Failed to persist button specs for message %s.", message.id)
        except discord.HTTPException as exc:
            await interaction.followup.send(self.cog._humanize_http_error(exc), ephemeral=True)
            return
        await interaction.followup.send("Embed sent.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EmbedsCog(bot))
