# Xolitical

A collaborative archive of political media — screenshots, images, videos, and articles from Twitter/X, Instagram, YouTube, and Substack — each cataloged with tags, a description, and a link to its original source.

## Browse

- [[topics/All Media.base|All Media]] — the full catalog, filterable by tag, platform, account, and contributor
- [[topics/]] — topic pages (curated views of the catalog)
- [[papers/]] — scientific papers and other primary sources referenced by media notes

## Contribute

1. Drop new material into the repo's `unsorted/` folder (not part of this vault).
2. Run the triage tool: `python tools/triage/server.py` and open `http://localhost:8484`.
3. Tag, describe, and save each item — it lands in `media/` with its file in `_assets/`.
4. Commit on a branch and open a pull request. See `CONTRIBUTING.md` in the repo root.

## Conventions

- Every media note **must** carry a `source` URL when one exists — it is the primary duplicate check and lets collaborators re-fetch media that isn't in git.
- Media files themselves live in `_assets/` and are **not committed**; only the notes and metadata are.
- Tags come from the shared vocabulary in `data/tags.json`; add new ones through the triage tool so the list stays in sync.
