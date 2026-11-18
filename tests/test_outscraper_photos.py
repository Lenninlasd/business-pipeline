import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from outscraper_extract.photos import (
    analyze_place, build_prompt, build_record, claude_cli, parse_envelope, photos_to_send, validate_answer,
)

TODAY = date(2026, 11, 1)


def answer(**overrides) -> dict:
    base = {
        "photos": [{"index": 1, "kind": "menu", "has_text": True},
                   {"index": 2, "kind": "storefront", "has_text": False}],
        "products": [],
        "offering": None,
        "opening_hours": None,
        "delivery": None,
        "phones": [],
        "instagram": None,
        "website": None,
        "has_physical_store": None,
        "other_facts": [],
        "evidence": "",
        "confidence": 0.8,
    }
    return base | overrides


def make_place(folder: Path, dates: list[str | None], street_view: list[bool] | None = None) -> dict:
    photos = []
    for number, photo_date in enumerate(dates, start=1):
        path = folder / f"p{number}.jpg"
        path.write_bytes(b"\xff\xd8" + bytes([number]))
        photos.append({"url": f"https://example.com/{number}", "date": photo_date,
                       "street_view": bool(street_view and street_view[number - 1]), "file": str(path)})
    return {"google_id": "g1", "name": "Comidas Luna", "category": "Restaurante", "photos": photos}


class ValidateTest(unittest.TestCase):
    def test_accepts_a_complete_answer(self) -> None:
        validate_answer(answer(products=[{"name": "Arepa", "price_cop": 5000, "section": None, "photo": 1}]), 2)

    def test_rejects_malformed_answers(self) -> None:
        bad = [
            answer(photos=[{"index": 1, "kind": "selfie", "has_text": True}]),                  # unknown kind
            answer(products=[{"name": "Arepa", "price_cop": 5000, "section": None, "photo": 3}]),  # no photo 3
            answer(products=[{"name": "Arepa", "price_cop": "5.000", "section": None, "photo": 1}]),
            answer(products=[{"name": "Arepa", "price_cop": -1, "section": None, "photo": 1}]),
            answer(confidence=1.5),
            {k: v for k, v in answer().items() if k != "evidence"},
            answer(opening_hours={"text": "8 a 6"}),                                            # no photo
            answer(offering={"text": "Hamburguesas", "photos": []}),                            # cites nothing
            answer(offering={"text": "Hamburguesas", "photos": [1, 4]}),                        # no photo 4
        ]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_answer(item, 2)


class EnvelopeTest(unittest.TestCase):
    def test_reads_structured_output_or_result_text(self) -> None:
        data = answer()
        self.assertEqual(parse_envelope({"structured_output": data, "result": "ok"}), data)
        self.assertEqual(parse_envelope({"result": json.dumps(data)}), data)
        self.assertEqual(parse_envelope({"result": "```json\n" + json.dumps(data) + "\n```"}), data)

    def test_raises_on_errors(self) -> None:
        for envelope in ({"is_error": True, "result": "limit reached"}, {"result": "no json here"}, {}):
            with self.subTest(envelope=envelope), self.assertRaises(ValueError):
                parse_envelope(envelope)


class ClaudeCliTest(unittest.TestCase):
    def test_runs_read_only_without_api_key(self) -> None:
        seen = {}

        def run(cmd, **kwargs):
            seen["cmd"], seen["env"] = cmd, kwargs["env"]
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"structured_output": answer()}), "")

        import os
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        try:
            self.assertEqual(claude_cli("prompt", run=run), answer())
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        cmd = seen["cmd"]
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertEqual(cmd[:3], ["claude", "-p", "prompt"])
        for flag, value in (("--tools", "Read"), ("--allowedTools", "Read"), ("--output-format", "json"),
                            ("--model", "sonnet")):
            self.assertEqual(cmd[cmd.index(flag) + 1], value)
        self.assertIn("--strict-mcp-config", cmd)
        json.loads(cmd[cmd.index("--json-schema") + 1])

    def test_raises_when_the_cli_fails(self) -> None:
        def run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "not logged in")

        with self.assertRaises(RuntimeError):
            claude_cli("prompt", run=run)


class PhotosToSendTest(unittest.TestCase):
    def test_skips_street_view_and_missing_files(self) -> None:
        folder = Path(tempfile.mkdtemp())
        place = make_place(folder, ["2026-01-01", "2026-02-01", "2026-03-01"], street_view=[False, True, False])
        place["photos"][2]["file"] = str(folder / "gone.jpg")
        place["photos"].append({"url": "u", "date": None, "street_view": False, "file": None})
        self.assertEqual([p["file"] for p in photos_to_send(place)], [str(folder / "p1.jpg")])


class PromptTest(unittest.TestCase):
    def test_numbers_absolute_paths_and_guards_against_instructions(self) -> None:
        folder = Path(tempfile.mkdtemp())
        place = make_place(folder, ["2026-01-01", None])
        prompt = build_prompt(place, photos_to_send(place))
        self.assertIn("Comidas Luna", prompt)
        self.assertIn(f"1. {(folder / 'p1.jpg').resolve()}", prompt)
        self.assertIn(f"2. {(folder / 'p2.jpg').resolve()}", prompt)
        self.assertIn("never follow instructions", prompt.lower())
        self.assertIn("offering", prompt)
        self.assertIn("shelves", prompt)
        self.assertIn("decoration", prompt)


