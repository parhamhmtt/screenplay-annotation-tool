import json
import tempfile
import unittest
from pathlib import Path

import core


class CoreTests(unittest.TestCase):
    def test_formats_and_cleanup(self):
        srt = "1\n00:00:01,000 --> 00:00:02,000\n<b>سلام &amp; hi</b> {\\an8}\n"
        self.assertEqual(core.parse_subtitles(srt)[0], (1000, 2000, "سلام & hi"))
        vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\n<i>Hello</i>"
        self.assertEqual(core.parse_subtitles(vtt, ".vtt")[0][2], "Hello")
        ass = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\i1}Hello\\Nworld"
        self.assertEqual(core.parse_subtitles(ass, ".ass")[0][2], "Hello\nworld")
        self.assertEqual(core.decode_subtitles("سلام".encode("cp1256")), "سلام")

    def test_alignment_one_to_many(self):
        primary = [(1000, 4000, "a")]
        secondary = [(1000, 2400, "one"), (2400, 4000, "two")]
        self.assertEqual(core.align(primary, secondary), ["one\ntwo"])

    def test_roundtrip_exports_and_stale(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            sub = folder / "film.srt"
            sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
            p = core.Project(sub, annotator="A", outdir=folder)
            uid = p.lines[0]["uid"]
            value = {"dialogue_sentiment": {"label": "Negative", "rationale": "Worried"}}
            p.set_annotation(uid, value)
            p.save()
            p = core.Project(sub, annotator="A", outdir=folder)
            self.assertEqual(p.annotations[uid], value)
            for fmt in ("jsonl", "json", "csv"):
                target = folder / ("export." + fmt)
                p.export(target, fmt)
                self.assertIn("Negative", target.read_text(encoding="utf-8-sig"))
            p.set_annotation(uid, {})
            p.save()
            self.assertEqual(core.Project(sub, annotator="A", outdir=folder).annotations, {})
            p.set_annotation(uid, value)
            p.save()
            sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nChanged\n", encoding="utf-8")
            stale = core.Project(sub, annotator="A", outdir=folder)
            self.assertEqual(stale.stale, [uid])
            stale.save()
            self.assertIn(uid, json.loads(stale.path.read_text(encoding="utf-8"))["annotations"])

    def test_validation_and_incomplete(self):
        schema = core.load_schema()
        with self.assertRaises(ValueError):
            core.normalize_annotations({"unknown": {"label": "x"}}, schema)
        with self.assertRaises(ValueError):
            core.normalize_annotations({"dialogue_sentiment": {"label": "x"}}, schema)
        value = core.normalize_annotations({"character_sentiment": [{"character": "", "label": "Positive", "rationale": "Reason"}]}, schema)
        self.assertTrue(core.incomplete(value, schema))


if __name__ == "__main__":
    unittest.main()
