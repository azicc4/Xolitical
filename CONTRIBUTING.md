# Contributing to Xolitical

## Setup

1. Clone (or fork) the repo.
2. Run `python tools/viewer/server.py`. On the start screen, set your **alias** and keep it stable. Every file you add is stamped with it, and the catalog can be sorted by it.
3. Media never enters git. The viewer moves your files into `vault/_media/`, which is gitignored. Only the notes (`vault/Events/`, `vault/Categories/`) and the index (`vault/.xolitical/`) are committed.

## Workflow

1. Create a branch for your batch: `git checkout -b add/<alias>-<short-description>`.
2. Sort a batch in the viewer.
3. Commit what the viewer produced:
   - `vault/.xolitical/` (the index)
   - new or changed notes in `vault/Events/` and `vault/Categories/`
4. Open a pull request.

## Rules

- **Source URL is required whenever one exists.** It is the provenance record, a duplicate check, and the way other collaborators re-fetch media they don't have locally. Leave it out only for material that genuinely has no online source.
- **Never commit media.** If a media file shows up in `git status`, check `.gitignore` before committing.
- **Change categories through the viewer** (✎ edit), not by renaming notes in Obsidian. A category edit rewrites every affected note and fixes links. Renaming files by hand leaves the index out of sync.
- **Write commentary below the line.** Every generated note ends with a `%% xolitical:end %%` line. Anything below it (analysis, links to `[[Papers/...]]`, context) is preserved. Anything above it is regenerated.
- **Papers** go in `vault/Papers/`. Link to them from an event note, below its `%% xolitical:end %%` line.

## Merges

The index tables keep one row per line, sorted by random IDs. Two people adding different events usually touch different lines, so git merges them cleanly.

If a merge conflicts inside a `.jsonl` file:
- Keep **both** sides' rows. Each line is an independent record.
- If the same `id` appears twice, keep the newer `updated` value.

Then regenerate the notes from the merged index:

```bash
python tools/viewer/server.py --rebuild
```

A category renamed on one branch while another branch added events to it is fixed by the same command.
