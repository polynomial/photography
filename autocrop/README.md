# autocrop — auto-crop DSLR photos to the main subject (darktable)

Three tools for a large action/portrait shoot:

1. **`autocrop.py`** — detects the main **person** in each Canon RAW (`.CR3`) and
   writes a *non-destructive* crop into a darktable `.xmp` sidecar, optionally
   applying a color "look" you designed on one reference image. Crops stay fully
   adjustable in darktable.
2. **`export_all.sh`** — renders every RAW+sidecar to a full-resolution JPEG in
   parallel (many `darktable-cli` workers), far faster than darktable's built-in
   export dialog.
3. **`realign.py`** — repairs the frames where step 1 locked onto the wrong
   person, using the frames around them where it got it right.
4. **`section_movie.py`** — groups the shoot into burst "sections" and turns each
   one into a subject-tracked video clip.

## Quick start (one command)

`autocrop-shoot` runs the whole pipeline — crop then export — with sensible
defaults. Run it from inside a shoot, or pass a directory:

```bash
cd /path/to/shoot
/path/to/photography/autocrop/autocrop-shoot          # processes the current dir
# or
autocrop-shoot /path/to/shoot -q 92 -x 16             # dir arg + overrides
```

Everything is overridable (`-w/--workers`, `-x/--export-workers`, `-q/--quality`,
`-t/--template`, `--crop-only`, `--export-only`); see `autocrop-shoot --help`.
Model weights and caches go under `~/.cache/photography-autocrop`, never into the
shoot directory. Symlink it onto your `PATH` for `autocrop-shoot` from anywhere:

```bash
ln -s /path/to/photography/autocrop/autocrop-shoot /usr/local/bin/autocrop-shoot
```

The sections below document the underlying tools it calls.

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

The tool applies a darktable **template** look plus a fresh per-image crop. It
copies only the template's *creative* modules (exposure, filmic, color balance,
…) and lets darktable auto-apply the camera/shot-specific base (raw black/white
point, white balance, input matrix, orientation) so mixed ISOs and camera bodies
stay correct.

A neutral default look ships as [`default-look.xmp`](default-look.xmp), so no
template is required:

```bash
# from the repo root, inside the shell (or via `nix develop .#autocrop --command`)
python autocrop/autocrop.py /path/to/shoot --workers 8 --write
```

To use your own look, edit one photo in darktable exactly how you want the whole
set to look (grade + a crop) and point `--template` at its sidecar:

```bash
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
| `--template <xmp>` | darktable look to clone onto every image (default: bundled `default-look.xmp`; pass `""` for inject-into-existing-sidecar mode) |
| `--write` | write the sidecars (omit for a dry run) |
| `--workers N` | parallel worker processes (CPU inference); ~8 is a good default |
| `--proof-dir <dir>` | also render darktable JPEG proofs of the results (sequential) |
| `--sample N` / `--limit N` | process an evenly-spread / first-N subset |
| `--weights <file>` | YOLO weights (default `yolo11m.pt`) |

With `--template ""`, no look is applied and the crop is instead injected into
each image's existing sidecar (`inject_crop`).

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

## 3. Fix wrong-subject frames (`realign.py`)

On a busy field autocrop sometimes locks onto the wrong person for part of a
burst — a nearer out-of-focus body, a bystander, the crowd merged into one huge
box. Because the shots come in bursts, the neighbouring frames it *did* get right
carry enough information to repair the bad ones automatically.

Triage by hand, then fix in one command:

```bash
# 1. flick through <shoot>/export/ and move the bad JPEGs into <shoot>/broken/
# 2. re-pick the subject on just those frames and re-export them
python autocrop/realign.py /path/to/shoot --workers 10
```

- `<shoot>/export/*.jpg` = "autocrop got these right" (anchors)
- `<shoot>/broken/*.jpg` = "autocrop got these wrong" (targets)
- output goes to `<shoot>/broken_fixed/`: a new sidecar + full-resolution JPEG
  per frame, plus `qa/` overlays and `realign-report.json`

The original sidecars are **never modified** — the rebuilt ones live in the
output dir, so old and new can be compared before anything is adopted. To keep a
fix, copy `broken_fixed/<name>.CR3.xmp` over the sidecar next to the RAW.

### How a frame is re-picked

1. Frames are grouped into burst sequences by capture time (`--gap`, default 2 s).
2. All people in the bad frame are detected (four orientations, confidence floor
   dropped to 0.15 — the right subject may have been missed for being small).
3. The subject's position and size are predicted from the nearest **known** boxes
   in the same burst: interpolated when the frame sits between two, otherwise
   extrapolated along their velocity, damped.
