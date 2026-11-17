"""Data shapes shared by every pipeline step."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Attachment:
    path: str  # relative to the data root
    kind: str  # image | video | pdf | contact | other | missing
    sha256: str | None  # None when the file is missing


@dataclass
class Post:
    """One unit of evidence: a message (or a TSV row, or a lone image) plus its files."""

    post_id: str  # stable across re-runs and overlapping exports
    source: str  # whatsapp | tsv | orphan_image
    channel: str  # chat name, TSV "shared in" value, or folder of a lone image
    timestamp: str | None  # ISO 8601, local time, when known
    sender: str | None  # WhatsApp display name, when known
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)  # media WhatsApp left out of the export
    annotations: dict[str, str] = field(default_factory=dict)  # hand-written TSV fields
