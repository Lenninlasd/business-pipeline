import unittest

from business_pipeline.whatsapp import group_into_posts, parse_chat

LRM = "‎"
NNBSP = " "

# Synthetic chat in the exact shape of an iOS Spanish export. Phone numbers are invalid (570 prefix).
CHAT = (
    f"[17/4/26, 12:55:17] Grupo: {LRM}Los mensajes y las llamadas están cifrados de extremo a extremo.\n"
    f"{LRM}[31/10/26, 7:39:10] ~{NNBSP}Ana: Buen día, a la orden {LRM}<adjunto: 0001-PHOTO-a.jpg>\n"
    f"[31/10/26, 9:23:51] ~{NNBSP}Luis: ¿Alguien vende desayunos?\n"
    f"{LRM}[1/11/26, 10:38:45] ~{NNBSP}Chef: *Buenos días vecinos*\n"
    "\t✓\t*Pechuga asada $22.000*\n"
    f"*PEDIDOS*\n570 000 0001 {LRM}<adjunto: 0002-PHOTO-b.jpg>\n"
    f"[1/11/26, 9:28:25] ~{NNBSP}Daisi: {LRM}<adjunto: 0003-PHOTO-c.jpg>\n"
    "[1/11/26, 9:28:26] ~ Daisi: https://example.com/catalogo\n"
    f"[9/11/26, 19:36:03] ~{NNBSP}Jesus: {LRM}Video omitido\n"
    f"[9/11/26, 19:36:38] ~{NNBSP}Jesus: Información al interno\n"
    f"[11/11/26, 15:47:27] ~{NNBSP}Daisi: Loguin.pdf {LRM}documento omitido\n"
    f"[13/11/26, 7:39:43] ~{NNBSP}Odonto: {LRM}Se eliminó este mensaje.\n"
    f"[6/11/26, 14:43:49] ~{NNBSP}Byron: ¿Precio? {LRM}<Se editó este mensaje.>\n"
    f"[9/11/26, 19:04:17] ~{NNBSP}Jesus: {LRM}~{NNBSP}Chef añadió a ~{NNBSP}Jesus.\n"
)


class ParseChatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = parse_chat(CHAT)

    def by_sender(self, sender: str):
        return [message for message in self.messages if message.sender == sender]

    def test_reads_day_first_dates_with_two_digit_years(self) -> None:
        self.assertEqual(self.by_sender("Ana")[0].timestamp, "2026-10-31T07:39:10")

    def test_strips_invisible_characters_and_tilde_from_sender(self) -> None:
        self.assertLessEqual({"Ana", "Chef", "Daisi"}, {m.sender for m in self.messages})

    def test_extracts_attachment_and_removes_it_from_text(self) -> None:
        ana = self.by_sender("Ana")[0]
        self.assertEqual(ana.attachments, ["0001-PHOTO-a.jpg"])
        self.assertEqual(ana.text, "Buen día, a la orden")

    def test_joins_continuation_lines_into_the_same_message(self) -> None:
        chef = self.by_sender("Chef")[0]
        self.assertIn("Pechuga asada $22.000", chef.text)
        self.assertIn("570 000 0001", chef.text)
        self.assertEqual(chef.attachments, ["0002-PHOTO-b.jpg"])

    def test_marks_system_messages(self) -> None:
        system_texts = [m.text for m in self.messages if m.is_system]
        self.assertTrue(any("cifrados" in text for text in system_texts))
        self.assertTrue(any("añadió" in text for text in system_texts))
        self.assertFalse(self.by_sender("Luis")[0].is_system)

    def test_records_omitted_media_without_marking_it_as_system(self) -> None:
        video = self.by_sender("Jesus")[0]
        self.assertEqual(video.omitted, ["Video omitido"])
        self.assertFalse(video.is_system)
        document = [m for m in self.by_sender("Daisi") if m.omitted][0]
        self.assertEqual(document.omitted, ["Loguin.pdf documento omitido"])
        self.assertEqual(document.text, "")

    def test_treats_deleted_messages_as_system(self) -> None:
        self.assertTrue(self.by_sender("Odonto")[0].is_system)

    def test_removes_edited_marker(self) -> None:
        self.assertEqual(self.by_sender("Byron")[0].text, "¿Precio?")


class GroupIntoPostsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.posts = group_into_posts(parse_chat(CHAT), channel="Barrio")

    def test_merges_close_messages_from_the_same_sender(self) -> None:
        daisi = [p for p in self.posts if p.sender == "Daisi"][0]
        self.assertEqual(daisi.attachments, ["0003-PHOTO-c.jpg"])
        self.assertIn("https://example.com/catalogo", daisi.text)

    def test_merges_omitted_video_with_following_text_within_the_window(self) -> None:
        jesus = [p for p in self.posts if p.sender == "Jesus"]
        self.assertEqual(len(jesus), 1)
        self.assertEqual(jesus[0].omitted, ["Video omitido"])
        self.assertIn("Información al interno", jesus[0].text)

    def test_keeps_far_apart_messages_from_the_same_sender_separate(self) -> None:
        self.assertEqual(len([p for p in self.posts if p.sender == "Daisi"]), 2)

    def test_excludes_system_messages(self) -> None:
        self.assertNotIn("Odonto", {p.sender for p in self.posts})

    def test_builds_stable_ids_from_channel_time_and_sender(self) -> None:
        ana = [p for p in self.posts if p.sender == "Ana"][0]
        self.assertEqual(ana.post_id, "whatsapp:Barrio:2026-10-31T07:39:10:Ana")

    def test_orders_posts_by_time(self) -> None:
        timestamps = [p.timestamp for p in self.posts]
        self.assertEqual(timestamps, sorted(timestamps))


if __name__ == "__main__":
    unittest.main()
