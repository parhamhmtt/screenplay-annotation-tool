"""Native film labeling workstation. Load any video + SRT, label, compare versions, export."""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import core

VIDEO_TYPES = [("Video", "*.mp4 *.mkv *.webm *.mov *.avi *.m4v"), ("All files", "*.*")]
SRT_TYPES = [("SubRip", "*.srt"), ("All files", "*.*")]
JSONL_TYPES = [("JSONL", "*.jsonl *.json"), ("All files", "*.*")]


def text_value(widget: tk.Text) -> str:
    return widget.get("1.0", "end-1c").strip()


def set_text(widget: tk.Text, value: str) -> None:
    widget.delete("1.0", "end")
    widget.insert("1.0", value or "")


class Studio:
    def __init__(self, root: tk.Tk):
        core.init_db()
        self.root = root
        self.root.title("Film Labeler")
        self.root.geometry("1480x900")
        self.root.minsize(1080, 680)
        self.film_id = None
        self.segment_id = None
        self.segment = None
        self.films = []
        self.rows = []
        self.actors = []
        self.versions = []
        self.video_process = None
        self.photo = None
        self.task_vars = {}
        self.task_rationales = {}
        self.task_evidence = {}
        self.task_frames = {}
        self.status = tk.StringVar(value="Ready")
        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="All")
        self.kind_var = tk.StringVar(value="All types")
        self.task_filter = tk.StringVar(value="All tasks")
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Hint.TLabel", foreground="#4d5f5c")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        with core.connect() as db:
            row = db.execute("SELECT id,username,role FROM users WHERE username='reviewer'").fetchone()
            self.user = {"id": row["id"], "username": row["username"], "role": row["role"]}
        self.build()
        self.reload_films()

    def run(self, action):
        try:
            action()
        except (ValueError, sqlite3.Error, OSError, KeyError, json.JSONDecodeError) as exc:
            messagebox.showerror("Film Labeler", str(exc), parent=self.root)
            self.status.set(str(exc))

    def is_reviewer(self):
        return self.user["role"] == "reviewer"

    def build(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Film Labeler", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="  Any film · timed labels · version compare", style="Hint.TLabel").pack(side="left")
        ttk.Label(top, text="Identity").pack(side="right", padx=(16, 4))
        self.actor_var = tk.StringVar()
        self.actor_combo = ttk.Combobox(top, textvariable=self.actor_var, state="readonly", width=22)
        self.actor_combo.pack(side="right")
        self.actor_combo.bind("<<ComboboxSelected>>", lambda _e: self.run(self.switch_actor))
        ttk.Button(top, text="Export", command=lambda: self.run(self.export_dialog)).pack(side="right", padx=4)
        ttk.Button(top, text="Agreement", command=lambda: self.run(self.show_agreement)).pack(side="right", padx=4)
        ttk.Button(top, text="Add identity", command=lambda: self.run(self.add_user)).pack(side="right", padx=4)

        bar = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        bar.pack(fill="x")
        ttk.Label(bar, text="Film").pack(side="left")
        self.film_var = tk.StringVar()
        self.film_combo = ttk.Combobox(bar, textvariable=self.film_var, state="readonly", width=42)
        self.film_combo.pack(side="left", padx=6)
        self.film_combo.bind("<<ComboboxSelected>>", lambda _e: self.choose_film())
        for label, method in (
            ("Open project", self.open_project),
            ("New film", self.new_film),
            ("Link video", self.link_video),
            ("Import SRT", self.import_srt),
            ("Import labels", self.import_labels),
            ("Add record", self.add_record),
            ("SDH note", self.sdh_dialog),
        ):
            ttk.Button(bar, text=label, command=lambda m=method: self.run(m)).pack(side="left", padx=2)

        paned = ttk.Panedwindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        left, middle, right = ttk.Frame(paned), ttk.Frame(paned), ttk.Frame(paned)
        paned.add(left, weight=2)
        paned.add(middle, weight=3)
        paned.add(right, weight=3)
        self.build_list(left)
        self.build_source(middle)
        self.build_labels(right)
        ttk.Label(self.root, textvariable=self.status, padding=(10, 6)).pack(fill="x")
        self.root.bind("<Control-s>", lambda _e: self.run(self.save_current))
        self.root.bind("<Control-S>", lambda _e: self.run(self.save_current))

    def build_list(self, frame):
        head = ttk.Frame(frame)
        head.pack(fill="x", pady=(0, 4))
        ttk.Label(head, text="Records", style="Heading.TLabel").pack(side="left")
        ttk.Combobox(head, textvariable=self.filter_var, state="readonly", width=16,
                     values=["All", "Unlabeled", "My labels", "Has versions", "Gold", "Description"]).pack(side="right")
        self.filter_var.trace_add("write", lambda *_: self.fill_list())
        ttk.Combobox(frame, textvariable=self.kind_var, state="readonly",
                     values=["All types", "dialogue", "description", "character_event"]).pack(fill="x", pady=2)
        self.kind_var.trace_add("write", lambda *_: self.fill_list())
        search = ttk.Entry(frame, textvariable=self.search_var)
        search.pack(fill="x", pady=(0, 4))
        self.search_var.trace_add("write", lambda *_: self.fill_list())
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("time", "kind", "text", "state"), show="headings", selectmode="browse")
        for key, title, width in (("time", "Time", 92), ("kind", "Type", 88), ("text", "Text", 260), ("state", "Labels", 110)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=key == "text")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.select_segment())
        nav = ttk.Frame(frame)
        nav.pack(fill="x", pady=4)
        ttk.Button(nav, text="Previous", command=lambda: self.step(-1)).pack(side="left")
        ttk.Button(nav, text="Next", command=lambda: self.step(1)).pack(side="left", padx=4)

    def build_source(self, frame):
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Film & sentence", style="Heading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.preview = ttk.Label(frame, text="Link a video to preview a frame and play the selected turn.", anchor="center")
        self.preview.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=6)
        frame.rowconfigure(1, weight=1)
        controls = ttk.Frame(frame)
        controls.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(controls, text="Play ±5 s", command=lambda: self.run(self.play_segment)).pack(side="left")
        ttk.Button(controls, text="Stop", command=self.stop_video).pack(side="left", padx=4)
        self.media_label = ttk.Label(controls, text="No video")
        self.media_label.pack(side="left", padx=6)
        self.fields = {}
        for row, (key, label, values) in enumerate((
            ("kind", "Record type", sorted(core.KINDS)),
            ("start_ms", "Start", None),
            ("end_ms", "End", None),
            ("speaker", "Speaker", None),
            ("translation_status", "Translation", list(core.TRANSLATION_STATUSES)),
        ), start=3):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
            widget = ttk.Combobox(frame, values=values, state="readonly") if values else ttk.Entry(frame)
            widget.grid(row=row, column=1, sticky="ew", padx=5, pady=2)
            self.fields[key] = widget
        self.text_fields = {}
        for offset, (key, label, height) in enumerate((("text_fa", "Original text", 5), ("text_en", "English translation", 4), ("note", "Editor note", 2))):
            ttk.Label(frame, text=label).grid(row=8 + offset * 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
            widget = tk.Text(frame, height=height, wrap="word", font=("Segoe UI", 10), undo=True)
            widget.grid(row=9 + offset * 2, column=0, columnspan=2, sticky="nsew")
            self.text_fields[key] = widget
        tools = ttk.Frame(frame)
        tools.grid(row=14, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Button(tools, text="Save sentence", command=lambda: self.run(self.save_segment)).pack(side="left")
        ttk.Button(tools, text="Split", command=lambda: self.run(self.split_segment)).pack(side="left", padx=3)
        ttk.Button(tools, text="Merge next", command=lambda: self.run(self.merge_next)).pack(side="left")
        ttk.Label(tools, text="Changing text or time invalidates older labels.", style="Hint.TLabel").pack(side="left", padx=8)

    def build_labels(self, frame):
        ttk.Label(frame, text="Labels", style="Heading.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(frame, text="Each task has its own classes. Every chosen class needs a rationale.",
                  style="Hint.TLabel", wraplength=420).pack(anchor="w")
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill="both", expand=True, pady=6)
        annotate = ttk.Frame(self.notebook)
        compare = ttk.Frame(self.notebook)
        self.notebook.add(annotate, text="Annotate")
        self.notebook.add(compare, text="Versions")
        canvas = tk.Canvas(annotate, highlightthickness=0)
        scroll = ttk.Scrollbar(annotate, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", tags="inside")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure("inside", width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        task_bar = ttk.Frame(inner)
        task_bar.pack(fill="x", pady=4)
        ttk.Label(task_bar, text="Focus").pack(side="left")
        values = ["All tasks"] + [f"{meta['number']}. {meta['title']}" for meta in core.TASK_META.values()]
        focus = ttk.Combobox(task_bar, textvariable=self.task_filter, values=values, state="readonly", width=28)
        focus.pack(side="left", padx=6)
        focus.bind("<<ComboboxSelected>>", lambda _e: self.apply_task_focus())
        for key, meta in core.TASK_META.items():
            box = ttk.LabelFrame(inner, text=f"{meta['number']}. {meta['title']}", padding=6)
            box.pack(fill="x", pady=4)
            ttk.Label(box, text=meta["help"], wraplength=400, style="Hint.TLabel").pack(anchor="w", pady=(0, 4))
            var = tk.StringVar()
            for label in meta["labels"]:
                ttk.Radiobutton(box, text=f"{label}    {meta['gloss'].get(label, '')}", value=label, variable=var).pack(anchor="w")
            evid = ttk.Entry(box)
            evid.insert(0, "")
            ttk.Label(box, text="Quoted evidence (optional)").pack(anchor="w", pady=(6, 0))
            evid.pack(fill="x")
            ttk.Label(box, text="Rationale (required)").pack(anchor="w", pady=(6, 0))
            rationale = tk.Text(box, height=3, wrap="word", font=("Segoe UI", 9), undo=True)
            rationale.pack(fill="x")
            self.task_frames[key] = box
            self.task_vars[key] = var
            self.task_evidence[key] = evid
            self.task_rationales[key] = rationale
        self.description_group = ttk.LabelFrame(inner, text="Scene / sound description", padding=6)
        self.description_group.pack(fill="x", pady=4)
        ttk.Label(self.description_group, text="Use only when missing visual or audio context would hide the action, place, speaker, or a meaningful sound.",
                  wraplength=400, style="Hint.TLabel").pack(anchor="w")
        self.desc_kind = ttk.Combobox(self.description_group, values=core.DESCRIPTION_KINDS, state="readonly")
        self.desc_kind.pack(fill="x", pady=4)
        self.desc_fields = {}
        for key, label in (("description_fa", "Description (original language)"),
                           ("description_en", "Description (English)"),
                           ("rationale", "Why this context is needed")):
            ttk.Label(self.description_group, text=label).pack(anchor="w", pady=(4, 0))
            widget = tk.Text(self.description_group, height=3, wrap="word", font=("Segoe UI", 9), undo=True)
            widget.pack(fill="x")
            self.desc_fields[key] = widget
        buttons = ttk.Frame(inner)
        buttons.pack(fill="x", pady=10)
        ttk.Button(buttons, text="Save labels", command=lambda: self.run(self.save_labels)).pack(side="left")
        ttk.Button(buttons, text="Save and next", command=lambda: self.run(self.save_and_next)).pack(side="left", padx=4)

        compare.columnconfigure(0, weight=1)
        ttk.Label(compare, text="Every saved identity is a version on this sentence. Select one to inspect or adopt as gold.",
                  wraplength=420, style="Hint.TLabel").pack(anchor="w", pady=4)
        self.version_list = tk.Listbox(compare, height=6, exportselection=False)
        self.version_list.pack(fill="x")
        self.version_list.bind("<<ListboxSelect>>", lambda _e: self.show_version())
        self.version_text = tk.Text(compare, wrap="word", font=("Consolas", 10), height=16)
        self.version_text.pack(fill="both", expand=True, pady=6)
        row = ttk.Frame(compare)
        row.pack(fill="x")
        ttk.Button(row, text="Load into editor", command=lambda: self.run(self.load_version_into_editor)).pack(side="left")
        ttk.Button(row, text="Adopt as gold", command=lambda: self.run(self.adopt_version)).pack(side="left", padx=4)

    def reload_actors(self):
        with core.connect() as db:
            self.actors = core.list_users(db)
        self.actor_combo["values"] = [core.actor_label(a["username"], a["role"]) for a in self.actors]
        index = next((i for i, a in enumerate(self.actors) if a["id"] == self.user["id"]), 0)
        if self.actors:
            self.actor_combo.current(index)

    def switch_actor(self):
        index = self.actor_combo.current()
        if index < 0:
            return
        chosen = self.actors[index]
        self.user = {"id": chosen["id"], "username": chosen["username"], "role": chosen["role"]}
        self.refresh()
        self.status.set(f"Working as {core.actor_label(chosen['username'], chosen['role'])}")

    def reload_films(self):
        self.reload_actors()
        with core.connect() as db:
            self.films = core.list_films(db)
        self.film_combo["values"] = [f"{f['id']}  {f['title']}" for f in self.films]
        if self.films:
            index = next((i for i, f in enumerate(self.films) if f["id"] == self.film_id), 0)
            self.film_combo.current(index)
            self.choose_film()

    def choose_film(self):
        index = self.film_combo.current()
        if index < 0:
            return
        self.film_id = self.films[index]["id"]
        self.segment_id = None
        self.refresh()

    def film(self):
        return next(f for f in self.films if f["id"] == self.film_id)

    def refresh(self, selected=None):
        if not self.film_id:
            return
        keep = selected or self.segment_id
        with core.connect() as db:
            self.films = core.list_films(db)
            self.rows = core.list_segments(db, self.film_id, self.user["id"])
        self.fill_list()
        path = core.media_path(self.film())
        self.media_label.configure(text=path.name if path else "No video")
        if keep and self.tree.exists(str(keep)):
            self.tree.selection_set(str(keep))
            self.tree.see(str(keep))
            self.select_segment()
        else:
            self.segment = None
            self.segment_id = None
        done = sum(1 for r in self.rows if r["kind"] == "dialogue" and (r["adjudicated"] if self.is_reviewer() else r["my_complete"]))
        dialogues = sum(1 for r in self.rows if r["kind"] == "dialogue")
        self.status.set(f"{self.film()['title']}  ·  {len(self.rows)} records  ·  {done}/{dialogues} dialogue labeled  ·  {core.actor_label(self.user['username'], self.user['role'])}")

    def fill_list(self):
        if not hasattr(self, "tree"):
            return
        selected = self.segment_id
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        query = self.search_var.get().casefold().strip()
        filt = self.filter_var.get()
        kind = self.kind_var.get()
        for row in self.rows:
            if kind != "All types" and row["kind"] != kind:
                continue
            if filt == "Unlabeled" and (row["adjudicated"] if self.is_reviewer() else row["my_complete"]):
                continue
            if filt == "My labels" and not row["my_annotation"]:
                continue
            if filt == "Has versions" and row["annotation_count"] < 1:
                continue
            if filt == "Gold" and not row["adjudicated"]:
                continue
            if filt == "Description" and row["kind"] != "description":
                continue
            blob = f"{row['text_fa']} {row['text_en']} {row['speaker']}".casefold()
            if query and query not in blob:
                continue
            names = ",".join(row["version_names"][:2])
            state = "Gold" if row["adjudicated"] else (names or f"{row['annotation_count']} ver")
            if not self.is_reviewer():
                state = "Done" if row["my_complete"] else ("Draft" if row["my_annotation"] else "—")
            text = (row["text_fa"] or row["text_en"] or row["speaker"] or "[empty]").replace("\n", " ")[:80]
            self.tree.insert("", "end", iid=str(row["id"]), values=(core.clock(row["start_ms"]), row["kind"], text, state))
        if selected and self.tree.exists(str(selected)):
            self.tree.selection_set(str(selected))

    def select_segment(self):
        selected = self.tree.selection()
        if not selected:
            return
        self.segment_id = int(selected[0])
        self.segment = next(r for r in self.rows if r["id"] == self.segment_id)
        row = self.segment
        writable = self.is_reviewer()
        for key, widget in self.fields.items():
            widget.configure(state="normal")
            if isinstance(widget, ttk.Combobox):
                widget.set(row[key])
                widget.configure(state="readonly" if writable else "disabled")
            else:
                widget.delete(0, "end")
                widget.insert(0, core.clock(row[key]) if key.endswith("_ms") else row[key])
                widget.configure(state="normal" if writable else "readonly")
        for key, widget in self.text_fields.items():
            widget.configure(state="normal")
            set_text(widget, row[key])
            widget.configure(state="normal" if writable else "disabled")
        payload = row["gold"] if self.is_reviewer() and row["gold"] else (row["my_annotation"] or {})
        self.fill_annotation(payload)
        self.fill_versions()
        self.preview_frame(row["start_ms"])

    def fill_annotation(self, payload):
        kind = self.segment["kind"] if self.segment else "dialogue"
        payload = payload or {}
        for key, meta in core.TASK_META.items():
            visible = kind in meta["applies_to"]
            if visible:
                self.task_frames[key].pack(fill="x", pady=4)
            else:
                self.task_frames[key].pack_forget()
            item = payload.get(key) if isinstance(payload.get(key), dict) else {}
            self.task_vars[key].set(item.get("label", ""))
            self.task_evidence[key].delete(0, "end")
            self.task_evidence[key].insert(0, item.get("evidence", ""))
            set_text(self.task_rationales[key], item.get("rationale", ""))
        if kind == "description":
            self.description_group.pack(fill="x", pady=4)
        else:
            self.description_group.pack_forget()
        self.desc_kind.set(payload.get("description_kind", ""))
        for key, widget in self.desc_fields.items():
            set_text(widget, payload.get(key, ""))
        self.apply_task_focus()

    def apply_task_focus(self):
        if not self.segment or self.segment["kind"] == "description":
            return
        focus = self.task_filter.get()
        for key, meta in core.TASK_META.items():
            if self.segment["kind"] not in meta["applies_to"]:
                self.task_frames[key].pack_forget()
                continue
            show = focus == "All tasks" or focus.startswith(f"{meta['number']}.")
            if show:
                self.task_frames[key].pack(fill="x", pady=4)
            else:
                self.task_frames[key].pack_forget()

    def collect_annotation(self):
        if not self.segment:
            raise ValueError("Choose a record")
        kind = self.segment["kind"]
        if kind == "description":
            return {"description_kind": self.desc_kind.get(), **{key: text_value(widget) for key, widget in self.desc_fields.items()}}
        payload = {}
        for key, meta in core.TASK_META.items():
            if kind not in meta["applies_to"]:
                continue
            label = self.task_vars[key].get()
            rationale = text_value(self.task_rationales[key])
            evidence = self.task_evidence[key].get().strip()
            if label or rationale:
                item = {"label": label, "rationale": rationale}
                if evidence:
                    item["evidence"] = evidence
                payload[key] = item
        return payload

    def fill_versions(self):
        self.version_list.delete(0, "end")
        self.versions = []
        if not self.segment:
            return
        if self.segment.get("gold"):
            self.versions.append({"username": "gold", "role": "reviewer", "payload": self.segment["gold"], "segment_version": self.segment["version"]})
            self.version_list.insert("end", "Gold (chosen version)")
        for item in self.segment.get("versions", []):
            self.versions.append(item)
            mark = "complete" if core.annotation_is_complete(self.segment["kind"], item["payload"]) else "partial"
            stale = "" if item["segment_version"] == self.segment["version"] else " · old text"
            self.version_list.insert("end", f"{core.actor_label(item['username'], item['role'])}  ·  {mark}{stale}")
        if self.versions:
            self.version_list.selection_set(0)
            self.show_version()
        else:
            set_text(self.version_text, "No saved versions for this sentence yet.")

    def selected_version(self):
        choice = self.version_list.curselection()
        if not choice:
            raise ValueError("Select a version")
        return self.versions[choice[0]]

    def show_version(self):
        choice = self.version_list.curselection()
        if not choice:
            return
        item = self.versions[choice[0]]
        set_text(self.version_text, core.format_payload(item["payload"]))
        self.version_text.configure(state="normal")

    def load_version_into_editor(self):
        self.fill_annotation(self.selected_version()["payload"])
        self.notebook.select(0)
        self.status.set("Loaded version into the editor. Save gold or labels to keep it.")

    def adopt_version(self):
        if not self.is_reviewer():
            raise ValueError("Only the reviewer can choose the gold version")
        payload = self.selected_version()["payload"]
        with core.connect() as db:
            core.adopt_version(db, self.segment_id, self.user, payload)
        self.refresh(self.segment_id)
        self.status.set("Adopted this version as gold")

    def save_labels(self):
        if not self.segment:
            raise ValueError("Choose a record")
        payload = self.collect_annotation()
        with core.connect() as db:
            kind = core.save_annotation(db, self.segment_id, self.user, payload, complete=self.is_reviewer())
        self.refresh(self.segment_id)
        self.status.set("Gold saved" if kind == "gold" else "Labels saved")

    def save_and_next(self):
        self.save_labels()
        self.step(1)

    def save_current(self):
        if self.notebook.index(self.notebook.select()) == 0:
            if self.is_reviewer():
                self.save_segment()
            self.save_labels()

    def save_segment(self):
        if not self.is_reviewer():
            raise ValueError("The reviewer maintains source text and timing")
        if not self.segment:
            raise ValueError("Choose a record")
        data = {}
        for key, widget in self.fields.items():
            data[key] = widget.get().strip()
        data.update({key: text_value(widget) for key, widget in self.text_fields.items()})
        data["start_ms"] = core.parse_clock(data["start_ms"])
        data["end_ms"] = core.parse_clock(data["end_ms"])
        if self.segment["annotation_count"] and not messagebox.askyesno(
            "Existing labels", "This edit will bump the record version. Older labels stay visible but drop out of gold until redone. Continue?",
            parent=self.root):
            return
        with core.connect() as db:
            core.update_segment(db, self.segment_id, self.user["id"], **data)
        self.refresh(self.segment_id)
        self.status.set("Sentence saved")

    def split_segment(self):
        if not self.segment:
            raise ValueError("Choose a record")
        value = simpledialog.askstring("Split", "Cut time HH:MM:SS.mmm",
                                       initialvalue=core.clock((self.segment["start_ms"] + self.segment["end_ms"]) // 2),
                                       parent=self.root)
        if not value:
            return
        with core.connect() as db:
            core.split_segment(db, self.segment_id, core.parse_clock(value), self.user["id"])
        self.refresh(self.segment_id)

    def merge_next(self):
        if not self.segment:
            raise ValueError("Choose a record")
        if not messagebox.askyesno("Merge", "Merge this record with the next adjacent record of the same type?", parent=self.root):
            return
        with core.connect() as db:
            core.merge_next(db, self.segment_id, self.user["id"])
        self.refresh(self.segment_id)

    def add_record(self):
        kind = simpledialog.askstring("New record", "Type: dialogue, description or character_event",
                                      initialvalue="description", parent=self.root)
        if not kind:
            return
        start = core.parse_clock(simpledialog.askstring("New record", "Start HH:MM:SS.mmm", initialvalue="00:00:00.000", parent=self.root) or "")
        end = core.parse_clock(simpledialog.askstring("New record", "End HH:MM:SS.mmm", initialvalue=core.clock(start + 3000), parent=self.root) or "")
        with core.connect() as db:
            new_id = core.create_segment(db, self.film_id, kind.strip(), start, end, self.user["id"])
        self.refresh(new_id)

    def open_project(self):
        title = simpledialog.askstring("Open project", "Film title:", parent=self.root)
        if not title:
            return
        video = filedialog.askopenfilename(parent=self.root, title="Choose video", filetypes=VIDEO_TYPES)
        if not video:
            return
        srt = filedialog.askopenfilename(parent=self.root, title="Choose original-language SRT", filetypes=SRT_TYPES)
        if not srt:
            return
        en = filedialog.askopenfilename(parent=self.root, title="Optional English SRT — cancel to skip", filetypes=SRT_TYPES)
        with core.connect() as db:
            film_id = core.create_film(db, title, self.user["id"])
            core.link_video(db, film_id, video, self.user["id"])
            result = core.import_srt(db, film_id, "fa", srt, self.user["id"])
            extra = ""
            if en:
                core.import_srt(db, film_id, "en", en, self.user["id"])
                extra = " English cues aligned by time."
            self.film_id = film_id
        self.reload_films()
        self.status.set(f"Loaded {result['cues']} cues.{extra}")

    def new_film(self):
        title = simpledialog.askstring("New film", "Film title:", parent=self.root)
        if not title:
            return
        with core.connect() as db:
            self.film_id = core.create_film(db, title, self.user["id"])
        self.reload_films()

    def link_video(self):
        path = filedialog.askopenfilename(parent=self.root, title="Link local video", filetypes=VIDEO_TYPES)
        if not path:
            return
        with core.connect() as db:
            core.link_video(db, self.film_id, path, self.user["id"])
        self.reload_films()

    def import_srt(self):
        kind = simpledialog.askstring("Import SRT", "Track: fa (original), en, or sdh", initialvalue="fa", parent=self.root)
        if not kind:
            return
        path = filedialog.askopenfilename(parent=self.root, title="Choose SRT", filetypes=SRT_TYPES)
        if not path:
            return
        with core.connect() as db:
            result = core.import_srt(db, self.film_id, kind, path, self.user["id"])
        self.refresh()
        extra = f"  {result['description_candidates']} description candidates." if result["description_candidates"] is not None else ""
        messagebox.showinfo("Imported", f"{result['cues']} cues imported.{extra}", parent=self.root)

    def import_labels(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import label JSONL as a named version", filetypes=JSONL_TYPES)
        if not path:
            return
        name = simpledialog.askstring("Version name", "Name for this label set (3–40 characters):",
                                      initialvalue=Path(path).stem[:32], parent=self.root)
        if name is None:
            return
        with core.connect() as db:
            result = core.import_label_jsonl(db, self.film_id, path, self.user["id"], name)
        self.reload_actors()
        self.refresh(self.segment_id)
        messagebox.showinfo("Imported labels", f"{result['applied']} of {result['rows']} rows attached as {result['username']}", parent=self.root)

    def add_user(self):
        name = simpledialog.askstring("New identity", "Username (3–40 characters):", parent=self.root)
        if not name:
            return
        role = simpledialog.askstring("New identity", "Role: annotator or reviewer", initialvalue="annotator", parent=self.root)
        if not role:
            return
        with core.connect() as db:
            core.new_user(db, name.strip(), os.urandom(12).hex(), role.strip().lower())
        self.reload_actors()

    def sdh_dialog(self):
        film = self.film()
        status = simpledialog.askstring("SDH", "Status: unknown, candidate, verified, rejected, unavailable",
                                        initialvalue=film["sdh_status"], parent=self.root)
        if status is None:
            return
        status = status.strip().lower()
        if status not in core.SDH_STATUSES:
            raise ValueError("Invalid SDH status")
        note = simpledialog.askstring("SDH", "Source, release, and what was checked:", initialvalue=film["sdh_note"], parent=self.root)
        if note is None:
            return
        if status in {"verified", "rejected", "unavailable"} and len(note.strip()) < 8:
            raise ValueError("Explain the SDH finding")
        with core.connect() as db:
            db.execute("UPDATE films SET sdh_status=?,sdh_note=? WHERE id=?", (status, note[:2000], self.film_id))
            core.audit(db, self.user["id"], "sdh_review", self.film_id, f"{status}: {note}")
        self.reload_films()

    def show_agreement(self):
        rows = [{"kind": r["kind"], "versions": r["versions"]} for r in self.rows]
        result = core.compute_agreement(rows)
        lines = [f"Records: {len(self.rows)}", f"With two complete versions: {sum(r['annotation_count'] >= 2 for r in self.rows)}", ""]
        for key, value in result.items():
            title = core.TASK_META[key]["title"]
            lines.append(f"{title}: n={value['n']}, agreement={value['agreement']}, Cohen κ={value['kappa']}")
        if len(lines) == 3:
            lines.append("Need two complete independent versions on the same sentence version.")
        messagebox.showinfo("Agreement", "\n".join(lines), parent=self.root)

    def export_dialog(self):
        kind = simpledialog.askstring("Export", "Choose: gold, all, fa-srt, en-srt", initialvalue="gold", parent=self.root)
        if not kind:
            return
        kind = kind.strip().lower()
        if kind not in {"gold", "all", "fa-srt", "en-srt"}:
            raise ValueError("Choose gold, all, fa-srt or en-srt")
        suffix = ".srt" if kind.endswith("srt") else ".jsonl"
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=suffix, initialfile=f"film-{self.film_id}-{kind}{suffix}")
        if not path:
            return
        with core.connect() as db:
            if kind.endswith("srt"):
                output = core.export_srt(db, self.film_id, kind[:2])
            else:
                output = core.export_jsonl(db, self.film_id, "all" if kind == "all" else "gold")
            core.audit(db, self.user["id"], "export", self.film_id, kind)
        Path(path).write_text(output, encoding="utf-8")
        messagebox.showinfo("Exported", path, parent=self.root)

    def preview_frame(self, position):
        path = core.media_path(self.film()) if self.film_id else None
        if not path:
            self.preview.configure(image="", text="No linked video for this film")
            return
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.preview.configure(image="", text="Install FFmpeg for stills. Play still works if ffplay is on PATH.")
            return
        try:
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{position/1000:.3f}", "-i", str(path),
                       "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2pipe", "-vcodec", "png", "-"]
            result = subprocess.run(command, capture_output=True, timeout=15, check=True)
            self.photo = tk.PhotoImage(data=base64.b64encode(result.stdout))
            self.preview.configure(image=self.photo, text="")
        except (OSError, subprocess.SubprocessError, tk.TclError) as exc:
            self.preview.configure(image="", text=f"Preview unavailable: {exc}")

    def play_segment(self):
        if not self.segment:
            raise ValueError("Choose a record")
        path = core.media_path(self.film())
        if not path:
            raise ValueError("Link a local video first")
        player = shutil.which("ffplay")
        if not player:
            raise ValueError("Install FFmpeg so ffplay is available")
        self.stop_video()
        start = max(0, self.segment["start_ms"] - 5000)
        duration = self.segment["end_ms"] - start + 5000
        command = [player, "-hide_banner", "-loglevel", "error", "-autoexit",
                   "-window_title", f"{self.film()['title']} · {core.clock(start)}",
                   "-ss", f"{start/1000:.3f}", "-t", f"{duration/1000:.3f}", "-i", str(path)]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.video_process = subprocess.Popen(command, creationflags=flags)

    def stop_video(self):
        if self.video_process and self.video_process.poll() is None:
            self.video_process.terminate()
        self.video_process = None

    def step(self, delta):
        ids = [int(i) for i in self.tree.get_children()]
        if not ids:
            return
        if self.segment_id in ids:
            index = ids.index(self.segment_id) + delta
        else:
            index = 0
        if 0 <= index < len(ids):
            self.tree.selection_set(str(ids[index]))
            self.tree.see(str(ids[index]))
            self.select_segment()

    def close(self):
        self.stop_video()
        self.root.destroy()


def main():
    root = tk.Tk()
    Studio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
