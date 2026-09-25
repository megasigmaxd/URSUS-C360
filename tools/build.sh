#!/usr/bin/env bash
# Full rebuild: textures -> sounds -> Blender model + i3d -> renders -> binarize/validate/zip.
#   tools/build.sh            headless Blender
#   USE_MCP=1 tools/build.sh  model built through the blender-mcp server (tools/mcp/)
set -euo pipefail
cd "$(dirname "$0")/.."
export URSUS_TEX_PNG="${URSUS_TEX_PNG:-/tmp/work/tex_png}"
BLEND=build/ursusC360.blend
mkdir -p build
python3 tools/textures/gen_textures.py
python3 tools/sound/synth_ursus_c360.py
if [ "${USE_MCP:-0}" = 1 ]; then
  python3 tools/mcp/blender_mcp_run.py --script blender/build_all.py -- --blend "$PWD/$BLEND"
else
  blender -b --factory-startup -P blender/build_all.py -- --blend "$PWD/$BLEND"
fi
blender -b "$BLEND" -P blender/render_previews.py -- --store build/store.png
blender -b "$BLEND" -P blender/render_previews.py -- --out build/renders
python3 tools/build_mod.py
