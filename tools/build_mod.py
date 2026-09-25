#!/usr/bin/env python3
"""Post-process the Blender export into a ready FS25 mod and zip it.

Steps: binarize the inline i3d (tools/i3d/binarize_i3d.py) into FS25_UrsusC360/,
inject build/i3dMappings.xml into ursusC360.xml, convert shop/icon renders to DDS,
validate, and write dist/FS25_UrsusC360.zip (modDesc.xml at the zip root).
Requires numpy + Pillow.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(REPO, "FS25_UrsusC360")
BUILD = os.path.join(REPO, "build")
MARK_START, MARK_END = "<!-- I3D_MAPPINGS -->", "<!-- /I3D_MAPPINGS -->"
# files that belong in the distributed zip (everything else in the mod dir is ignored)
ZIP_EXT = (".xml", ".i3d", ".shapes", ".dds", ".ogg", ".txt")


def run(cmd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=REPO)


def inject_mappings(xml_path, mappings_path):
    with open(xml_path, encoding="utf-8") as fh:
        xml = fh.read()
    with open(mappings_path, encoding="utf-8") as fh:
        block = fh.read().rstrip("\n")
    if MARK_START not in xml:
        sys.exit(f"{xml_path}: marker {MARK_START} missing")
    if MARK_END not in xml:
        xml = xml.replace(MARK_START, f"{MARK_START}\n{MARK_END}", 1)
    pattern = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)
    indent = re.search(r"([ \t]*)" + re.escape(MARK_START), xml).group(1)
    xml = pattern.sub(lambda _: f"{MARK_START}\n{block}\n{indent}{MARK_END}", xml, count=1)
    with open(xml_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(xml)


def to_dds(png, dds, fmt, size=None, mips=False):
    src = png
    if size:
        from PIL import Image
        src = os.path.join(BUILD, f"_{size}_" + os.path.basename(png))
        Image.open(png).convert("RGBA").resize((size, size), Image.LANCZOS).save(src)
    cmd = [sys.executable, "tools/textures/png2dds.py", src, dds, "--format", fmt]
    if not mips:
        cmd.append("--no-mips")
    run(cmd)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inline", default=os.path.join(BUILD, "ursusC360.i3d"))
    ap.add_argument("--store-png", default=os.path.join(BUILD, "store.png"))
    ap.add_argument("--no-zip", action="store_true")
    args = ap.parse_args()

    run([sys.executable, "tools/i3d/binarize_i3d.py", args.inline, "--out", os.path.join(MOD, "ursusC360.i3d")])
    inject_mappings(os.path.join(MOD, "ursusC360.xml"), os.path.join(BUILD, "i3dMappings.xml"))
    if os.path.exists(args.store_png):
        to_dds(args.store_png, os.path.join(MOD, "store_ursusC360.dds"), "bc3", 512)
        to_dds(args.store_png, os.path.join(MOD, "icon_ursusC360.dds"), "bc3", 256)
    brand_png = os.path.join(BUILD, "brand_ursus.png")
    if os.path.exists(brand_png):
        to_dds(brand_png, os.path.join(MOD, "brand_ursus.dds"), "bc3")
    run([sys.executable, "tools/validate_mod.py"])
    if args.no_zip:
        return
    os.makedirs(os.path.join(REPO, "dist"), exist_ok=True)
    out = os.path.join(REPO, "dist", "FS25_UrsusC360.zip")
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for base, _, files in os.walk(MOD):
            for fn in sorted(files):
                if not fn.endswith(ZIP_EXT):
                    continue
                full = os.path.join(base, fn)
                zf.write(full, os.path.relpath(full, MOD))
    shutil.move(tmp, out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "modDesc.xml" in names, "modDesc.xml must be at the zip root"
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB, {len(names)} files)")


if __name__ == "__main__":
    main()
