#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Move inline i3d geometry into a GIANTS v10 ``.i3d.shapes`` file (FS25).

Usage:  python3 tools/i3d/binarize_i3d.py in.i3d [--out out.i3d] [--seed N]

Reads the inline ``<Shapes>`` children (i3d 1.6 schema, as written by GIANTS
Editor or StjerneIdioten's Blender exporter), writes ``<out name>.shapes``
(e.g. ``out.i3d.shapes``) next to the output i3d, and replaces the ``<Shapes>``
element with exactly what GIANTS Editor 10 saves::

    <Shapes externalShapesFile="out.i3d.shapes">
    </Shapes>

Every byte outside that element (XML declaration and encoding, comments,
formatting, Scene, ...) is kept. Without --out the input is rewritten in place.

XML -> binary mapping (layout: see i3d_shapes_writer.py)
  IndexedTriangleSet @name, @shapeId      -> entity name / id (entity type 1)
    @bvCenter + @bvRadius                  -> bounding sphere (else exact minimum sphere)
    @meshUsage                             -> options bits 16-31 (256 = CPU mesh)
    @vertexCompressionRange                -> float, "auto"/absent = 0.0
    @isOptimized                           -> ignored (vertex/index order is kept)
    Vertices @normal @uv0-3 @color @tangent @blendweights @singleblendweights
             @generic                      -> option flags; v @p @n @t0-3 @c @bw @bi @g
    tangent="true"                         -> tangents computed from uv0 (not in XML)
    Subset @firstVertex @numVertices @firstIndex @numIndices, @uvDensity0-3
           (computed when absent), @materialSlotName
  NurbsCurve @type cubic|linear, @form, cv @c -> spline entity type 2|6
Unsupported (error, nothing written): <Attachments>, isAttachment="true",
Precipitation, NavigationMesh, PrefracturedMesh.
"""

import argparse
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib
from xml.sax.saxutils import escape

try:
    from . import i3d_shapes_writer as W
except ImportError:
    import i3d_shapes_writer as W


class I3DError(Exception):
    pass


def _bool(value):
    return value is not None and value.strip().lower() in ("true", "1")


def _floats(text, n, what, pad=None):
    if text is None:
        raise I3DError("%s: attribute missing" % what)
    parts = text.split()
    if pad is not None and len(parts) < n:
        parts += [pad] * (n - len(parts))
    if len(parts) != n:
        raise I3DError("%s: expected %d values, got %r" % (what, n, text))
    try:
        return tuple(float(v) for v in parts)
    except ValueError:
        raise I3DError("%s: not a number list: %r" % (what, text)) from None


def _int(el, attr, what, default=None):
    value = el.get(attr)
    if value is None:
        if default is None:
            raise I3DError("%s: missing %s" % (what, attr))
        return default
    try:
        return int(value, 0) if value.strip().lower().startswith("0x") else int(value)
    except ValueError:
        raise I3DError("%s: bad integer %s=%r" % (what, attr, value)) from None


def parse_indexed_triangle_set(el):
    name = el.get("name", "")
    sid = _int(el, "shapeId", "IndexedTriangleSet %r" % name)
    what = "IndexedTriangleSet %r (shapeId %d)" % (name, sid)
    if _bool(el.get("isAttachment")) or el.find("Attachments") is not None:
        raise I3DError("%s: attachments / isAttachment are not supported" % what)
    verts = el.find("Vertices")
    tris_el = el.find("Triangles")
    if verts is None or tris_el is None:
        raise I3DError("%s: needs <Vertices> and <Triangles>" % what)

    flag = {k: _bool(verts.get(k)) for k in ("normal", "uv0", "uv1", "uv2", "uv3", "color", "tangent",
                                                "blendweights", "singleblendweights", "generic")}
    if flag["blendweights"] and flag["singleblendweights"]:
        raise I3DError("%s: blendweights and singleblendweights are exclusive" % what)
    vs = verts.findall("v")
    if verts.get("count") is not None and _int(verts, "count", what) != len(vs):
        raise I3DError("%s: Vertices count=%s but %d <v>" % (what, verts.get("count"), len(vs)))

    pos = []
    nrm = [] if flag["normal"] else None
    uvs = [[] if flag["uv%d" % k] else None for k in range(4)]
    col = [] if flag["color"] else None
    bw = [] if flag["blendweights"] else None
    bi = [] if (flag["blendweights"] or flag["singleblendweights"]) else None
    gen = [] if flag["generic"] else None
    for i, v in enumerate(vs):
        vw = "%s vertex %d" % (what, i)
        pos.append(_floats(v.get("p"), 3, vw + " p"))
        if nrm is not None:
            nrm.append(_floats(v.get("n"), 3, vw + " n"))
        for k in range(4):
            if uvs[k] is not None:
                uvs[k].append(_floats(v.get("t%d" % k), 2, vw + " t%d" % k))
        if col is not None:
            c = _floats(v.get("c"), 4, vw + " c", pad="1")
            col.append(c)
        if bw is not None:
            bw.append(_floats(v.get("bw"), 4, vw + " bw", pad="0"))
            bi.append(tuple(int(x) for x in _floats(v.get("bi"), 4, vw + " bi", pad="0")))
        elif bi is not None:
            bi.append(int(_floats(v.get("bi"), 1, vw + " bi")[0]))
        if gen is not None:
            gen.append(_floats(v.get("g"), 1, vw + " g")[0])

    ts = tris_el.findall("t")
    if tris_el.get("count") is not None and _int(tris_el, "count", what) != len(ts):
        raise I3DError("%s: Triangles count=%s but %d <t>" % (what, tris_el.get("count"), len(ts)))
    tris = [tuple(int(x) for x in _floats(t.get("vi"), 3, "%s triangle %d" % (what, i)))
            for i, t in enumerate(ts)]

    subsets = []
    subs_el = el.find("Subsets")
    for i, s in enumerate(subs_el.findall("Subset") if subs_el is not None else []):
        sw = "%s subset %d" % (what, i)
        subsets.append(dict(
            first_vertex=_int(s, "firstVertex", sw), num_vertices=_int(s, "numVertices", sw),
            first_index=_int(s, "firstIndex", sw), num_indices=_int(s, "numIndices", sw),
            uv_densities={k: _floats(s.get("uvDensity%d" % k), 1, "%s uvDensity%d" % (sw, k))[0] for k in range(4)
                          if s.get("uvDensity%d" % k) is not None},
            material_slot_name=s.get("materialSlotName", "")))

    bv = None
    if el.get("bvCenter") is not None and el.get("bvRadius") is not None:
        bv = _floats(el.get("bvCenter"), 3, what + " bvCenter") + _floats(el.get("bvRadius"), 1, what + " bvRadius")
    vcr = (el.get("vertexCompressionRange") or "auto").strip().lower()
    try:
        vertex_compression = 0.0 if vcr == "auto" else float(vcr)
    except ValueError:
        raise I3DError("%s: bad vertexCompressionRange %r" % (what, vcr)) from None

    shape = dict(name=name, shape_id=sid, positions=pos, normals=nrm, colors=col, triangles=tris,
                 subsets=subsets, tangents=True if flag["tangent"] else None, blend_weights=bw,
                 blend_indices=bi, generic=gen, bounding_volume=bv,
                 mesh_usage=_int(el, "meshUsage", what, default=0), vertex_compression=vertex_compression)
    for k in range(4):
        shape["uv%d" % k] = uvs[k]
    return shape


def parse_nurbs_curve(el):
    name = el.get("name", "")
    sid = _int(el, "shapeId", "NurbsCurve %r" % name)
    kind = (el.get("type") or "cubic").strip().lower()
    if kind not in ("cubic", "linear"):
        raise I3DError("NurbsCurve %r: unknown type %r" % (name, kind))
    pts = [_floats(cv.get("c"), 3, "NurbsCurve %r cv %d" % (name, i)) for i, cv in enumerate(el.findall("cv"))]
    return dict(kind="spline", name=name, shape_id=sid, points=pts, cubic=kind == "cubic",
                closed=(el.get("form") or "open").strip().lower() == "closed")


def parse_inline_shapes(shapes_el):
    items = []
    for child in shapes_el:
        if not isinstance(child.tag, str):
            continue  # comments / processing instructions
        if child.tag == "IndexedTriangleSet":
            items.append(parse_indexed_triangle_set(child))
        elif child.tag == "NurbsCurve":
            items.append(parse_nurbs_curve(child))
        else:
            raise I3DError("<%s> inside <Shapes> is not supported" % child.tag)
    ids = [it["shape_id"] for it in items]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise I3DError("duplicate shapeId(s): %s" % dup)
    return items


# --- text-level rewrite of the <Shapes> element ------------------------------

_COMMENT_RE = re.compile(rb"<!--.*?-->", re.S)
_START_RE = re.compile(rb"<Shapes(?=[\s/>])[^>]*>")
_END_RE = re.compile(rb"</Shapes\s*>")
_ENCODING_RE = re.compile(rb"""^\s*<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")


def _find_outside_comments(regex, raw, start=0):
    spans = [m.span() for m in _COMMENT_RE.finditer(raw)]
    for m in regex.finditer(raw, start):
        if not any(a <= m.start() < b for a, b in spans):
            return m
    return None


def shapes_element_span(raw):
    """(start, end) byte offsets of the <Shapes ...>...</Shapes> element."""
    m = _find_outside_comments(_START_RE, raw)
    if m is None:
        raise I3DError("no <Shapes> element found")
    if m.group(0).endswith(b"/>"):
        return m.start(), m.end()
    e = _find_outside_comments(_END_RE, raw, m.end())
    if e is None:
        raise I3DError("unterminated <Shapes> element")
    return m.start(), e.end()


def rewrite_shapes_element(raw, shapes_name):
    start, end = shapes_element_span(raw)
    line_start = raw.rfind(b"\n", 0, start) + 1
    indent = raw[line_start:start]
    if indent.strip():
        indent = b""
    newline = b"\r\n" if b"\r\n" in raw[:start] else b"\n"
    m = _ENCODING_RE.match(raw)
    encoding = m.group(1).decode("ascii") if m else "utf-8"
    attr = escape(shapes_name, {'"': "&quot;"}).encode(encoding)
    element = b'<Shapes externalShapesFile="' + attr + b'">' + newline + indent + b"</Shapes>"
    return raw[:start] + element + raw[end:]


def _atomic_write(path, data):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".binarize-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def binarize(in_path, out_path=None, seed=None):
    """Binarize ``in_path``; returns dict(out_path, shapes_path, seed, entities)."""
    out_path = out_path or in_path
    with open(in_path, "rb") as f:
        raw = f.read()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise I3DError("XML parse error: %s" % e) from None
    shapes_el = root.find("Shapes")
    if shapes_el is None:
        raise I3DError("no <Shapes> element under <i3D>")
    children = [c for c in shapes_el if isinstance(c.tag, str)]
    if shapes_el.get("externalShapesFile"):
        raise I3DError("already binary (externalShapesFile=%r)%s" % (
            shapes_el.get("externalShapesFile"), " but also has inline children" if children else ""))
    items = parse_inline_shapes(shapes_el)

    shapes_name = os.path.basename(out_path) + ".shapes"
    shapes_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), shapes_name)
    if seed is None:
        seed = zlib.crc32(shapes_name.encode("utf-8")) & 0xFF  # deterministic; GE picks any byte
    new_xml = rewrite_shapes_element(raw, shapes_name)
    check = ET.fromstring(new_xml).find("Shapes")
    if check is None or check.get("externalShapesFile") != shapes_name or len(check):
        raise I3DError("internal error: rewritten <Shapes> element is not as expected")
    try:
        data, infos = W.build_shapes_bytes(items, seed)
    except ValueError as e:
        raise I3DError(str(e)) from None
    _atomic_write(shapes_path, data)
    _atomic_write(out_path, new_xml)
    return dict(out_path=out_path, shapes_path=shapes_path, seed=seed, size=len(data), entities=infos)


