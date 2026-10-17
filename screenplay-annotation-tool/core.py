"""Subtitle parsing and durable, Qt-independent annotation storage."""
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import tempfile
from pathlib import Path

MAX_RATIONALE = 4000

DEFAULT_SCHEMA = {"tasks": [
    {"id": "character_sentiment", "code": "CS", "name": "Character sentiment", "description": "Viewer's perception of a character at this moment.", "per_character": True, "multi": False, "rationale": "required", "labels": [
        {"value": "Positive", "hint": "Sympathetic / moral", "color": "#5d9e75"}, {"value": "Negative", "hint": "Unsympathetic / compromised", "color": "#d16a66"}, {"value": "Neutral", "hint": "Ambiguous", "color": "#8091aa"}]},
    {"id": "dialogue_sentiment", "code": "DS", "name": "Dialogue sentiment", "description": "Private state of the speaker, beyond surface tone.", "per_character": False, "multi": False, "rationale": "required", "labels": [
        {"value": "Positive", "hint": "Genuine relief / affection", "color": "#5d9e75"}, {"value": "Negative", "hint": "Suppressed fear / resentment", "color": "#d16a66"}, {"value": "Neutral", "hint": "Detached / numb", "color": "#8091aa"}]},
    {"id": "pragmatic_intent", "code": "PI", "name": "Pragmatic intent", "description": "What the utterance is doing in context.", "per_character": False, "multi": False, "rationale": "required", "labels": [
        {"value": "Taarof", "hint": "Politeness masking intent", "color": "#aa80bd"}, {"value": "Sarcasm/Irony", "hint": "Ironic meaning", "color": "#d39b5d"}, {"value": "Evasion/Deception", "hint": "Avoiding or concealing", "color": "#d16a66"}, {"value": "Genuine/Direct", "hint": "Direct intent", "color": "#5d9e75"}]},
    {"id": "offensive_language", "code": "OL", "name": "Offensive language", "description": "Insult, threat, or manipulation in this line.", "per_character": False, "multi": False, "rationale": "required", "labels": [
        {"value": "Non-Offensive", "hint": "No offense", "color": "#8091aa"}, {"value": "Mild Insult", "hint": "E.g. shameless", "color": "#d3aa5d"}, {"value": "Severe Insult/Vulgarity", "hint": "E.g. you trash", "color": "#d16a66"}, {"value": "Threatening/Intimidating", "hint": "Threat or intimidation", "color": "#b65151"}, {"value": "Gaslighting/Manipulative", "hint": "Manipulative speech", "color": "#aa80bd"}]}]}


def load_schema(path=None):
    schema = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else DEFAULT_SCHEMA
    tasks = schema.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Schema needs a nonempty tasks array")
    ids = set()
    for task in tasks:
        if not all(k in task for k in ("id", "code", "name", "description", "labels", "per_character", "multi", "rationale")):
            raise ValueError("Task has missing fields")
        if task["id"] in ids or task["rationale"] not in ("required", "optional"):
            raise ValueError("Duplicate task or invalid rationale rule")
        ids.add(task["id"])
        labels = task["labels"]
        if not labels or any(not all(k in label for k in ("value", "hint", "color")) for label in labels):
            raise ValueError("Each task needs labeled values, hints and colors")
        if len({label["value"] for label in labels}) != len(labels):
            raise ValueError("Duplicate label")
    return schema


