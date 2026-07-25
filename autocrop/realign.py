#!/usr/bin/env python3
"""Re-align autocrop's subject choice on frames where it picked the wrong person,
using the frames around them where it picked the right one.

Premise: photos come in burst sequences. If autocrop got the subject right on
most of a burst and wrong on a few frames, the good frames tell us both *where*
the real subject was (its track through the burst) and *what it looks like*
(clothing colour signature). That is enough to re-pick the correct detection on
the bad frames without any manual input.

Workflow (matches how a bad run gets triaged by hand):
  <shoot>/export/<name>.jpg   -> autocrop got this one right  ("anchor")
  <shoot>/broken/<name>.jpg   -> autocrop got this one wrong  ("target")
  <shoot>/broken_fixed/       -> re-cropped re-export written here

Per target frame:
  1. Group frames into burst sequences by capture time (--gap).
  2. Detect *all* people in the target frame (4 orientations, low conf floor).
  3. Predict where the subject should be, from the nearest already-known boxes in
     the same sequence (interpolate between them, or extrapolate with velocity).
  4. Score every detection on: agreement with that prediction (position + size,
     with uncertainty that grows with time distance), colour-signature match to
     the anchor subjects, and detector confidence. Pick the best.
  5. Newly solved frames become known boxes themselves, so a long broken run is
     walked outward from the good frames one step at a time instead of being
     extrapolated in one jump. Colour reference always stays the original anchors.
  6. Write a sidecar (same look as autocrop) into the output dir and export.

Original sidecars are never touched: fixed sidecars live in the output dir, so
the old and new versions can be compared side by side.
"""
import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from autocrop import extract_preview, _map_box_back, PERSON_CLASS, get_model
from dt_xmp import build_from_template

DEFAULT_TEMPLATE = str(Path(__file__).resolve().parent / "default-look.xmp")
EXIFTOOL = "exiftool"

# Detection floor for re-picking. Lower than autocrop's 0.35: the right subject
# may be small or partly hidden and have been missed the first time. A wrong
# low-confidence box is harmless here because the prediction has to agree too.
RECHECK_CONF = 0.15
# Frames whose numbers differ by more than this are never burst neighbours;
# used to keep the (slow) metadata read off the whole shoot.
NEIGHBOUR_SPAN = 60
CROP_RE = re.compile(
    r'darktable:operation="crop"[^>]*?darktable:params="([0-9a-fA-F]+)"')
NAME_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)$")


# ---------------------------------------------------------------- frame index

def parse_frame(name):
    """('r3_card0_100EOSR3_hotness_023A8659.CR3') -> ('r3_..._023A', 8659)."""
    m = NAME_RE.match(name[:-4] if name.endswith(".CR3") else name)
    if not m:
        return name, 0
    return m.group("prefix"), int(m.group("num"))


def read_crop(xmp_path):
    """Normalized (left, top, right, bottom) from a sidecar's crop op."""
    if not xmp_path.exists():
        return None
    m = CROP_RE.search(xmp_path.read_text())
    if not m:
        return None
    return tuple(struct.unpack("<ffffii", bytes.fromhex(m.group(1)))[:4])


