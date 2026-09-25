"""Build the Ursus C-360 scene and export the i3d (run via blender-mcp or headless).

    blender -b -P blender/build_all.py -- [--blend /tmp/work/ursusC360.blend]
    python3 tools/mcp/blender_mcp_run.py --script blender/build_all.py
"""
import argparse
import importlib
import os
import sys
import time

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import ursus_lib  # noqa: E402
import build_ursus_c360  # noqa: E402
import export_i3d  # noqa: E402

for _mod in (ursus_lib, build_ursus_c360, export_i3d):
    importlib.reload(_mod)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", default=os.path.join(REPO, "FS25_UrsusC360"))
    ap.add_argument("--blend", default=os.path.join(REPO, "build", "ursusC360.blend"))
    args, _ = ap.parse_known_args(argv)
    t0 = time.time()
    root = build_ursus_c360.build()
    t1 = time.time()
    os.makedirs(args.mod, exist_ok=True)
    # inline-geometry i3d goes to build/; tools/build_mod.py binarizes it into the mod folder
    stats = export_i3d.export(root, os.path.join(REPO, "build", "ursusC360.i3d"),
                              os.path.join(REPO, "build", "i3dMappings.xml"), mod_dir=args.mod)
    t2 = time.time()
    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=args.blend, copy=True)
    print(f"objects={len(bpy.data.objects)} shapes={stats['shapes']} tris={stats['tris']} "
          f"verts={stats['verts']} build={t1 - t0:.1f}s export={t2 - t1:.1f}s")


main()
