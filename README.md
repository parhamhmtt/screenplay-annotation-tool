# Screenplay Annotation Tool

A desktop application for annotating screenplay dialogue and subtitle lines for research datasets. The application supports task-based annotation, speaker selection, descriptions, and AI-generated pre-annotations that can be reviewed and corrected by human annotators.

## Features

- **Subtitle-based annotation:** Open an SRT subtitle file to create or load an annotation project.
- **Multiple annotation tasks:** Annotate character sentiment, dialogue sentiment, pragmatic intent, and offensive language.
- **Speaker selection:** Associate dialogue lines with speakers where applicable.
- **Description mode:** Mark non-dialogue content, such as scene sounds or explanatory text, as descriptions.
- **Annotation status filters:** Find annotated, incomplete, and unannotated lines.
- **Edit attribution:** Track who last edited a dialogue line and when it was changed.
- **AI pre-annotation import:** Import model-generated JSON annotations and review them in the application.
- **Optional video and bilingual subtitles:** Work with a movie file and Persian and English subtitle tracks when available.
- **Search and review:** Search annotation rationales and locate uncertain AI predictions.

## Requirements

- Python 3
- PySide6

Install the dependency:

```bash
pip install PySide6
```

## Running the application

Run the application:

```bash
python app.py
```

You can also provide a movie, subtitle files, language identifiers, and an annotator name:

```bash
python app.py --movie film.mp4 --subs film.fa.srt --subs2 film.en.srt --lang1 fa --lang2 en --annotator parsa
```

To export or inspect the default annotation schema, use:

```bash
python app.py --dump-schema my_schema.json
```

For the complete list of available options, run:

```bash
python app.py --help
```

## Annotation tasks

The default annotation schema contains four tasks.

### 1. Character sentiment

Describes how each relevant character is perceived by the viewer at a particular moment in the story.

### 2. Dialogue sentiment

Captures the speaker's underlying emotional state rather than the surface tone of the utterance.

### 3. Pragmatic intent

Identifies what the speaker is doing through the utterance, such as expressing a direct intention, using sarcasm, evading a question, or employing taarof.

### 4. Offensive language

Identifies offensive or non-offensive language, including insults, threats, intimidation, and manipulative language.

The active schema determines the available tasks and labels. If a custom schema is used, annotation prompts and imported JSON must use the corresponding task names and label strings.

## Description mode

Use description mode for subtitle lines that do not represent a character speaking, including scene sounds, music, explanatory text, and other non-dialogue content.

Description lines do not require the four regular annotation tasks. They are treated as complete when the description annotation is completed according to the project's active schema.

## AI Pre-annotations (JSON Import)

The application supports importing AI-generated annotations through **File → Import pre-annotations (JSON)…**.

This feature can reduce the time required for initial annotation while allowing a human annotator to review and correct the results.

### Workflow

1. Open the Persian SRT file in the application to create or load a project.
2. If available, provide the same SRT file and its English counterpart to an AI model for contextual annotation.
3. Follow the project's annotation prompt and request the relevant cue range.
4. Save the model's output as a JSON file, such as `pre1.json`.
5. In the application, select **File → Import pre-annotations (JSON)…** and choose one or more JSON files.
6. Review and correct the imported annotations.

The full prompt is available here:

**[AI Pre-annotation Prompt](docs/ai_preannotation_prompt.md)**

### Import behavior

- **Timestamp-based matching:** Subtitle lines are matched by their start timestamps rather than cue numbers.
- **Existing annotations are preserved:** Lines that already have human annotations, including completed descriptions, are not overwritten.
- **Validation:** Invalid annotations and unknown labels are skipped and reported in the import summary.
- **Description lines:** Lines classified as `description` are imported without task annotations and count as complete.
- **Uncertain predictions:** Rationales beginning with `[?]` identify predictions that need additional review. Search for `[?]` in the application to locate them.
- **Multiple files:** Multiple JSON files can be selected for import in a single operation.
- **Markdown-wrapped JSON:** JSON output enclosed in Markdown code fences is accepted.

### Recommended practices

- Process long subtitle files in batches of approximately 40–60 cues to reduce the risk of incomplete AI responses.
- Keep cue start timestamps exactly as they appear in the original SRT file.
- Use consistent character names throughout the prompt and across batches.
- Include a film synopsis and character descriptions to provide context without encouraging the model to invent facts.
- Review all AI-generated labels and rationales before treating them as final research annotations.
- Remember that speaker identification from subtitles alone may be uncertain, especially when the video is unavailable.

AI-generated annotations are preliminary suggestions, not verified ground truth.

## Reusable task templates

The application supports task-based annotation according to its active schema. When using custom task templates or labels, ensure that imported annotations match the schema currently configured in the project.

## Viewer and filters

The application provides annotation status filters to help review project progress. Use the relevant filters to find completed annotations, incomplete lines, and lines that have not yet been annotated.

Search can also be used to locate specific text or review flags such as `[?]` in AI-generated rationales.

## Annotation attribution

The application tracks the last editor of a line and its last-edit timestamp. AI-imported annotations are attributed to `AI pre-annotation` until a human annotator edits the line.

This helps distinguish preliminary machine-generated annotations from subsequent human revisions.

## Working with multiple annotators

Annotators can work on the same project file sequentially and pass the file between collaborators. Existing line-level annotations and editor attribution should be preserved when the project is transferred.

For sequential collaboration, make sure only one person edits the project file at a time and keep backups before merging or transferring annotation work.

## Development and testing

Run the application from the project directory:

```bash
python app.py
```

Install the dependencies required by the tests or development workflow before running the project's test suite.

When troubleshooting stale Python bytecode after replacing source files, you can remove the `__pycache__` directories and restart the application.

## Project structure

```text
screenplay-annotation-tool/
├── app.py
├── core.py
├── docs/
│   └── ai_preannotation_prompt.md
└── README.md
```

Additional project files may be present depending on the current version of the repository.

## Notes

- The active annotation schema defines which tasks and labels are valid.
- The JSON import feature expects the output structure described in the AI pre-annotation prompt.
- Human review is essential for correcting ambiguous sentiment, pragmatic intent, offensive language, and speaker attribution.

