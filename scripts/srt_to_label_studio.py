"""Convert a Persian SRT to one Label Studio task per timed subtitle cue.

Optional English SRT cues are matched by maximum temporal overlap, but every
alignment and translation still needs human review. No dialogue is invented.
"""

import argparse
import json
import re
from pathlib import Path

TIMING = re.compile(r"^(\d\d):(\d\d):(\d\d)[,.](\d\d\d)\s*-->\s*(\d\d):(\d\d):(\d\d)[,.](\d\d\d)")


def milliseconds(parts):
    h, m, s, ms = map(int, parts)
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def stamp(ms):
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"


def read_srt(path):
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", raw):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            match = TIMING.match(line.strip())
            if match:
                start = milliseconds(match.groups()[:4])
                end = milliseconds(match.groups()[4:])
                body = "\n".join(lines[i + 1:]).strip()
                if end <= start or not body:
                    raise ValueError(f"Invalid cue near {line!r} in {path}")
                cues.append((start, end, body))
                break
    if not cues:
        raise ValueError(f"No SRT cues found in {path}")
    return cues


def closest_translation(fa, english):
    start, end, _ = fa
    overlaps = [(max(0, min(end, e) - max(start, s)), text) for s, e, text in english]
    if not overlaps:
        return ""
    overlap, text = max(overlaps, key=lambda item: item[0])
    return text if overlap >= min(500, (end - start) / 2) else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--film-id", required=True)
    parser.add_argument("--film-title", required=True)
    parser.add_argument("--fa", required=True, type=Path)
    parser.add_argument("--en", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source-note", default="")
    args = parser.parse_args()
    fa = read_srt(args.fa)
    en = read_srt(args.en) if args.en else []
    tasks = []
    for number, cue in enumerate(fa, 1):
        start, end, text = cue
        tasks.append({"data": {
            "record_id": f"{args.film_id}-{number:05}",
            "record_type": "dialogue_candidate",
            "film_id": args.film_id,
            "film_title": args.film_title,
            "start_ms": start,
            "end_ms": end,
            "timecode": f"{stamp(start)}–{stamp(end)}",
            "speaker": "[verify speaker]",
            "text_fa": text,
            "text_en": closest_translation(cue, en),
            "translation_status": "candidate_unreviewed" if en else "needed",
            "source_note": args.source_note,
        }})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(tasks)} candidate tasks to {args.out}")


if __name__ == "__main__":
    main()
