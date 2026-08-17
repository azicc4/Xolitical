# Contributing to Xolitical

## Setup

1. Clone (or fork) the repo.
2. Copy `config.example.json` → `config.json` and set `"alias"` to your contributor name. Keep it stable — every note you save is stamped `added_by: your-alias`, and the catalog is sortable by it.
3. `config.json`, `unsorted/`, and `vault/_assets/` are gitignored: your working files never enter git, only the notes and metadata do.

## Workflow

1. Create a branch for your batch: `git checkout -b add/<alias>-<short-description>`.
2. Put the files you want to catalog into `unsorted/`.
3. Run `python tools/triage/server.py` and work through the queue.
4. Commit what triage produced: new notes in `vault/media/`, updates to `data/index.json` and (if you added tags) `data/tags.json`.
5. Open a pull request.

## Rules

- **Source URL is required whenever one exists.** It is the primary duplicate check, the provenance record, and the way other collaborators re-fetch media that isn't in git. Only omit it for material that genuinely has no online source (e.g. an original screenshot of ephemeral content).
- **Never commit media files.** If a media file shows up in your `git status`, something is wrong — check `.gitignore` before committing.
- **Tags come from the shared vocabulary** (`data/tags.json`). Adding a new tag through the triage UI updates the file — that's fine, but prefer existing tags and keep new ones `lowercase-kebab-case`.
- **Don't hand-edit `data/index.json`.** The triage tool maintains it. If a merge conflicts on it, take both sides' entries (it's a pure key→value map).
- Paper notes go in `vault/papers/`; link them from media notes via the `papers` frontmatter property.

## Resolving merge conflicts

Two people cataloging different items will touch `data/index.json` and possibly `data/tags.json` concurrently. Both are append-mostly JSON maps/lists: resolve by keeping both sides' additions. Conflicting *notes* (same item cataloged twice) mean the dedup index diverged — keep the earlier note and delete the later duplicate.
