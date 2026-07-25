#!/usr/bin/env python3
"""Turn a burst-shot stills shoot into subject-tracked video clips.

A movie made from the full frames doesn't work: the subject wanders around the
frame. A movie made from autocrop's per-image crops doesn't work either, because
every crop is a different size. So:

  1. Group the shoot into *sections* — bursts, split on a gap in capture time.
  2. Per section, take the subject box autocrop already found for each frame
     (from the darktable sidecars) and derive ONE window size for the whole
     section, big enough for the subject throughout.
  3. Slide that fixed-size window along a smoothed path of the subject's centre,
     clamped to the frame edges. Constant size + moving centre = a stable,
     subject-centred shot that video can be made from.
  4. Render each frame through darktable with that window as its crop (so the
     clips carry the same look as the exported stills), then encode the section
     to an H.264 MP4 that plays anywhere, Android included.

Robustness matters here because a single bad subject box would otherwise blow up
a whole section: box sizes are taken from a percentile rather than the maximum,
and centres that jump implausibly are treated as outliers and interpolated over.

Output (under <shoot>/movies/):
  section_NN_<first>-<last>.mp4   one clip per section
  all_sections.mp4                every clip end to end
  sections.json                   what each clip covers, its window, its fps
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from realign import parse_frame, read_crop, read_times, split_sequences
from dt_xmp import build_from_template

DEFAULT_TEMPLATE = str(Path(__file__).resolve().parent / "default-look.xmp")
EXIFTOOL = "exiftool"


# --------------------------------------------------------------------- index

def frame_size(d):
    """Oriented (width, height) of a frame: EXIF orientation 5-8 means the raw
    dimensions are stored rotated, so swap them to get display geometry."""
    w, h = d.get("ImageWidth"), d.get("ImageHeight")
    if not w or not h:
        return None
    return (h, w) if int(d.get("Orientation") or 1) in (5, 6, 7, 8) else (w, h)


def read_sizes(paths):
    """{filename: (w, h)} in display orientation, one exiftool pass."""
    if not paths:
        return {}
    proc = subprocess.run(
        [EXIFTOOL, "-j", "-n", "-ImageWidth", "-ImageHeight", "-Orientation",
         "-FileName", "-@", "-"],
        input="\n".join(str(p) for p in paths), capture_output=True, text=True)
    try:
        recs = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    out = {}
    for d in recs:
        s = frame_size(d)
        if s:
            out[d["FileName"]] = s
    return out


def build_index(shoot, override_dir, cache, refresh=False):
    """Every CR3 with its capture time, display size and subject box."""
    if cache.exists() and not refresh:
        try:
            rows = json.loads(cache.read_text())
            print(f"  reusing cached index ({cache.name}; --refresh to redo)",
                  flush=True)
            return rows
        except json.JSONDecodeError:
            pass
    cr3s = sorted(shoot.glob("*.CR3"))
    print(f"  reading metadata for {len(cr3s)} frames (slow, cached after) ...",
          flush=True)
    times = read_times(cr3s)
    sizes = read_sizes(cr3s)
    rows = []
    for c in cr3s:
        crop = None
        if override_dir:
            crop = read_crop(override_dir / (c.name + ".xmp"))
        fixed = crop is not None
        if crop is None:
            crop = read_crop(Path(str(c) + ".xmp"))
        pre, num = parse_frame(c.name)
        rows.append({"name": c.name, "path": str(c), "prefix": pre, "num": num,
                     "t": times.get(c.name), "size": sizes.get(c.name),
                     "crop": list(crop) if crop else None, "fixed": fixed})
    rows = [r for r in rows if r["t"] is not None and r["size"]]
    rows.sort(key=lambda r: r["t"])
    cache.write_text(json.dumps(rows))
    return rows


def split_on_size(seq):
    """Split a section where the frame geometry changes (portrait/landscape or a
    different body), since one window size can't serve both."""
    out, cur = [], []
    for f in seq:
        if cur and tuple(f["size"]) != tuple(cur[-1]["size"]):
            out.append(cur)
            cur = []
        cur.append(f)
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------- window solving

def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def percentile(xs, p):
    s = sorted(xs)
    if not s:
        return 0.0
    i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[i]


def median_filter(vals, k=5):
    half = k // 2
    return [median(vals[max(0, i - half):i + half + 1]) for i in range(len(vals))]


def smooth(vals, k):
    """Moving average, shrinking at the ends so the shot doesn't drift in/out."""
    if k <= 1:
        return list(vals)
    half = k // 2
    out = []
    for i in range(len(vals)):
        lo, hi = max(0, i - half), min(len(vals), i + half + 1)
        out.append(sum(vals[lo:hi]) / (hi - lo))
    return out


