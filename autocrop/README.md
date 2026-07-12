# autocrop — auto-crop DSLR photos to the main subject (darktable)

Two tools for a large action/portrait shoot:

1. **`autocrop.py`** — detects the main **person** in each Canon RAW (`.CR3`) and
   writes a *non-destructive* crop into a darktable `.xmp` sidecar, optionally
   applying a color "look" you designed on one reference image. Crops stay fully
   adjustable in darktable.
2. **`export_all.sh`** — renders every RAW+sidecar to a full-resolution JPEG in
   parallel (many `darktable-cli` workers), far faster than darktable's built-in
   export dialog.

## Requirements

- [Nix](https://nixos.org) with flakes. The `autocrop` dev shell provides Python
  (YOLO/ultralytics, rawpy, OpenCV, Pillow, NumPy) and exiftool.
- **darktable** installed (the tools shell out to `darktable-cli`). It's found via
  `$DARKTABLE_CLI`, then `PATH`, then the macOS app bundle
  (`/Applications/darktable.app/Contents/MacOS/darktable-cli`).
- First detection run downloads the YOLO weights (`yolo11m.pt`) — needs network once.

Enter the environment from the repo root:

```bash
nix develop .#autocrop
```

## 1. Generate crops (`autocrop.py`)

The recommended workflow uses a **template**: edit *one* photo in darktable the
way you want the whole set to look (your color grade + a crop), save its sidecar,
and point `--template` at it. The tool copies only your *creative* modules
(exposure, filmic, color balance, …) plus a fresh per-image crop, and lets
darktable auto-apply the camera/shot-specific base (raw black/white point, white
balance, input matrix, orientation) so mixed ISOs and camera bodies stay correct.

```bash
# from the repo root, inside the shell (or via `nix develop .#autocrop --command`)
python autocrop/autocrop.py /path/to/shoot \
  --template /path/to/shoot/A_GOOD_EDIT.CR3.xmp \
  --workers 8 --write
```

- Writes/overwrites `<name>.CR3.xmp` next to each RAW (idempotent — safe to re-run).
- The template's own image is skipped so your reference edit is preserved.
- Quit darktable (or keep this folder out of its library) while writing, so it
  doesn't clobber the sidecars from its database.

Key options:

| flag | meaning |
|------|---------|
| `--template <xmp>` | clone this darktable edit's look onto every image (recommended) |
| `--write` | write the sidecars (omit for a dry run) |
| `--workers N` | parallel worker processes (CPU inference); ~8 is a good default |
| `--proof-dir <dir>` | also render darktable JPEG proofs of the results (sequential) |
| `--sample N` / `--limit N` | process an evenly-spread / first-N subset |
| `--weights <file>` | YOLO weights (default `yolo11m.pt`) |

Without `--template`, the crop is instead injected into each image's existing
sidecar (`inject_crop`).

### How the subject is chosen

Detection runs at four orientations (0/90/180/270°) so horizontal/inverted action
poses are found, and the preview is rotated to the RAW's EXIF orientation first so
crops land in darktable's display space. Among detected people the winner maximizes:

```
score = area × (1 − center_distance)² × (0.5 + 0.5 × relative_sharpness)
```

i.e. large, centered, **and in focus** — so a big, out-of-focus background person
doesn't beat the sharp subject you framed. Tunables live at the top of
`autocrop.py` (`CONF`, `PERSON_CLASS`).

## 2. Export to JPEG (`export_all.sh`)

Renders every RAW with its sidecar to `<shoot>/export/*.jpg` at full resolution,
in parallel:

```bash
bash autocrop/export_all.sh /path/to/shoot [workers] [quality]
# e.g. saturate a 16-core machine at max quality:
bash autocrop/export_all.sh /path/to/shoot 24 100
```

- Defaults: `workers=8`, `quality=95`.
- The output directory is wiped of `*.jpg` at the start of each run, because
  `darktable-cli` appends `_01/_02` to existing names instead of overwriting —
  so you always end up with exactly one JPEG per RAW.

## Files

- `autocrop.py` — detection, subject selection, sidecar writing (CLI).
- `dt_xmp.py` — darktable XMP helpers (`build_from_template`, `inject_crop`, crop
  param packing). Verified against darktable 5.0.
- `export_all.sh` — parallel `darktable-cli` JPEG export.
