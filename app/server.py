"""Local, dependency-free screenplay annotation server."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
DATA = Path(os.environ.get("ANNOTATOR_DATA_DIR", ROOT / "data")).resolve()
DB = DATA / "annotations.sqlite3"
MEDIA = DATA / "media"
TRACK_FILES = DATA / "tracks"
MAX_JSON = 5_000_000
MAX_TRACK = 10_000_000
TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
FILM_TITLES = [
    ("world-time", "در دنیای تو ساعت چند است؟"),
    ("separation", "جدایی نادر از سیمین"),
    ("white-nights", "شب‌های روشن"),
    ("pool-party", "پول و پارتی"),
    ("just-65", "متری شیش و نیم"),
    ("saint-petersburg", "سن پطرزبورگ"),
]
TASKS = {
    "character_sentiment": ["Positive", "Negative", "Neutral", "Insufficient evidence"],
    "dialogue_sentiment": ["Positive", "Negative", "Neutral", "Insufficient evidence"],
    "pragmatic_intent": ["Taarof", "Sarcasm/Irony", "Evasion/Deception", "Genuine/Direct", "Insufficient evidence"],
    "offensive_language": ["Non-Offensive", "Mild Insult", "Severe Insult/Vulgarity", "Threatening/Intimidating", "Gaslighting/Manipulative", "Insufficient evidence"],
}
TASK_META = {
    "character_sentiment": {
        "number": 1, "title_fa": "احساس شخصیت", "title_en": "Temporal character sentiment",
        "help_fa": "برداشت بیننده از این شخصیت در این نقطه از داستان. به کنش یا تغییر روایت استناد کنید.",
        "applies_to": ["character_event", "dialogue"], "required_on": ["character_event"],
        "gloss": {"Positive": "همدل / اخلاقی", "Negative": "غیرهمدل / خدشه‌دار", "Neutral": "مبهم", "Insufficient evidence": "شواهد ناکافی"},
    },
    "dialogue_sentiment": {
        "number": 2, "title_fa": "احساس دیالوگ", "title_en": "Dialogue sentiment · private state",
        "help_fa": "حالت خصوصی گوینده، حتی اگر ظاهر جمله آرام باشد. مثال: «نشنیدم» با وحشت و خودحفاظی.",
        "applies_to": ["dialogue"], "required_on": ["dialogue"],
        "gloss": {"Positive": "آسودگی یا محبت واقعی", "Negative": "ترس یا رنجش فروخورده", "Neutral": "جدا / بی‌حس", "Insufficient evidence": "شواهد ناکافی"},
    },
    "pragmatic_intent": {
        "number": 3, "title_fa": "قصد کاربردی", "title_en": "Pragmatic intent",
        "help_fa": "هدف ارتباطی اصلی این نوبت. ادب به‌تنهایی تعارف نیست.",
        "applies_to": ["dialogue"], "required_on": ["dialogue"],
        "gloss": {"Taarof": "تعارف؛ ادب پوشانندهٔ قصد", "Sarcasm/Irony": "کنایه یا طعنه", "Evasion/Deception": "طفره یا فریب", "Genuine/Direct": "صادق و مستقیم", "Insufficient evidence": "شواهد ناکافی"},
    },
    "offensive_language": {
        "number": 4, "title_fa": "زبان توهین‌آمیز", "title_en": "Offensive language",
        "help_fa": "یک طبقه برای کل نوبت. اگر هم توهین شدید هست و هم تهدید، کنش اصلی را انتخاب کنید و در دلیل به هر دو اشاره کنید.",
        "applies_to": ["dialogue"], "required_on": ["dialogue"],
        "gloss": {"Non-Offensive": "بدون توهین", "Mild Insult": "توهین خفیف", "Severe Insult/Vulgarity": "توهین شدید یا رکیک", "Threatening/Intimidating": "تهدید یا ارعاب", "Gaslighting/Manipulative": "دستکاری یا گس‌لایتینگ", "Insufficient evidence": "شواهد ناکافی"},
    },
}
ACTOR_LABELS = {"reviewer": "بازبین", "annotator-a": "حاشیه‌نویس الف", "annotator-b": "حاشیه‌نویس ب"}
# Catalog check on 2026-10-14. "listed" means a catalog entry exists; timing and file contents are not verified.
SUBTITLE_SURVEY = {
    "world-time": {"checked": "2026-10-14", "fa": "listed", "en": "listed", "sdh": "unverified",
                   "note_fa": "فارسی و انگلیسی در فهرست MySubs هست و نسخهٔ جشنواره‌ای انگلیسی هم ثبت شده. یک ردیف Hearing Impaired دیده شده، ولی فایل گفتار به‌همراه صدا هنوز با خود فیلم تطبیق نشده است."},
    "separation": {"checked": "2026-10-14", "fa": "listed", "en": "listed", "sdh": "listed",
                   "note_fa": "چند زیرنویس فارسی و انگلیسی در SubDL، OpenSubtitles و Subf2m فهرست شده. در Subf2m یک نسخهٔ فارسی مخصوص ناشنوایان (تهیه‌شده برای یک بینندهٔ ناشنوا) توضیح داده شده؛ فایل را باید با همان ریلیز فیلم باز کنید."},
    "white-nights": {"checked": "2026-10-14", "fa": "listed", "en": "listed", "sdh": "missing",
                     "note_fa": "زیرنویس فارسی فیلم ۱۳۸۱ فرزاد مؤتمن در Subf2m فهرست شده. زیرنویس فارسی «شب‌های روشن» ۱۹۶۰ موسفیلم فیلم دیگری است و نباید وارد شود. نسخهٔ توضیح‌دار نابینایان زیرنویس ناشنوایان نیست. SDH فارسی پیدا نشد."},
    "pool-party": {"checked": "2026-10-14", "fa": "missing", "en": "missing", "sdh": "unverified",
                   "note_fa": "فایل SRT مستقل فارسی یا انگلیسی پیدا نشد. فیلیمو و تلویکا نشان زیرنویس دارند، اما فایل قابل ورود به ابزار نیست. صدای اصلی فارسی است. وضعیت مخصوص ناشنوایان نامشخص است."},
    "just-65": {"checked": "2026-10-14", "fa": "listed", "en": "listed", "sdh": "unverified",
                "note_fa": "فارسی در SubDL هست و علامت Hearing Impaired ندارد. انگلیسی متعدد است و Apple TV هم زیرنویس انگلیسی دارد. یک نسخه با عنوان مخصوص ناشنوایان در فیلیمو دیده می‌شود و باید جدا از فایل‌های معمولی بررسی شود."},
    "saint-petersburg": {"checked": "2026-10-14", "fa": "unverified", "en": "unverified", "sdh": "missing",
                         "note_fa": "IMVBox زبان فارسی و انگلیسی را برای سن پطرزبورگ نشان می‌دهد، اما خود فایل SRT در آن فهرست نیست. زیرنویس مخصوص ناشنوایان پیدا نشد."},
}
KINDS = {"dialogue", "description", "character_event"}
lock = threading.RLock()


class Database(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    MEDIA.mkdir(parents=True, exist_ok=True)
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
        CREATE TABLE IF NOT EXISTS sessions(
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS films(
          id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
          release_note TEXT NOT NULL DEFAULT '', media_name TEXT NOT NULL DEFAULT '',
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
        for slug, title in FILM_TITLES:
            db.execute("INSERT OR IGNORE INTO films(slug,title,created_at) VALUES(?,?,?)", (slug, title, int(time.time())))
        ensure_local_actors(db)


def ensure_local_actors(db):
    for username, role in (("reviewer", "reviewer"), ("annotator-a", "annotator"), ("annotator-b", "annotator")):
        if not db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            new_user(db, username, secrets.token_urlsafe(24), role)


def actor_public(row):
    return {"id": row["id"], "username": row["username"], "role": row["role"],
            "label": ACTOR_LABELS.get(row["username"], row["username"])}


def survey_brief(film):
    item = SUBTITLE_SURVEY.get(film.get("slug", ""), {})
    if not item:
        return "Subtitle catalog: not checked"
    return f"Catalog {item['checked']}: FA {item['fa']} · EN {item['en']} · SDH {item['sdh']}"


def audit(db, actor, action, target, detail=""):
    db.execute("INSERT INTO audit(actor_id,action,target,detail,at) VALUES(?,?,?,?,?)",
               (actor, action, str(target), detail[:2000], int(time.time())))


def pass_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def new_user(db, username, password, role):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise ValueError("Username must be 3–40 letters, numbers, dots, underscores or hyphens")
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters")
    if role not in {"annotator", "reviewer"}:
        raise ValueError("Invalid role")
    salt = secrets.token_hex(16)
    cur = db.execute("INSERT INTO users(username,role,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
                     (username, role, salt, pass_hash(password, salt), int(time.time())))
    return cur.lastrowid


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
                body = "\n".join(lines[i + 1:]).strip()
                body = re.sub(r"<[^>]+>", "", body)
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


def validate_annotation(kind, payload, complete=True):
    if not isinstance(payload, dict):
        raise ValueError("Annotation must be an object")
    if kind == "description":
        if payload.get("description_kind") not in {"Visual action", "Scene setting", "Sound cue", "Speaker identification"}:
            raise ValueError("Choose a description kind")
        for field in ["description_fa", "description_en", "rationale"]:
            if len(str(payload.get(field, "")).strip()) < 4:
                raise ValueError(f"Complete {field}")
    elif kind in {"dialogue", "character_event"}:
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
                raise ValueError(f"Choose a valid {key} label")
            if len(str(item.get("rationale", "")).strip()) < 8:
                raise ValueError(f"Write an evidence-based rationale for {key}")
            evidence = item.get("evidence", "")
            if evidence and len(str(evidence)) > 500:
                raise ValueError("Evidence quote is too long")
    else:
        raise ValueError("Invalid segment kind")
    if len(json.dumps(payload, ensure_ascii=False)) > 20000:
        raise ValueError("Annotation is too long")


def annotation_is_complete(kind, payload):
    try:
        validate_annotation(kind, payload, complete=True)
        return True
    except ValueError:
        return False


def version_annotations(db, segment_id, version):
    return [json.loads(row["payload"]) for row in db.execute(
        "SELECT payload FROM annotations WHERE segment_id=? AND segment_version=? ORDER BY user_id",
        (segment_id, version))]


def complete_payloads(kind, payloads):
    return [payload for payload in payloads if annotation_is_complete(kind, payload)]


def compute_agreement(rows):
    keys = list(TASKS)
    pairs = {key: [] for key in keys}
    for row in rows:
        annotations = row.get("annotations", [])
        if len(annotations) != 2:
            continue
        left, right = [a["payload"] for a in annotations]
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


class App(BaseHTTPRequestHandler):
    server_version = "FilmAnnotator/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def send_json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def error_json(self, message, status=400):
        self.send_json({"error": str(message)}, status)

    def body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_JSON:
            raise ValueError("Request is too large")
        return json.loads(self.rfile.read(length))

    def actor(self, db):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie["film_session"].value
        except (KeyError, Exception):
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = db.execute("SELECT users.id,users.username,users.role FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires_at>?",
                         (digest, int(time.time()))).fetchone()
        return actor_public(row) if row else None

    def guard(self):
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            raise PermissionError("Cross-origin writes are blocked")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise PermissionError("Cross-site writes are blocked")

    def serve_file(self, path, mime):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def serve_media(self, db, film_id):
        film = db.execute("SELECT media_name FROM films WHERE id=?", (film_id,)).fetchone()
        if not film or not film["media_name"]:
            return self.error_json("Media not found", 404)
        path = MEDIA / film["media_name"]
        if not path.is_file():
            return self.error_json("Media file missing", 404)
        size = path.stat().st_size
        range_header = self.headers.get("Range", "")
        start, end = 0, size - 1
        if range_header:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
            if not match:
                return self.error_json("Invalid range", 416)
            start = int(match.group(1))
            end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
            if start >= size or end < start:
                return self.error_json("Range unavailable", 416)
        mime = "video/mp4" if path.suffix.lower() == ".mp4" else "video/webm" if path.suffix.lower() == ".webm" else "video/x-matroska"
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as file:
            file.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = file.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            return self.serve_file(STATIC / "index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self.serve_file(STATIC / "app.js", "text/javascript; charset=utf-8")
        if path == "/style.css":
            return self.serve_file(STATIC / "style.css", "text/css; charset=utf-8")
        if not path.startswith("/api/"):
            return self.error_json("Not found", 404)
        query = parse_qs(parsed.query)
        with connect() as db:
            actor = self.actor(db)
            if path == "/api/bootstrap":
                actors = [actor_public(row) for row in db.execute(
                    "SELECT id,username,role FROM users ORDER BY CASE role WHEN 'reviewer' THEN 0 ELSE 1 END, id")]
                return self.send_json({"needs_setup": False, "user": actor, "actors": actors, "tasks": TASKS,
                                       "task_meta": TASK_META,
                                       "llm_ready": bool(os.environ.get("OPENAI_API_KEY") and os.environ.get("ANNOTATOR_LLM_MODEL"))})
            if not actor:
                return self.error_json("Choose a local identity first", 401)
            if path == "/api/films":
                films = []
                for row in db.execute("SELECT * FROM films ORDER BY id"):
                    film = dict(row)
                    film["subtitle_survey"] = SUBTITLE_SURVEY.get(film["slug"], {"checked": "", "fa": "unverified", "en": "unverified", "sdh": "unverified", "note_fa": ""})
                    films.append(film)
                return self.send_json(films)
            if path == "/api/tracks":
                film_id = int(query.get("film_id", ["0"])[0])
                return self.send_json([dict(x) for x in db.execute("SELECT id,kind,filename,sha256,cue_count,created_at FROM tracks WHERE film_id=? ORDER BY id", (film_id,))])
            match = re.fullmatch(r"/api/films/(\d+)/media", path)
            if match:
                return self.serve_media(db, int(match.group(1)))
            if path == "/api/segments":
                film_id = int(query.get("film_id", ["0"])[0])
                rows = [dict(x) for x in db.execute("SELECT * FROM segments WHERE film_id=? ORDER BY start_ms,id", (film_id,))]
                my = {x["segment_id"]: json.loads(x["payload"]) for x in db.execute(
                    "SELECT a.segment_id,a.payload FROM annotations a JOIN segments s ON s.id=a.segment_id WHERE a.user_id=? AND s.film_id=? AND a.segment_version=s.version",
                    (actor["id"], film_id))}
                for row in rows:
                    payloads = version_annotations(db, row["id"], row["version"])
                    row["my_annotation"] = my.get(row["id"])
                    row["my_complete"] = bool(row["my_annotation"] and annotation_is_complete(row["kind"], row["my_annotation"]))
                    complete = len(complete_payloads(row["kind"], payloads))
                    if actor["role"] == "reviewer":
                        row["annotation_count"] = complete
                        row["adjudicated"] = bool(db.execute("SELECT 1 FROM adjudications WHERE segment_id=? AND segment_version=?", (row["id"], row["version"])).fetchone())
                    else:
                        row["annotation_count"] = 1 if row["my_complete"] else 0
                        row["adjudicated"] = False
                return self.send_json(rows)
            match = re.fullmatch(r"/api/segments/(\d+)/review", path)
            if match and actor["role"] == "reviewer":
                segment_id = int(match.group(1))
                seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
                if not seg:
                    return self.error_json("Not found", 404)
                anns = [{"username": x["username"], "payload": json.loads(x["payload"]), "segment_version": x["segment_version"]}
                        for x in db.execute("SELECT users.username,annotations.* FROM annotations JOIN users ON users.id=annotations.user_id WHERE segment_id=? ORDER BY submitted_at", (segment_id,))]
                gold = db.execute("SELECT payload,segment_version FROM adjudications WHERE segment_id=?", (segment_id,)).fetchone()
                return self.send_json({"segment": dict(seg), "annotations": anns,
                                       "adjudication": json.loads(gold["payload"]) if gold and gold["segment_version"] == seg["version"] else None})
            if path == "/api/review-queue" and actor["role"] == "reviewer":
                film_id = int(query.get("film_id", ["0"])[0])
                output = []
                for seg in db.execute("SELECT * FROM segments WHERE film_id=? ORDER BY start_ms", (film_id,)):
                    anns = [{"payload": payload} for payload in complete_payloads(seg["kind"], version_annotations(db, seg["id"], seg["version"]))]
                    if len(anns) < 2:
                        continue
                    def decisions(payload):
                        return {k: (v.get("label") if isinstance(v, dict) else v) for k, v in payload.items()}
                    disagreement = decisions(anns[0]["payload"]) != decisions(anns[1]["payload"])
                    sample = int(hashlib.sha256(f"{seg['id']}:qa".encode()).hexdigest()[:8], 16) % 5 == 0
                    gold = db.execute("SELECT 1 FROM adjudications WHERE segment_id=? AND segment_version=?", (seg["id"], seg["version"])).fetchone()
                    if disagreement or sample or gold:
                        output.append({"segment_id": seg["id"], "time_ms": seg["start_ms"], "kind": seg["kind"],
                                       "disagreement": disagreement, "sampled": sample, "done": bool(gold)})
                return self.send_json(output)
            if path == "/api/stats":
                film_id = int(query.get("film_id", ["0"])[0])
                rows = []
                for seg in db.execute("SELECT * FROM segments WHERE film_id=?", (film_id,)):
                    anns = [{"payload": payload} for payload in complete_payloads(seg["kind"], version_annotations(db, seg["id"], seg["version"]))]
                    rows.append({"annotations": anns})
                return self.send_json({"segments": len(rows), "double_annotated": sum(len(r["annotations"]) >= 2 for r in rows),
                                       "agreement": compute_agreement(rows)})
            if path == "/api/export" and actor["role"] == "reviewer":
                film_id = int(query.get("film_id", ["0"])[0])
                include_raw = query.get("raw", ["0"])[0] == "1"
                out = []
                for seg in db.execute("SELECT * FROM segments WHERE film_id=? ORDER BY start_ms,id", (film_id,)):
                    record = dict(seg)
                    gold = db.execute("SELECT payload,segment_version FROM adjudications WHERE segment_id=?", (seg["id"],)).fetchone()
                    if gold and gold["segment_version"] == seg["version"]:
                        record["gold"] = json.loads(gold["payload"])
                    elif not include_raw:
                        continue
                    if not include_raw and seg["kind"] == "dialogue" and seg["translation_status"] != "approved":
                        continue
                    if include_raw:
                        record["annotations"] = [{"annotator": x["username"], "payload": json.loads(x["payload"]),
                                                  "segment_version": x["segment_version"]} for x in db.execute(
                            "SELECT users.username,annotations.payload,annotations.segment_version FROM annotations JOIN users ON users.id=annotations.user_id WHERE segment_id=?", (seg["id"],))]
                    out.append(record)
                body = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="film-{film_id}-{"all" if include_raw else "gold"}.jsonl"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(body)
            if path == "/api/export-srt" and actor["role"] == "reviewer":
                film_id = int(query.get("film_id", ["0"])[0])
                lang = query.get("lang", [""])[0]
                if lang not in {"fa", "en"}: return self.error_json("Choose fa or en", 400)
                lines = []
                for seg in db.execute("SELECT * FROM segments WHERE film_id=? AND kind='dialogue' ORDER BY start_ms,id", (film_id,)):
                    text_value = seg["text_fa"] if lang == "fa" else seg["text_en"] if seg["translation_status"] == "approved" else ""
                    if text_value.strip():
                        lines.append(f"{len(lines)+1}\n{srt_time(seg['start_ms'])} --> {srt_time(seg['end_ms'])}\n{text_value}\n")
                body = ("\ufeff" + "\n".join(lines)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/x-subrip; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="film-{film_id}-{lang}.srt"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(body)
        return self.error_json("Not found", 404)

    def do_POST(self):
        try:
            self.guard()
            parsed = urlparse(self.path)
            path = parsed.path
            with lock, connect() as db:
                if path == "/api/setup":
                    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                        return self.error_json("Setup already complete", 403)
                    data = self.body_json()
                    user_id = new_user(db, data.get("username", ""), data.get("password", ""), "reviewer")
                    audit(db, user_id, "setup", "reviewer")
                    db.commit()
                    return self.send_json({"ok": True})
                if path == "/api/act-as":
                    data = self.body_json()
                    row = db.execute("SELECT id,username,role FROM users WHERE username=?", (str(data.get("username", "")).strip(),)).fetchone()
                    if not row:
                        return self.error_json("Unknown local identity", 404)
                    token = secrets.token_urlsafe(32)
                    db.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                               (hashlib.sha256(token.encode()).hexdigest(), row["id"], int(time.time()) + 7 * 86400))
                    audit(db, row["id"], "act_as", row["username"])
                    db.commit()
                    return self.send_json({"user": actor_public(row)},
                                          headers={"Set-Cookie": f"film_session={token}; HttpOnly; SameSite=Strict; Path=/"})
                if path == "/api/login":
                    data = self.body_json()
                    row = db.execute("SELECT * FROM users WHERE username=?", (data.get("username", ""),)).fetchone()
                    if not row or not hmac.compare_digest(pass_hash(str(data.get("password", "")), row["salt"]), row["password_hash"]):
                        return self.error_json("Invalid username or password", 401)
                    token = secrets.token_urlsafe(32)
                    db.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                               (hashlib.sha256(token.encode()).hexdigest(), row["id"], int(time.time()) + 7 * 86400))
                    audit(db, row["id"], "login", row["username"])
                    db.commit()
                    return self.send_json({"user": {"id": row["id"], "username": row["username"], "role": row["role"]}},
                                          headers={"Set-Cookie": f"film_session={token}; HttpOnly; SameSite=Strict; Path=/"})
                actor = self.actor(db)
                if not actor:
                    return self.error_json("Sign in first", 401)
                if path == "/api/logout":
                    cookie = SimpleCookie(); cookie.load(self.headers.get("Cookie", ""))
                    if "film_session" in cookie:
                        db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(cookie["film_session"].value.encode()).hexdigest(),))
                    db.commit()
                    return self.send_json({"ok": True}, headers={"Set-Cookie": "film_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"})
                if path == "/api/users" and actor["role"] == "reviewer":
                    data = self.body_json()
                    user_id = new_user(db, data.get("username", ""), data.get("password", ""), data.get("role", "annotator"))
                    audit(db, actor["id"], "create_user", user_id)
                    db.commit()
                    return self.send_json({"id": user_id})
                if path == "/api/films" and actor["role"] == "reviewer":
                    data = self.body_json()
                    title = str(data.get("title", "")).strip()
                    if not title or len(title) > 200:
                        raise ValueError("Enter a film title")
                    slug = secrets.token_hex(6)
                    cur = db.execute("INSERT INTO films(slug,title,release_note,created_at) VALUES(?,?,?,?)",
                                     (slug, title, str(data.get("release_note", ""))[:1000], int(time.time())))
                    audit(db, actor["id"], "create_film", cur.lastrowid)
                    db.commit()
                    return self.send_json({"id": cur.lastrowid})
                match = re.fullmatch(r"/api/films/(\d+)/release", path)
                if match and actor["role"] == "reviewer":
                    data = self.body_json(); film_id = int(match.group(1))
                    db.execute("UPDATE films SET release_note=? WHERE id=?", (str(data.get("release_note", ""))[:1000], film_id))
                    audit(db, actor["id"], "update_release", film_id)
                    db.commit(); return self.send_json({"ok": True})
                match = re.fullmatch(r"/api/films/(\d+)/sdh", path)
                if match and actor["role"] == "reviewer":
                    data = self.body_json(); film_id = int(match.group(1))
                    status = data.get("status", "")
                    if status not in {"unknown", "candidate", "verified", "rejected", "unavailable"}:
                        raise ValueError("Invalid SDH status")
                    note = str(data.get("note", "")).strip()[:2000]
                    if status in {"verified", "rejected", "unavailable"} and len(note) < 8:
                        raise ValueError("Explain the SDH finding and the film release checked")
                    db.execute("UPDATE films SET sdh_status=?,sdh_note=? WHERE id=?", (status, note, film_id))
                    audit(db, actor["id"], "sdh_review", film_id, f"{status}: {note}")
                    db.commit(); return self.send_json({"ok": True})
                if path == "/api/segments" and actor["role"] == "reviewer":
                    data = self.body_json()
                    film_id = int(data.get("film_id", 0))
                    if not db.execute("SELECT 1 FROM films WHERE id=?", (film_id,)).fetchone():
                        raise ValueError("Film not found")
                    kind = data.get("kind", "dialogue")
                    if kind not in KINDS:
                        raise ValueError("Invalid record type")
                    start, end = int(data.get("start_ms", 0)), int(data.get("end_ms", 0))
                    if start < 0 or end <= start:
                        raise ValueError("End time must be after start time")
                    cur = db.execute("INSERT INTO segments(film_id,kind,start_ms,end_ms,speaker,text_fa,text_en,source,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                     (film_id, kind, start, end, str(data.get("speaker", ""))[:120], str(data.get("text_fa", ""))[:5000],
                                      str(data.get("text_en", ""))[:5000], "manual", str(data.get("note", ""))[:2000], int(time.time())))
                    audit(db, actor["id"], "create_segment", cur.lastrowid)
                    db.commit(); return self.send_json({"id": cur.lastrowid})
                match = re.fullmatch(r"/api/segments/(\d+)", path)
                if match and actor["role"] == "reviewer":
                    segment_id = int(match.group(1)); data = self.body_json()
                    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
                    if not seg: return self.error_json("Segment not found", 404)
                    if int(data.get("version", 0)) != seg["version"]:
                        return self.error_json("Record changed; reload it", 409)
                    start, end = int(data.get("start_ms", seg["start_ms"])), int(data.get("end_ms", seg["end_ms"]))
                    if start < 0 or end <= start: raise ValueError("Invalid time interval")
                    kind = data.get("kind", seg["kind"])
                    if kind not in KINDS: raise ValueError("Invalid record type")
                    status = data.get("translation_status", seg["translation_status"])
                    if status not in {"needed", "candidate", "draft", "approved", "needs_revision"}:
                        raise ValueError("Invalid translation status")
                    if kind == "dialogue" and status == "approved" and (not str(data.get("text_fa", "")).strip() or not str(data.get("text_en", "")).strip()):
                        raise ValueError("Approved dialogue needs verified Persian and English text")
                    db.execute("UPDATE segments SET kind=?,start_ms=?,end_ms=?,speaker=?,text_fa=?,text_en=?,translation_status=?,note=?,version=version+1 WHERE id=?",
                               (kind, start, end, str(data.get("speaker", ""))[:120], str(data.get("text_fa", ""))[:5000],
                                str(data.get("text_en", ""))[:5000], status, str(data.get("note", ""))[:2000], segment_id))
                    audit(db, actor["id"], "update_segment", segment_id, json.dumps(dict(seg), ensure_ascii=False))
                    db.commit(); return self.send_json({"ok": True, "version": seg["version"] + 1})
                match = re.fullmatch(r"/api/segments/(\d+)/annotate", path)
                if match and actor["role"] == "annotator":
                    segment_id = int(match.group(1)); data = self.body_json()
                    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
                    if not seg: return self.error_json("Segment not found", 404)
                    if int(data.get("version", 0)) != seg["version"]:
                        return self.error_json("Record changed; reload it", 409)
                    if seg["kind"] == "dialogue" and seg["translation_status"] != "approved":
                        raise ValueError("Reviewer must approve the English translation before annotation")
                    payload = data.get("payload")
                    validate_annotation(seg["kind"], payload, complete=False)
                    db.execute("INSERT INTO annotations(segment_id,user_id,payload,segment_version,submitted_at) VALUES(?,?,?,?,?) ON CONFLICT(segment_id,user_id) DO UPDATE SET payload=excluded.payload,segment_version=excluded.segment_version,submitted_at=excluded.submitted_at",
                               (segment_id, actor["id"], json.dumps(payload, ensure_ascii=False), seg["version"], int(time.time())))
                    audit(db, actor["id"], "annotate", segment_id)
                    db.commit(); return self.send_json({"ok": True})
                match = re.fullmatch(r"/api/segments/(\d+)/adjudicate", path)
                if match and actor["role"] == "reviewer":
                    segment_id = int(match.group(1)); data = self.body_json()
                    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
                    if not seg: return self.error_json("Segment not found", 404)
                    if int(data.get("version", 0)) != seg["version"]:
                        return self.error_json("Record changed; reload it", 409)
                    if seg["kind"] == "dialogue" and seg["translation_status"] != "approved":
                        raise ValueError("Approve the English translation before adjudication")
                    count = len(complete_payloads(seg["kind"], version_annotations(db, segment_id, seg["version"])))
                    if count < 2: raise ValueError("Two complete independent annotations are required")
                    payload = data.get("payload"); validate_annotation(seg["kind"], payload)
                    db.execute("INSERT INTO adjudications(segment_id,reviewer_id,payload,segment_version,decided_at) VALUES(?,?,?,?,?) ON CONFLICT(segment_id) DO UPDATE SET reviewer_id=excluded.reviewer_id,payload=excluded.payload,segment_version=excluded.segment_version,decided_at=excluded.decided_at",
                               (segment_id, actor["id"], json.dumps(payload, ensure_ascii=False), seg["version"], int(time.time())))
                    audit(db, actor["id"], "adjudicate", segment_id)
                    db.commit(); return self.send_json({"ok": True})
                match = re.fullmatch(r"/api/segments/(\d+)/translate", path)
                if match and actor["role"] == "reviewer":
                    segment_id = int(match.group(1))
                    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
                    if not seg: return self.error_json("Segment not found", 404)
                    if not seg["text_fa"].strip(): raise ValueError("Persian text is empty")
                    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("ANNOTATOR_LLM_MODEL"):
                        raise ValueError("Set OPENAI_API_KEY and ANNOTATOR_LLM_MODEL on the server")
                    neighbors = db.execute("SELECT text_fa FROM segments WHERE film_id=? AND kind='dialogue' AND start_ms BETWEEN ? AND ? ORDER BY start_ms LIMIT 8",
                                           (seg["film_id"], max(0, seg["start_ms"] - 60000), seg["end_ms"] + 60000)).fetchall()
                    draft = translate(seg["text_fa"], [x["text_fa"] for x in neighbors])
                    db.execute("UPDATE segments SET text_en=?,translation_status='draft',version=version+1 WHERE id=?", (draft[:5000], segment_id))
                    audit(db, actor["id"], "translate_draft", segment_id)
                    db.commit(); return self.send_json({"text_en": draft, "version": seg["version"] + 1})
                match = re.fullmatch(r"/api/segments/(\d+)/split", path)
                if match and actor["role"] == "reviewer":
                    segment_id = int(match.group(1)); data = self.body_json()
                    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
                    if not seg: return self.error_json("Segment not found", 404)
                    if db.execute("SELECT 1 FROM annotations WHERE segment_id=?", (segment_id,)).fetchone():
                        raise ValueError("Split before annotation, or create a new record manually")
                    cut = int(data.get("cut_ms", 0))
                    if not seg["start_ms"] < cut < seg["end_ms"]: raise ValueError("Split point must lie within the record")
                    db.execute("UPDATE segments SET end_ms=?,version=version+1 WHERE id=?", (cut, segment_id))
                    cur = db.execute("INSERT INTO segments(film_id,kind,start_ms,end_ms,speaker,source,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                                     (seg["film_id"], seg["kind"], cut, seg["end_ms"], seg["speaker"], seg["source"], "Split from record #" + str(segment_id), int(time.time())))
                    audit(db, actor["id"], "split", segment_id, f"at {cut}; new record {cur.lastrowid}")
                    db.commit(); return self.send_json({"new_id": cur.lastrowid})
                match = re.fullmatch(r"/api/segments/(\d+)/merge-next", path)
                if match and actor["role"] == "reviewer":
                    segment_id = int(match.group(1))
                    seg = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
                    if not seg: return self.error_json("Segment not found", 404)
                    nxt = db.execute("SELECT * FROM segments WHERE film_id=? AND kind=? AND start_ms>=? AND id<>? ORDER BY start_ms,id LIMIT 1",
                                     (seg["film_id"], seg["kind"], seg["end_ms"] - 1000, segment_id)).fetchone()
                    if not nxt or nxt["start_ms"] - seg["end_ms"] > 5000:
                        raise ValueError("No adjacent record of the same kind within five seconds")
                    if db.execute("SELECT 1 FROM annotations WHERE segment_id IN (?,?)", (segment_id, nxt["id"])).fetchone():
                        raise ValueError("Merge before annotation")
                    en = "\n".join(x for x in [seg["text_en"], nxt["text_en"]] if x)
                    status = "candidate" if en else "needed"
                    db.execute("UPDATE segments SET end_ms=?,text_fa=?,text_en=?,translation_status=?,version=version+1 WHERE id=?",
                               (max(seg["end_ms"], nxt["end_ms"]), "\n".join(x for x in [seg["text_fa"], nxt["text_fa"]] if x), en, status, segment_id))
                    db.execute("DELETE FROM segments WHERE id=?", (nxt["id"],))
                    audit(db, actor["id"], "merge_next", segment_id, json.dumps(dict(nxt), ensure_ascii=False))
                    db.commit(); return self.send_json({"ok": True})
            return self.error_json("Not found or not allowed", 404)
        except (ValueError, json.JSONDecodeError) as exc:
            return self.error_json(exc)
        except PermissionError as exc:
            return self.error_json(exc, 403)
        except sqlite3.IntegrityError as exc:
            return self.error_json(exc, 409)
        except urllib.error.HTTPError as exc:
            return self.error_json(f"Translation API error: {exc.code}", 502)

    def do_PUT(self):
        try:
            self.guard()
            match = re.fullmatch(r"/api/films/(\d+)/(media|track)", urlparse(self.path).path)
            if not match: return self.error_json("Not found", 404)
            film_id, mode = int(match.group(1)), match.group(2)
            with lock, connect() as db:
                actor = self.actor(db)
                if not actor or actor["role"] != "reviewer": return self.error_json("Reviewer sign-in required", 403)
                film = db.execute("SELECT * FROM films WHERE id=?", (film_id,)).fetchone()
                if not film: return self.error_json("Film not found", 404)
                filename = Path(unquote(self.headers.get("X-Filename", "upload"))).name
                size = int(self.headers.get("Content-Length", "0"))
                if mode == "media":
                    suffix = Path(filename).suffix.lower()
                    if suffix not in {".mp4", ".webm", ".mkv"}: raise ValueError("Use MP4, WebM or MKV")
                    if size <= 0 or size > 20 * 1024**3: raise ValueError("Invalid media size")
                    name = f"film-{film_id}-{secrets.token_hex(5)}{suffix}"
                    dest = MEDIA / name
                    with dest.open("wb") as out:
                        remaining = size
                        while remaining:
                            part = self.rfile.read(min(1024 * 1024, remaining))
                            if not part: raise ValueError("Upload ended early")
                            out.write(part); remaining -= len(part)
                    db.execute("UPDATE films SET media_name=? WHERE id=?", (name, film_id))
                    audit(db, actor["id"], "upload_media", film_id, filename)
                    db.commit(); return self.send_json({"ok": True, "media_name": name})
                if size <= 0 or size > MAX_TRACK: raise ValueError("Invalid subtitle size")
                kind = self.headers.get("X-Track-Kind", "")
                if kind not in {"fa", "en", "sdh"}: raise ValueError("Track kind must be fa, en or sdh")
                raw = self.rfile.read(size)
                try: text = raw.decode("utf-8-sig")
                except UnicodeDecodeError: raise ValueError("Save SRT as UTF-8 first")
                cues = parse_srt(text)
                digest = hashlib.sha256(raw).hexdigest()
                if db.execute("SELECT 1 FROM tracks WHERE film_id=? AND kind=? AND sha256=?", (film_id, kind, digest)).fetchone():
                    raise ValueError("This exact track was already imported")
                if kind == "fa":
                    if db.execute("SELECT 1 FROM segments WHERE film_id=? AND kind='dialogue'", (film_id,)).fetchone():
                        raise ValueError("Persian dialogue records already exist; import into a new film/release project")
                    for start, end, body in cues:
                        db.execute("INSERT INTO segments(film_id,kind,start_ms,end_ms,text_fa,source,created_at) VALUES(?,'dialogue',?,?,?,?,?)",
                                   (film_id, start, end, body, filename[:200], int(time.time())))
                elif kind == "en":
                    segments = db.execute("SELECT id,start_ms,end_ms,text_fa FROM segments WHERE film_id=? AND kind='dialogue'", (film_id,)).fetchall()
                    if not segments: raise ValueError("Import Persian track first")
                    if db.execute("SELECT 1 FROM annotations WHERE segment_id IN (SELECT id FROM segments WHERE film_id=?)", (film_id,)).fetchone():
                        raise ValueError("Import English subtitles before annotation")
                    for seg in segments:
                        candidate = closest((seg["start_ms"], seg["end_ms"], seg["text_fa"]), cues)
                        if candidate:
                            db.execute("UPDATE segments SET text_en=?,translation_status='candidate',version=version+1 WHERE id=?", (candidate, seg["id"]))
                else:
                    count = 0
                    for start, end, body in cues:
                        if re.search(r"\[[^]]+\]|\([^)]*(?:music|door|laugh|scream|صدای|موسیقی|خنده)[^)]*\)|[♪♫]", body, re.I):
                            db.execute("INSERT INTO segments(film_id,kind,start_ms,end_ms,text_fa,translation_status,source,created_at) VALUES(?,'description',?,?,?,'needed',?,?)",
                                       (film_id, start, end, body, filename[:200], int(time.time())))
                            count += 1
                    db.execute("UPDATE films SET sdh_status='candidate' WHERE id=? AND sdh_status='unknown'", (film_id,))
                db.execute("INSERT INTO tracks(film_id,kind,filename,sha256,cue_count,created_at) VALUES(?,?,?,?,?,?)",
                           (film_id, kind, filename[:200], digest, len(cues), int(time.time())))
                (TRACK_FILES / f"film-{film_id}-{kind}-{digest}.srt").write_bytes(raw)
                audit(db, actor["id"], "import_track", film_id, f"{kind}:{filename}:{len(cues)}")
                db.commit(); return self.send_json({"ok": True, "cues": len(cues), "description_candidates": count if kind == "sdh" else None})
        except (ValueError, sqlite3.IntegrityError) as exc:
            return self.error_json(exc)
        except PermissionError as exc:
            return self.error_json(exc, 403)


def translate(text_fa, context):
    prompt = {"context_fa": context, "target_fa": text_fa}
    payload = {"model": os.environ["ANNOTATOR_LLM_MODEL"], "store": False,
               "instructions": "Translate only target_fa from Persian to natural English for a timed film subtitle. Use context_fa only for disambiguation. Preserve speaker intent, taarof, sarcasm, names and register. Do not add events or sound descriptions. Return only the English translation, with no quotation marks or commentary.",
               "input": json.dumps(prompt, ensure_ascii=False)}
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
                                          "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as response:
        data = json.load(response)
    output = "\n".join(item.get("text", "") for message in data.get("output", []) if message.get("type") == "message"
                       for item in message.get("content", []) if item.get("type") == "output_text").strip()
    if not output: raise ValueError("The translation API returned no text")
    return output


def main():
    init_db()
    host = "127.0.0.1"
    port = int(os.environ.get("ANNOTATOR_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), App)
    print(f"Screenplay Annotation Tool: http://{host}:{port}", flush=True)
    print(f"Data directory: {DATA}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
