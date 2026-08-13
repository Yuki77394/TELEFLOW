from telethon import Button
from database.database import get_media_filters, set_media_filter
from core.logger import logger

# Human-readable labels and icons for each media type
MEDIA_TYPE_META = {
    "text":       ("📝 Text",           "text"),
    "sticker":    ("🎭 Sticker",        "sticker"),
    "photo":      ("🖼️ Photo",          "photo"),
    "audio":      ("🎵 Audio",          "audio"),
    "video":      ("🎬 Video",          "video"),
    "gif":        ("🎞️ GIF",            "gif"),
    "inline_btn": ("🔘 Inline Buttons", "inline_btn"),
}


async def show_media_filters(event, source_id: int, target_id: int):
    """Display the per-link media type filter toggle menu."""
    from assistant.callbacks import get_chat_name
    from core.client import client

    src_name = await get_chat_name(client, source_id)
    tgt_name = await get_chat_name(client, target_id)

    filters = get_media_filters(source_id, target_id)

    text = (
        f"⚙️ **Media Filters**\n\n"
        f"📣 **Source:** {src_name}\n"
        f"🎯 **Target:** {tgt_name}\n\n"
        f"Toggle which message types get forwarded.\n"
        f"🟢 **ON** = forward this type  |  🔴 **OFF** = skip/drop\n\n"
        f"_If media is OFF but Text is ON → only caption is sent._\n"
        f"_If Inline Buttons is OFF → buttons are stripped from message._"
    )

    buttons = _build_filter_buttons(filters, source_id, target_id)
    await event.edit(text, buttons=buttons)


def _build_filter_buttons(filters: dict, source_id: int, target_id: int):
    """Build 2-per-row toggle button layout for all 7 media types."""
    row = []
    all_buttons = []

    type_order = ["text", "sticker", "photo", "audio", "video", "gif", "inline_btn"]

    for mtype in type_order:
        label, _ = MEDIA_TYPE_META[mtype]
        state = filters.get(mtype, 1)
        icon = "🟢" if state else "🔴"
        btn = Button.inline(
            f"{label} {icon}",
            f"mf_toggle:{source_id}:{target_id}:{mtype}".encode()
        )
        row.append(btn)
        if len(row) == 2:
            all_buttons.append(row)
            row = []

    # Append any leftover single button (inline_btn is the 7th — odd one out)
    if row:
        all_buttons.append(row)

    all_buttons.append([Button.inline("🔙 Back", f"target_detail:{source_id}:{target_id}".encode())])
    return all_buttons
