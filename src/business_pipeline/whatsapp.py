"""Reads a WhatsApp chat export (`_chat.txt`, iOS, Spanish) into messages, then into posts.

A post is a burst of messages from one sender: WhatsApp sends each photo as its own message,
so a flyer, a second photo and the caption usually arrive as three lines seconds apart.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

# Invisible characters WhatsApp adds: direction marks and isolates around numbers and @mentions.
INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‬⁦⁧⁨⁩"))
SPACES = {ord(" "): " ", ord(" "): " "}

MESSAGE_START = re.compile(
    r"^‎?\[(\d{1,2})/(\d{1,2})/(\d{2,4}), (\d{1,2}):(\d{2}):(\d{2})\] ([^:]+?): ?(.*)$"
)
ATTACHMENT = re.compile(r"‎?<adjunto: ([^>]+)>")
EDITED = re.compile(r"‎?<Se editó este mensaje\.>")
OMITTED = re.compile(
    r"(?:(\S+\.\w{2,4})\s+)?‎?(video|imagen|audio|sticker|documento|gif|contacto) omitid[oa]",
    re.IGNORECASE,
)
DELETED = "Se eliminó este mensaje."

# Seconds between two messages of the same sender that still belong to one post.
POST_WINDOW_SECONDS = 120


@dataclass
class ChatMessage:
    timestamp: str
    sender: str
    text: str
    attachments: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    is_system: bool = False


@dataclass
class ChatPost:
    post_id: str
    channel: str
    timestamp: str
    sender: str
    text: str
    attachments: list[str]
    omitted: list[str]


def clean(value: str) -> str:
    return value.translate(INVISIBLE).translate(SPACES)


def clean_sender(raw: str) -> str:
    return clean(raw).removeprefix("~").strip()


def to_iso(day: str, month: str, year: str, hour: str, minute: str, second: str) -> str:
    full_year = int(year) + 2000 if len(year) == 2 else int(year)
    moment = datetime(full_year, int(month), int(day), int(hour), int(minute), int(second))
    return moment.isoformat()


def build_message(timestamp: str, sender: str, raw_body: str) -> ChatMessage:
    attachments = ATTACHMENT.findall(raw_body)
    body = EDITED.sub("", ATTACHMENT.sub("", raw_body))
    omitted = [clean(match.group(0)).strip() for match in OMITTED.finditer(body)]
    body = OMITTED.sub("", body)
    text = clean(body).strip()
    # System lines start with a direction mark and carry no media: "X añadió a Y", "Se activaron...".
    is_system = text == DELETED or (
        raw_body.startswith("‎") and not attachments and not omitted
    )
    return ChatMessage(timestamp, clean_sender(sender), text, attachments, omitted, is_system)


def parse_chat(content: str) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    current: tuple[str, str, list[str]] | None = None

    def flush() -> None:
        if current is not None:
            timestamp, sender, lines = current
            messages.append(build_message(timestamp, sender, "\n".join(lines)))

    for line in content.splitlines():
        match = MESSAGE_START.match(line)
        if match:
            flush()
            *date_parts, sender, body = match.groups()
            current = (to_iso(*date_parts), sender, [body])
        elif current is not None:
            current[2].append(line)  # continuation of a multi-line message
    flush()
    return messages


def seconds_between(earlier: str, later: str) -> float:
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()


def group_into_posts(messages: list[ChatMessage], channel: str) -> list[ChatPost]:
    content = sorted((m for m in messages if not m.is_system), key=lambda m: m.timestamp)
    groups: list[list[ChatMessage]] = []
    for message in content:
        last_group = groups[-1] if groups else None
        if (
            last_group
            and last_group[-1].sender == message.sender
            and seconds_between(last_group[-1].timestamp, message.timestamp) <= POST_WINDOW_SECONDS
        ):
            last_group.append(message)
        else:
            groups.append([message])

    return [
        ChatPost(
            post_id=f"whatsapp:{channel}:{group[0].timestamp}:{group[0].sender}",
            channel=channel,
            timestamp=group[0].timestamp,
            sender=group[0].sender,
            text="\n".join(m.text for m in group if m.text),
            attachments=[a for m in group for a in m.attachments],
            omitted=[o for m in group for o in m.omitted],
        )
        for group in groups
    ]
