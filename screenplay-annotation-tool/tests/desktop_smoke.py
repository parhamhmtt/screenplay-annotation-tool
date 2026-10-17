"""Headful Tkinter smoke test using an isolated temporary database."""

import os
import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with tempfile.TemporaryDirectory() as directory:
    os.environ["ANNOTATOR_DATA_DIR"] = directory
    from app import core
    from app.desktop import Studio

    root = tk.Tk()
    root.withdraw()
    app = Studio(root)
    with core.connect() as db:
        film_id = db.execute("SELECT id FROM films ORDER BY id LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO segments(film_id,kind,start_ms,end_ms,text_fa,text_en,translation_status,created_at) VALUES(?,'dialogue',1000,3000,'سلام','Hello','approved',0)", (film_id,))
    app.film_id = film_id
    app.reload_films()
    root.update()
    assert len(app.rows) == 1
    row_id = app.rows[0]["id"]
    app.tree.selection_set(str(row_id))
    app.select_segment()
    assert app.segment["text_fa"] == "سلام"
    app.fields["speaker"].insert(0, "Nader")
    app.save_segment()
    with core.connect() as db:
        assert db.execute("SELECT speaker FROM segments WHERE id=?", (row_id,)).fetchone()[0] == "Nader"
    root.destroy()
print("Desktop smoke passed")
