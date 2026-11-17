import tempfile
import unittest
from pathlib import Path

from business_pipeline.ingest import ingest

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


class IngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Loose images annotated by hand, one referenced without its extension.
        write(self.root / "PHOTO-1.jpg", JPEG)
        write(self.root / "PHOTO-2.jpg", JPEG + b"2")
        write(self.root / "extensionless", JPEG + b"3")
        write(self.root / "orphan.PNG", PNG)
        write(self.root / "Soledad" / "far.png", PNG + b"far")
        write(
            self.root / "negocios.tsv",
            "Imagenes\tNombre\tCompartido en\tUbicacion\tTelefono\n"
            "PHOTO-1.jpg, PHOTO-2\tKuki Club\tChat Esmeralda\tConjunto Esmeralda\t570 000 0002\n"
            "extensionless, missing.jpg\t\tChat Esmeralda\t\t\n",
        )
        # A WhatsApp export, its zip copy, and a duplicate of a loose image.
        export = self.root / "WhatsApp Chat - BARRIO"
        write(export / "_chat.txt", "‎[31/10/26, 7:39:10] ~ Ana: Hola ‎<adjunto: 0001-PHOTO-a.jpg>\n")
        write(export / "0001-PHOTO-a.jpg", JPEG)  # same bytes as PHOTO-1.jpg
        write(self.root / "WhatsApp Chat - BARRIO.zip", b"PK")
        self.result = ingest(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def posts_from(self, source: str):
        return [p for p in self.result.posts if p.source == source]

    def test_reads_whatsapp_export_with_channel_from_folder_name(self) -> None:
        [post] = self.posts_from("whatsapp")
        self.assertEqual(post.channel, "BARRIO")
        self.assertEqual(post.attachments[0].path, "WhatsApp Chat - BARRIO/0001-PHOTO-a.jpg")
        self.assertEqual(post.attachments[0].kind, "image")

    def test_turns_each_tsv_row_into_a_post_with_annotations(self) -> None:
        kuki = [p for p in self.posts_from("tsv") if p.annotations.get("name") == "Kuki Club"][0]
        self.assertEqual(kuki.channel, "Chat Esmeralda")
        self.assertEqual(kuki.annotations["phone"], "570 000 0002")
        self.assertEqual(kuki.annotations["location"], "Conjunto Esmeralda")

    def test_resolves_tsv_references_missing_their_extension(self) -> None:
        kuki = [p for p in self.posts_from("tsv") if p.annotations.get("name") == "Kuki Club"][0]
        self.assertEqual([a.path for a in kuki.attachments], ["PHOTO-1.jpg", "PHOTO-2.jpg"])

    def test_detects_kind_of_files_without_extension(self) -> None:
        unnamed = [p for p in self.posts_from("tsv") if not p.annotations.get("name")][0]
        self.assertEqual(unnamed.attachments[0].kind, "image")

    def test_reports_tsv_references_that_do_not_exist(self) -> None:
        self.assertIn("missing.jpg", self.result.unresolved_references)

    def test_turns_unreferenced_images_into_orphan_posts_with_their_folder(self) -> None:
        orphans = {p.attachments[0].path: p.channel for p in self.posts_from("orphan_image")}
        self.assertEqual(orphans, {"orphan.PNG": "(raíz)", "Soledad/far.png": "Soledad"})

    def test_ignores_zip_copies_of_exports(self) -> None:
        self.assertIn("WhatsApp Chat - BARRIO.zip", self.result.ignored_files)

    def test_reports_identical_files_found_in_several_places(self) -> None:
        self.assertIn(
            ["PHOTO-1.jpg", "WhatsApp Chat - BARRIO/0001-PHOTO-a.jpg"],
            self.result.duplicate_files,
        )

    def test_is_deterministic_across_runs(self) -> None:
        again = ingest(self.root)
        self.assertEqual([p.post_id for p in again.posts], [p.post_id for p in self.result.posts])


if __name__ == "__main__":
    unittest.main()
