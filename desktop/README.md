# Screenplay Annotator – desktop app (PySide6)

Native Python/Qt app: no web server, no browser. Loads a movie + subtitle file(s), lets you label
lines for 4 tasks (from the Screenplay Annotation slides) with a rationale for each label, and
doubles as a viewer/editor for saved annotations.

## Install & run
    pip install PySide6
    python app.py                       # File > Open… (Browse buttons for all files)
    python app.py --movie film.mp4 --subs film.fa.srt --subs2 film.en.srt --lang1 fa --lang2 en --annotator parsa
    python app.py --dump-schema my_schema.json     # edit labels/tasks, then open it as "Schema JSON"

Subtitles: .srt / .vtt / .ass (UTF-8, UTF-16, legacy Persian cp1256 auto-detected). The optional 2nd
subtitle file is aligned to the 1st by time → `text_fa` / `text_en` side by side (View menu: one or both).
Movie is optional. Without PySide6 multimedia / a playable file the app still works for subtitles.

## Annotating
The left workspace keeps the optional video, selected bilingual dialogue, neighboring context,
and searchable line queue together. The right workspace lists every task with its current state.
Choose a task to see its label choices and reason field in one place. Switch tasks without losing edits.
The app uses an explicit light palette so fields, menus, selected rows, and labels remain readable
when Windows uses a dark theme.

Select a line (click, J/K, or let the video play with *Follow video*), choose a task on the right,
click a label, and write the reason. Tasks are independent – label only what applies.
Character Sentiment holds several rows (character + label + reason). Click an active label again to clear it.
Labels without a required rationale are **incomplete** ("Needs attention" in the task list, amber queue rows, filterable).
Autosave after every edit.

**Next pending line** finds the next visible line where the selected task is empty or incomplete,
including lines with other tasks already annotated. It wraps to the beginning and respects the queue filters.
Previous / Next also follow the filtered queue. Progress distinguishes tasks complete on the current line
from lines complete for the selected task. If saving fails, the app keeps the current line open.

Keys (single keys don't fire while typing): J/K or Ctrl+↓/↑ next/prev · N next pending · Space play/pause ·
P play this line · T switch task · 1-9 pick label · R rationale · Esc leave text box.
N goes to the next pending line for the selected task. Single keys are disabled while typing or using a dropdown.

## Reusable task templates
Choose **Create task template…** in the toolbar. Start with the current project's tasks (or the four
screenplay tasks), edit or remove them, and add your own. Each task supports a name, instructions,
one choice per line, optional choice guidance after `|`, single or multiple labels, character entries,
and required or optional reasons. Save the template as JSON, then select it in **Task template**
when opening subtitles. No JSON editing is required.

Leaving the template blank uses the four tasks from the Screenplay Annotation slides.
The same annotation applies to both language versions of a line. This is a configurable annotation
workspace for timed dialogue and subtitles; it does not yet support image boxes, text spans, or adjudication.
Saved projects retain their task definitions. The app rejects a mismatched template when reopening
existing annotations; use a different output folder or annotator name to start a different task set.

## Viewer / filters
Right list shows all lines with colored label summaries (hover = rationales). Filter by status
(annotated / unannotated / incomplete), task, label, free text (also searches rationales/characters).

## Data
* Working file (autosave): `annotations/<movie>.<annotator>.annotations.json` next to the subtitle file
  (one per annotator → two teams never overwrite each other). Reopen with the same name to continue/edit.
* File > Export JSONL / JSON / CSV (Excel-safe). Record: `id, uid, start, end, text_<lang1>, text_<lang2>,
  annotator, movie, annotations{task:{label,rationale}}`; `character_sentiment` is a list of `{character,label,rationale}`.
* `uid` detects a saved file that no longer matches its subtitle file.

## Layout
`core.py` – parsing, schema, storage, export (no GUI) · `app.py` – the window · `tests/` · `sample/`

## Tests
    python -I -m unittest discover -s tests          # GUI test runs offscreen, skipped if PySide6 missing

## Next steps (not in the MVP)
IAA (Cohen's kappa / Krippendorff's alpha) + merge/adjudication script, span highlighting, subtitle time offset.
