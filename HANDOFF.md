# Handoff — 2026-09-24

Triggered by a peer session flagging an imminent machine reboot. Written by the Claude Code
session that did the work below (session_018DqMVg1HRRuLM1hvLj8esa).

## What was being done

Compared the repo's demucs stem-separation setup against optimizations described in an external
blog post, then implemented two improvements on request:

1. Default demucs model bumped from `htdemucs` to `htdemucs_ft` (fine-tuned bag-of-4-models
   variant — better separation quality, ~4x slower per file) for both the local subprocess path
   and the remote `demucs.stanley.arpa` API path.
2. Optional multi-model ensembling: `DEMUCS_ENSEMBLE_MODELS` (local) / `DEMUCS_API_ENSEMBLE_MODELS`
   (remote API) — comma-separated model lists. When set, runs each model against a file and blends
   the resulting stems by sample-averaging (ffmpeg `amix`, `normalize=0` + `volume=1/n`). Unset,
   behavior is identical to before (single model, no blending). New shared helper module:
   `scripts/demucs/ensemble.py`.

Before implementing, cloned the sibling `homelab-demucs` API service repo (not checked out locally
on this machine — pulled read-only into `/tmp/.../scratchpad/homelab-demucs`, not left in this
repo) to confirm:
- It validates `model` against a `DEMUCS_MODELS` allowlist, default `htdemucs,htdemucs_ft,mdx,mdx_q`
  — `htdemucs_ft` is already in it, so no server-side change needed for change #1.
- The service is strictly one-model-per-job with no server-side ensemble concept, so ensembling
  (#2) is implemented entirely client-side in this repo: one job per model, blend after download.
  No changes needed in `homelab-demucs` for either feature.
- Fixed an early mistake: the first draft of `.env.example` used `mdx_extra` as an example second
  ensemble model for the API path, which is **not** in that service's default allowlist and would
   have been rejected (`unknown_model`, HTTP 400). Corrected to `htdemucs_ft,mdx_q`.

Also noted in passing (not fixed): the local demucs pipx venv (`~/.local/pipx/venvs/demucs`) is
currently broken — its `bin/python3` symlink points to system Python 3.12 instead of the venv's
own interpreter, so `import demucs` fails and the local (non-`--api`) code path can't run right
now. Likely fallout from a system Python upgrade. Not addressed — flagging for whoever has time;
probably needs `pipx reinstall demucs` or similar.

## Current state

- Branch: `feat/demucs-model-optimizations`, pushed to origin.
- PR open: https://github.com/jakestanley/music/pull/2
- Two commits, both already pushed — nothing from this task is uncommitted or stashed:
  - `8a1f6c2` feat(demucs): default to htdemucs_ft for better separation quality
  - `3e24163` feat(demucs): add optional multi-model ensembling for both local and API paths
- Verified: `python3 -m py_compile` passes on all changed/added demucs modules.
- **Not yet verified**: an actual end-to-end run against the live `demucs.stanley.arpa` service.
  It returned `504 Gateway Time-out` when queried directly during development (the backing host,
  `shrike`, appears to have been asleep — UpSnap wake wasn't invoked for that ad-hoc check). The
  normal pipeline path (`demucs.py --api ...`) does invoke UpSnap wake automatically, so this is
  likely a non-issue in real usage, but the PR's test plan still has an unchecked box for a real
  run confirming `htdemucs_ft` and an ensembled pair produce valid blended output end-to-end.

## Uncommitted changes in the working tree — NOT part of this task

These were already present in the working tree before this session started (per the session's
initial `git status`) and were deliberately left untouched — no context on their intent, so they
were not committed, stashed, or discarded:

```
 M .claude/settings.local.json
 M ipod/ipod-manifest.json
 M manifest.json
 M mix_generator.py
?? key_groups.py
?? merge_batw_rejects.sh
?? q
```

These survive the reboot fine as plain working-tree state (not stashed) since the reboot doesn't
touch disk-backed files — just flagging them so they aren't mistaken for reboot-related loss, and
so whoever resumes knows to check with the user before touching them.

## Suggested next steps

1. Review/merge (or request changes on) PR #2.
2. Once `demucs.stanley.arpa` is confirmed awake, do a real run with `DEMUCS_API_MODEL=htdemucs_ft`
   and with `DEMUCS_API_ENSEMBLE_MODELS=htdemucs_ft,mdx_q` set, on one small test file, to confirm
   the PR's test plan end-to-end box.
3. Separately: fix the broken local demucs pipx venv if the local (non-API) path is still wanted.
4. Ask the user about the pre-existing uncommitted files listed above before touching them.
