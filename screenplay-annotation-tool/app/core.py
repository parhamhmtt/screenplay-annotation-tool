"""Offline film-dialogue corpus: SQLite store, SRT import, labels, versions, export."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("ANNOTATOR_DATA_DIR", ROOT / "data")).resolve()
DB = DATA / "annotations.sqlite3"
TRACK_FILES = DATA / "tracks"
SCHEMA_PATH = Path(os.environ.get("ANNOTATOR_SCHEMA", ROOT / "app" / "schema.json"))
MAX_TRACK = 10_000_000
TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
DEFAULT_FILMS = [
    ("world-time", "What's the Time in Your World? / در دنیای تو ساعت چند است؟"),
    ("separation", "A Separation / جدایی نادر از سیمین"),
    ("white-nights", "White Nights / شب‌های روشن"),
    ("pool-party", "Pool & Party / پول و پارتی"),
    ("just-65", "Just 6.5 / متری شیش و نیم"),
    ("saint-petersburg", "Saint Petersburg / سن پطرزبورگ"),
]
DEFAULT_ACTORS = (("reviewer", "reviewer"), ("annotator-a", "annotator"), ("annotator-b", "annotator"))
ACTOR_LABELS = {"reviewer": "Reviewer", "annotator-a": "Annotator A", "annotator-b": "Annotator B"}
TRANSLATION_STATUSES = ("needed", "candidate", "draft", "approved", "needs_revision")
SDH_STATUSES = ("unknown", "candidate", "verified", "rejected", "unavailable")


def load_schema():
    raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    tasks = raw["tasks"]
    return {
        "name": raw.get("name", "Film annotation"),
        "kinds": set(raw.get("kinds", ["dialogue", "description", "character_event"])),
        "description_kinds": list(raw.get("description_kinds", [])),
        "tasks": {key: item["labels"] for key, item in tasks.items()},
        "meta": tasks,
    }


SCHEMA = load_schema()
TASKS = SCHEMA["tasks"]
TASK_META = SCHEMA["meta"]
KINDS = SCHEMA["kinds"]
DESCRIPTION_KINDS = SCHEMA["description_kinds"]


class Database(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    TRACK_FILES.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30, factory=Database)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, role TEXT NOT NULL,
          salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS films(
          id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
          release_note TEXT NOT NULL DEFAULT '', media_name TEXT NOT NULL DEFAULT '',
          media_path TEXT NOT NULL DEFAULT '',
          sdh_status TEXT NOT NULL DEFAULT 'unknown', sdh_note TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS tracks(
          id INTEGER PRIMARY KEY, film_id INTEGER NOT NULL REFERENCES films(id),
          kind TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
          cue_count INTEGER NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS segments(
          id INTEGER PRIMARY KEY, film_id INTEGER NOT NULL REFERENCES films(id),
          kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
          speaker TEXT NOT NULL DEFAULT '', text_fa TEXT NOT NULL DEFAULT '',
          text_en TEXT NOT NULL DEFAULT '', translation_status TEXT NOT NULL DEFAULT 'needed',
          source TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
          version INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
          CHECK(end_ms > start_ms));
        CREATE INDEX IF NOT EXISTS idx_segments_film_time ON segments(film_id,start_ms,id);
        CREATE TABLE IF NOT EXISTS annotations(
          id INTEGER PRIMARY KEY, segment_id INTEGER NOT NULL REFERENCES segments(id),
          user_id INTEGER NOT NULL REFERENCES users(id), payload TEXT NOT NULL,
          segment_version INTEGER NOT NULL, submitted_at INTEGER NOT NULL,
          UNIQUE(segment_id,user_id));
        CREATE TABLE IF NOT EXISTS adjudications(
          segment_id INTEGER PRIMARY KEY REFERENCES segments(id), reviewer_id INTEGER NOT NULL REFERENCES users(id),
          payload TEXT NOT NULL, segment_version INTEGER NOT NULL, decided_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
          target TEXT NOT NULL, detail TEXT NOT NULL, at INTEGER NOT NULL);
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(films)")}
        if "sdh_status" not in columns:
            db.execute("ALTER TABLE films ADD COLUMN sdh_status TEXT NOT NULL DEFAULT 'unknown'")
        if "sdh_note" not in columns:
            db.execute("ALTER TABLE films ADD COLUMN sdh_note TEXT NOT NULL DEFAULT ''")
        if "media_path" not in columns:
            db.execute("ALTER TABLE films ADD COLUMN media_path TEXT NOT NULL DEFAULT ''")
        for slug, title in DEFAULT_FILMS:
            db.execute("INSERT OR IGNORE INTO films(slug,title,created_at) VALUES(?,?,?)", (slug, title, int(time.time())))
        for username, role in DEFAULT_ACTORS:
            if not db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                new_user(db, username, secrets.token_urlsafe(24), role)


def as_dict(row):
    return dict(row) if row is not None else None


def actor_label(username, role=""):
    return ACTOR_LABELS.get(username, f"{username} ({role})" if role else username)


def audit(db, actor, action, target, detail=""):
    db.execute("INSERT INTO audit(actor_id,action,target,detail,at) VALUES(?,?,?,?,?)",
               (actor, action, str(target), str(detail)[:2000], int(time.time())))


def new_user(db, username, password, role):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise ValueError("Username must be 3–40 letters, numbers, dots, underscores or hyphens")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if role not in {"annotator", "reviewer"}:
        raise ValueError("Role must be annotator or reviewer")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    cur = db.execute("INSERT INTO users(username,role,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
                     (username, role, salt, digest, int(time.time())))
    return cur.lastrowid


def get_or_create_user(db, username, role="annotator"):
    row = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if row:
        return row["id"]
    return new_user(db, username, secrets.token_urlsafe(24), role)


def list_users(db):
    return [dict(r) for r in db.execute(
        "SELECT id,username,role FROM users ORDER BY CASE role WHEN 'reviewer' THEN 0 ELSE 1 END, id")]


def list_films(db):
    return [dict(r) for r in db.execute("SELECT * FROM films ORDER BY id")]


def create_film(db, title, actor_id, release_note=""):
    title = title.strip()
    if not title or len(title) > 200:
        raise ValueError("Enter a film title")
    slug = hashlib.sha256(f"{title}:{time.time()}".encode()).hexdigest()[:12]
    cur = db.execute("INSERT INTO films(slug,title,release_note,created_at) VALUES(?,?,?,?)",
                     (slug, title, release_note[:1000], int(time.time())))
    audit(db, actor_id, "create_film", cur.lastrowid, title)
    return cur.lastrowid


def link_video(db, film_id, path, actor_id):
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError("Video file was not found")
    if resolved.suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}:
        raise ValueError("Use MP4, MKV, WebM, MOV or AVI")
    db.execute("UPDATE films SET media_path=? WHERE id=?", (str(resolved), film_id))
    audit(db, actor_id, "link_media", film_id, resolved.name)


def media_path(film):
    if film.get("media_path"):
        path = Path(film["media_path"])
        if path.is_file():
            return path
    return None


def decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1256", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            if "\ufffd" not in text[:4000]:
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def ms(parts):
    h, m, s, milli = map(int, parts)
    return ((h * 60 + m) * 60 + s) * 1000 + milli


def parse_srt(text):
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").lstrip("\ufeff")):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            found = TIME_RE.match(line.strip())
            if found:
                start, end = ms(found.groups()[:4]), ms(found.groups()[4:])
                body = re.sub(r"<[^>]+>", "", "\n".join(lines[i + 1:]).strip())
                if not body or end <= start:
                    raise ValueError(f"Invalid SRT cue at {line}")
                cues.append((start, end, body))
                break
    if not cues:
        raise ValueError("No valid SRT cues found")
    return cues


def closest(cue, candidates):
    start, end, _ = cue
    ranked = [(max(0, min(end, e) - max(start, s)), text) for s, e, text in candidates]
    if not ranked:
        return ""
    overlap, text = max(ranked)
    return text if overlap >= min(500, (end - start) / 2) else ""


def srt_time(value):
    h, rest = divmod(int(value), 3600000)
    m, rest = divmod(rest, 60000)
    s, milli = divmod(rest, 1000)
    return f"{h:02}:{m:02}:{s:02},{milli:03}"


def clock(ms_value: int) -> str:
    return srt_time(ms_value).replace(",", ".")


def parse_clock(value: str) -> int:
    value = value.strip().replace(",", ".")
    if re.fullmatch(r"\d+", value):
        return int(value)
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("Time must be HH:MM:SS.mmm")
    h, m, s = parts
    seconds, dot, fraction = s.partition(".")
    if not (h.isdigit() and m.isdigit() and seconds.isdigit() and (not dot or fraction.isdigit())):
        raise ValueError("Time must be HH:MM:SS.mmm")
    return ((int(h) * 60 + int(m)) * 60 + int(seconds)) * 1000 + int((fraction + "000")[:3])


def import_srt(db, film_id, kind, path, actor_id):
    kind = kind.strip().lower()
    if kind not in {"fa", "en", "sdh"}:
        raise ValueError("Track kind must be fa, en or sdh")
    file_path = Path(path)
    raw = file_path.read_bytes()
    if not raw or len(raw) > MAX_TRACK:
        raise ValueError("SRT is empty or too large")
    cues = parse_srt(decode_text(raw))
    digest = hashlib.sha256(raw).hexdigest()
    if db.execute("SELECT 1 FROM tracks WHERE film_id=? AND kind=? AND sha256=?", (film_id, kind, digest)).fetchone():
        raise ValueError("This exact subtitle file was already imported")
    description_candidates = 0
    if kind == "fa":
        if db.execute("SELECT 1 FROM segments WHERE film_id=? AND kind='dialogue'", (film_id,)).fetchone():
            raise ValueError("This film already has dialogue records. Open a new film for another release")
        for start, end, body in cues:
            db.execute("INSERT INTO segments(film_id,kind,start_ms,end_ms,text_fa,source,created_at) VALUES(?,'dialogue',?,?,?,?,?)",
                       (film_id, start, end, body, file_path.name[:200], int(time.time())))
    elif kind == "en":
        segments = db.execute("SELECT * FROM segments WHERE film_id=? AND kind='dialogue'", (film_id,)).fetchall()
        if not segments:
            raise ValueError("Import the original-language SRT first")
        if db.execute("SELECT 1 FROM annotations WHERE segment_id IN (SELECT id FROM segments WHERE film_id=?)", (film_id,)).fetchone():
            raise ValueError("Import English before annotation starts")
        for seg in segments:
            candidate = closest((seg["start_ms"], seg["end_ms"], seg["text_fa"]), cues)
            if candidate:
                db.execute("UPDATE segments SET text_en=?,translation_status='candidate',version=version+1 WHERE id=?",
                           (candidate, seg["id"]))
    else:
        for start, end, body in cues:
            if re.search(r"\[[^]]+\]|\([^)]*(?:music|door|laugh|scream|صدای|موسیقی|خنده)[^)]*\)|[♪♫]", body, re.I):
                db.execute("INSERT INTO segments(film_id,kind,start_ms,end_ms,text_fa,source,created_at) VALUES(?,'description',?,?,?,?,?)",
                           (film_id, start, end, body, file_path.name[:200], int(time.time())))
                description_candidates += 1
        db.execute("UPDATE films SET sdh_status='candidate' WHERE id=? AND sdh_status='unknown'", (film_id,))
    db.execute("INSERT INTO tracks(film_id,kind,filename,sha256,cue_count,created_at) VALUES(?,?,?,?,?,?)",
               (film_id, kind, file_path.name[:200], digest, len(cues), int(time.time())))
    (TRACK_FILES / f"film-{film_id}-{kind}-{digest}.srt").write_bytes(raw)
    audit(db, actor_id, "import_track", film_id, f"{kind}:{file_path.name}:{len(cues)}")
    return {"cues": len(cues), "description_candidates": description_candidates if kind == "sdh" else None}


def create_segment(db, film_id, kind, start_ms, end_ms, actor_id, **fields):
    if kind not in KINDS:
        raise ValueError("Invalid record type")
    start_ms, end_ms = int(start_ms), int(end_ms)
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("End must be after start")
    if not db.execute("SELECT 1 FROM films WHERE id=?", (film_id,)).fetchone():
        raise ValueError("Film not found")
    cur = db.execute(
        "INSERT INTO segments(film_id,kind,start_ms,end_ms,speaker,text_fa,text_en,translation_status,source,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (film_id, kind, start_ms, end_ms, str(fields.get("speaker", ""))[:120],
         str(fields.get("text_fa", ""))[:5000], str(fields.get("text_en", ""))[:5000],
         fields.get("translation_status", "needed"), fields.get("source", "manual"),
         str(fields.get("note", ""))[:2000], int(time.time())))
    audit(db, actor_id, "create_segment", cur.lastrowid, kind)
    return cur.lastrowid


def update_segment(db, segment_id, actor_id, **data):
    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
    if not seg:
        raise ValueError("Record not found")
    kind = data.get("kind", seg["kind"])
    if kind not in KINDS:
        raise ValueError("Invalid record type")
    start_ms, end_ms = int(data.get("start_ms", seg["start_ms"])), int(data.get("end_ms", seg["end_ms"]))
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("End must be after start")
    status = data.get("translation_status", seg["translation_status"])
    if status not in TRANSLATION_STATUSES:
        raise ValueError("Invalid translation status")
    text_fa = str(data.get("text_fa", seg["text_fa"]))[:5000]
    text_en = str(data.get("text_en", seg["text_en"]))[:5000]
    if kind == "dialogue" and status == "approved" and (not text_fa.strip() or not text_en.strip()):
        raise ValueError("Approved dialogue needs verified original and English text")
    fields = ("kind", "start_ms", "end_ms", "speaker", "text_fa", "text_en", "translation_status", "note")
    values = (kind, start_ms, end_ms, str(data.get("speaker", seg["speaker"]))[:120], text_fa, text_en,
              status, str(data.get("note", seg["note"]))[:2000])
    current = tuple(seg[k] for k in fields)
    if values == current:
        return seg["version"]
    db.execute("UPDATE segments SET kind=?,start_ms=?,end_ms=?,speaker=?,text_fa=?,text_en=?,translation_status=?,note=?,version=version+1 WHERE id=?",
               values + (segment_id,))
    audit(db, actor_id, "update_segment", segment_id)
    return seg["version"] + 1


def split_segment(db, segment_id, cut_ms, actor_id):
    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
    if not seg:
        raise ValueError("Record not found")
    if db.execute("SELECT 1 FROM annotations WHERE segment_id=?", (segment_id,)).fetchone():
        raise ValueError("Split before annotation")
    if not seg["start_ms"] < cut_ms < seg["end_ms"]:
        raise ValueError("Cut must lie within the record")
    db.execute("UPDATE segments SET end_ms=?,version=version+1 WHERE id=?", (cut_ms, segment_id))
    cur = db.execute("INSERT INTO segments(film_id,kind,start_ms,end_ms,speaker,source,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (seg["film_id"], seg["kind"], cut_ms, seg["end_ms"], seg["speaker"], seg["source"],
                      f"Split from #{seg['id']}", int(time.time())))
    audit(db, actor_id, "split", segment_id, f"at {cut_ms}; new {cur.lastrowid}")
    return cur.lastrowid


def merge_next(db, segment_id, actor_id):
    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
    if not seg:
        raise ValueError("Record not found")
    nxt = db.execute("SELECT * FROM segments WHERE film_id=? AND kind=? AND start_ms>=? AND id<>? ORDER BY start_ms,id LIMIT 1",
                     (seg["film_id"], seg["kind"], seg["end_ms"] - 1000, segment_id)).fetchone()
    if not nxt or nxt["start_ms"] - seg["end_ms"] > 5000:
        raise ValueError("No adjacent record of the same kind within five seconds")
    if db.execute("SELECT 1 FROM annotations WHERE segment_id IN (?,?)", (segment_id, nxt["id"])).fetchone():
        raise ValueError("Merge before annotation")
    fa = "\n".join(x for x in (seg["text_fa"], nxt["text_fa"]) if x)
    en = "\n".join(x for x in (seg["text_en"], nxt["text_en"]) if x)
    db.execute("UPDATE segments SET end_ms=?,text_fa=?,text_en=?,translation_status=?,version=version+1 WHERE id=?",
               (max(seg["end_ms"], nxt["end_ms"]), fa, en, "candidate" if en else "needed", segment_id))
    db.execute("DELETE FROM segments WHERE id=?", (nxt["id"],))
    audit(db, actor_id, "merge_next", segment_id, nxt["id"])


def validate_annotation(kind, payload, complete=True):
    if not isinstance(payload, dict):
        raise ValueError("Annotation must be an object")
    if kind == "description":
        if payload.get("description_kind") not in DESCRIPTION_KINDS:
            raise ValueError("Choose a description kind")
        for field in ["description_fa", "description_en", "rationale"]:
            if len(str(payload.get(field, "")).strip()) < 4:
                raise ValueError(f"Complete {field}")
    elif kind in KINDS:
        applicable = [key for key, meta in TASK_META.items() if kind in meta["applies_to"]]
        required = [key for key, meta in TASK_META.items() if kind in meta["required_on"]]
        unknown = [key for key in payload if key not in applicable]
        if unknown:
            raise ValueError("This record does not use " + ", ".join(unknown))
        present = [key for key in applicable if payload.get(key)]
        if complete:
            missing = [key for key in required if key not in present]
            if missing:
                raise ValueError("Choose a label and rationale for " + ", ".join(missing))
        elif not present:
            raise ValueError("Choose at least one task label and write its rationale")
        for key in present:
            item = payload[key]
            if not isinstance(item, dict) or item.get("label") not in TASKS[key]:
                raise ValueError(f"Choose a valid {TASK_META[key]['title']} label")
            if len(str(item.get("rationale", "")).strip()) < 8:
                raise ValueError(f"Write an evidence-based rationale for {TASK_META[key]['title']}")
            if item.get("evidence") and len(str(item.get("evidence"))) > 500:
                raise ValueError("Evidence quote is too long")
    else:
        raise ValueError("Invalid record type")
    if len(json.dumps(payload, ensure_ascii=False)) > 20000:
        raise ValueError("Annotation is too long")


def annotation_is_complete(kind, payload):
    try:
        validate_annotation(kind, payload, complete=True)
        return True
    except ValueError:
        return False


def save_annotation(db, segment_id, user, payload, complete=False):
    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
    if not seg:
        raise ValueError("Record not found")
    if seg["kind"] == "dialogue" and seg["translation_status"] != "approved" and user["role"] != "reviewer":
        raise ValueError("The reviewer must approve the English translation first")
    validate_annotation(seg["kind"], payload, complete=complete or user["role"] == "reviewer")
    if user["role"] == "reviewer":
        validate_annotation(seg["kind"], payload, complete=True)
        db.execute("INSERT INTO adjudications(segment_id,reviewer_id,payload,segment_version,decided_at) VALUES(?,?,?,?,?) ON CONFLICT(segment_id) DO UPDATE SET reviewer_id=excluded.reviewer_id,payload=excluded.payload,segment_version=excluded.segment_version,decided_at=excluded.decided_at",
                   (segment_id, user["id"], json.dumps(payload, ensure_ascii=False), seg["version"], int(time.time())))
        audit(db, user["id"], "adjudicate", segment_id)
        return "gold"
    db.execute("INSERT INTO annotations(segment_id,user_id,payload,segment_version,submitted_at) VALUES(?,?,?,?,?) ON CONFLICT(segment_id,user_id) DO UPDATE SET payload=excluded.payload,segment_version=excluded.segment_version,submitted_at=excluded.submitted_at",
               (segment_id, user["id"], json.dumps(payload, ensure_ascii=False), seg["version"], int(time.time())))
    audit(db, user["id"], "annotate", segment_id)
    return "annotation"


def adopt_version(db, segment_id, reviewer, payload):
    return save_annotation(db, segment_id, reviewer, payload, complete=True)


def version_payloads(db, segment_id, version=None):
    if version is None:
        rows = db.execute("SELECT u.username,u.role,a.payload,a.segment_version,a.submitted_at FROM annotations a JOIN users u ON u.id=a.user_id WHERE a.segment_id=? ORDER BY a.submitted_at", (segment_id,))
    else:
        rows = db.execute("SELECT u.username,u.role,a.payload,a.segment_version,a.submitted_at FROM annotations a JOIN users u ON u.id=a.user_id WHERE a.segment_id=? AND a.segment_version=? ORDER BY a.submitted_at", (segment_id, version))
    return [{"username": r["username"], "role": r["role"], "payload": json.loads(r["payload"]),
             "segment_version": r["segment_version"], "submitted_at": r["submitted_at"]} for r in rows]


def gold_payload(db, segment_id, version):
    row = db.execute("SELECT payload,segment_version FROM adjudications WHERE segment_id=?", (segment_id,)).fetchone()
    if row and row["segment_version"] == version:
        return json.loads(row["payload"])
    return None


def list_segments(db, film_id, user_id=None):
    rows = [dict(r) for r in db.execute("SELECT * FROM segments WHERE film_id=? ORDER BY start_ms,id", (film_id,))]
    for row in rows:
        versions = version_payloads(db, row["id"], row["version"])
        complete = [v for v in versions if annotation_is_complete(row["kind"], v["payload"])]
        row["versions"] = versions
        row["annotation_count"] = len(complete)
        row["gold"] = gold_payload(db, row["id"], row["version"])
        row["adjudicated"] = row["gold"] is not None
        if user_id:
            raw = db.execute("SELECT payload FROM annotations WHERE segment_id=? AND user_id=? AND segment_version=?",
                             (row["id"], user_id, row["version"])).fetchone()
            row["my_annotation"] = json.loads(raw["payload"]) if raw else None
            row["my_complete"] = bool(row["my_annotation"] and annotation_is_complete(row["kind"], row["my_annotation"]))
        else:
            row["my_annotation"] = None
            row["my_complete"] = False
        row["version_names"] = [actor_label(v["username"], v["role"]) for v in versions]
    return rows


def compute_agreement(rows):
    keys = list(TASKS)
    pairs = {key: [] for key in keys}
    for row in rows:
        annotations = row.get("annotations") or row.get("versions") or []
        payloads = []
        for item in annotations:
            payload = item["payload"] if isinstance(item, dict) and "payload" in item else item
            payloads.append(payload)
        complete = [p for p in payloads if annotation_is_complete(row.get("kind", "dialogue"), p)]
        if len(complete) < 2:
            continue
        left, right = complete[0], complete[1]
        for key in keys:
            a, b = left.get(key), right.get(key)
            if isinstance(a, dict) and isinstance(b, dict):
                pairs[key].append((a.get("label"), b.get("label")))
    result = {}
    for key, values in pairs.items():
        n = len(values)
        if not n:
            continue
        observed = sum(a == b for a, b in values) / n
        labels = TASKS[key]
        expected = sum((sum(a == label for a, _ in values) / n) *
                       (sum(b == label for _, b in values) / n) for label in labels)
        kappa = (observed - expected) / (1 - expected) if expected < 1 else None
        result[key] = {"n": n, "agreement": round(observed, 3), "kappa": None if kappa is None else round(kappa, 3)}
    return result


def export_jsonl(db, film_id, kind="gold"):
    records = []
    for seg in db.execute("SELECT * FROM segments WHERE film_id=? ORDER BY start_ms,id", (film_id,)):
        record = dict(seg)
        gold = db.execute("SELECT payload,segment_version FROM adjudications WHERE segment_id=?", (seg["id"],)).fetchone()
        if gold and gold["segment_version"] == seg["version"]:
            record["gold"] = json.loads(gold["payload"])
        elif kind == "gold":
            continue
        if kind == "gold" and seg["kind"] == "dialogue" and seg["translation_status"] != "approved":
            continue
        record["versions"] = version_payloads(db, seg["id"])
        records.append(record)
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


def export_srt(db, film_id, lang):
    if lang not in {"fa", "en"}:
        raise ValueError("Choose fa or en")
    cues = []
    for seg in db.execute("SELECT * FROM segments WHERE film_id=? AND kind='dialogue' ORDER BY start_ms,id", (film_id,)):
        value = seg["text_fa"] if lang == "fa" else seg["text_en"] if seg["translation_status"] == "approved" else ""
        if value.strip():
            cues.append(f"{len(cues)+1}\n{srt_time(seg['start_ms'])} --> {srt_time(seg['end_ms'])}\n{value}\n")
    return "\ufeff" + "\n".join(cues)


def import_label_jsonl(db, film_id, path, actor_id, version_name=None):
    text = decode_text(Path(path).read_bytes())
    lines = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("JSONL file is empty")
    name = version_name or re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(path).stem)[:32] or "import"
    username = name if re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", name) else f"imp-{hashlib.sha256(name.encode()).hexdigest()[:8]}"
    uid = get_or_create_user(db, username, "annotator")
    applied = 0
    segments = [dict(r) for r in db.execute("SELECT * FROM segments WHERE film_id=? ORDER BY start_ms,id", (film_id,))]
    for item in lines:
        payload = item.get("gold") or item.get("payload") or item.get("annotation")
        if not payload and isinstance(item.get("versions"), list) and item["versions"]:
            payload = item["versions"][0].get("payload")
        if not isinstance(payload, dict):
            continue
        match = None
        if item.get("id"):
            match = next((s for s in segments if s["id"] == item["id"]), None)
        if match is None and item.get("start_ms") is not None:
            match = next((s for s in segments if s["start_ms"] == int(item["start_ms"]) and s["kind"] == item.get("kind", s["kind"])), None)
        if match is None:
            continue
        try:
            validate_annotation(match["kind"], payload, complete=False)
        except ValueError:
            continue
        db.execute("INSERT INTO annotations(segment_id,user_id,payload,segment_version,submitted_at) VALUES(?,?,?,?,?) ON CONFLICT(segment_id,user_id) DO UPDATE SET payload=excluded.payload,segment_version=excluded.segment_version,submitted_at=excluded.submitted_at",
                   (match["id"], uid, json.dumps(payload, ensure_ascii=False), match["version"], int(time.time())))
        applied += 1
    audit(db, actor_id, "import_labels", film_id, f"{username}:{applied}")
    return {"username": username, "applied": applied, "rows": len(lines)}


def format_payload(payload):
    if not payload:
        return "(empty)"
    lines = []
    if payload.get("description_kind"):
        lines.append(f"Kind: {payload.get('description_kind')}")
        lines.append(payload.get("description_fa", ""))
        lines.append(payload.get("description_en", ""))
        lines.append("Rationale: " + payload.get("rationale", ""))
        return "\n".join(lines)
    for key, meta in TASK_META.items():
        item = payload.get(key)
        if not isinstance(item, dict):
            continue
        extra = f"  evidence: {item['evidence']}" if item.get("evidence") else ""
        lines.append(f"{meta['number']}. {meta['title']}: {item.get('label','')}")
        lines.append(f"   {item.get('rationale','')}{extra}")
    return "\n".join(lines) or json.dumps(payload, ensure_ascii=False, indent=2)