def decode_subtitles(raw, encoding=None):
    if encoding:
        return raw.decode(encoding)
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw[:100].count(b"\x00") > len(raw[:100]) // 5:
        return raw.decode("utf-16-le" if raw[1:2] == b"\x00" else "utf-16-be")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        persian = raw.decode("cp1256")
        if sum("\u0600" <= c <= "\u08ff" for c in persian) >= max(1, sum(c.isalpha() for c in persian) // 10):
            return persian
    except UnicodeDecodeError:
        pass
    return raw.decode("latin-1")


def clean_text(value):
    value = re.sub(r"\{\\[^}]*\}", "", value)
    value = re.sub(r"<[^>]*>", "", value)
    value = html.unescape(value)
    return "\n".join(line.strip() for line in value.splitlines()).strip()


def timestamp(value):
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        parts.insert(0, "0")
    if len(parts) != 3:
        raise ValueError("Invalid subtitle timestamp: " + value)
    return int((int(parts[0])*3600 + int(parts[1])*60 + float(parts[2]))*1000 + .5)


def parse_subtitles(source, extension=".srt", encoding=None):
    if isinstance(source, (str, Path)) and "\n" not in str(source) and Path(source).is_file():
        path = Path(source)
        extension = path.suffix.lower()
        text = decode_subtitles(path.read_bytes(), encoding)
    elif isinstance(source, bytes):
        text = decode_subtitles(source, encoding)
    else:
        text = str(source)
    extension = extension.lower()
    cues = []
    if extension in (".srt", ".vtt"):
        text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        for block in re.split(r"\n\s*\n", text):
            lines = block.splitlines()
            for i, line in enumerate(lines):
                match = re.search(r"((?:\d+:)?\d{2}:\d{2}[,.]\d{3})\s*-->\s*((?:\d+:)?\d{2}:\d{2}[,.]\d{3})", line)
                if match:
                    start, end = timestamp(match[1]), timestamp(match[2])
                    body = clean_text("\n".join(lines[i+1:]))
                    if body and end > start:
                        cues.append((start, end, body))
                    break
    elif extension in (".ass", ".ssa"):
        in_events = False
        fields = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("["):
                in_events = line.lower() == "[events]"
            elif in_events and line.lower().startswith("format:"):
                fields = [x.strip().lower() for x in line[7:].split(",")]
            elif in_events and line.lower().startswith("dialogue:") and fields:
                values = [x.strip() for x in line[9:].split(",", len(fields)-1)]
                if len(values) == len(fields):
                    row = dict(zip(fields, values))
                    try:
                        start, end = timestamp(row["start"]), timestamp(row["end"])
                        body = clean_text(re.sub(r"\\[Nn]", "\n", row["text"]))
                        if body and end > start:
                            cues.append((start, end, body))
                    except (KeyError, ValueError):
                        pass
    else:
        raise ValueError("Supported subtitles: SRT, VTT, ASS, SSA")
    if not cues:
        raise ValueError("No valid subtitle cues found")
    return sorted(cues, key=lambda cue: (cue[0], cue[1]))


def align(primary, secondary):
    result = []
    for start, end, _ in primary:
        matches = []
        for s, e, body in secondary:
            overlap = min(end, e) - max(start, s)
            if overlap > 0 and overlap / min(end-start, e-s) >= .4:
                matches.append(body)
        result.append("\n".join(matches))
    return result


def make_lines(primary, secondary=()):
    aligned = align(primary, secondary) if secondary else [""] * len(primary)
    return [{"id": i, "uid": hashlib.sha1(f"{i}|{start}|{body}".encode("utf-8")).hexdigest()[:10],
             "start": start, "end": end, "text1": body, "text2": aligned[i-1]}
            for i, (start, end, body) in enumerate(primary, 1)]


def normalize_annotations(data, schema):
    tasks = {task["id"]: task for task in schema["tasks"]}
    clean = {}
    for task_id, value in data.items():
        if task_id not in tasks:
            raise ValueError("Unknown task: " + task_id)
        task = tasks[task_id]
        allowed = {label["value"] for label in task["labels"]}
        entries = value if task["per_character"] else [value]
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("Invalid annotation structure")
        normalized = []
        for entry in entries:
            label = entry.get("label", []) if task["multi"] else entry.get("label", "")
            if task["multi"]:
                label = [str(x).strip() for x in label]
                if any(x not in allowed for x in label):
                    raise ValueError("Unknown label in " + task_id)
            else:
                label = str(label).strip()
                if label and label not in allowed:
                    raise ValueError("Unknown label in " + task_id)
            rationale = str(entry.get("rationale", "")).strip()[:MAX_RATIONALE]
            item = {"label": label, "rationale": rationale}
            if task["per_character"]:
                item["character"] = str(entry.get("character", "")).strip()[:120]
            if label or rationale or (task["per_character"] and item["character"]):
                normalized.append(item)
        if normalized:
            clean[task_id] = normalized if task["per_character"] else normalized[0]
    return clean


def incomplete(data, schema):
    for task in schema["tasks"]:
        value = data.get(task["id"])
        for entry in (value if task["per_character"] else [value]) if value else []:
            label = entry.get("label")
            rationale = entry.get("rationale", "")
            if (rationale and not label) or (label and task["rationale"] == "required" and not rationale):
                return True
            if task["per_character"] and not entry.get("character"):
                return True
    return False


def working_path(outdir, movie, subs, annotator):
    stem = Path(movie or subs).stem
    safe = re.sub(r"[^\w.-]+", "_", annotator.strip(), flags=re.UNICODE).strip("._")
    if not safe:
        raise ValueError("Annotator name is required")
    return Path(outdir) / f"{stem}.{safe}.annotations.json"


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".screenplay-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Project:
    def __init__(self, subs, subs2="", movie="", lang1="fa", lang2="en", annotator="", schema_path="", outdir="", encoding=""):
        self.subs, self.subs2, self.movie = str(subs), str(subs2), str(movie)
        self.lang1, self.lang2, self.annotator = lang1.strip(), lang2.strip(), annotator.strip()
        if not self.lang1 or not self.lang2 or self.lang1 == self.lang2:
            raise ValueError("Enter two distinct language codes")
        self.schema = load_schema(schema_path or None)
        self.lines = make_lines(parse_subtitles(subs, encoding=encoding or None), parse_subtitles(subs2, encoding=encoding or None) if subs2 else ())
        self.path = working_path(outdir or Path(subs).parent / "annotations", movie, subs, self.annotator)
        self.annotations = {}
        self.stale = []
        self.orphans = {}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            current = {line["uid"] for line in self.lines}
            self.stale = [uid for uid in saved.get("annotations", {}) if uid not in current]
            self.orphans = {uid: value for uid, value in saved.get("annotations", {}).items() if uid not in current}
            self.annotations = {uid: normalize_annotations(value, self.schema) for uid, value in saved.get("annotations", {}).items() if uid in current}

    def set_annotation(self, uid, data):
        if uid not in {line["uid"] for line in self.lines}:
            raise ValueError("Unknown line UID")
        value = normalize_annotations(data, self.schema)
        if value:
            self.annotations[uid] = value
        else:
            self.annotations.pop(uid, None)

    def save(self):
        atomic_json(self.path, {"version": 1, "annotator": self.annotator, "movie": self.movie,
                                "subs": self.subs, "lang1": self.lang1, "lang2": self.lang2,
                                "annotations": {**self.orphans, **self.annotations}})

    def records(self, include_unannotated=False):
        for line in self.lines:
            ann = self.annotations.get(line["uid"], {})
            if ann or include_unannotated:
                yield {"id": line["id"], "uid": line["uid"], "start": line["start"], "end": line["end"],
                       f"text_{self.lang1}": line["text1"], f"text_{self.lang2}": line["text2"],
                       "annotator": self.annotator, "movie": self.movie, "annotations": ann}

    def export(self, path, fmt, include_unannotated=False):
        records = list(self.records(include_unannotated))
        path = Path(path)
        if fmt == "jsonl":
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
        elif fmt == "json":
            path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        elif fmt == "csv":
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["id", "uid", "start", "end", f"text_{self.lang1}", f"text_{self.lang2}", "annotator", "movie", "annotations"])
                writer.writeheader()
                for row in records:
                    row["annotations"] = json.dumps(row["annotations"], ensure_ascii=False)
                    writer.writerow(row)
        else:
            raise ValueError("Export format must be jsonl, json, or csv")
