import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core as A

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "sample")


class Core(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = {"subs": os.path.join(SAMPLE, "demo.fa.srt"), "subs2": os.path.join(SAMPLE, "demo.en.srt"),
                    "annotator": "t1", "outdir": self.d}

    def test_srt_vtt_ass(self):
        self.assertEqual(A.parse_srt_vtt("1\n00:00:01,500 --> 00:00:02,000\n<i>Hi</i>\n{\\an8}there\n"),
                         [(1.5, 2.0, "Hi\nthere")])
        self.assertEqual(A.parse_srt_vtt("WEBVTT\n\n01:02.250 --> 01:03.000\nx\n"), [(62.25, 63.0, "x")])
        ass = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" \
              "Dialogue: 0,0:00:01.50,0:00:03.00,Default,,0,0,0,,Hello, {\\i1}world\\Nline2\n"
        self.assertEqual(A.parse_ass(ass), [(1.5, 3.0, "Hello, world\nline2")])

    def test_cp1256_fallback(self):
        p = os.path.join(self.d, "x.srt")
        open(p, "wb").write("1\n00:00:01,000 --> 00:00:02,000\nسلام\n".encode("cp1256"))
        self.assertEqual(A.parse_subtitles(p)[0][2], "سلام")

    def test_alignment(self):
        P = A.Project(self.cfg)
        self.assertEqual(P.lines[2]["t2"], "You trash. You've killed my child.")   # 1 fa cue <-> 2 en cues
        self.assertIn("set myself on fire", P.lines[3]["t2"])

    def test_save_edit_export_reload(self):
        P = A.Project(self.cfg)
        P.set_annotation(1, {"dialogue_sentiment": {"label": "Negative", "rationale": " panic "},
                             "character_sentiment": [{"character": "Nader", "label": "Negative", "rationale": "lies"},
                                                     {"character": "", "label": "", "rationale": ""}]})
        P.set_annotation(3, {"offensive_language": {"label": "Severe Insult/Vulgarity", "rationale": "slur"}})
        recs = list(P.records())
        self.assertEqual([r["id"] for r in recs], [1, 3])
        self.assertEqual(recs[0]["annotations"]["dialogue_sentiment"]["rationale"], "panic")
        self.assertEqual(len(recs[0]["annotations"]["character_sentiment"]), 1)
        # edit, then clear
        P.set_annotation(1, {"dialogue_sentiment": {"label": "Neutral", "rationale": "numb"}})
        P.set_annotation(3, {})
        P2 = A.Project(self.cfg)                       # reload from disk
        self.assertEqual(list(P2.items), [1])
        self.assertEqual(P2.items[1]["tasks"]["dialogue_sentiment"]["label"], "Neutral")
        body, _ = P2.export("jsonl")
        self.assertEqual(json.loads(body.strip())["text_en"], "I didn't hear them.")
        self.assertIn("dialogue_sentiment_label", P2.export("csv")[0])
        self.assertEqual(len(list(P2.records(include_all=True))), len(P2.lines))

    def test_validation(self):
        P = A.Project(self.cfg)
        with self.assertRaises(ValueError): P.set_annotation(1, {"nope": {}})
        with self.assertRaises(ValueError): P.set_annotation(1, {"dialogue_sentiment": {"label": "Bogus"}})
        with self.assertRaises(ValueError): P.set_annotation(99, {})

    def test_template_validation_and_saved_template_protection(self):
        with self.assertRaises(ValueError): A.normalize_schema({"tasks": []})
        with self.assertRaises(ValueError):
            A.normalize_schema({"tasks": [{"id": "emotion", "labels": ["Fear", "Fear"]}]})
        P = A.Project(self.cfg)
        P.set_annotation(1, {"dialogue_sentiment": {"label": "Negative", "rationale": "fear"}})
        schema = A.load_schema()
        schema["tasks"][1]["labels"][0]["value"] = "Happy"
        path = os.path.join(self.d, "changed.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(schema, f)
        with self.assertRaisesRegex(ValueError, "different task template"):
            A.Project({**self.cfg, "schema": path})
        self.assertEqual(A.Project(self.cfg).items[1]["tasks"]["dialogue_sentiment"]["label"], "Negative")

    def test_stale_warning(self):
        P = A.Project(self.cfg); P.set_annotation(2, {"pragmatic_intent": {"label": "Taarof", "rationale": "x"}})
        d = json.load(open(P.path)); d["items"]["2"]["uid"] = "deadbeef"; json.dump(d, open(P.path, "w"))
        self.assertTrue(A.Project(self.cfg).warnings)

if __name__ == "__main__":
    unittest.main()
