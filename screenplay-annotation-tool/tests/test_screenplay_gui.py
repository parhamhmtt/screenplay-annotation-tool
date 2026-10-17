"""Run with QT_QPA_PLATFORM=offscreen after installing PySide6."""
import json
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    spec = importlib.util.spec_from_file_location("screenplay_gui", Path(__file__).resolve().parents[1] / "app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class GuiSmoke(unittest.TestCase):
    def test_label_through_widgets_and_save(self):
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            app.SETTINGS = folder / "settings.json"
            subs = Path(__file__).resolve().parents[1] / "sample" / "dialogue.fa.srt"
            window = app.MainWindow({"subs": str(subs), "subs2": "", "movie": "", "lang1": "fa", "lang2": "en", "annotator": "smoke", "schema": "", "outdir": str(folder), "encoding": ""})
            application.processEvents()
            card = window.cards["dialogue_sentiment"]
            card.labels.buttons[1].click()
            card.rationale.setPlainText("Private anxiety")
            window.flush()
            saved = json.loads(window.project.path.read_text(encoding="utf-8"))
            uid = window.project.lines[0]["uid"]
            self.assertEqual(saved["annotations"][uid]["dialogue_sentiment"]["label"], "Negative")
            window.close()


if __name__ == "__main__":
    unittest.main()
