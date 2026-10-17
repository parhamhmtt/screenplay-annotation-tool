import json
import os
import tempfile
import unittest
from pathlib import Path


class CoreWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["ANNOTATOR_DATA_DIR"] = cls.temp.name
        from app import core
        cls.core = core
        core.DATA = Path(cls.temp.name)
        core.DB = core.DATA / "annotations.sqlite3"
        core.TRACK_FILES = core.DATA / "tracks"
        core.init_db()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_open_project_label_compare_and_export(self):
        core = self.core
        with core.connect() as db:
            reviewer = dict(db.execute("SELECT id,username,role FROM users WHERE username='reviewer'").fetchone())
            a = dict(db.execute("SELECT id,username,role FROM users WHERE username='annotator-a'").fetchone())
            b = dict(db.execute("SELECT id,username,role FROM users WHERE username='annotator-b'").fetchone())
            film_id = core.create_film(db, "Test Film", reviewer["id"])
            fa = Path(self.temp.name) / "fa.srt"
            fa.write_text("1\n00:00:01,000 --> 00:00:03,000\nسلام\n\n2\n00:00:04,000 --> 00:00:06,000\nخوبی؟\n", encoding="utf-8")
            en = Path(self.temp.name) / "en.srt"
            en.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n\n2\n00:00:04,000 --> 00:00:06,000\nHow are you?\n", encoding="utf-8")
            self.assertEqual(core.import_srt(db, film_id, "fa", fa, reviewer["id"])["cues"], 2)
            core.import_srt(db, film_id, "en", en, reviewer["id"])
            segments = core.list_segments(db, film_id, a["id"])
            self.assertEqual([s["text_en"] for s in segments], ["Hello", "How are you?"])
            first = segments[0]
            core.update_segment(db, first["id"], reviewer["id"], translation_status="approved",
                                text_fa=first["text_fa"], text_en=first["text_en"],
                                start_ms=first["start_ms"], end_ms=first["end_ms"], kind="dialogue")
            payload = {
                "dialogue_sentiment": {"label": "Neutral", "rationale": "Calm wording in the current exchange."},
                "pragmatic_intent": {"label": "Genuine/Direct", "rationale": "A direct answer to the preceding question."},
                "offensive_language": {"label": "Non-Offensive", "rationale": "No insult, threat, or manipulation in this turn."},
            }
            other = dict(payload)
            other["pragmatic_intent"] = {"label": "Evasion/Deception", "rationale": "The speaker is avoiding a direct answer."}
            core.save_annotation(db, first["id"], a, payload)
            core.save_annotation(db, first["id"], b, other)
            rows = core.list_segments(db, film_id, reviewer["id"])
            self.assertEqual(rows[0]["annotation_count"], 2)
            self.assertEqual(len(rows[0]["versions"]), 2)
            core.adopt_version(db, first["id"], reviewer, other)
            gold = core.list_segments(db, film_id, reviewer["id"])[0]["gold"]
            self.assertEqual(gold["pragmatic_intent"]["label"], "Evasion/Deception")
            exported = core.export_jsonl(db, film_id, "gold")
            record = json.loads(exported.splitlines()[0])
            self.assertEqual(record["gold"]["pragmatic_intent"]["label"], "Evasion/Deception")
            self.assertIn("Hello", core.export_srt(db, film_id, "en"))

    def test_partial_labels_and_jsonl_import_as_version(self):
        core = self.core
        with core.connect() as db:
            reviewer = dict(db.execute("SELECT id,username,role FROM users WHERE username='reviewer'").fetchone())
            a = dict(db.execute("SELECT id,username,role FROM users WHERE username='annotator-a'").fetchone())
            film_id = core.create_film(db, "Second Film", reviewer["id"])
            seg_id = core.create_segment(db, film_id, "dialogue", 1000, 2500, reviewer["id"],
                                         text_fa="نمی‌دانم", text_en="I don't know", translation_status="approved")
            partial = {"pragmatic_intent": {"label": "Evasion/Deception", "rationale": "The speaker avoids a direct answer."}}
            core.save_annotation(db, seg_id, a, partial)
            view = core.list_segments(db, film_id, a["id"])[0]
            self.assertFalse(view["my_complete"])
            self.assertEqual(view["my_annotation"]["pragmatic_intent"]["label"], "Evasion/Deception")
            incoming = Path(self.temp.name) / "labels.jsonl"
            incoming.write_text(json.dumps({
                "id": seg_id, "kind": "dialogue", "start_ms": 1000,
                "payload": {
                    "dialogue_sentiment": {"label": "Negative", "rationale": "Private fear behind a short answer."},
                    "pragmatic_intent": {"label": "Evasion/Deception", "rationale": "Avoids naming the fact."},
                    "offensive_language": {"label": "Non-Offensive", "rationale": "No insult in this turn."},
                }
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            result = core.import_label_jsonl(db, film_id, incoming, reviewer["id"], "team-c")
            self.assertEqual(result["applied"], 1)
            versions = core.list_segments(db, film_id, reviewer["id"])[0]["versions"]
            names = {v["username"] for v in versions}
            self.assertIn("annotator-a", names)
            self.assertIn("team-c", names)

    def test_description_from_sdh_and_srt_encodings(self):
        core = self.core
        with core.connect() as db:
            reviewer = dict(db.execute("SELECT id,username,role FROM users WHERE username='reviewer'").fetchone())
            film_id = core.create_film(db, "Third Film", reviewer["id"])
            sdh = Path(self.temp.name) / "sdh.srt"
            sdh.write_text("1\n00:00:07,000 --> 00:00:08,000\n[door slam]\n\n2\n00:00:09,000 --> 00:00:10,000\nHello\n", encoding="utf-8")
            result = core.import_srt(db, film_id, "sdh", sdh, reviewer["id"])
            self.assertEqual(result["description_candidates"], 1)
            kinds = [r["kind"] for r in core.list_segments(db, film_id)]
            self.assertEqual(kinds, ["description"])


if __name__ == "__main__":
    unittest.main()
