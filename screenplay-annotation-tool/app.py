"""Native, offline Screenplay Annotator. Run: python app.py --help"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QShortcut, QTextOption
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QCompleter, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSlider, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
    QHeaderView
)

import core

SETTINGS = Path.home() / ".screenplay_annotator.json"


def direction(text):
    arabic = sum(1 for c in text if "\u0600" <= c <= "\u08ff")
    latin = sum(1 for c in text if "A" <= c <= "z")
    return Qt.LayoutDirection.RightToLeft if arabic > latin else Qt.LayoutDirection.LeftToRight


def timecode(ms):
    s, milli = divmod(max(0, int(ms)), 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02}:{m:02}:{s:02}.{milli:03}"


def button(text, callback):
    widget = QPushButton(text)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    widget.clicked.connect(callback)
    return widget


class OpenDialog(QDialog):
    def __init__(self, parent=None, values=None):
        super().__init__(parent)
        self.setWindowTitle("Open annotation project")
        self.setMinimumWidth(650)
        values = values or {}
        form = QFormLayout(self)
        self.fields = {}
        for key, label, kind in (("movie", "Movie (optional)", "file"), ("subs", "Subtitle 1", "file"),
                                 ("subs2", "Subtitle 2 (optional)", "file"), ("schema", "Schema JSON (optional)", "file"),
                                 ("outdir", "Output folder", "dir"), ("lang1", "Language 1", "text"),
                                 ("lang2", "Language 2", "text"), ("annotator", "Annotator", "text"),
                                 ("encoding", "Encoding override (optional)", "text")):
            edit = QLineEdit(str(values.get(key, "")))
            self.fields[key] = edit
            if kind == "text":
                form.addRow(label, edit)
            else:
                row = QHBoxLayout()
                row.addWidget(edit)
                row.addWidget(button("Browse…", lambda _, e=edit, k=kind: self.browse(e, k)))
                form.addRow(label, row)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("Cancel", self.reject))
        actions.addWidget(button("Open", self.accept))
        form.addRow(actions)

    def browse(self, edit, kind):
        if kind == "dir":
            value = QFileDialog.getExistingDirectory(self, "Output folder", edit.text())
        else:
            value, _ = QFileDialog.getOpenFileName(self, "Choose file", edit.text())
        if value:
            edit.setText(value)

    def values(self):
        return {key: edit.text().strip() for key, edit in self.fields.items()}


class CharacterRow(QWidget):
    def __init__(self, task, names, on_change, on_delete, value=None):
        super().__init__()
        self.task, self.on_change = task, on_change
        value = value or {}
        box = QVBoxLayout(self)
        box.setContentsMargins(8, 5, 8, 5)
        top = QHBoxLayout()
        self.name = QLineEdit(value.get("character", ""))
        self.name.setPlaceholderText("Character")
        self.name.setCompleter(QCompleter(sorted(names)))
        self.name.textChanged.connect(on_change)
        top.addWidget(self.name)
        top.addWidget(button("Delete", lambda: on_delete(self)))
        box.addLayout(top)
        self.labels = LabelButtons(task, value.get("label", ""), on_change)
        box.addWidget(self.labels)
        self.rationale = QTextEdit(value.get("rationale", ""))
        self.rationale.setPlaceholderText("Why this label?")
        self.rationale.setMaximumHeight(70)
        option = self.rationale.document().defaultTextOption()
        option.setTextDirection(Qt.LayoutDirection.LayoutDirectionAuto)
        self.rationale.document().setDefaultTextOption(option)
        self.rationale.textChanged.connect(on_change)
        box.addWidget(self.rationale)

    def value(self):
        return {"character": self.name.text(), "label": self.labels.value(), "rationale": self.rationale.toPlainText()}


class LabelButtons(QWidget):
    def __init__(self, task, selected, on_change):
        super().__init__()
        self.task, self.on_change = task, on_change
        self.selected = selected if task["multi"] and isinstance(selected, list) else (selected or ([] if task["multi"] else ""))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.buttons = []
        for i, label in enumerate(task["labels"]):
            btn = button(f"{i+1} {label['value']}", lambda _, v=label["value"]: self.toggle(v))
            btn.setToolTip(label["hint"])
            btn.setCheckable(True)
            btn.setStyleSheet(f"QPushButton:checked {{ background: {label['color']}; color: white; }}")
            layout.addWidget(btn)
            self.buttons.append(btn)
        layout.addStretch()
        self.refresh()

    def toggle(self, value):
        if self.task["multi"]:
            current = set(self.selected)
            current.symmetric_difference_update({value})
            self.selected = [x["value"] for x in self.task["labels"] if x["value"] in current]
        else:
            self.selected = "" if self.selected == value else value
        self.refresh()
        self.on_change()

    def refresh(self):
        for btn, label in zip(self.buttons, self.task["labels"]):
            btn.setChecked(label["value"] in self.selected if self.task["multi"] else label["value"] == self.selected)

    def value(self):
        return self.selected


class TaskCard(QWidget):
    def __init__(self, task, names, on_change, activate):
        super().__init__()
        self.task, self.names, self.on_change = task, names, on_change
        self.activate = activate
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        title = QLabel(f"{task['code']} · {task['name']}")
        title.setStyleSheet("font-weight: bold; font-size: 14px")
        layout.addWidget(title)
        layout.addWidget(QLabel(task["description"]))
        if task["per_character"]:
            self.rows_layout = QVBoxLayout()
            layout.addLayout(self.rows_layout)
            layout.addWidget(button("+ character", lambda: self.add_row()))
        else:
            self.labels = LabelButtons(task, "", self.changed)
            layout.addWidget(self.labels)
            self.rationale = QTextEdit()
            self.rationale.setPlaceholderText("Why this label?")
            self.rationale.setMaximumHeight(88)
            option = self.rationale.document().defaultTextOption()
            option.setTextDirection(Qt.LayoutDirection.LayoutDirectionAuto)
            self.rationale.document().setDefaultTextOption(option)
            self.rationale.textChanged.connect(self.changed)
            layout.addWidget(self.rationale)
        self.setStyleSheet("TaskCard { border: 2px solid #d7dce2; border-radius: 7px; }")

    def changed(self):
        self.activate(self.task["id"])
        self.on_change()

    def mousePressEvent(self, event):
        self.activate(self.task["id"])
        super().mousePressEvent(event)

    def add_row(self, value=None):
        row = CharacterRow(self.task, self.names, self.changed, self.delete_row, value)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self.changed()
        return row

    def delete_row(self, row):
        self.rows.remove(row)
        row.deleteLater()
        self.changed()

    def set_value(self, value):
        if self.task["per_character"]:
            for row in self.rows:
                row.deleteLater()
            self.rows.clear()
            for entry in value or []:
                self.add_row(entry)
        else:
            value = value or {}
            self.labels.selected = value.get("label", [] if self.task["multi"] else "")
            self.labels.refresh()
            self.rationale.setPlainText(value.get("rationale", ""))

    def value(self):
        if self.task["per_character"]:
            return [row.value() for row in self.rows]
        return {"label": self.labels.value(), "rationale": self.rationale.toPlainText()}

    def focus_rationale(self):
        if self.task["per_character"]:
            if not self.rows:
                self.add_row()
            self.rows[-1].rationale.setFocus()
        else:
            self.rationale.setFocus()

    def pick(self, index):
        if index >= len(self.task["labels"]):
            return
        if self.task["per_character"]:
            if not self.rows:
                self.add_row()
            self.rows[-1].labels.toggle(self.task["labels"][index]["value"])
        else:
            self.labels.toggle(self.task["labels"][index]["value"])

    def highlight(self, active, warning):
        color = "#d58a27" if warning else "#4d83bd" if active else "#d7dce2"
        self.setStyleSheet(f"TaskCard {{ border: 2px solid {color}; border-radius: 7px; }}")


class MainWindow(QMainWindow):
    def __init__(self, initial=None):
        super().__init__()
        self.setWindowTitle("Screenplay Annotator")
        self.resize(1350, 850)
        self.project = None
        self.index = -1
        self.loading = False
        self.active_task = ""
        self.play_line_end = None
        self.follow = True
        self.mode = "both"
        self.include_unannotated = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(450)
        self.timer.timeout.connect(self.flush)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(lambda d: self.seek.setMaximum(d))
        self.player.errorOccurred.connect(lambda *_: self.video_message.setText("Video playback unavailable. You can keep annotating."))
        self.build_ui()
        self.shortcuts()
        if initial and initial.get("subs"):
            self.open_values(initial)
        else:
            QTimer.singleShot(0, self.open_dialog)

    def build_ui(self):
        file_menu = self.menuBar().addMenu("File")
        self.action(file_menu, "Open…", self.open_dialog)
        for fmt in ("jsonl", "json", "csv"):
            self.action(file_menu, "Export " + fmt.upper(), lambda _=False, f=fmt: self.export(f))
        toggle = self.action(file_menu, "Include unannotated lines", self.toggle_export, check=True)
        toggle.setChecked(False)
        self.action(file_menu, "Quit", self.close)
        view = self.menuBar().addMenu("View")
        for mode, label in (("both", "Both languages"), ("one", "Language 1 only"), ("two", "Language 2 only")):
            self.action(view, label, lambda _=False, m=mode: self.set_mode(m))
        follow = self.action(view, "Follow video", lambda checked: setattr(self, "follow", checked), check=True)
        follow.setChecked(True)
        help_menu = self.menuBar().addMenu("Help")
        self.action(help_menu, "Shortcuts", lambda: QMessageBox.information(self, "Shortcuts", "J/Down next · K/Up previous · N next unannotated · Space play/pause · P play line · T next task · 1–9 label · R rationale · Esc leave editor"))
        splitter = QSplitter()
        self.setCentralWidget(splitter)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left = QWidget()
        self.left_layout = QVBoxLayout(left)
        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(220)
        self.player.setVideoOutput(self.video)
        self.left_layout.addWidget(self.video)
        self.video_message = QLabel("No movie loaded — subtitle annotation is ready.")
        self.left_layout.addWidget(self.video_message)
        controls = QHBoxLayout()
        controls.addWidget(button("▶ / ❚❚", self.play_pause))
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.sliderMoved.connect(self.player.setPosition)
        controls.addWidget(self.seek, 1)
        self.clock = QLabel("00:00:00.000")
        controls.addWidget(self.clock)
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(70)
        volume.setMaximumWidth(90)
        volume.valueChanged.connect(lambda n: self.audio.setVolume(n/100))
        controls.addWidget(volume)
        self.left_layout.addLayout(controls)
        self.caption = QLabel("")
        self.caption.setWordWrap(True)
        self.left_layout.addWidget(self.caption)
        self.current = QLabel("Open a subtitle file to begin")
        self.current.setWordWrap(True)
        self.current.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.current.setStyleSheet("font-size: 16px; padding: 12px; background: #f2f4f7")
        self.left_layout.addWidget(self.current)
        nav = QHBoxLayout()
        nav.addWidget(button("◀ Previous", lambda: self.select(self.index-1)))
        nav.addWidget(button("▶ Play line", self.play_line))
        nav.addWidget(button("Next ▶|", lambda: self.select(self.index+1)))
        self.left_layout.addLayout(nav)
        self.cards_box = QVBoxLayout()
        self.left_layout.addLayout(self.cards_box)
        self.left_layout.addStretch()
        right = QWidget()
        right_layout = QVBoxLayout(right)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search text, rationale, character")
        self.search.textChanged.connect(self.filter_rows)
        filters.addWidget(self.search)
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All", "Annotated", "Unannotated", "Incomplete"])
        self.status_filter.currentIndexChanged.connect(self.filter_rows)
        filters.addWidget(self.status_filter)
        self.task_filter = QComboBox()
        self.task_filter.currentIndexChanged.connect(self.update_label_filter)
        filters.addWidget(self.task_filter)
        self.label_filter = QComboBox()
        self.label_filter.currentIndexChanged.connect(self.filter_rows)
        filters.addWidget(self.label_filter)
        right_layout.addLayout(filters)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Time", "Language 1", "Language 2", "Labels"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 105)
        self.table.setColumnWidth(2, 230)
        self.table.setColumnWidth(3, 230)
        self.table.setColumnWidth(4, 210)
        self.table.cellClicked.connect(lambda row, _: self.select(row))
        right_layout.addWidget(self.table)
        splitter.addWidget(right)
        splitter.setSizes([700, 650])
        self.statusBar().showMessage("No project")

    def action(self, menu, title, callback, check=False):
        action = QAction(title, self)
        action.setCheckable(check)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def open_dialog(self):
        values = self.last_values()
        if self.project:
            values.update({"movie": self.project.movie, "subs": self.project.subs, "subs2": self.project.subs2,
                           "lang1": self.project.lang1, "lang2": self.project.lang2, "annotator": self.project.annotator,
                           "outdir": str(self.project.path.parent)})
        dialog = OpenDialog(self, values)
        if dialog.exec():
            self.open_values(dialog.values())

    def last_values(self):
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"lang1": "fa", "lang2": "en", "annotator": "annotator-a"}

    def open_values(self, values):
        self.flush()
        try:
            project = core.Project(**{key: values.get(key, "") for key in ("subs", "subs2", "movie", "lang1", "lang2", "annotator", "encoding")},
                                   schema_path=values.get("schema", ""), outdir=values.get("outdir", ""))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "Could not open project", str(exc))
            return
        self.project = project
        core.atomic_json(SETTINGS, values)
        self.player.stop()
        self.video_message.setText("" if project.movie else "No movie loaded — subtitle annotation is ready.")
        self.player.setSource(QUrl.fromLocalFile(str(Path(project.movie).resolve())) if project.movie else QUrl())
        self.task_filter.blockSignals(True)
        self.task_filter.clear()
        self.task_filter.addItem("All tasks", "")
        for task in project.schema["tasks"]:
            self.task_filter.addItem(task["code"], task["id"])
        self.task_filter.blockSignals(False)
        self.update_label_filter()
        self.active_task = project.schema["tasks"][0]["id"]
        self.rebuild_cards()
        self.populate_table()
        self.select(0)
        if project.stale:
            QMessageBox.warning(self, "Changed subtitles", f"{len(project.stale)} saved line UID(s) do not match these subtitles. Matching lines remain available.")

    def rebuild_cards(self):
        while self.cards_box.count():
            item = self.cards_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        names = {entry.get("character", "") for ann in self.project.annotations.values() for task in self.project.schema["tasks"] if task["per_character"] for entry in ann.get(task["id"], [])}
        self.cards = {}
        for task in self.project.schema["tasks"]:
            card = TaskCard(task, names - {""}, self.edited, self.activate_task)
            self.cards_box.addWidget(card)
            self.cards[task["id"]] = card

    def populate_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.project.lines))
        for row, line in enumerate(self.project.lines):
            for col, text in enumerate((str(line["id"]), timecode(line["start"]), line["text1"], line["text2"], "")):
                item = QTableWidgetItem(text.replace("\n", " / "))
                item.setTextAlignment((Qt.AlignmentFlag.AlignRight if direction(text) == Qt.LayoutDirection.RightToLeft else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
            self.refresh_row(row, update_status=False)
        self.table.blockSignals(False)
        self.update_status()

    def annotation_summary(self, ann):
        parts, rationales = [], []
        for task in self.project.schema["tasks"]:
            value = ann.get(task["id"])
            for entry in (value if task["per_character"] else [value]) if value else []:
                label = entry.get("label")
                if label:
                    parts.append(task["code"] + "·" + (entry.get("character", "") + ":" if task["per_character"] else "") + (", ".join(label) if isinstance(label, list) else label))
                if entry.get("rationale"):
                    rationales.append(task["name"] + ": " + entry["rationale"])
        return "  ".join(parts), "\n".join(rationales)

    def refresh_row(self, row, update_status=True):
        line = self.project.lines[row]
        ann = self.project.annotations.get(line["uid"], {})
        summary, tooltip = self.annotation_summary(ann)
        warning = core.incomplete(ann, self.project.schema)
        self.table.item(row, 4).setText(("⚠ " if warning else "") + summary)
        self.table.item(row, 4).setForeground(QColor("#30343a"))
        for task in self.project.schema["tasks"]:
            value = ann.get(task["id"])
            if value:
                entry = value[0] if task["per_character"] else value
                selected = entry.get("label")
                if isinstance(selected, list):
                    selected = selected[0] if selected else ""
                color = next((label["color"] for label in task["labels"] if label["value"] == selected), None)
                if color:
                    self.table.item(row, 4).setForeground(QColor(color))
                    break
        for col in range(5):
            item = self.table.item(row, col)
            item.setToolTip(tooltip)
            item.setBackground(QColor("#fff1d9") if warning else QColor("#ffffff"))
        if update_status:
            self.update_status()

    def select(self, index, seek=True):
        if not self.project or not 0 <= index < len(self.project.lines):
            return
        if self.index != index:
            self.flush()
        self.index = index
        line = self.project.lines[index]
        if seek and self.project.movie:
            self.player.setPosition(line["start"])
        shown = [line["text1"]] if self.mode == "one" else [line["text2"]] if self.mode == "two" else [line["text1"], line["text2"]]
        self.current.setText(f"#{line['id']} · {timecode(line['start'])} → {timecode(line['end'])}\n" + "\n".join(x for x in shown if x))
        self.current.setLayoutDirection(direction(shown[0] if shown else ""))
        self.loading = True
        ann = self.project.annotations.get(line["uid"], {})
        for task in self.project.schema["tasks"]:
            self.cards[task["id"]].set_value(ann.get(task["id"]))
            self.cards[task["id"]].highlight(task["id"] == self.active_task, core.incomplete({task["id"]: ann.get(task["id"])}, {"tasks": [task]}))
        self.loading = False
        self.activate_task(self.active_task)
        self.table.selectRow(index)
        self.table.scrollToItem(self.table.item(index, 0))

    def edited(self):
        if self.loading or not self.project or self.index < 0:
            return
        data = {key: card.value() for key, card in self.cards.items()}
        line = self.project.lines[self.index]
        self.project.set_annotation(line["uid"], data)
        self.refresh_row(self.index)
        for task in self.project.schema["tasks"]:
            self.cards[task["id"]].highlight(task["id"] == self.active_task,
                core.incomplete({task["id"]: self.project.annotations.get(line["uid"], {}).get(task["id"])}, {"tasks": [task]}))
        self.timer.start()
        self.statusBar().showMessage(self.status_text() + " · unsaved")

    def status_text(self):
        if not self.project:
            return "No project"
        annotated = sum(bool(self.project.annotations.get(line["uid"])) for line in self.project.lines)
        incomplete = sum(core.incomplete(self.project.annotations.get(line["uid"], {}), self.project.schema) for line in self.project.lines)
        return f"{annotated} / {len(self.project.lines)} lines annotated · {incomplete} incomplete"

    def update_status(self):
        self.statusBar().showMessage(self.status_text())

    def flush(self):
        if not self.project or not self.timer.isActive():
            return
        self.timer.stop()
        try:
            self.project.save()
            from datetime import datetime
            self.statusBar().showMessage(self.status_text() + " · saved " + datetime.now().strftime("%H:%M:%S"))
        except OSError as exc:
            self.statusBar().showMessage(self.status_text() + " · save failed: " + str(exc))
            QMessageBox.critical(self, "Save failed", str(exc))

    def update_label_filter(self):
        self.label_filter.blockSignals(True)
        self.label_filter.clear()
        self.label_filter.addItem("All labels", "")
        if self.project:
            task_id = self.task_filter.currentData()
            for task in self.project.schema["tasks"]:
                if task["id"] == task_id:
                    for label in task["labels"]:
                        self.label_filter.addItem(label["value"], label["value"])
        self.label_filter.blockSignals(False)
        self.filter_rows()

    def filter_rows(self):
        if not self.project:
            return
        query = self.search.text().casefold()
        task_id, label = self.task_filter.currentData(), self.label_filter.currentData()
        status = self.status_filter.currentText()
        for row, line in enumerate(self.project.lines):
            ann = self.project.annotations.get(line["uid"], {})
            searchable = line["text1"] + " " + line["text2"] + " " + json.dumps(ann, ensure_ascii=False)
            matched = query in searchable.casefold()
            matched &= status == "All" or (status == "Annotated" and bool(ann)) or (status == "Unannotated" and not ann) or (status == "Incomplete" and core.incomplete(ann, self.project.schema))
            if task_id:
                value = ann.get(task_id)
                matched &= bool(value)
                if label and value:
                    entries = value if isinstance(value, list) else [value]
                    matched &= any(label in entry.get("label", []) if isinstance(entry.get("label"), list) else entry.get("label") == label for entry in entries)
            self.table.setRowHidden(row, not matched)

    def activate_task(self, task_id):
        if self.loading:
            return
        self.active_task = task_id
        for key, card in self.cards.items():
            value = {}
            if self.project and self.index >= 0:
                uid = self.project.lines[self.index]["uid"]
                value = {key: self.project.annotations.get(uid, {}).get(key)}
            card.highlight(key == task_id, core.incomplete(value, {"tasks": [card.task]}))

    def position_changed(self, position):
        self.seek.blockSignals(True)
        self.seek.setValue(position)
        self.seek.blockSignals(False)
        self.clock.setText(timecode(position))
        if self.play_line_end is not None and position >= self.play_line_end:
            self.player.pause()
            self.play_line_end = None
        if not self.project:
            return
        match = next((i for i, line in enumerate(self.project.lines) if line["start"] <= position < line["end"]), None)
        if match is not None:
            line = self.project.lines[match]
            shown = [line["text1"]] if self.mode == "one" else [line["text2"]] if self.mode == "two" else [line["text1"], line["text2"]]
            self.caption.setText("\n".join(x for x in shown if x))
            self.caption.setLayoutDirection(direction(shown[0] if shown else ""))
            if self.follow and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and match != self.index and not isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit)):
                self.select(match, seek=False)
        else:
            self.caption.setText("")

    def play_pause(self):
        if not self.project or not self.project.movie:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.play_line_end = None
            self.player.play()

    def play_line(self):
        if self.project and self.project.movie and self.index >= 0:
            line = self.project.lines[self.index]
            self.player.setPosition(line["start"])
            self.play_line_end = line["end"]
            self.player.play()

    def toggle_export(self, checked):
        self.include_unannotated = checked

    def export(self, fmt):
        if not self.project:
            return
        self.flush()
        path, _ = QFileDialog.getSaveFileName(self, "Export " + fmt.upper(), str(self.project.path.with_suffix("." + fmt)), "All files (*)")
        if path:
            try:
                self.project.export(path, fmt, self.include_unannotated)
                self.statusBar().showMessage("Exported " + path)
            except OSError as exc:
                QMessageBox.critical(self, "Export failed", str(exc))

    def set_mode(self, mode):
        self.mode = mode
        self.table.setColumnHidden(2, mode == "two")
        self.table.setColumnHidden(3, mode == "one")
        if self.project and self.index >= 0:
            self.select(self.index, seek=False)
            self.position_changed(self.player.position())

    def shortcuts(self):
        bindings = {"J": lambda: self.select(self.index+1), "Down": lambda: self.select(self.index+1),
                    "K": lambda: self.select(self.index-1), "Up": lambda: self.select(self.index-1),
                    "N": self.next_unannotated, "Space": self.play_pause, "P": self.play_line,
                    "T": self.next_task, "R": self.focus_rationale}
        for n in range(1, 10):
            bindings[str(n)] = lambda i=n-1: self.pick_label(i)
        for key, callback in bindings.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda cb=callback: self.run_shortcut(cb))
        escape = QShortcut(QKeySequence("Escape"), self)
        escape.activated.connect(lambda: self.setFocus())

    def run_shortcut(self, callback):
        if isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit)):
            return
        callback()

    def next_unannotated(self):
        if not self.project:
            return
        for i in range(self.index+1, len(self.project.lines)):
            if self.project.lines[i]["uid"] not in self.project.annotations:
                self.select(i)
                break

    def next_task(self):
        if self.project:
            ids = [task["id"] for task in self.project.schema["tasks"]]
            self.activate_task(ids[(ids.index(self.active_task)+1) % len(ids)])

    def pick_label(self, index):
        if self.project:
            self.cards[self.active_task].pick(index)

    def focus_rationale(self):
        if self.project:
            self.cards[self.active_task].focus_rationale()

    def closeEvent(self, event):
        self.flush()
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline native screenplay annotation")
    for flag in ("movie", "subs", "subs2", "lang1", "lang2", "annotator", "schema", "outdir", "encoding"):
        parser.add_argument("--" + flag, default="")
    parser.add_argument("--dump-schema", metavar="FILE")
    args = parser.parse_args(argv)
    if args.dump_schema:
        core.atomic_json(args.dump_schema, core.DEFAULT_SCHEMA)
        return 0
    app = QApplication(sys.argv[:1])
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow(vars(args) if args.subs else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
