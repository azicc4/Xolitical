# Contributing to Xolitical

This repository holds the **viewer program** only (`tools/viewer/`, the launcher and the docs). Each person's vault (media, index, notes, Obsidian settings) is local to their machine and gitignored: `vault/` never enters git.

## Setup

1. Clone (or fork) the repo.
2. Start the viewer by double-clicking `Start Xolitical Viewer.bat` (Windows) or running `python tools/viewer/server.py`.
3. On the start screen, set your **alias** and keep it stable. Every file you add is stamped with it.
4. If the vault folder doesn't exist yet, click **Initialize vault here**.

## Changing the program

1. Create a branch: `git checkout -b <alias>/<short-description>`.
2. Make your change. To test it, use a scratch origin folder and a scratch vault, not your real ones. The viewer's `--port` and `--state <file>` flags let a test copy run alongside your normal viewer.
3. Check that `git status` shows no files under `vault/`. If one appears, `.gitignore` has been changed by mistake.
4. Open a pull request.

## Rules for vault content

These apply to your own vault, whatever program changes you make.

- **Fill in the source URL whenever one exists.** It is the provenance record and a duplicate check.
- **Change categories through the viewer** (✎ edit), not by renaming notes in Obsidian. A category edit rewrites every affected note and fixes links. Renaming files by hand leaves the index out of sync.
- **Write commentary below the line.** Every generated note ends with a `%% xolitical:end %%` line. Anything below it is preserved; anything above it is regenerated.
- **Back up your vault.** It isn't in git. Copy `vault/`, or at least `vault/.xolitical/` (the index), from which `python tools/viewer/server.py --rebuild` can regenerate every note.