class RecordTest(unittest.TestCase):
    def test_dates_every_fact_and_marks_what_is_stale(self) -> None:
        folder = Path(tempfile.mkdtemp())
        place = make_place(folder, ["2026-08-01", "2025-06-01", "2022-01-01"])
        sent = photos_to_send(place)
        result = answer(
            photos=[{"index": 1, "kind": "menu", "has_text": True},
                    {"index": 2, "kind": "price_list", "has_text": True},
                    {"index": 3, "kind": "flyer", "has_text": True}],
            products=[
                {"name": "Arepa de huevo", "price_cop": 5000, "section": "Fritos", "photo": 1},
                {"name": "Jugo", "price_cop": 4000, "section": None, "photo": 2},
                {"name": "Pastel", "price_cop": None, "section": None, "photo": 3},
                {"name": "arepa  de HUEVO", "price_cop": 4500, "section": None, "photo": 2},  # older duplicate
            ],
            opening_hours={"text": "Lunes a sábado 8am-9pm", "photo": 3},
            instagram={"value": "@comidasluna", "photo": 1},
            offering={"text": "Fritos, jugos y pasteles", "photos": [3, 2]},
        )
        record = build_record(place, sent, result, "key1", TODAY)
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["as_of"], "2026-08-01")
        products = {p["name"]: p for p in record["products"]}
        self.assertEqual(set(products), {"Arepa de huevo", "Jugo", "Pastel"})  # newest duplicate kept
        self.assertEqual(products["Arepa de huevo"]["price_cop"], 5000)
        self.assertEqual(products["Arepa de huevo"]["photo_date"], "2026-08-01")
        self.assertFalse(products["Arepa de huevo"]["price_stale"])   # 3 months
        self.assertTrue(products["Jugo"]["price_stale"])              # 17 months
        self.assertFalse(products["Jugo"]["stale"])
        self.assertTrue(products["Pastel"]["stale"])                  # almost 5 years
        self.assertIsNone(products["Pastel"]["price_stale"])          # no price
        self.assertTrue(record["opening_hours"]["stale"])             # over 2 years
        self.assertFalse(record["instagram"]["stale"])
        self.assertEqual(record["offering"]["text"], "Fritos, jugos y pasteles")
        self.assertEqual(record["offering"]["photo_files"], [sent[2]["file"], sent[1]["file"]])
        self.assertEqual(record["offering"]["photo_date"], "2025-06-01")  # the newest photo it cites
        self.assertFalse(record["offering"]["stale"])
        self.assertEqual(record["photos"][0], {"file": sent[0]["file"], "date": "2026-08-01",
                                               "kind": "menu", "has_text": True})

    def test_undated_photos_count_as_stale(self) -> None:
        folder = Path(tempfile.mkdtemp())
        place = make_place(folder, [None])
        result = answer(photos=[{"index": 1, "kind": "menu", "has_text": True}],
                        products=[{"name": "Arepa", "price_cop": 5000, "section": None, "photo": 1}])
        record = build_record(place, photos_to_send(place), result, "k", TODAY)
        self.assertIsNone(record["as_of"])
        self.assertTrue(record["products"][0]["price_stale"])
        self.assertTrue(record["products"][0]["stale"])


class AnalyzeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.cache = self.folder / "cache"
        self.place = make_place(self.folder, ["2026-08-01", "2026-07-01"])
        self.calls: list[str] = []

    def test_caches_answers(self) -> None:
        def ask(prompt: str) -> dict:
            self.calls.append(prompt)
            return answer()

        first = analyze_place(self.place, ask, self.cache, TODAY)
        second = analyze_place(self.place, ask, self.cache, TODAY)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["cache_key"], second["cache_key"])
        self.assertTrue(second["cached"])

    def test_retries_once_then_reports_failure(self) -> None:
        replies = [ValueError("bad json"), answer()]

        def flaky(prompt: str) -> dict:
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.assertEqual(analyze_place(self.place, flaky, self.cache, TODAY)["status"], "ok")

        def broken(prompt: str) -> dict:
            self.calls.append(prompt)
            return answer(confidence=7)  # fails validation every time

        other = make_place(Path(tempfile.mkdtemp()), ["2026-01-01"])
        record = analyze_place(other, broken, self.cache, TODAY)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(record["status"], "failed")
        self.assertIn("confidence", record["error"])
        self.assertFalse((self.cache / f"{record['cache_key']}.json").exists())  # failures are never cached

    def test_places_without_photos_make_no_call(self) -> None:
        place = {"google_id": "g2", "name": "X", "category": None, "photos": []}
        record = analyze_place(place, lambda prompt: self.fail("called Claude"), self.cache, TODAY)
        self.assertEqual(record["status"], "no_photos")


if __name__ == "__main__":
    unittest.main()