def _describe(info):
    if info["kind"] == "spline":
        return "spline id %d %r: %d points, %s, entity type %d" % (
            info["shape_id"], info["name"], info["points"], "closed" if info["closed"] else "open",
            info["entity_type"])
    dens = [s["uv_densities"] for s in info["subsets"]]
    names = [s["material_slot_name"] for s in info["subsets"] if s["material_slot_name"]]
    bv = info["bounding_volume"]
    return ("shape id %d %r: %d verts, %d tris, %d subsets, options 0x%08x, %d-bit indices, "
            "bv (%.6g %.6g %.6g) r=%.6g, uvDensity %s%s" % (
                info["shape_id"], info["name"], info["vertices"], info["triangles"], len(info["subsets"]),
                info["options"], 8 * info["index_bytes"], bv[0], bv[1], bv[2], bv[3],
                [[round(d, 6) for d in ds] for ds in dens], ", slots %s" % names if names else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert inline i3d <Shapes> into a FS25 v10 .i3d.shapes file.")
    ap.add_argument("input", help="i3d file with inline <Shapes>")
    ap.add_argument("--out", help="output i3d (default: rewrite input in place); .shapes goes next to it")
    ap.add_argument("--seed", type=int, help="cipher seed 0-255 (default: derived from the file name)")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)
    try:
        res = binarize(args.input, args.out, args.seed)
    except (I3DError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if not args.quiet:
        print("wrote %s (v%d, seed %d, %d entities, %d bytes)" % (
            res["shapes_path"], W.SHAPES_VERSION, res["seed"], len(res["entities"]), res["size"]))
        print("wrote %s" % res["out_path"])
        for info in res["entities"]:
            print("  " + _describe(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
