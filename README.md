# Xolitical

A collaborative, GitHub-hosted **Obsidian vault** cataloging political media — screenshots, images, videos, and articles from Twitter/X, Instagram, YouTube, and Substack. Every item gets a companion Markdown note with tags, a description, a link to its original source, and the alias of whoever added it.

**Metadata lives in git; media stays local.** The notes, tag vocabulary, and dedup index are committed. The media files themselves (`vault/_assets/`) are gitignored — each note's `source` URL lets any collaborator re-fetch the original.

## Layout

| Path | What it is |
|---|---|
| `vault/` | The Obsidian vault — open **this folder** in Obsidian |
| `vault/media/` | One note per media item (the catalog itself) |
| `vault/papers/` | Scientific papers / primary sources |
| `vault/topics/` | Topic pages and Base views over the catalog |
| `vault/_assets/` | The actual media files (**not committed**) |
| `unsorted/` | Incoming files awaiting triage (**not committed**) |
| `tools/triage/` | The browser-based sorting tool |
| `data/tags.json` | Shared tag vocabulary |
| `data/index.json` | Dedup index (source URL + file hash → note) |

## Quickstart

Requirements: [Python 3.9+](https://www.python.org/downloads/) and [Obsidian](https://obsidian.md). No pip installs needed.

1. Clone the repo and copy `config.example.json` → `config.json`; set `"alias"` to your contributor name.
2. Drop files to catalog into `unsorted/` (subfolders named after accounts help prefill metadata).
3. Run the triage tool:

   ```bash
   python tools/triage/server.py
   ```

   It opens `http://localhost:8484` — a sequential viewer where you tag, describe, and save each item. Saving writes the note into `vault/media/`, moves the file into `vault/_assets/`, and records it in the dedup index.
4. Open `vault/` as a vault in Obsidian. Start at `Home.md`; browse the catalog through `topics/All Media.base` (requires Obsidian 1.9+ for Bases; the Dataview community plugin works as an alternative).

### Filename smarts

Twitter downloads named `account-tweetID-hash-YYYYMMDD_HHMMSS.ext` are recognized automatically: the tool prefills the account, posting date, and reconstructs the source URL (`https://x.com/account/status/tweetID`). Files inside a folder named after an account get that account prefilled. PDFs are guessed as Substack articles.

### Duplicates

On save, the tool checks the new item against `data/index.json` by **normalized source URL** and by **SHA-256 file hash**. Matches prompt you to either move the file to `unsorted/_duplicates/` or save anyway.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version: work on a branch, triage a batch, commit the notes + index, open a PR. Source URLs are required whenever one exists.

## Roadmap

- **YouTube ingest** — `yt-dlp` wrapper over the shared playlist; downloads media + metadata and queues prefilled items into `unsorted/`.
- **Instagram ingest** — batch-download saved collections (`gallery-dl`/`instaloader`); the collection name becomes a seed tag.
- **CI validation** — GitHub Action on PRs: frontmatter schema check, duplicate source URLs, `added_by` present.
