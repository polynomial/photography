#!/usr/bin/env python3
"""Auto-crop DSLR (Canon CR3) photos to the main human subject by writing a
non-destructive `crop` operation into each darktable XMP sidecar.

Pipeline per image:
  1. Extract the embedded JPEG preview from the CR3 (fast; no raw decode).
  2. Orient it to match darktable's display (apply EXIF orientation).
  3. Detect people with YOLO; score boxes by area * centrality; pick the subject.
  4. Convert the (tight) box to normalized crop edges and write the crop op
     into the .xmp sidecar (darktable-compatible).

No person detected -> sidecar left unchanged (logged as a miss).
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from dt_xmp import inject_crop, build_from_template

EXIFTOOL = "exiftool"
CONF = 0.35          # min detection confidence for "person"
CENTRALITY = 0.6     # how much to penalize off-center subjects (0=ignore, 1=strong)
PERSON_CLASS = 0     # COCO class id for "person"
_DEVICE = None       # inference device for model.predict (None=auto, or "cpu")


# EXIF orientation -> PIL transpose op to make the image upright, same table
# darktable uses. The embedded JPEG previews carry NO orientation tag, so we
# read the CR3's orientation and apply it ourselves; otherwise portrait
# (orientation 6/8) shots would be detected in landscape space and the crop
# would come out rotated 90 deg.
_ORIENT_OP = {
    2: Image.FLIP_LEFT_RIGHT,
    3: Image.ROTATE_180,
    4: Image.FLIP_TOP_BOTTOM,
    5: Image.TRANSPOSE,
    6: Image.ROTATE_270,
    7: Image.TRANSVERSE,
    8: Image.ROTATE_90,
}


def _cr3_orientation(cr3_path):
    out = subprocess.run([EXIFTOOL, "-Orientation", "-n", "-s3", str(cr3_path)],
                        capture_output=True, text=True)
    try:
        return int(out.stdout.strip())
    except ValueError:
        return 1


def extract_preview(cr3_path):
    """Return a PIL.Image of the CR3's embedded preview, rotated to match the
    orientation darktable displays (so crop coords land in the right space)."""
    ori = _cr3_orientation(cr3_path)
    # JpgFromRaw is the full-size embedded JPEG on Canon R3; fall back to Preview.
    for tag in ("-JpgFromRaw", "-PreviewImage"):
        out = subprocess.run(
            [EXIFTOOL, "-b", tag, str(cr3_path)],
            capture_output=True)
        if out.returncode == 0 and out.stdout:
            img = Image.open(io.BytesIO(out.stdout))
            op = _ORIENT_OP.get(ori)
            if op is not None:
                img = img.transpose(op)
            return img.convert("RGB")
    raise RuntimeError(f"no embedded preview in {cr3_path}")


_model = None


def get_model(weights):
    global _model
    if _model is None:
        from ultralytics import YOLO
        _model = YOLO(weights)
    return _model


def _map_box_back(x1, y1, x2, y2, k, W, H):
    """Map a box from an image rotated by k*90deg CCW (np.rot90 semantics)
    back to original-image pixel coords. Returns axis-aligned (x1,y1,x2,y2)."""
    def pt(x, y):
        if k % 4 == 0:
            return x, y
        if k % 4 == 1:            # rotated = rot90(orig): (xo,yo)=(W-1-yr, xr)
            return W - 1 - y, x
        if k % 4 == 2:
            return W - 1 - x, H - 1 - y
        return y, H - 1 - x       # k==3: (xo,yo)=(yr, H-1-xr)
    xs, ys = zip(pt(x1, y1), pt(x2, y1), pt(x2, y2), pt(x1, y2))
    return min(xs), min(ys), max(xs), max(ys)


def detect_subject(img, weights):
    """Return (cx,cy,cw,ch) normalized crop edges for the main person, or None.

    Coordinates are fractions of the (oriented) image: left, top, right, bottom.
    Detection runs at 0/90/180/270 deg so horizontal/inverted action poses
    (e.g. a pole-vaulter over the bar) are found; boxes are mapped back to the
    upright frame. Selection favours the subject that is large, central AND
    in focus: score = area * (1-dist)^2 * (0.5 + 0.5*relative_sharpness). This
    stops a big, off-center, out-of-focus background athlete from winning.
    """
    W, H = img.size
    model = get_model(weights)
    base = np.array(img)
    gray = cv2.cvtColor(base, cv2.COLOR_RGB2GRAY)
    rotated = [np.rot90(base, k) for k in range(4)]
    results = model.predict(rotated, classes=[PERSON_CLASS], conf=CONF,
                            verbose=False, device=_DEVICE)

    boxes, confs = [], []
    for k, res in enumerate(results):
        if res.boxes is None or len(res.boxes) == 0:
            continue
        for xyxy, cf in zip(res.boxes.xyxy.cpu().numpy(),
                            res.boxes.conf.cpu().numpy()):
            x1, y1, x2, y2 = _map_box_back(*xyxy, k, W, H)
            boxes.append((x1, y1, x2, y2))
            confs.append(float(cf))
    if not boxes:
        return None, []

    img_cx, img_cy = W / 2.0, H / 2.0
    max_d = ((W / 2.0) ** 2 + (H / 2.0) ** 2) ** 0.5

    # per-box focus proxy: variance of the Laplacian inside the box
    sharps = []
    for (x1, y1, x2, y2) in boxes:
        ix1, iy1 = max(0, int(x1)), max(0, int(y1))
        ix2, iy2 = min(W, int(x2)), min(H, int(y2))
        sub = gray[iy1:iy2, ix1:ix2]
        sharps.append(cv2.Laplacian(sub, cv2.CV_64F).var() if sub.size else 0.0)
    max_sharp = max(sharps) if sharps else 0.0

    scored = []
    for ((x1, y1, x2, y2), cf, sharp) in zip(boxes, confs, sharps):
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        area_frac = area / (W * H)
        bcx, bcy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        dist = (((bcx - img_cx) ** 2 + (bcy - img_cy) ** 2) ** 0.5) / max_d
        sharp_rel = (sharp / max_sharp) if max_sharp > 0 else 1.0
        score = area_frac * (1.0 - dist) ** 2 * (0.5 + 0.5 * sharp_rel)
        scored.append((score, area_frac, dist, cf, (x1, y1, x2, y2), sharp_rel))

    scored.sort(key=lambda s: s[0], reverse=True)
    best = scored[0]
    x1, y1, x2, y2 = best[4]
    crop = (
        max(0.0, min(1.0, x1 / W)),
        max(0.0, min(1.0, y1 / H)),
        max(0.0, min(1.0, x2 / W)),
        max(0.0, min(1.0, y2 / H)),
    )
    meta = {"n": len(scored), "score": best[0], "area_frac": best[1],
            "dist": best[2], "conf": float(best[3]), "sharp_rel": best[5]}
    return crop, meta


def render_proof(cr3, xmp, out_jpg, dt_cli, cfg, cache, box=1400):
    """Render CR3+XMP to a JPEG via darktable-cli (authoritative preview)."""
    cmd = [dt_cli, str(cr3), str(xmp), str(out_jpg),
           "--width", str(box), "--height", str(box),
           "--core", "--library", ":memory:",
           "--configdir", str(cfg), "--cachedir", str(cache)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return out_jpg.exists()


# ---------- parallel workers ----------
_W_TEMPLATE = None
_W_WEIGHTS = None


def _worker_init(weights, device, template_text):
    global _DEVICE, _W_TEMPLATE, _W_WEIGHTS
    _DEVICE = device
    _W_TEMPLATE = template_text
    _W_WEIGHTS = weights
    get_model(weights)  # load model once per worker


def _worker_process(cr3_str):
    cr3 = Path(cr3_str)
    xmp = Path(cr3_str + ".xmp")
    try:
        img = extract_preview(cr3)
        crop, meta = detect_subject(img, _W_WEIGHTS)
    except Exception as e:
        return ("ERROR", cr3.name, str(e))
    if crop is None:
        return ("MISS", cr3.name, None)
    cx, cy, cw, ch = crop
    try:
        if _W_TEMPLATE is not None:
            text = build_from_template(_W_TEMPLATE, cr3.name, cx, cy, cw, ch)
        else:
            if not xmp.exists():
                return ("NOXMP", cr3.name, None)
            text = inject_crop(xmp.read_text(), cx, cy, cw, ch,
                               modversion=1, operation="crop")
        xmp.write_text(text)
    except Exception as e:
        return ("ERROR", cr3.name, str(e))
    return ("CROP", cr3.name, (cx, cy, cw, ch, meta))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photos_dir")
    ap.add_argument("--weights", default="yolo11m.pt")
    ap.add_argument("--limit", type=int, default=0, help="process first N only")
    ap.add_argument("--sample", type=int, default=0,
                    help="process N evenly-spread images across the set")
    ap.add_argument("--glob", default="*.CR3")
    ap.add_argument("--write", action="store_true",
                    help="write crop into the .xmp sidecars")
    ap.add_argument("--template", default="",
                    help="build each sidecar from this known-good darktable "
                         ".xmp (keeps its palette + adjustable crop), swapping "
                         "in the per-image detected crop box")
    ap.add_argument("--proof-dir", default="",
                    help="render darktable JPGs of results here")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel worker processes (CPU inference). "
                         "Ignored when --proof-dir is set.")
    ap.add_argument("--dt-cli",
                    default=(os.environ.get("DARKTABLE_CLI")
                             or shutil.which("darktable-cli")
                             or "/Applications/darktable.app/Contents/MacOS/darktable-cli"),
                    help="darktable-cli path (only needed for --proof-dir)")
    ap.add_argument("--dt-cfg", default="")
    ap.add_argument("--dt-cache", default="")
    args = ap.parse_args()

    photos = Path(args.photos_dir)
    cr3s = sorted(photos.glob(args.glob))
    if args.sample and len(cr3s) > args.sample:
        n = len(cr3s)
        cr3s = [cr3s[i * (n - 1) // (args.sample - 1)] for i in range(args.sample)]
    elif args.limit:
        cr3s = cr3s[:args.limit]
    proof_dir = Path(args.proof_dir) if args.proof_dir else None
    if proof_dir:
        proof_dir.mkdir(parents=True, exist_ok=True)

    template_text = None
    template_cr3 = None
    if args.template:
        tp = Path(args.template)
        template_text = tp.read_text()
        # the template's own image: "<name>.CR3.xmp" -> "<name>.CR3"
        template_cr3 = tp.name[:-4] if tp.name.endswith(".xmp") else tp.name

    hits = misses = errors = skipped = 0

    # -------- parallel path (write-only, no proof rendering) --------
    if args.workers > 1 and not proof_dir:
        import concurrent.futures as cf
        import multiprocessing as mp
        todo = [c for c in cr3s if not (template_cr3 and c.name == template_cr3)]
        skipped = len(cr3s) - len(todo)
        ctx = mp.get_context("spawn")
        n = len(cr3s)
        done = 0
        with cf.ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx,
                                    initializer=_worker_init,
                                    initargs=(args.weights, "cpu",
                                              template_text)) as ex:
            futs = {ex.submit(_worker_process, str(c)): c for c in todo}
            for fut in cf.as_completed(futs):
                done += 1
                status, name, payload = fut.result()
                if status == "CROP":
                    hits += 1
                    cx, cy, cw, ch, meta = payload
                    print(f"[{done}/{n}] CROP  {name}: "
                          f"({cx:.3f},{cy:.3f})-({cw:.3f},{ch:.3f}) "
                          f"people={meta['n']} conf={meta['conf']:.2f}", flush=True)
                elif status == "MISS":
                    misses += 1
                    print(f"[{done}/{n}] MISS  {name}: no person", flush=True)
                else:
                    errors += 1
                    print(f"[{done}/{n}] {status} {name}: {payload}", flush=True)
        print(f"\nDONE: {hits} cropped, {misses} no-person, {errors} errors, "
              f"{skipped} skipped, {len(cr3s)} total", flush=True)
        return

    for i, cr3 in enumerate(cr3s, 1):
        xmp = Path(str(cr3) + ".xmp")
        if template_cr3 and cr3.name == template_cr3:
            skipped += 1
            print(f"[{i}/{len(cr3s)}] SKIP  {cr3.name}: template image", flush=True)
            continue
        try:
            img = extract_preview(cr3)
            crop, meta = detect_subject(img, args.weights)
        except Exception as e:
            errors += 1
            print(f"[{i}/{len(cr3s)}] ERROR {cr3.name}: {e}", flush=True)
            continue

        if crop is None:
            misses += 1
            print(f"[{i}/{len(cr3s)}] MISS  {cr3.name}: no person", flush=True)
            continue

        hits += 1
        cx, cy, cw, ch = crop
        print(f"[{i}/{len(cr3s)}] CROP  {cr3.name}: "
              f"({cx:.3f},{cy:.3f})-({cw:.3f},{ch:.3f}) "
              f"people={meta['n']} conf={meta['conf']:.2f} "
              f"area={meta['area_frac']:.2f} dist={meta['dist']:.2f}", flush=True)

        if args.write or proof_dir:
            try:
                if template_text is not None:
                    new_xmp_text = build_from_template(template_text, cr3.name,
                                                       cx, cy, cw, ch)
                else:
                    if not xmp.exists():
                        print(f"        (no sidecar {xmp.name}; skipping write)",
                              flush=True)
                        continue
                    new_xmp_text = inject_crop(xmp.read_text(), cx, cy, cw, ch,
                                               modversion=1, operation="crop")
            except Exception as e:
                errors += 1
                print(f"        XMP-WRITE ERROR {xmp.name}: {e}", flush=True)
                continue
            if args.write:
                xmp.write_text(new_xmp_text)
            if proof_dir:
                # render from a (possibly temp) xmp reflecting the crop
                tmp_xmp = xmp if args.write else proof_dir / (cr3.stem + ".xmp")
                if not args.write:
                    tmp_xmp.write_text(new_xmp_text)
                render_proof(cr3, tmp_xmp, proof_dir / (cr3.stem + "_crop.jpg"),
                             args.dt_cli, args.dt_cfg or (proof_dir / "dtcfg"),
                             args.dt_cache or (proof_dir / "dtcache"))

    print(f"\nDONE: {hits} cropped, {misses} no-person, {errors} errors, "
          f"{skipped} skipped, {len(cr3s)} total", flush=True)


if __name__ == "__main__":
    main()