def read_times(paths):
    """{filename: epoch seconds (sub-second precision)} via one exiftool pass."""
    if not paths:
        return {}
    args = [EXIFTOOL, "-j", "-n", "-DateTimeOriginal", "-SubSecTime",
            "-FileName", "-@", "-"]
    proc = subprocess.run(args, input="\n".join(str(p) for p in paths),
                          capture_output=True, text=True)
    out = {}
    try:
        recs = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return out
    for d in recs:
        dto, sub = d.get("DateTimeOriginal"), d.get("SubSecTime")
        if not dto:
            continue
        try:
            t = datetime.strptime(str(dto), "%Y:%m:%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        if sub is not None:
            frac = str(sub).strip()
            if frac.isdigit():
                t += float("0." + frac)
        out[d["FileName"]] = t
    return out


def build_index(shoot, good_dir, broken_dir):
    """Frames relevant to the fix: every target, plus every frame close enough in
    numbering to be a burst neighbour of one. Returns list of dicts sorted by
    time, and the list of target names."""
    targets = sorted(p.stem + ".CR3" for p in (shoot / broken_dir).glob("*.jpg"))
    if not targets:
        return [], []
    want = {}
    for t in targets:
        pre, num = parse_frame(t)
        want.setdefault(pre, set()).update(
            range(num - NEIGHBOUR_SPAN, num + NEIGHBOUR_SPAN + 1))

    picked = []
    for cr3 in sorted(shoot.glob("*.CR3")):
        pre, num = parse_frame(cr3.name)
        if num in want.get(pre, ()):
            picked.append(cr3)

    times = read_times(picked)
    target_set = set(targets)
    frames = []
    for cr3 in picked:
        stem = cr3.name[:-4]
        status = ("target" if cr3.name in target_set
                  else "anchor" if (shoot / good_dir / (stem + ".jpg")).exists()
                  else "other")
        pre, num = parse_frame(cr3.name)
        frames.append({
            "name": cr3.name, "path": str(cr3), "prefix": pre, "num": num,
            "t": times.get(cr3.name), "status": status,
            "crop": read_crop(Path(str(cr3) + ".xmp")),
        })
    # frames with no timestamp fall back to frame number ordering within prefix
    frames = [f for f in frames if f["t"] is not None]
    frames.sort(key=lambda f: f["t"])
    return frames, targets


def split_sequences(frames, gap):
    """Split time-sorted frames into bursts: a gap longer than `gap` seconds (or
    a camera change) starts a new sequence."""
    seqs, cur = [], []
    for f in frames:
        if cur and (f["t"] - cur[-1]["t"] > gap or f["prefix"] != cur[-1]["prefix"]):
            seqs.append(cur)
            cur = []
        cur.append(f)
    if cur:
        seqs.append(cur)
    return seqs


# ------------------------------------------------------------- appearance

HIST_BINS = (24, 24)          # hue x saturation
PATCH = (64, 128)             # w, h that every person patch is resized to


def patch_signature(img_rgb, box):
    """Colour signature of a person box: HS histograms of its upper and lower
    halves (torso vs legs), so top/shorts colours are compared separately."""
    H, W = img_rgb.shape[:2]
    x1, y1, x2, y2 = box
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(W, int(x2)), min(H, int(y2))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    patch = cv2.resize(img_rgb[y1:y2, x1:x2], PATCH,
                       interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
    halves = []
    for sub in (hsv[: PATCH[1] // 2], hsv[PATCH[1] // 2:]):
        h = cv2.calcHist([sub], [0, 1], None, HIST_BINS, [0, 180, 0, 256])
        cv2.normalize(h, h, 0, 1, cv2.NORM_MINMAX)
        halves.append(h.flatten())
    return np.concatenate(halves)


def signature_match(sig, refs):
    """Best correlation of a candidate signature against the anchor signatures,
    mapped from [-1,1] to (0,1]."""
    if sig is None or not refs:
        return 0.5
    a = sig.reshape(2, -1)
    best = -1.0
    for r in refs:
        b = r.reshape(2, -1)
        c = float(np.mean([
            cv2.compareHist(a[i].astype("float32"), b[i].astype("float32"),
                            cv2.HISTCMP_CORREL) for i in range(2)]))
        best = max(best, c)
    return max(0.02, 0.5 + 0.5 * best)


# ------------------------------------------------------------- detection

def detect_all(img, weights, conf=RECHECK_CONF):
    """Every person box in the frame, detected at 0/90/180/270 deg and merged.
    Returns list of dicts: box (pixels, upright frame), conf, sharp, sig."""
    W, H = img.size
    model = get_model(weights)
    base = np.array(img)
    gray = cv2.cvtColor(base, cv2.COLOR_RGB2GRAY)
    results = model.predict([np.rot90(base, k) for k in range(4)],
                            classes=[PERSON_CLASS], conf=conf, verbose=False,
                            device="cpu")
    raw = []
    for k, res in enumerate(results):
        if res.boxes is None or len(res.boxes) == 0:
            continue
        for xyxy, cf in zip(res.boxes.xyxy.cpu().numpy(),
                            res.boxes.conf.cpu().numpy()):
            raw.append((_map_box_back(*xyxy, k, W, H), float(cf)))

    # merge duplicates found at several orientations
    raw.sort(key=lambda b: b[1], reverse=True)
    kept = []
    for box, cf in raw:
        if any(iou(box, k[0]) > 0.6 for k in kept):
            continue
        kept.append((box, cf))

    out = []
    for box, cf in kept:
        x1, y1, x2, y2 = box
        ix1, iy1 = max(0, int(x1)), max(0, int(y1))
        ix2, iy2 = min(W, int(x2)), min(H, int(y2))
        sub = gray[iy1:iy2, ix1:ix2]
        out.append({
            "box": [float(v) for v in box], "conf": cf,
            "sharp": float(cv2.Laplacian(sub, cv2.CV_64F).var()) if sub.size else 0.0,
            "sig": patch_signature(base, box),
        })
    return out


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


# ------------------------------------------------------------ worker plumbing

_WEIGHTS = None
_THUMB_DIR = None


def _init(weights, thumb_dir):
    global _WEIGHTS, _THUMB_DIR
    _WEIGHTS = weights
    _THUMB_DIR = thumb_dir
    get_model(weights)


def _work_target(path):
    """Detect every person in a target frame (+ QA thumbnail). Runs in a pool."""
    try:
        img = extract_preview(Path(path))
        cands = detect_all(img, _WEIGHTS)
        if _THUMB_DIR:
            _save_thumb(img, Path(path).name)
        W, H = img.size
        return {"path": path, "size": [W, H],
                "cands": [{**c,
                           "box": [c["box"][0] / W, c["box"][1] / H,
                                   c["box"][2] / W, c["box"][3] / H],
                           "sig": None if c["sig"] is None
                           else c["sig"].tolist()} for c in cands]}
    except Exception as e:                                  # noqa: BLE001
        return {"path": path, "error": str(e)}


def _work_anchor(path_and_crop):
    """Colour signature of the accepted subject in an anchor frame."""
    path, crop = path_and_crop
    try:
        img = extract_preview(Path(path))
        W, H = img.size
        cx, cy, cw, ch = crop
        box = (cx * W, cy * H, cw * W, ch * H)
        sig = patch_signature(np.array(img), box)
        return {"path": path, "size": [W, H],
                "sig": None if sig is None else sig.tolist()}
    except Exception as e:                                  # noqa: BLE001
        return {"path": path, "error": str(e)}


def _save_thumb(img, name, long_edge=1400):
    w, h = img.size
    s = long_edge / max(w, h)
    thumb = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.BILINEAR)
    thumb.save(Path(_THUMB_DIR) / (name + ".thumb.jpg"), quality=88)


def run_pool(fn, items, workers, weights, thumb_dir, label):
    if not items:
        return []
    if workers <= 1:
        _init(weights, thumb_dir)
        out = []
        for i, it in enumerate(items, 1):
            out.append(fn(it))
            print(f"  [{i}/{len(items)}] {label}", flush=True)
        return out
    import concurrent.futures as cf
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    out, done = [], 0
    with cf.ProcessPoolExecutor(max_workers=workers, mp_context=ctx,
                                initializer=_init,
                                initargs=(weights, thumb_dir)) as ex:
        for res in ex.map(fn, items):
            done += 1
            out.append(res)
            print(f"  [{done}/{len(items)}] {label}", flush=True)
    return out


# ------------------------------------------------------------- prediction

def _box_state(box):
    """(centre x, centre y, log width, log height) — the space we interpolate in.
    Boxes are normalized (0..1) edges, so anchors and targets are comparable
    without caring about each frame's pixel dimensions."""
    x1, y1, x2, y2 = box
    w, h = max(1e-4, x2 - x1), max(1e-4, y2 - y1)
    return np.array([(x1 + x2) / 2, (y1 + y2) / 2, math.log(w), math.log(h)])


def _state_box(s):
    cx, cy, lw, lh = s
    w, h = math.exp(lw), math.exp(lh)
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def predict(known, t):
    """Predict the subject box at time t from known (t, box) pairs in the burst.

    Between two knowns: interpolate. Outside them: extrapolate along the velocity
    of the two nearest, damped, because a long throw forward is a guess. Returns
    (box, dt) where dt is the time distance to the nearest known — the scale of
    how much the prediction should be trusted.
    """
    ks = sorted(known, key=lambda k: k[0])
    before = [k for k in ks if k[0] <= t]
    after = [k for k in ks if k[0] > t]
    dt = min(abs(k[0] - t) for k in ks)

    if before and after:
        t0, b0 = before[-1]
        t1, b1 = after[0]
        span = t1 - t0
        f = 0.5 if span <= 0 else (t - t0) / span
        s = _box_state(b0) * (1 - f) + _box_state(b1) * f
        return _state_box(s), dt

    side = before[::-1] if before else after
    t0, b0 = side[0]
    s = _box_state(b0)
    if len(side) >= 2:
        t1, b1 = side[1]
        if abs(t0 - t1) > 1e-6:
            vel = (_box_state(b0) - _box_state(b1)) / (t0 - t1)
            step = t - t0
            # damp the throw: trust velocity fully for a frame or two, then taper
            damp = 1.0 / (1.0 + abs(step) / 0.25)
            s = s + vel * step * damp
    return _state_box(s), dt


def score_candidates(cands, pred_box, dt, refs, size):
    """Rank detections against the prediction + anchor colour signature.

    Boxes are normalized; `size` un-squashes them so that distances and areas are
    measured in real (pixel-proportional) geometry.
    """
    W, H = size
    diag = math.hypot(W, H)
    px1, py1, px2, py2 = pred_box
    pcx, pcy = (px1 + px2) / 2 * W, (py1 + py2) / 2 * H
    pscale = math.sqrt(max(1.0, (px2 - px1) * W * (py2 - py1) * H))

    # uncertainty grows with how far in time the nearest known frame is
    sig_pos = min(0.35, 0.025 + 0.20 * dt)
    sig_size = min(1.2, 0.20 + 0.55 * dt)
    max_sharp = max((c["sharp"] for c in cands), default=0.0)

    scored = []
    for c in cands:
        x1, y1, x2, y2 = (c["box"][0] * W, c["box"][1] * H,
                          c["box"][2] * W, c["box"][3] * H)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        d = math.hypot(cx - pcx, cy - pcy) / diag
        s_pos = math.exp(-0.5 * (d / sig_pos) ** 2)
        scale = math.sqrt(max(1.0, (x2 - x1) * (y2 - y1)))
        s_size = math.exp(-0.5 * (math.log(scale / pscale) / sig_size) ** 2)
        sig = None if c["sig"] is None else np.asarray(c["sig"], dtype="float32")
        s_app = signature_match(sig, refs)
        sharp_rel = (c["sharp"] / max_sharp) if max_sharp > 0 else 1.0
        total = (s_pos
                 * s_size ** 0.7
                 * s_app ** 1.5
                 * (0.55 + 0.45 * c["conf"])
                 * (0.7 + 0.3 * sharp_rel))
        scored.append({**c, "score": total, "s_pos": s_pos, "s_size": s_size,
                       "s_app": s_app, "dist": d})
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored


# ------------------------------------------------------------------- QA sheet

def qa_overlay(thumb_dir, name, old_box, new_box, cands, out_path):
    """Full frame with the old pick (red), the new pick (green) and the other
    candidates (thin blue) drawn on it. All boxes normalized."""
    thumb = thumb_dir / (name + ".thumb.jpg")
    if not thumb.exists():
        return False
    img = cv2.imread(str(thumb))
    if img is None:
        return False
    h, w = img.shape[:2]

    def rect(box, colour, thick):
        x1, y1, x2, y2 = (int(round(box[0] * w)), int(round(box[1] * h)),
                          int(round(box[2] * w)), int(round(box[3] * h)))
        cv2.rectangle(img, (x1, y1), (x2, y2), colour, thick)

    for c in cands:
        rect(c["box"], (200, 140, 60), 1)
    if old_box:
        rect(old_box, (60, 60, 220), 3)
    if new_box:
        rect(new_box, (60, 200, 60), 3)
    cv2.putText(img, name, (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(out_path), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return True


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Re-pick the subject on mis-cropped frames using the "
                    "correctly-cropped frames around them.")
    ap.add_argument("shoot_dir")
    ap.add_argument("--broken-dir", default="broken",
                    help="subdir of frames autocrop got wrong (default: broken)")
    ap.add_argument("--good-dir", default="export",
                    help="subdir of frames autocrop got right (default: export)")
    ap.add_argument("--out-dir", default="broken_fixed",
                    help="where fixed sidecars + JPEGs go (default: broken_fixed)")
    ap.add_argument("--weights", default="yolo11m.pt")
    ap.add_argument("--template", default=DEFAULT_TEMPLATE,
                    help="darktable look for the rebuilt sidecars")
    ap.add_argument("--gap", type=float, default=2.0,
                    help="seconds between frames that splits burst sequences")
    ap.add_argument("--max-dt", type=float, default=8.0,
                    help="give up if the nearest good frame is further away "
                         "than this many seconds")
    ap.add_argument("--anchors", type=int, default=4,
                    help="good frames per sequence used as colour reference")
    ap.add_argument("--refresh", action="store_true",
                    help="ignore the cached detections and re-run YOLO")
    ap.add_argument("--only", default="",
                    help="process only target frames whose name matches this "
                         "glob (e.g. '*8659*'); for trying settings out")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--export-workers", type=int, default=0,
                    help="darktable-cli workers (default: same as --workers)")
    ap.add_argument("--quality", type=int, default=95)
    ap.add_argument("--no-export", action="store_true",
                    help="write sidecars + QA only, skip the JPEG export")
    ap.add_argument("--no-qa", action="store_true",
                    help="skip the QA overlay sheet")
    ap.add_argument("--dt-cli",
                    default=(os.environ.get("DARKTABLE_CLI")
                             or shutil.which("darktable-cli")
                             or "/Applications/darktable.app/Contents/MacOS/darktable-cli"))
    args = ap.parse_args()

    shoot = Path(args.shoot_dir)
    out = shoot / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    qa_dir = None if args.no_qa else (out / "qa")
    # full-frame thumbnails the QA overlays are drawn on; kept (hidden) so a
    # re-run off the detection cache can still redraw them
    thumbs = None if args.no_qa else (out / ".thumbs")
    for d in (qa_dir, thumbs):
        if d:
            d.mkdir(exist_ok=True)

    print(f"indexing {shoot} ...", flush=True)
    frames, targets = build_index(shoot, args.good_dir, args.broken_dir)
    if not targets:
        print(f"no JPEGs in {shoot / args.broken_dir}; nothing to do")
        return 1
    if args.only:
        import fnmatch
        keep = set(fnmatch.filter(targets, args.only))
        targets = [t for t in targets if t in keep]
        for f in frames:
            if f["status"] == "target" and f["name"] not in keep:
                f["status"] = "other"
        print(f"  --only {args.only}: {len(targets)} target frame(s)", flush=True)
        if not targets:
            return 1
    by_name = {f["name"]: f for f in frames}
    seqs = split_sequences(frames, args.gap)
    seq_of = {f["name"]: i for i, s in enumerate(seqs) for f in s}
    n_t = sum(1 for f in frames if f["status"] == "target")
    n_a = sum(1 for f in frames if f["status"] == "anchor")
    print(f"  {len(targets)} target frames, {n_a} good neighbours in "
          f"{len(seqs)} burst sequences", flush=True)
    missing = [t for t in targets if t not in by_name]
    if missing:
        print(f"  warning: {len(missing)} target(s) have no readable CR3/time: "
              f"{', '.join(missing[:5])}", flush=True)

    # ---- colour reference: the good frames closest in time to each target.
    # Per target, not per sequence — a burst can span several athletes, and
    # pooling every anchor's colours together would match anyone in the shot.
    ref_of = {}
    ref_needed = {}
    for f in frames:
        if f["status"] != "target":
            continue
        good = [g for g in seqs[seq_of[f["name"]]]
                if g["status"] == "anchor" and g["crop"] is not None]
        good.sort(key=lambda g: abs(g["t"] - f["t"]))
        near = good[:args.anchors]
        ref_of[f["name"]] = [g["name"] for g in near]
        for g in near:
            ref_needed[g["name"]] = (g["path"], g["crop"])

    print(f"reading {len(ref_needed)} anchor subjects ...", flush=True)
    anchor_sigs = {}
    for r in run_pool(_work_anchor, list(ref_needed.values()), args.workers,
                      args.weights, None, "anchor"):
        if r.get("sig") is not None:
            anchor_sigs[Path(r["path"]).name] = np.asarray(r["sig"],
                                                           dtype="float32")

    # ---- detect everything in the target frames (order-independent, parallel)
    cache_path = out / ".detections.pkl"
    det = {}
    if cache_path.exists() and not args.refresh:
        import pickle
        try:
            det = pickle.loads(cache_path.read_bytes())
            print(f"reusing {len(det)} cached detections "
                  f"({cache_path.name}; --refresh to redo)", flush=True)
        except Exception as e:                              # noqa: BLE001
            print(f"  ignoring unreadable cache: {e}", flush=True)
            det = {}
    todo = [by_name[t]["path"] for t in targets
            if t in by_name and t not in det]
    if todo:
        print(f"detecting people in {len(todo)} target frames ...", flush=True)
        for r in run_pool(_work_target, todo, args.workers, args.weights,
                          str(thumbs) if thumbs else None, "detect"):
            det[Path(r["path"]).name] = r
        import pickle
        cache_path.write_bytes(pickle.dumps(det))

    # ---- solve each sequence, walking outward from the good frames
    template_text = Path(args.template).read_text()
    report, fixed = [], []
    for si, seq in enumerate(seqs):
        tgt = [f for f in seq if f["status"] == "target"]
        if not tgt:
            continue
        anchors = [f for f in seq if f["status"] == "anchor" and f["crop"]]
        known = [(f["t"], list(f["crop"])) for f in anchors]
        unsolved = {f["name"]: f for f in tgt if f["name"] in det
                    and "cands" in det[f["name"]]}

        while unsolved:
            if not known:
                for f in unsolved.values():
                    report.append({"name": f["name"], "status": "no-anchor"})
                break
            # nearest-first: solve the frame closest in time to a known box, so a
            # long broken run is walked one frame at a time from the good end
            nm, f = min(unsolved.items(),
                        key=lambda kv: min(abs(k[0] - kv[1]["t"])
                                           for k in known))
            unsolved.pop(nm)
            d = det[nm]
            pred, dt = predict(known, f["t"])
            if dt > args.max_dt:
                report.append({"name": nm, "status": "too-far",
                               "dt": round(dt, 2)})
                continue
            cands = d["cands"]
            if not cands:
                report.append({"name": nm, "status": "no-detections"})
                continue
            refs = [anchor_sigs[a] for a in ref_of.get(nm, ())
                    if a in anchor_sigs]
            scored = score_candidates(cands, pred, dt, refs, d["size"])
            best = scored[0]
            runner = scored[1]["score"] if len(scored) > 1 else 0.0
            crop = tuple(max(0.0, min(1.0, v)) for v in best["box"])
            old = by_name[nm]["crop"]
            (out / (nm + ".xmp")).write_text(
                build_from_template(template_text, nm, *crop))
            known.append((f["t"], list(crop)))
            fixed.append(nm)
            rec = {"name": nm, "status": "fixed", "seq": si, "dt": round(dt, 2),
                   "n_cands": len(cands), "score": round(best["score"], 4),
                   # how far clear of the runner-up the winner was; capped
                   # because a hopeless runner-up makes the ratio meaningless
                   "margin": (min(999.0, round(best["score"] / runner, 2))
                              if runner else None),
                   "s_pos": round(best["s_pos"], 3),
                   "s_size": round(best["s_size"], 3),
                   "s_app": round(best["s_app"], 3),
                   "conf": round(best["conf"], 3),
                   "crop": [round(v, 4) for v in crop],
                   "old_crop": None if not old else [round(v, 4) for v in old],
                   "iou_with_old": (round(iou(crop, old), 3) if old else None)}
            report.append(rec)
            print(f"  FIX {nm}: dt={dt:5.2f}s cands={len(cands):2d} "
                  f"pos={best['s_pos']:.2f} size={best['s_size']:.2f} "
                  f"app={best['s_app']:.2f} margin="
                  f"{rec['margin'] if rec['margin'] else 0:.1f} "
                  f"iou_old={rec['iou_with_old']}", flush=True)
            if qa_dir:
                qa_overlay(thumbs, nm, old, crop, cands,
                           qa_dir / (nm[:-4] + "_qa.jpg"))

    (out / "realign-report.json").write_text(json.dumps(report, indent=1))
    ok = sum(1 for r in report if r["status"] == "fixed")
    print(f"\nre-aligned {ok}/{len(targets)} frames -> {out}", flush=True)
    for st in ("no-anchor", "too-far", "no-detections"):
        n = sum(1 for r in report if r["status"] == st)
        if n:
            print(f"  {n} {st}", flush=True)

    # ---- export
    if fixed and not args.no_export:
        nw = args.export_workers or args.workers
        print(f"exporting {len(fixed)} frames with {nw} workers ...", flush=True)
        export(shoot, out, fixed, args.dt_cli, nw, args.quality)
    return 0


def export(shoot, out, names, dt_cli, workers, quality):
    """Full-resolution JPEG export of the fixed sidecars into the output dir."""
    import concurrent.futures as cf
    import tempfile

    def one(name):
        cr3 = shoot / name
        xmp = out / (name + ".xmp")
        jpg = out / (name[:-4] + ".jpg")
        if jpg.exists():
            jpg.unlink()          # darktable-cli would write _01 instead
        with tempfile.TemporaryDirectory() as cfg:
            r = subprocess.run(
                [dt_cli, str(cr3), str(xmp), str(jpg),
                 "--width", "0", "--height", "0", "--hq", "true",
                 "--upscale", "false", "--core", "--library", ":memory:",
                 "--configdir", cfg, "--cachedir", cfg,
                 "--conf", f"plugins/imageio/format/jpeg/quality={quality}"],
                capture_output=True, text=True)
        return name, jpg.exists(), r.returncode

    ok = fail = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (name, good, rc) in enumerate(ex.map(one, names), 1):
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
            print(f"  [{i}/{len(names)}] {'OK  ' if good else 'FAIL'} {name}",
                  flush=True)
    print(f"export done: {ok} ok, {fail} failed", flush=True)


if __name__ == "__main__":
    sys.exit(main())