def solve_section(seq, out_w, out_h, pad, min_scale, smooth_frames):
    """Return (window_w, window_h, [(x, y) top-left per frame], stats).

    Window size is constant; only the centre moves. Sizes come from a percentile
    of the section's boxes so one runaway box can't zoom the whole clip out.
    """
    W, H = seq[0]["size"]
    boxes = []
    for f in seq:
        c = f["crop"]
        if not c:
            boxes.append(None)
            continue
        x1, y1, x2, y2 = c[0] * W, c[1] * H, c[2] * W, c[3] * H
        boxes.append(((x1 + x2) / 2, (y1 + y2) / 2, max(1.0, x2 - x1),
                      max(1.0, y2 - y1)))

    have = [b for b in boxes if b]
    if not have:
        return None
    med_area = median([b[2] * b[3] for b in have])

    # ---- reject boxes that can't be the same subject as the rest of the burst
    keep = []
    for b in boxes:
        if b is None:
            keep.append(None)
            continue
        area = b[2] * b[3]
        keep.append(None if (area > 4.0 * med_area or area < 0.2 * med_area)
                    else b)
    if not any(k for k in keep):
        keep = list(boxes)

    # ---- window size: percentile of the surviving boxes, padded, then forced
    # to the output aspect. Floored so we never zoom in past `min_scale` of the
    # output size (that would be pure upscaling of a tiny crop).
    good = [k for k in keep if k]
    need_w = percentile([k[2] for k in good], 0.9) * pad
    need_h = percentile([k[3] for k in good], 0.9) * pad
    aspect = out_w / out_h
    win_h = max(need_h, need_w / aspect, out_h * min_scale)
    win_w = win_h * aspect
    if win_w > W:                       # too wide for the frame: fit width
        win_w, win_h = float(W), W / aspect
    if win_h > H:                       # too tall: fit height
        win_h, win_w = float(H), H * aspect
    win_w, win_h = min(win_w, W), min(win_h, H)

    # ---- centre path: fill gaps, kill spikes, smooth
    cxs = [k[0] if k else None for k in keep]
    cys = [k[1] if k else None for k in keep]

    def fill(vals, fallback):
        known = [i for i, v in enumerate(vals) if v is not None]
        if not known:
            return [fallback] * len(vals)
        out = list(vals)
        for i in range(len(vals)):
            if out[i] is not None:
                continue
            prev = max((j for j in known if j < i), default=None)
            nxt = min((j for j in known if j > i), default=None)
            if prev is None:
                out[i] = vals[nxt]
            elif nxt is None:
                out[i] = vals[prev]
            else:
                f = (i - prev) / (nxt - prev)
                out[i] = vals[prev] * (1 - f) + vals[nxt] * f
        return out

    cxs = smooth(median_filter(fill(cxs, W / 2)), smooth_frames)
    cys = smooth(median_filter(fill(cys, H / 2)), smooth_frames)

    # ---- top-left per frame, clamped so the window stays inside the frame
    pos = []
    for cx, cy in zip(cxs, cys):
        x = min(max(0.0, cx - win_w / 2), W - win_w)
        y = min(max(0.0, cy - win_h / 2), H - win_h)
        pos.append((x, y))

    stats = {"frame": [W, H], "window": [round(win_w), round(win_h)],
             "boxes_used": len(good), "boxes_rejected": sum(
                 1 for b, k in zip(boxes, keep) if b and not k),
             "boxes_missing": sum(1 for b in boxes if b is None),
             "zoom": round(win_h / out_h, 2)}
    return win_w, win_h, pos, stats


# ------------------------------------------------------------------- render

_TEMPLATE = None
_DT = None
_OUT = None


def _init(template_text, dt_cli, out_size):
    global _TEMPLATE, _DT, _OUT
    _TEMPLATE, _DT, _OUT = template_text, dt_cli, out_size


def _render(job):
    """Render one frame cropped to its window, at the output size."""
    import tempfile
    cr3, crop, dest = job["cr3"], job["crop"], job["dest"]
    ow, oh = _OUT
    xmp = Path(dest).with_suffix(".xmp")
    try:
        xmp.write_text(build_from_template(_TEMPLATE, Path(cr3).name, *crop))
        with tempfile.TemporaryDirectory() as cfg:
            subprocess.run(
                [_DT, cr3, str(xmp), dest,
                 "--width", str(ow), "--height", str(oh),
                 "--hq", "true", "--upscale", "true",
                 "--core", "--library", ":memory:",
                 "--configdir", cfg, "--cachedir", cfg,
                 "--conf", "plugins/imageio/format/jpeg/quality=94"],
                capture_output=True, text=True)
    except Exception as e:                                  # noqa: BLE001
        return dest, False, str(e)
    finally:
        xmp.unlink(missing_ok=True)
    return dest, Path(dest).exists(), ""


