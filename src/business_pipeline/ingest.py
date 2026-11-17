"""Step 1: turns every source in the data folder into posts.

Sources it recognises:
- A folder with `_chat.txt`: a WhatsApp export. Its zip copy is ignored.
- A `.tsv` file: one post per row, with hand-written annotations and the images it names.
- Any other media file: an orphan post, so nothing in the folder is silently dropped.
"""

import argparse
import csv
import json
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .files import is_ignored, make_attachment
from .models import Attachment, Post
from .whatsapp import group_into_posts, parse_chat

EXPORT_PREFIX = "WhatsApp Chat - "
ROOT_LABEL = "(raíz)"

# Spanish TSV headers (accents and case ignored) -> annotation keys. Unknown headers are kept as is.
TSV_HEADERS = {
    "imagenes": "images",
    "nombre": "name",
    "compartido en": "shared_in",
    "ubicacion": "location",
    "telefono": "phone",
    "whatsapp": "phone",
    "categoria": "category",
    "texto del mensaje": "text",
    "fecha": "date",
    "remitente": "sender",
    "notas": "notes",
}


@dataclass
class IngestResult:
    posts: list[Post] = field(default_factory=list)
    unresolved_references: list[str] = field(default_factory=list)
    ignored_files: list[str] = field(default_factory=list)
    duplicate_files: list[list[str]] = field(default_factory=list)
    system_messages: int = 0


def normalize_header(header: str) -> str:
    decomposed = unicodedata.normalize("NFD", header.strip().lower())
    plain = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return TSV_HEADERS.get(plain, plain)


def channel_of_export(folder: Path) -> str:
    return folder.name.removeprefix(EXPORT_PREFIX).strip()


def read_exports(root: Path, export_dirs: list[Path], result: IngestResult) -> None:
    for folder in export_dirs:
        messages = parse_chat((folder / "_chat.txt").read_text(encoding="utf-8"))
        result.system_messages += sum(m.is_system for m in messages)
        for chat_post in group_into_posts(messages, channel_of_export(folder)):
            result.posts.append(
                Post(
                    post_id=chat_post.post_id,
                    source="whatsapp",
                    channel=chat_post.channel,
                    timestamp=chat_post.timestamp,
                    sender=chat_post.sender,
                    text=chat_post.text,
                    attachments=[make_attachment(folder / name, root) for name in chat_post.attachments],
                    omitted=chat_post.omitted,
                )
            )


def resolve_reference(reference: str, candidates: list[Path]) -> Path | None:
    """Matches by exact name first, then by name without extension (the TSV often omits it)."""
    wanted = reference.strip().lower()
    for path in candidates:
        if path.name.lower() == wanted:
            return path
    for path in candidates:
        if path.stem.lower() == wanted:
            return path
    return None


def read_tsv(root: Path, tsv: Path, candidates: list[Path], result: IngestResult) -> set[Path]:
    referenced: set[Path] = set()
    with tsv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows:
        return referenced
    headers = [normalize_header(header) for header in rows[0]]

    for row in rows[1:]:
        values = {key: value.strip() for key, value in zip(headers, row) if value.strip()}
        references = [r for r in values.pop("images", "").split(",") if r.strip()]
        attachments: list[Attachment] = []
        for reference in references:
            path = resolve_reference(reference, candidates)
            if path is None:
                result.unresolved_references.append(reference.strip())
                attachments.append(Attachment(path=reference.strip(), kind="missing", sha256=None))
            else:
                referenced.add(path)
                attachments.append(make_attachment(path, root))

        key = Path(references[0].strip()).stem if references else values.get("name", "sin-imagen")
        result.posts.append(
            Post(
                post_id=f"tsv:{tsv.relative_to(root).as_posix()}:{key}",
                source="tsv",
                channel=values.get("shared_in", tsv.stem),
                timestamp=values.get("date"),
                sender=values.get("sender"),
                text=values.get("text", ""),
                attachments=attachments,
                annotations=values,
            )
        )
    return referenced


def find_duplicates(posts: list[Post]) -> list[list[str]]:
    paths_by_hash: dict[str, set[str]] = defaultdict(set)
    for post in posts:
        for attachment in post.attachments:
            if attachment.sha256:
                paths_by_hash[attachment.sha256].add(attachment.path)
    return sorted(sorted(paths) for paths in paths_by_hash.values() if len(paths) > 1)


def ingest(root: Path) -> IngestResult:
    result = IngestResult()
    all_files = sorted(p for p in root.rglob("*") if p.is_file())
    export_dirs = sorted({p.parent for p in all_files if p.name == "_chat.txt"})

    def inside_export(path: Path) -> bool:
        return any(folder in path.parents for folder in export_dirs)

    loose_media = [p for p in all_files if not inside_export(p) and not is_ignored(p)]
    result.ignored_files = [
        p.relative_to(root).as_posix()
        for p in all_files
        if not inside_export(p) and p.suffix.lower() == ".zip"
    ]

    read_exports(root, export_dirs, result)

    referenced: set[Path] = set()
    for tsv in (p for p in all_files if p.suffix.lower() == ".tsv" and not inside_export(p)):
        referenced |= read_tsv(root, tsv, loose_media, result)

    for path in loose_media:
        if path in referenced:
            continue
        attachment = make_attachment(path, root)
        folder = path.parent.relative_to(root).as_posix()
        result.posts.append(
            Post(
                post_id=f"image:{attachment.sha256[:16]}",
                source="orphan_image",
                channel=ROOT_LABEL if folder == "." else folder,
                timestamp=None,
                sender=None,
                text="",
                attachments=[attachment],
            )
        )

    result.duplicate_files = find_duplicates(result.posts)
    return result


def write_report(result: IngestResult, path: Path) -> None:
    by_source: dict[str, int] = defaultdict(int)
    by_kind: dict[str, int] = defaultdict(int)
    for post in result.posts:
        by_source[post.source] += 1
        for attachment in post.attachments:
            by_kind[attachment.kind] += 1
    omitted = sum(len(post.omitted) for post in result.posts)

    lines = ["# Ingest report", "", "## Posts by source"]
    lines += [f"- {source}: {count}" for source, count in sorted(by_source.items())]
    lines += ["", "## Attachments by kind"]
    lines += [f"- {kind}: {count}" for kind, count in sorted(by_kind.items())]
    lines += ["", f"System messages skipped: {result.system_messages}", f"Media omitted by WhatsApp: {omitted}"]
    lines += ["", "## TSV references not found"] + [f"- {r}" for r in result.unresolved_references or ["(none)"]]
    lines += ["", "## Identical files in several places"]
    lines += [f"- {' = '.join(group)}" for group in result.duplicate_files] or ["- (none)"]
    lines += ["", "## Ignored files"] + [f"- {f}" for f in result.ignored_files or ["(none)"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 1: build posts from the raw data folder.")
    parser.add_argument("data_root", type=Path, help="Folder with chat exports, images and TSV files")
    parser.add_argument("--out", type=Path, default=Path("output"), help="Output folder (default: output)")
    args = parser.parse_args()

    result = ingest(args.data_root.resolve())
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "posts.jsonl").open("w", encoding="utf-8") as handle:
        for post in result.posts:
            handle.write(json.dumps(asdict(post), ensure_ascii=False) + "\n")
    write_report(result, args.out / "ingest_report.md")
    print(f"{len(result.posts)} posts -> {args.out / 'posts.jsonl'}")
    print(f"Report -> {args.out / 'ingest_report.md'}")


if __name__ == "__main__":
    main()
