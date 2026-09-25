# tools/i3d – licence and attribution notice

| File | Licence |
|---|---|
| `i3d_shapes_writer.py`, `binarize_i3d.py`, `verify_shapes.py`, `test_data/*` | GPL-3.0-or-later |
| `_i3d_cipher_keys.py` | MIT (Copyright (c) 2017 Daniel Hultgren), values copied verbatim |

The GPL-3.0-or-later files re-implement format knowledge and algorithms from
the sources below. Only the cipher key table is copied verbatim; no other code
is copied.

## Sources

1. **I3DShapesTool** – Daniel Hultgren (Donkie), MIT,
   <https://github.com/Donkie/I3DShapesTool> (commit `1a2ecf3`).
   Cipher key table (`keyConst` in `Container/Cipher/I3DCipher.cs`, copied into
   `_i3d_cipher_keys.py`); cipher algorithm, `CipherStream` write semantics,
   entity framing (`ShapesFileWriter.cs`, `Entity.cs`, `FileHeader.cs`) and the
   `I3DPart` / `I3DShape` / `I3DShapeSubset` / `I3DShapeAttachment` / `Spline`
   layouts, ported to Python in `i3d_shapes_writer.py`.
2. **i3d-to-objx** (fork of I3DShapesTool, via VidhosticeSDK/I3DShapesTool-OBJx),
   MIT, <https://github.com/nadine-brinkmann/i3d-to-objx> (commit `c480816`).
   Version-10 notes: the `vtxCompression` float before the subsets, the
   per-subset material names, and the extra spline attribute word.
3. **blender-i3d-importer** – nadine-brinkmann, GPL-3.0-or-later,
   <https://github.com/nadine-brinkmann/blender-i3d-importer> (commit `5dab8bb`).
   `i3d_shapes_models.py`: the 2-byte padding after each material slot name and
   the `0x01000000` CPU-mesh option bit. `verify_shapes.py` loads its
   `i3d_shapes_reader.py` / `i3d_shapes_models.py` at run time through
   `--reader-path` as the independent decoder. They are not vendored here.
4. **GIANTS I3D exporter**, `util/i3d_densityUtil.py` – GIANTS Software,
   GPL-2.0-or-later header, as redistributed in
   <https://github.com/dtapgaming/GiantsExporterRework-Blender> (GPL-3.0,
   commit `2898f3f`). The uvDensity rule (per-triangle mean edge ratio clamped
   to 1, ignore < 1/64, `max(min, max(mean - std, 0.75 * mean))`) is
   re-implemented in `compute_uv_density`.
5. **I3D-Blender-Addon (i3dio)** – StjerneIdioten, GPL-3.0,
   <https://github.com/StjerneIdioten/I3D-Blender-Addon> (commit `a021515`).
   Consulted only for the inline XML attribute set: `meshUsage` 256 = CPU mesh,
   `vertexCompressionRange` values, `materialSlotName`, and `tangent="true"`
   without per-vertex values.
6. **GIANTS i3d 1.6 schema** – <http://i3d.giants.ch/schema/i3d-1.6.xsd>,
   consulted for element and attribute names.

## MIT License (I3DShapesTool, i3d-to-objx)

```
MIT License

Copyright (c) 2017 Daniel Hultgren

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The GPL-3.0 text: <https://www.gnu.org/licenses/gpl-3.0.txt>.
