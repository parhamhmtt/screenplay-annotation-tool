"""GUI smoke test (offscreen): python -I -m unittest tests.test_gui  (needs PySide6)"""
import json, os, sys, tempfile, unittest
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    import app as APP, core
except ImportError:
    APP = None
SAMPLE = os.path.join(os.path.dirname(__file__), "..", "sample")


@unittest.skipIf(APP is None, "PySide6 not installed")
class Gui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = QApplication.instance() or QApplication([])

    def make_window(self, **extra):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        cfg = {"subs": os.path.join(SAMPLE, "demo.fa.srt"), "subs2": os.path.join(SAMPLE, "demo.en.srt"),
               "annotator": "test", "outdir": directory.name, **extra}
        w = APP.MainWindow()
        w.set_project(core.Project(cfg))
        w.show()
        self.addCleanup(w.close)
        return w

    def test_task_selection_and_pending_partial_lines(self):
        w = self.make_window()
        w.task_list.setCurrentRow(1)
        self.assertEqual(w.active, 1)
        self.assertEqual(w.cards_stack.currentIndex(), 1)
        w.cards[1].editor.btns[0].click()
        w.cards[1].editor.rat.setPlainText("relief")
        w.save_now()
        # A line with another finished task still needs dialogue sentiment.
        w.P.set_annotation(2, {"pragmatic_intent": {"label": "Taarof", "rationale": "polite refusal"}})
        w.next_todo()
        self.assertEqual(w.cur, 1)
        self.assertIn("Complete", w.task_list.item(2).text())
        self.assertIn("Not started", w.task_list.item(1).text())
        # Respect the visible queue, including wraparound.
        w.f_q.setText("doubts")
        w.next_todo()
        self.assertEqual(w.cur, 4)

    def test_typing_does_not_activate_label_shortcuts(self):
        w = self.make_window()
        w.set_active(1)
        editor = w.cards[1].editor
        editor.rat.setFocus()
        self.qa.processEvents()
        QTest.keyClicks(editor.rat, "rn1 t panic")
        self.assertEqual(editor.rat.toPlainText(), "rn1 t panic")
        self.assertEqual(editor.get()["label"], "")
        self.assertEqual(w.active, 1)

    def test_template_form_roundtrip_multi_labels(self):
        w = self.make_window()
        dlg = APP.TemplateDialog(w)
        dlg.add_task()
        dlg.task_name.setText("Emotion")
        dlg.labels.setPlainText("Fear | Anticipated harm\nAnger\nRelief")
        dlg.multi.setChecked(True)
        dlg.required.setChecked(False)
        task_schema = dlg.schema()
        path = os.path.join(w.P.cfg["outdir"], "tasks.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(task_schema, f)
        p = core.Project({**w.P.cfg, "schema": path})
        w.set_project(p)
        w.task_list.setCurrentRow(4)
        ed = w.cards[4].editor
        ed.btns[0].click(); ed.btns[1].click()
        w.save_now()
        self.assertEqual(w.P.items[1]["tasks"]["task_5"]["label"], ["Fear", "Anger"])
        self.assertIn("Complete", w.task_list.item(4).text())
        dlg.deleteLater()

    def test_failed_save_keeps_current_line(self):
        w = self.make_window()
        w.set_active(1)
        w.cards[1].editor.btns[0].click()
        with patch.object(w.P, "set_annotation", side_effect=OSError("disk unavailable")):
            w.select_line(1)
            self.assertEqual(w.cur, 0)
            self.assertTrue(w.dirty)
            self.assertIn("Save failed", w.saved.text())

    def test_label_edit_reload(self):
        qa = QApplication.instance() or QApplication([])
        out = tempfile.mkdtemp()
        cfg = {"subs": os.path.join(SAMPLE, "demo.fa.srt"), "subs2": os.path.join(SAMPLE, "demo.en.srt"),
               "annotator": "t", "outdir": out}
        w = APP.MainWindow(); w.set_project(core.Project(cfg)); w.show()
        self.assertEqual(len(w.rows), 5)
        w.select_line(0)
        w.cards[1].editor.btns[1].click()                      # dialogue sentiment -> Negative
        w.cards[1].editor.rat.setPlainText("panic")
        w.cards[0].add_row(focus=False)                        # per-character row
        w.cards[0].rows[0].char.setText("Nader"); w.cards[0].rows[0].btns[1].click()
        w.cards[0].rows[0].rat.setPlainText("lies")
        w.select_line(2)                                       # flushes save of line 1
        data = json.load(open(w.P.path, encoding="utf-8"))
        t = data["items"]["1"]["tasks"]
        self.assertEqual(t["dialogue_sentiment"], {"label": "Negative", "rationale": "panic"})
        self.assertEqual(t["character_sentiment"][0]["character"], "Nader")
        self.assertIn("DS·Negative", w.rows[0].text(4))
        # incomplete: label without rationale
        w.pick(0) if False else None
        w.set_active(2); w.pick(0)                             # pragmatic intent -> Taarof, no rationale
        w.select_line(0)
        self.assertIn("⚠", w.rows[2].text(4))
        # edit existing: reopen line 0, change label, clear rationale
        w.cards[1].editor.btns[2].click()
        w.select_line(1)
        self.assertEqual(w.P.items[1]["tasks"]["dialogue_sentiment"]["label"], "Neutral")
        # filters
        w.f_status.setCurrentIndex(3)
        self.assertEqual([not it.isHidden() for it in w.rows], [False, False, True, False, False])
        # reload from disk into a fresh window
        w2 = APP.MainWindow(); w2.set_project(core.Project(cfg)); w2.select_line(0)
        self.assertEqual(w2.cards[1].editor.get()["label"], "Neutral")
        self.assertEqual(w2.cards[0].rows[0].get()["character"], "Nader")

if __name__ == "__main__":
    unittest.main()
