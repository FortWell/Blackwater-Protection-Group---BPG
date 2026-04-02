from __future__ import annotations

from typing import Any

import discord


def _format_template(text: str, context: dict[str, Any] | None) -> str:
    if not context or "{" not in text:
        return text
    try:
        return text.format(**context)
    except Exception:
        return text


def apply_embed_template(
    embed: discord.Embed,
    template: dict[str, Any] | None,
    *,
    context: dict[str, Any] | None = None,
) -> discord.Embed:
    if not template:
        return embed

    title = template.get("title")
    if title is not None:
        embed.title = _format_template(str(title), context)
    description = template.get("description")
    if description is not None:
        embed.description = _format_template(str(description), context)
    color = template.get("color")
    if color is not None:
        embed.color = color

    author_text = template.get("author_text")
    author_url = template.get("author_url")
    author_icon_url = template.get("author_icon_url")
    if author_text or author_url or author_icon_url:
        embed.set_author(
            name=_format_template(str(author_text), context) if author_text else discord.Embed.Empty,
            url=_format_template(str(author_url), context) if author_url else discord.Embed.Empty,
            icon_url=_format_template(str(author_icon_url), context) if author_icon_url else discord.Embed.Empty,
        )

    thumbnail_url = template.get("thumbnail_url")
    if thumbnail_url is not None:
        if thumbnail_url:
            embed.set_thumbnail(url=_format_template(str(thumbnail_url), context))
        else:
            embed.set_thumbnail(url=discord.Embed.Empty)

    image_url = template.get("image_url")
    if image_url is not None:
        if image_url:
            embed.set_image(url=_format_template(str(image_url), context))
        else:
            embed.set_image(url=discord.Embed.Empty)

    footer_text = template.get("footer_text")
    footer_icon_url = template.get("footer_icon_url")
    if footer_text or footer_icon_url:
        embed.set_footer(
            text=_format_template(str(footer_text), context) if footer_text else discord.Embed.Empty,
            icon_url=_format_template(str(footer_icon_url), context) if footer_icon_url else discord.Embed.Empty,
        )

    fields = template.get("fields")
    if fields:
        if template.get("replace_fields"):
            embed.clear_fields()
        for field in fields:
            name = _format_template(str(field.get("name", "")).strip(), context)
            value = _format_template(str(field.get("value", "")).strip(), context)
            if not name or not value:
                continue
            inline = bool(field.get("inline", False))
            embed.add_field(name=name[:256], value=value[:1024], inline=inline)

    return embed