4. Every detection is scored on agreement with that prediction (position and
   size, with uncertainty that grows with time distance to the nearest known
   frame), colour-signature match against the accepted subject in the anchor
   frames (HS histograms of the box's upper and lower halves, so top and shorts
   colours are matched separately), detector confidence and sharpness.
5. A frame that gets solved becomes a known box itself, and the next-closest
   frame is solved from it. A run of 20 bad frames is therefore walked outward
   from the good ends one 1/30 s step at a time instead of being extrapolated in
   one jump. The colour reference always stays the original anchors, so drift
   can't feed on itself.

Key options:

| flag | meaning |
|------|---------|
| `--broken-dir` / `--good-dir` / `--out-dir` | subdir names (default `broken` / `export` / `broken_fixed`) |
| `--gap <s>` | time gap that starts a new burst sequence (default 2.0) |
| `--max-dt <s>` | refuse a frame whose nearest good neighbour is further away (default 8.0) |
| `--anchors N` | good frames per burst used as colour reference (default 4) |
| `--only <glob>` | process just the matching target frames, for trying settings out |
| `--no-export` / `--no-qa` | skip the JPEG export / the QA overlays |
| `--workers N` / `--export-workers N` / `--quality N` | as in the tools above |

`realign-report.json` records, per frame, the winning score and its component
parts, the margin over the runner-up, and the IoU between the new crop and the
one it replaced (near 0 = a genuinely different subject was chosen).

## 4. Burst sections as video (`section_movie.py`)

A movie made straight from the full frames doesn't work — the subject wanders
around the frame. A movie made from autocrop's per-image crops doesn't work
either, because every crop is a different size. This tool takes the middle path:
one **fixed-size window per section**, slid along the subject's path.

```bash
python autocrop/section_movie.py /path/to/shoot --list      # see the sections
python autocrop/section_movie.py /path/to/shoot --workers 12
```

1. Frames are grouped into sections — bursts, split on a gap in capture time
   (`--gap`, default 2 s) and on any change of frame geometry.
2. Per section, the subject boxes autocrop already wrote into the sidecars give
   one window size for the whole section: the 90th percentile of the box sizes
   times `--pad` (default 1.6), forced to the output aspect ratio. A percentile
   rather than the maximum, so a single runaway box can't zoom the clip out; the
   padding also rescues frames where the detector only boxed part of the subject.
3. That window slides along a smoothed path of the subject's centre — spikes
   median-filtered out, gaps interpolated, then a `--smooth` moving average —
   clamped so it never leaves the frame.
4. Each frame is rendered through darktable with the window as its crop, so the
   clips carry the same look as the exported stills, then the section is encoded
   to H.264 / yuv420p / bt709 MP4 with `+faststart` — plays on Android as is.

Output lands in `<shoot>/movies/`: `section_NN_<first>-<last>.mp4` per section,
`all_sections.mp4` with every clip end to end, and `sections.json` recording each
clip's frame range, window size, zoom factor and frame rates. The intermediate
rendered JPEGs are deleted per section as it finishes (`--keep-frames` to keep
them); the frame metadata pass is cached in `movies/.index.json`.

Key options:

| flag | meaning |
|------|---------|
| `--width` / `--height` | output size (default 1080x1920, portrait) |
| `--speed <f>` | playback speed vs capture rate (default 0.25 = quarter speed) |
| `--fps <n>` | frame rate of the encoded file (default 30) |
| `--pad <f>` | window size as a multiple of the subject box (default 1.6) |
| `--min-scale <f>` | smallest window as a fraction of the output size, so a tiny crop isn't just upscaled (default 0.5) |
| `--smooth <n>` | frames of moving average on the subject path (default 9) |
| `--gap <s>` / `--min-frames <n>` | section splitting; skip sections shorter than n |
| `--sections 0,3,7-9` | build only these (numbering from `--list`) |
| `--override-dir <dir>` | sidecars that win over the ones next to the RAWs (default `broken_fixed`, i.e. `realign.py`'s fixes are used automatically) |
| `--crf <n>` | H.264 quality, lower is better (default 20) |

Sections shot at a low frame rate get flagged: at quarter speed a 6 fps burst
plays at 1.5 fps, which reads as a slideshow rather than motion.

## Files

- `autocrop.py` — detection, subject selection, sidecar writing (CLI).
- `realign.py` — re-pick the subject on mis-cropped frames from their burst
  neighbours; re-export to a separate directory (CLI).
- `section_movie.py` — group bursts into sections, build a subject-tracked
  fixed-size window per section, render and encode to MP4 (CLI).
- `dt_xmp.py` — darktable XMP helpers (`build_from_template`, `inject_crop`, crop
  param packing). Verified against darktable 5.0.
- `export_all.sh` — parallel `darktable-cli` JPEG export.