def render_frames(jobs, template_text, dt_cli, out_size, workers, label):
    import concurrent.futures as cf
    ok = fail = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_render, j) for j in jobs]
        for i, fut in enumerate(cf.as_completed(futs), 1):
            _, good, err = fut.result()
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
            if i % 25 == 0 or i == len(jobs):
                print(f"    {label}: rendered {i}/{len(jobs)}"
                      f"{f' ({fail} failed)' if fail else ''}", flush=True)
    return ok, fail


def encode(frames_dir, out_mp4, fps_in, out_w, out_h, fps_out, crf, ffmpeg):
    """Encode a numbered JPEG sequence to a phone-friendly H.264 MP4."""
    # in_range=pc -> out_range=tv: JPEGs are full-range, video players expect
    # limited range. Without this the file lands as yuvj420p and contrast shifts
    # depending on which player opens it.
    vf = (f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease"
          f":in_range=pc:out_range=tv,"
          f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p")
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-framerate", f"{fps_in:.4f}", "-i", str(frames_dir / "%05d.jpg"),
           "-vf", vf, "-r", str(fps_out),
           "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
           "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p",
           "-color_range", "tv", "-colorspace", "bt709",
           "-color_primaries", "bt709", "-color_trc", "bt709",
           "-movflags", "+faststart", str(out_mp4)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ffmpeg failed: {r.stderr.strip()[:400]}", flush=True)
        return False
    return out_mp4.exists()


def concat(clips, out_mp4, ffmpeg, work):
    """Join the section clips end to end (all share codec + geometry)."""
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    r = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", "-movflags", "+faststart",
         str(out_mp4)], capture_output=True, text=True)
    lst.unlink(missing_ok=True)
    if r.returncode != 0:
        print(f"  concat failed: {r.stderr.strip()[:400]}", flush=True)
        return False
    return out_mp4.exists()


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Build subject-tracked video clips, one per burst section.")
    ap.add_argument("shoot_dir")
    ap.add_argument("--out-dir", default="movies")
    ap.add_argument("--override-dir", default="broken_fixed",
                    help="sidecars here win over the ones next to the RAWs "
                         "(so realign.py's fixes are used); '' to disable")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--gap", type=float, default=2.0,
                    help="seconds between frames that starts a new section")
    ap.add_argument("--min-frames", type=int, default=8,
                    help="skip sections shorter than this")
    ap.add_argument("--speed", type=float, default=0.25,
                    help="playback speed vs capture rate (0.25 = quarter speed)")
    ap.add_argument("--fps", type=float, default=30.0,
                    help="output frame rate of the encoded file")
    ap.add_argument("--pad", type=float, default=1.6,
                    help="window size as a multiple of the subject box")
    ap.add_argument("--min-scale", type=float, default=0.5,
                    help="smallest window as a fraction of the output size; "
                         "below this the crop would just be upscaled")
    ap.add_argument("--smooth", type=int, default=9,
                    help="frames of moving average on the subject path")
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--sections", default="",
                    help="only these sections, e.g. '0,3,7-9' (after listing)")
    ap.add_argument("--list", action="store_true",
                    help="print the section breakdown and exit")
    ap.add_argument("--keep-frames", action="store_true",
                    help="keep the rendered JPEGs instead of deleting them")
    ap.add_argument("--no-concat", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="re-read frame metadata instead of using the cache")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--template", default=DEFAULT_TEMPLATE)
    ap.add_argument("--ffmpeg", default=os.environ.get("FFMPEG") or
                    shutil.which("ffmpeg") or "ffmpeg")
    ap.add_argument("--dt-cli",
                    default=(os.environ.get("DARKTABLE_CLI")
                             or shutil.which("darktable-cli")
                             or "/Applications/darktable.app/Contents/MacOS/darktable-cli"))
    args = ap.parse_args()

    shoot = Path(args.shoot_dir)
    out = shoot / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    work = out / ".work"
    work.mkdir(exist_ok=True)
    override = (shoot / args.override_dir) if args.override_dir else None
    if override and not override.exists():
        override = None

    print(f"indexing {shoot} ...", flush=True)
    rows = build_index(shoot, override, out / ".index.json", args.refresh)
    n_fixed = sum(1 for r in rows if r["fixed"])
    n_nocrop = sum(1 for r in rows if not r["crop"])
    print(f"  {len(rows)} frames"
          + (f", {n_fixed} using realigned boxes" if n_fixed else "")
          + (f", {n_nocrop} with no subject box" if n_nocrop else ""), flush=True)

    seqs = [s for grp in split_sequences(rows, args.gap)
            for s in split_on_size(grp)]
    keep, skipped = [], 0
    for s in seqs:
        if len(s) >= args.min_frames:
            keep.append(s)
        else:
            skipped += 1
    print(f"  {len(keep)} sections (>= {args.min_frames} frames), "
          f"{skipped} too short to film", flush=True)

    wanted = None
    if args.sections:
        wanted = set()
        for part in args.sections.split(","):
            if "-" in part:
                a, b = part.split("-")
                wanted.update(range(int(a), int(b) + 1))
            elif part.strip():
                wanted.add(int(part))

    template_text = Path(args.template).read_text()
    _init(template_text, args.dt_cli, (args.width, args.height))

    manifest, clips = [], []
    for si, seq in enumerate(keep):
        span = seq[-1]["t"] - seq[0]["t"]
        native = (len(seq) - 1) / span if span > 0 else args.fps
        first, last = seq[0]["name"][-12:-4], seq[-1]["name"][-12:-4]
        head = (f"[{si}] {len(seq):4d} frames  {span:6.2f}s  "
                f"{native:5.1f}fps  {first}..{last}")
        if args.list:
            print(head, flush=True)
            continue
        if wanted is not None and si not in wanted:
            continue
        print(head, flush=True)

        solved = solve_section(seq, args.width, args.height, args.pad,
                               args.min_scale, args.smooth)
        if not solved:
            print("    no subject boxes in this section; skipping", flush=True)
            continue
        win_w, win_h, pos, stats = solved
        print(f"    window {stats['window'][0]}x{stats['window'][1]} px of "
              f"{stats['frame'][0]}x{stats['frame'][1]} (zoom {stats['zoom']}x), "
              f"boxes: {stats['boxes_used']} used, "
              f"{stats['boxes_rejected']} rejected, "
              f"{stats['boxes_missing']} missing", flush=True)

        fdir = work / f"section_{si:02d}"
        if fdir.exists():
            shutil.rmtree(fdir)
        fdir.mkdir(parents=True)
        W, H = seq[0]["size"]
        jobs = []
        for i, (f, (x, y)) in enumerate(zip(seq, pos), 1):
            crop = (x / W, y / H, (x + win_w) / W, (y + win_h) / H)
            jobs.append({"cr3": f["path"], "crop": crop,
                         "dest": str(fdir / f"{i:05d}.jpg")})
        ok, fail = render_frames(jobs, template_text, args.dt_cli,
                                 (args.width, args.height), args.workers,
                                 f"[{si}]")
        if fail:
            print(f"    {fail} frame(s) failed to render; renumbering the rest",
                  flush=True)
            for i, p in enumerate(sorted(fdir.glob("*.jpg")), 1):
                p.rename(fdir / f"tmp{i:05d}.jpg")
            for i, p in enumerate(sorted(fdir.glob("tmp*.jpg")), 1):
                p.rename(fdir / f"{i:05d}.jpg")
        n_frames = len(list(fdir.glob("*.jpg")))
        if n_frames < 2:
            print("    too few frames rendered; skipping", flush=True)
            continue

        clip = out / f"section_{si:02d}_{first}-{last}.mp4"
        fps_in = max(1.0, native * args.speed)
        if fps_in < 4.0:
            print(f"    note: shot at only {native:.0f}fps, so at "
                  f"{args.speed:g}x speed this plays at {fps_in:.1f}fps — "
                  f"more slideshow than motion", flush=True)
        if encode(fdir, clip, fps_in, args.width, args.height, args.fps,
                  args.crf, args.ffmpeg):
            dur = n_frames / fps_in
            size_mb = clip.stat().st_size / 1e6
            print(f"    -> {clip.name}  {dur:.1f}s  {size_mb:.1f} MB", flush=True)
            clips.append(clip)
            manifest.append({"section": si, "clip": clip.name,
                             "frames": n_frames, "first": first, "last": last,
                             "captured_s": round(span, 2),
                             "native_fps": round(native, 2),
                             "play_fps_in": round(fps_in, 2),
                             "duration_s": round(dur, 1), **stats})
        if not args.keep_frames:
            shutil.rmtree(fdir, ignore_errors=True)

    if args.list:
        return 0

    (out / "sections.json").write_text(json.dumps(manifest, indent=1))
    print(f"\n{len(clips)} clip(s) -> {out}", flush=True)
    if clips and not args.no_concat:
        allmp4 = out / "all_sections.mp4"
        if concat(clips, allmp4, args.ffmpeg, work):
            total = sum(m["duration_s"] for m in manifest)
            print(f"combined: {allmp4.name}  {total:.0f}s  "
                  f"{allmp4.stat().st_size / 1e6:.1f} MB", flush=True)
    if not args.keep_frames:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
