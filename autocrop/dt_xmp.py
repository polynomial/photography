"""Inject / replace a darktable `crop` operation in a darktable XMP sidecar.

darktable stores each edit as an <rdf:li> in the darktable:history Seq. A crop is
the `crop` module whose params are a little-endian binary blob:
    float cx, cy, cw, ch;   # normalized crop edges: left, top, right, bottom (0..1)
    int   ratio_n, ratio_d; # aspect-ratio lock; 0,0 = freeform
packed as raw hex (no gz prefix) because it's small.
"""
import re
import struct

# A disabled/standard blendop blob reused by geometric ops in these sidecars.
BLENDOP = "gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU="
BLENDOP_VERSION = 13


def pack_crop_params(cx, cy, cw, ch, ratio_n=0, ratio_d=0):
    """Return hex string for the crop module params."""
    blob = struct.pack("<ffffii", cx, cy, cw, ch, int(ratio_n), int(ratio_d))
    return blob.hex()


def make_crop_li(num, params_hex, modversion, operation="crop"):
    return (
        f'     <rdf:li\n'
        f'      darktable:num="{num}"\n'
        f'      darktable:operation="{operation}"\n'
        f'      darktable:enabled="1"\n'
        f'      darktable:modversion="{modversion}"\n'
        f'      darktable:params="{params_hex}"\n'
        f'      darktable:multi_name=""\n'
        f'      darktable:multi_name_hand_edited="0"\n'
        f'      darktable:multi_priority="0"\n'
        f'      darktable:blendop_version="{BLENDOP_VERSION}"\n'
        f'      darktable:blendop_params="{BLENDOP}"/>\n'
    )


def inject_crop(xmp_text, cx, cy, cw, ch, modversion, operation="crop",
                ratio_n=0, ratio_d=0):
    """Return new xmp text with a crop op appended to the history and
    history_end bumped. Removes any pre-existing crop/clipping op first."""
    text = xmp_text

    # 1. strip any existing crop/clipping <rdf:li> blocks
    text = re.sub(
        r'\s*<rdf:li\b[^>]*?darktable:operation="(?:crop|clipping)"[^>]*?/>',
        "", text, flags=re.DOTALL)

    # 2. find current max num within the history Seq
    hist_match = re.search(
        r'(<darktable:history>\s*<rdf:Seq>)(.*?)(</rdf:Seq>\s*</darktable:history>)',
        text, flags=re.DOTALL)
    if not hist_match:
        raise ValueError("no darktable:history Seq found")
    body = hist_match.group(2)
    nums = [int(n) for n in re.findall(r'darktable:num="(\d+)"', body)]
    next_num = (max(nums) + 1) if nums else 0

    params_hex = pack_crop_params(cx, cy, cw, ch, ratio_n, ratio_d)
    li = make_crop_li(next_num, params_hex, modversion, operation)
    new_body = body.rstrip("\n") + "\n" + li
    text = text[:hist_match.start()] + hist_match.group(1) + new_body + \
        hist_match.group(3) + text[hist_match.end():]

    # 3. bump history_end to include the new op (count of entries)
    new_count = next_num + 1
    if re.search(r'darktable:history_end="\d+"', text):
        text = re.sub(r'darktable:history_end="\d+"',
                      f'darktable:history_end="{new_count}"', text)
    return text


def strip_ops(text, ops_to_remove):
    """Remove the named darktable operations from the history Seq, renumber the
    remaining entries 0..M-1, and set history_end=M. Used to drop camera/shot-
    specific modules (rawprepare, temperature, colorin, flip, channelmixerrgb)
    so darktable auto-applies correct per-image defaults for them."""
    hist_match = re.search(
        r'(<darktable:history>\s*<rdf:Seq>)(.*?)(</rdf:Seq>\s*</darktable:history>)',
        text, flags=re.DOTALL)
    if not hist_match:
        raise ValueError("no darktable:history Seq found")
    body = hist_match.group(2)

    # split into individual <rdf:li ... /> blocks
    lis = re.findall(r'<rdf:li\b.*?/>', body, flags=re.DOTALL)
    kept = []
    for li in lis:
        m = re.search(r'darktable:operation="([^"]*)"', li)
        if m and m.group(1) in ops_to_remove:
            continue
        kept.append(li)

    # renumber remaining
    renum = []
    for i, li in enumerate(kept):
        li2 = re.sub(r'(darktable:num=")\d+(")', rf'\g<1>{i}\g<2>', li)
        renum.append(li2)
    new_body = "\n     " + "\n     ".join(renum) + "\n    "
    text = (text[:hist_match.start()] + hist_match.group(1) + new_body +
            hist_match.group(3) + text[hist_match.end():])
    text = re.sub(r'darktable:history_end="\d+"',
                  f'darktable:history_end="{len(renum)}"', text)
    return text


# camera/shot-specific modules to NOT copy from the template (darktable will
# auto-apply correct per-image defaults for these on load)
SHOT_SPECIFIC_OPS = {"rawprepare", "temperature", "colorin", "flip",
                     "channelmixerrgb"}


def build_from_template(template_text, cr3_filename, cx, cy, cw, ch):
    """Return a sidecar built from a known-good darktable template, with only
    the crop box + per-image identity changed. This mirrors a real
    darktable-authored history exactly (crop op stays in its original position),
    so darktable keeps the crop adjustable instead of baking it.

    - replaces the params of the existing `crop` op with the detected box
    - repoints xmpMM:DerivedFrom at this image
    - drops darktable's history hashes so darktable recomputes them cleanly
    """
    t = template_text
    params_hex = pack_crop_params(cx, cy, cw, ch, 0, 0)

    def repl_crop(m):
        return m.group(1) + params_hex + m.group(2)
    t, n = re.subn(
        r'(darktable:operation="crop"[^>]*?darktable:params=")[0-9a-fA-F]+(")',
        repl_crop, t)
    if n != 1:
        raise ValueError(f"expected exactly 1 crop op to patch, found {n}")

    t = re.sub(r'(xmpMM:DerivedFrom=")[^"]*(")',
               lambda m: m.group(1) + cr3_filename + m.group(2), t)

    # remove hashes (stale after changing crop params); darktable recomputes them
    t = re.sub(r'\s*darktable:history_auto_hash="[0-9a-fA-F]*"', "", t)
    t = re.sub(r'\s*darktable:history_current_hash="[0-9a-fA-F]*"', "", t)

    # drop camera/shot-specific base modules so darktable auto-applies the
    # correct per-image defaults (the template baked one shot's raw black/white
    # point, WB and input matrix, which blows out other ISOs/bodies).
    t = strip_ops(t, SHOT_SPECIFIC_OPS)
    return t


if __name__ == "__main__":
    import sys
    src, dst = sys.argv[1], sys.argv[2]
    cx, cy, cw, ch = map(float, sys.argv[3:7])
    modversion = int(sys.argv[7]) if len(sys.argv) > 7 else 2
    op = sys.argv[8] if len(sys.argv) > 8 else "crop"
    ratio_n = int(sys.argv[9]) if len(sys.argv) > 9 else 0
    ratio_d = int(sys.argv[10]) if len(sys.argv) > 10 else 0
    with open(src) as f:
        t = f.read()
    out = inject_crop(t, cx, cy, cw, ch, modversion, op, ratio_n, ratio_d)
    with open(dst, "w") as f:
        f.write(out)
    print(f"wrote {dst}: op={op} mv={modversion} crop=({cx},{cy},{cw},{ch}) ratio={ratio_n}/{ratio_d}")
