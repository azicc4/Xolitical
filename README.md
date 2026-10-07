# Xolitical

A collaborative, GitHub-hosted **Obsidian vault** cataloging political media (screenshots, images, videos and articles from Twitter/X, Instagram, YouTube and Substack). Media is grouped into **named events** and filed under **categories › subcategories**. Every file keeps a link to its original source and the alias of whoever added it.

**GitHub holds the program, the notes and the index. It never holds media.** Media files live only on each collaborator's machine, in the vault's gitignored `_media/` folder.

## How it fits together

```
Downloads/XMedia/ ─────────── ORIGIN: wherever your downloaders save. Any folder, any subfolders.
      │  Xolitical viewer: one file at a time → name an event, pick categories and tags, save
      │  (save = MOVE, never copy; instant on the same drive)
      ▼
Xolitical/                    this repo (can live anywhere, e.g. later on a portable drive)
  tools/viewer/               the viewer (Python standard library only)
  vault/                      ← open this folder in Obsidian
    Home.md                   dashboard
    Events/<year>/            one note per event: category links at the top, then each file with its own note
    Categories/               one note per category and subcategory; each lists its events
    Papers/                   scientific papers and primary sources
    .xolitical/               the index: relational tables (JSONL), committed to git
    _media/                   the media itself (gitignored, local only)
```

- **Event.** A named group of files about the same thing, made by *pairing* files together. Categories, subcategories and tags belong to the event, so every file in it shares them. Each file also gets a **note** of its own that isn't shared.
- **Index.** `vault/.xolitical/` holds `categories.jsonl`, `events.jsonl`, `files/*.jsonl` and `sessions.jsonl`. Each row is one line, so git can diff and merge them. Every note in `Events/` and `Categories/` is **generated** from this index, and the whole vault can be rebuilt from it.
- **Portable.** No absolute path is stored anywhere. Notes embed media by filename, and Obsidian finds a file wherever it sits in the vault. To move to a portable drive, move the whole `Xolitical/` folder.

## Quickstart

Requirements: [Python 3.9+](https://www.python.org/downloads/) and [Obsidian](https://obsidian.md) 1.9+ (for Bases). Nothing needs to be installed with pip. If [Pillow](https://pypi.org/project/pillow/) and [ffmpeg](https://ffmpeg.org/) are present, the viewer uses them for faster thumbnails.

1. Clone the repo.
2. Start the viewer:
   - **Windows:** double-click **`Start Xolitical Viewer.bat`** in the repo folder.
     - It finds Python, starts the viewer in a minimized window and opens `http://localhost:8484` in your browser.
     - Double-clicking it again while the viewer is running just reopens the tab.
     - To stop the viewer, close the minimized "Xolitical Viewer" window.
     - To pin it to the desktop or taskbar, right-click the file and choose *Send to › Desktop (create shortcut)*.
   - **Any OS, from a terminal:**

     ```bash
     python tools/viewer/server.py
     ```
3. On the start screen:
   1. Enter your **alias**.
   2. Pick the **origin** folder (your downloads).
   3. Confirm the **vault**, which defaults to `vault/` in this repo.

   The viewer finds the vault's index and shows how many files are waiting. Click **Begin session**. Your alias and folders are remembered in `~/.xolitical/state.json`.
4. Open `vault/` in Obsidian and start at `Home.md`.

## The viewer

| Area | What it does |
|---|---|
| **Top left: counter** | Sorted (total, plus this session), remaining, new since your last session, average files per session, files per minute, time left. Rates are calculated from your past sessions, counting only active time (pauses over 5 minutes are excluded). Hover over a number for its meaning. **↻** recomputes them, and **⏏** ends the session. |
| **Top: event strip** | Your 10 most recently saved **events**. An event with several files shows as an overlapping stack, with its title underneath (up to two lines) and shown in full on hover. ◀ ▶ page back through up to 50 events. **Click an event (or press Alt+1…0) to pair** the current file with it: its name, categories and tags load into the form. |
| **Center** | The photo, video, PDF or text file being sorted, scaled to fit the viewer, which takes up most of the screen. Files come by creation date, **newest first**, across every subfolder of the origin. |
| **Event** (required) | Every file needs an event name. Typing suggests existing events (autocomplete), and picking one pairs with it, even if it's older than the strip shows. While paired, editing the name renames the event (after you confirm). |
| **Tags** | Your 10 most recent tags as one-click chips, plus a search box (Enter creates a new tag). Tags belong to the event. |
| **Note** | Applies to this file only. |
| **Details** | Source URL, account, posted date and platform, filled in automatically from Twitter-style filenames (`account-tweetID-…-YYYYMMDD_HHMMSS`). **Fill in the source URL whenever one exists.** |
| **Right: categories** | The 5 most used categories at the top, then every other category A–Z. All subcategories are listed, indented. The order stays fixed for the whole session. Checking a subcategory also checks its parent. **✎** (edit mode) lets you rename, merge, move or delete a category. Every edit first shows how many notes it will rewrite, then updates the whole index and vault. |

**Keyboard shortcuts:**

| Shortcut | Action |
|---|---|
| Ctrl+Enter | Save |
| Alt+S | Skip |
| Alt+Z | Undo the last save (the file goes back to the origin) |
| Alt+1…0 | Pair with an event in the strip |
| Alt+← / Alt+→ | Page through the strip |
| P | Pair with the event that already holds a file from the same post |

**Duplicates:**
- A file with the same bytes as one already in the vault is offered a move to `<origin>/_duplicates/`.
- A file with the same post URL but different bytes is offered pairing with that post's event instead. This is usually the second image of a multi-image tweet.

**Autosave.** Every save writes the index and the event note immediately. There is no batching, so nothing is lost if the window closes.

## Rebuilding the vault

After a git merge, or if notes are ever edited by hand above their `%% xolitical:end %%` line, regenerate every note from the index:

```bash
python tools/viewer/server.py --rebuild
```

Text written **below** the `%% xolitical:end %%` line of any generated note is always kept.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

- **YouTube ingest:** a `yt-dlp` wrapper over the shared playlist that downloads into an origin folder, with metadata for the details panel.
- **Instagram ingest:** batch-download saved collections (`gallery-dl` or `instaloader`), using the collection name as a seed tag.
- **CI validation:** a GitHub Action that checks index integrity, duplicate sources and `added_by` on pull requests.
