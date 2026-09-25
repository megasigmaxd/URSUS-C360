#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Round-trip tests for binarize_i3d.py / i3d_shapes_writer.py.

Decoding uses the independent reader from blender-i3d-importer
(i3d_shapes_reader.py + i3d_shapes_models.py, a GPL-3.0 port of I3DShapesTool),
loaded from --reader-path at run time (not vendored).

  inline files (default: test_data/*.i3d): binarize into a temp dir, decode the
      .i3d.shapes and compare with the inline XML, parsed here independently of
      binarize_i3d.py: names/ids, positions, normals, uv0-3, colors, generic,
      blend data, indices, subsets, slot names, uvDensity (explicit or an
      independent recomputation), bvCenter/bvRadius or min-sphere bounds,
      tangent sanity, option bits, splines, reader consumed every byte, and the
      rewritten XML differs from the input only inside <Shapes>.
  synthetic (skip with --no-synthetic): in-memory shapes covering uint32 indices
      (>65536 vertices), uv0-3, colors, skinning, single blend weights with an
      unaligned entity size, meshUsage, splines, both seeds 0 and 255, and
      numpy/pure-Python cipher parity.
  --reencode FILE...: decode GE-saved v10 files and re-encode them with the
      writer (same seed and fields) - output must be byte-identical.

Float tolerance: |a - b| <= 1e-5 * max(1, |b|) (values are stored as float32).

Usage: python3 tools/i3d/verify_shapes.py --reader-path DIR [file.i3d ...]
"""

import argparse
import glob
import math
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import binarize_i3d  # noqa: E402
import i3d_shapes_writer as W  # noqa: E402

TOL = 1e-5


class Check:
    def __init__(self, label):
        self.label = label
        self.count = 0
        self.errors = []

    def ok(self, cond, msg):
        self.count += 1
        if not cond:
            self.errors.append(msg)

    def eq(self, what, got, want):
        self.ok(got == want, "%s: got %r, want %r" % (what, got, want))

    def close(self, what, got, want, tol=TOL):
        self.count += 1
        if len(got) != len(want):
            self.errors.append("%s: %d rows, want %d" % (what, len(got), len(want)))
            return
        for i, (g, w) in enumerate(zip(got, want)):
            g = g if isinstance(g, (tuple, list)) else (g,)
            w = w if isinstance(w, (tuple, list)) else (w,)
            if len(g) != len(w) or any(abs(a - b) > tol * max(1.0, abs(b)) for a, b in zip(g, w)):
                self.errors.append("%s[%d]: got %r, want %r" % (what, i, tuple(g), tuple(w)))
                return

    def report(self):
        status = "PASS" if not self.errors else "FAIL"
        print("[%s] %s (%d checks)" % (status, self.label, self.count))
        for e in self.errors[:25]:
            print("       " + e)
        return not self.errors


def load_reader(path):
    if not os.path.isfile(os.path.join(path, "i3d_shapes_reader.py")):
        sys.exit("error: i3d_shapes_reader.py not found in --reader-path %s" % path)
    sys.path.insert(0, os.path.abspath(path))
    import i3d_shapes_reader as R
    import i3d_shapes_models as M
    return R, M


def decode(R, M, path):
    sf = R.read_shapes_file(path)
    ents = {}
    for e in sf.entities:
        if e.entity_type.name == "SHAPE":
            obj = M.parse_shape_entity(e, sf.header.version)
        elif e.entity_type.name in ("SPLINE", "SPLINE_L"):
            obj = M.parse_spline_entity(e, sf.header.version)
        else:
            obj = None
        ents[obj.id if obj is not None else -len(ents) - 1] = (e.type, obj)
    return sf.header, ents


def v3(xs):
    return [(p.x, p.y, p.z) for p in xs]


def v4(xs):
    return [(p.x, p.y, p.z, p.w) for p in xs]


def uvs_of(xs):
    return [(u.u, u.v) for u in xs]


def f32(v):
    return array("f", [v])[0]


# --- independent inline XML reading ----------------------------------------

def _nums(text, n=None, pad=None):
    vals = [float(x) for x in text.split()]
    if n is not None and pad is not None:
        vals += [pad] * (n - len(vals))
    return tuple(vals)


def read_inline(path):
    root = ET.parse(path).getroot()
    items = []
    for el in root.find("Shapes"):
        if el.tag == "IndexedTriangleSet":
            V = el.find("Vertices")
            fl = {k: (V.get(k) or "").lower() == "true" for k in (
                "normal", "uv0", "uv1", "uv2", "uv3", "color", "tangent", "blendweights",
                "singleblendweights", "generic")}
            vs = V.findall("v")
            tris = [tuple(int(x) for x in t.get("vi").split()) for t in el.find("Triangles").findall("t")]
            subs = el.find("Subsets")
            subsets = ([dict(s.attrib) for s in subs.findall("Subset")] if subs is not None else
                       [dict(firstVertex="0", numVertices=str(len(vs)), firstIndex="0", numIndices=str(3 * len(tris)))])
            it = dict(kind="shape", name=el.get("name", ""), id=int(el.get("shapeId")), flags=fl,
                      p=[_nums(v.get("p")) for v in vs],
                      n=[_nums(v.get("n")) for v in vs] if fl["normal"] else None,
                      t=[[_nums(v.get("t%d" % k)) for v in vs] if fl["uv%d" % k] else None for k in range(4)],
                      c=[_nums(v.get("c"), 4, 1.0) for v in vs] if fl["color"] else None,
                      bw=[_nums(v.get("bw"), 4, 0.0) for v in vs] if fl["blendweights"] else None,
                      bi=[tuple(int(x) for x in _nums(v.get("bi"), 4, 0.0)) for v in vs] if fl["blendweights"]
                      else ([(int(_nums(v.get("bi"))[0]),) for v in vs] if fl["singleblendweights"] else None),
                      g=[_nums(v.get("g")) for v in vs] if fl["generic"] else None,
                      tris=tris, subsets=subsets,
                      bv=(_nums(el.get("bvCenter")) + _nums(el.get("bvRadius"))
                          if el.get("bvCenter") and el.get("bvRadius") else None),
                      mesh_usage=int(el.get("meshUsage", "0")),
                      vcr=el.get("vertexCompressionRange", "auto"))
            items.append(it)
        elif el.tag == "NurbsCurve":
            items.append(dict(kind="spline", name=el.get("name", ""), id=int(el.get("shapeId")),
                              cubic=el.get("type", "cubic") == "cubic", closed=el.get("form") == "closed",
                              pts=[_nums(cv.get("c")) for cv in el.findall("cv")]))
    return items


def ref_uv_density(P, U, tris):
    """Independent re-statement of the GIANTS uvDensity rule."""
    vals = []
    for tri in tris:
        ratios = []
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            wl = math.dist(P[a], P[b])
            ul = math.dist(U[a], U[b])
            ratios.append(ul / wl if wl > 0 and ul > 0 else None)
        if None in ratios:
            continue
        d = min(1.0, sum(ratios) / 3.0)
        if d >= 1.0 / 64.0:
            vals.append(d)
    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
    return max(min(vals), mean - std, 0.75 * mean)


def ref_face_tangent(P, U, tri):
    (a, b, c) = tri
    e1 = [P[b][i] - P[a][i] for i in range(3)]
    e2 = [P[c][i] - P[a][i] for i in range(3)]
    du1, dv1 = U[b][0] - U[a][0], U[b][1] - U[a][1]
    du2, dv2 = U[c][0] - U[a][0], U[c][1] - U[a][1]
    det = du1 * dv2 - du2 * dv1
    if abs(det) < 1e-12:
        return None
    t = [(e1[i] * dv2 - e2[i] * dv1) / det for i in range(3)]
    bt = [(e2[i] * du1 - e1[i] * du2) / det for i in range(3)]
    ln = math.sqrt(sum(x * x for x in t))
    lb = math.sqrt(sum(x * x for x in bt))
    if ln == 0 or lb == 0:
        return None
    return [x / ln for x in t] + [x / lb for x in bt]


# --- comparisons -------------------------------------------------------------

def check_shape(chk, x, etype, sh):
    L = "shape %d %r" % (x["id"], x["name"])
    fl = x["flags"]
    chk.eq(L + " entity type", etype, 1)
    chk.eq(L + " name", sh.name, x["name"])
    chk.eq(L + " unread bytes", sh.unread_bytes, 0)
    chk.close(L + " positions", v3(sh.positions), x["p"])
    if fl["normal"]:
        chk.close(L + " normals", v3(sh.normals or []), x["n"])
    else:
        chk.ok(sh.normals is None, L + ": unexpected normals")
    for k in range(4):
        if x["t"][k] is not None:
            chk.close(L + " uv%d" % k, uvs_of(sh.uv_sets[k] or []), x["t"][k])
        else:
            chk.ok(sh.uv_sets[k] is None, L + ": unexpected uv%d" % k)
    if x["c"] is not None:
        chk.close(L + " colors", v4(sh.vertex_colors or []), x["c"])
    else:
        chk.ok(sh.vertex_colors is None, L + ": unexpected colors")
    if x["g"] is not None:
        chk.close(L + " generic", sh.generic_data or [], x["g"])
    if x["bi"] is not None:
        chk.eq(L + " blend indices", [tuple(b) for b in sh.blend_indices or []], x["bi"])
        chk.eq(L + " single blend weights", sh.is_single_blend_weights, fl["singleblendweights"])
        if x["bw"] is not None:
            chk.close(L + " blend weights", sh.blend_weights or [], x["bw"])
    chk.eq(L + " triangles", [(t.p1 - 1, t.p2 - 1, t.p3 - 1) for t in sh.triangles], x["tris"])

    opts = int(sh.options)
    want = (0x1 if fl["normal"] else 0) | sum(0x2 << k for k in range(4) if fl["uv%d" % k])
    want |= (0x20 if fl["color"] else 0) | (0x80 if fl["tangent"] else 0) | (0x200 if fl["generic"] else 0)
    if fl["blendweights"] or fl["singleblendweights"]:
        want |= 0x40 | (0x100 if fl["singleblendweights"] else 0)
    chk.eq(L + " option flags", hex(opts), hex(want))
    chk.eq(L + " option high bits (meshUsage << 16)", hex(sh.options_high_bits), hex(x["mesh_usage"] << 16))
    vcr = 0.0 if x["vcr"].lower() == "auto" else f32(float(x["vcr"]))
    chk.eq(L + " vertexCompressionRange", sh.vtx_compression, vcr)
    chk.eq(L + " attachments", len(sh.attachments), 0)

    chk.eq(L + " subset count", len(sh.subsets), len(x["subsets"]))
    for si, (s, xs) in enumerate(zip(sh.subsets, x["subsets"])):
        SL = "%s subset %d" % (L, si)
        chk.eq(SL + " ranges", (s.first_vertex, s.num_vertices, s.first_index, s.num_indices),
               tuple(int(xs[k]) for k in ("firstVertex", "numVertices", "firstIndex", "numIndices")))
        chk.eq(SL + " materialSlotName", sh.material_slot_names[si], xs.get("materialSlotName", ""))
        got = [s.uv_density1, s.uv_density2, s.uv_density3, s.uv_density4]
        tris = x["tris"][int(xs["firstIndex"]) // 3:(int(xs["firstIndex"]) + int(xs["numIndices"])) // 3]
        for k in range(4):
            if x["t"][k] is None:
                chk.ok(got[k] is None, SL + ": unexpected uvDensity%d" % k)
                continue
            explicit = xs.get("uvDensity%d" % k)
            want_d = float(explicit) if explicit is not None else ref_uv_density(x["p"], x["t"][k], tris)
            chk.close(SL + " uvDensity%d%s" % (k, "" if explicit else " (computed)"), [got[k]], [want_d])

    bv = sh.bounding_volume
    if x["bv"] is not None:
        chk.close(L + " bvCenter/bvRadius", [(bv.x, bv.y, bv.z, bv.w)], [x["bv"]])
    else:
        c = (bv.x, bv.y, bv.z)
        far = max(math.dist(c, p) for p in x["p"])
        chk.ok(far <= bv.w * (1 + 1e-6) + 1e-7, L + ": vertex outside bounding sphere (%g > %g)" % (far, bv.w))
        lo = [min(p[i] for p in x["p"]) for i in range(3)]
        hi = [max(p[i] for p in x["p"]) for i in range(3)]
        mid = [(a + b) / 2 for a, b in zip(lo, hi)]
        box_r = max(math.dist(mid, p) for p in x["p"])
        chk.ok(bv.w <= box_r * (1 + 1e-6) + 1e-7, L + ": sphere larger than bbox sphere (%g > %g)" % (bv.w, box_r))

    if fl["tangent"]:
        tg = v4(sh.tangents or [])
        chk.eq(L + " tangent count", len(tg), len(x["p"]))
        bad = []
        ref = [None] * len(x["p"])
        if x["t"][0] is not None:
            for tri in x["tris"]:
                ft = ref_face_tangent(x["p"], x["t"][0], tri)
                if ft is not None:
                    for vi in tri:
                        ref[vi] = ft if ref[vi] is None else [a + b for a, b in zip(ref[vi], ft)]
        for i, t in enumerate(tg):
            ln = math.sqrt(t[0] ** 2 + t[1] ** 2 + t[2] ** 2)
            nd = abs(sum(a * b for a, b in zip(t, x["n"][i]))) if x["n"] else 0.0
            r = ref[i]
            rd = 1.0
            rw = t[3]
            if r is not None and x["n"]:
                n = x["n"][i]
                d = sum(a * b for a, b in zip(r[:3], n))
                rt = [r[j] - n[j] * d for j in range(3)]
                rl = math.sqrt(sum(v * v for v in rt))
                if rl > 1e-12:
                    rd = sum(a * b for a, b in zip(t, rt)) / rl
                    nxt = (n[1] * rt[2] - n[2] * rt[1], n[2] * rt[0] - n[0] * rt[2], n[0] * rt[1] - n[1] * rt[0])
                    rw = 1.0 if sum(a * b for a, b in zip(nxt, r[3:])) >= 0 else -1.0
            if abs(ln - 1) > 1e-4 or nd > 1e-4 or t[3] not in (-1.0, 1.0) or rd < 0.9999 or t[3] != rw:
                bad.append(i)
        chk.ok(not bad, L + ": bad tangents at vertices %s" % bad[:10])
    else:
        chk.ok(sh.tangents is None, L + ": unexpected tangents")


def check_spline(chk, x, etype, sp):
    L = "spline %d %r" % (x["id"], x["name"])
    chk.eq(L + " entity type", etype, 2 if x["cubic"] else 6)
    chk.eq(L + " name", sp.name, x["name"])
    chk.eq(L + " closed", sp.form_closed, x["closed"])
    chk.eq(L + " attribute flags", sp.attr_flags, 0)
    chk.eq(L + " unread bytes", sp.unread_bytes, 0)
    chk.close(L + " points", v3(sp.points), x["pts"])


def check_xml(chk, src, out, shapes_name):
    i = out.find(b"<Shapes externalShapesFile=")
    chk.ok(i >= 0, "rewritten XML lacks <Shapes externalShapesFile=...>")
    if i < 0:
        return
    j = out.find(b"</Shapes>", i) + len(b"</Shapes>")
    prefix, suffix = out[:i], out[j:]
    chk.ok(src.startswith(prefix) and src[i:].startswith(b"<Shapes"), "bytes before <Shapes> changed")
    chk.ok(src.endswith(suffix) and src[:len(src) - len(suffix)].endswith(b"</Shapes>"),
           "bytes after </Shapes> changed")
    indent = prefix[prefix.rfind(b"\n") + 1:]
    newline = b"\r\n" if prefix.endswith(b"\r\n" + indent) else b"\n"
    want = b'<Shapes externalShapesFile="' + shapes_name.encode() + b'">' + newline + indent + b"</Shapes>"
    chk.eq("rewritten <Shapes> element", out[i:j], want)
    el = ET.fromstring(out).find("Shapes")
    chk.ok(el is not None and len(el) == 0 and not (el.text or "").strip(), "<Shapes> must be empty")


def test_inline(R, M, path, workdir):
    chk = Check("inline %s" % os.path.basename(path))
    out_i3d = os.path.join(workdir, os.path.basename(path))
    shutil.copyfile(path, out_i3d)
    res = binarize_i3d.binarize(out_i3d)  # in place, like i3dConverter -in X -out X
    shapes_name = os.path.basename(out_i3d) + ".shapes"
    chk.eq("shapes file path", res["shapes_path"], os.path.join(os.path.abspath(workdir), shapes_name))
    header, ents = decode(R, M, res["shapes_path"])
    chk.eq("file version", header.version, 10)
    chk.eq("seed", header.seed, res["seed"])
    with open(res["shapes_path"], "rb") as f:
        head = f.read(4)
    chk.eq("header bytes 1 and 3", (head[1], head[3]), (0, 0))
    items = read_inline(path)
    chk.eq("entity ids", sorted(ents), sorted(x["id"] for x in items))
    for x in items:
        if x["id"] not in ents:
            continue
        etype, obj = ents[x["id"]]
        (check_shape if x["kind"] == "shape" else check_spline)(chk, x, etype, obj)
    with open(path, "rb") as f:
        src = f.read()
    with open(out_i3d, "rb") as f:
        out = f.read()
    check_xml(chk, src, out, shapes_name)
    try:  # a second run must refuse the now-binary file
        binarize_i3d.binarize(out_i3d)
        chk.ok(False, "binarizing an already external file should fail")
    except binarize_i3d.I3DError:
        chk.ok(True, "")
    return chk.report()


def _grid(nx, ny):
    pos, nrm, uv, tris = [], [], [[], [], [], []], []
    for y in range(ny):
        for x in range(nx):
            h = 0.05 * math.sin(x * 0.3) * math.cos(y * 0.2)
            pos.append((x * 0.01, h, y * 0.01))
            nrm.append((0.0, 1.0, 0.0))
            for k in range(4):
                uv[k].append(((x / (nx - 1)) * (k + 1), (y / (ny - 1)) * (k + 1)))
    for y in range(ny - 1):
        for x in range(nx - 1):
            a = y * nx + x
            tris += [(a, a + nx, a + 1), (a + 1, a + nx, a + nx + 1)]
    return pos, nrm, uv, tris


def test_synthetic(R, M, workdir):
    chk = Check("synthetic shapes (uint32 indices, uv0-3, colors, skinning, splines, seeds 0/255)")
    pos, nrm, uv, tris = _grid(257, 257)  # 66049 vertices -> uint32 indices
    half = 3 * (len(tris) // 2)
    big = dict(name="bigGrid", shape_id=7, positions=pos, normals=nrm, uv0=uv[0], uv1=uv[1], uv2=uv[2], uv3=uv[3],
               colors=[(p[0], p[1], p[2], 1.0) for p in pos], generic=[float(i % 5) for i in range(len(pos))],
               triangles=tris, tangents=True,
               subsets=[dict(first_vertex=0, num_vertices=len(pos), first_index=0, num_indices=half,
                             material_slot_name="abc"),
                        dict(first_vertex=0, num_vertices=len(pos), first_index=half,
                             num_indices=3 * len(tris) - half, material_slot_name="de"),
                        dict(first_vertex=0, num_vertices=0, first_index=3 * len(tris), num_indices=0,
                             material_slot_name="x")])
    skin = dict(name="skin5", shape_id=8, positions=[(i, i * 0.5, 0.0) for i in range(5)],
                triangles=[(0, 1, 2), (2, 3, 4)], blend_weights=[(0.5, 0.25, 0.25, 0.0)] * 5,
                blend_indices=[(i, i + 1, 0, 3) for i in range(5)])
    merge = dict(name="merge3", shape_id=9, positions=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], triangles=[(0, 1, 2)],
                 blend_indices=[0, 1, 2], mesh_usage=256, vertex_compression=4.0,
                 bounding_volume=(0.25, 0.25, 0.0, 2.0))
    after = dict(name="afterUnaligned", shape_id=10, positions=[(0, 0, 0), (0, 0, 1), (1, 0, 0)],
                 normals=[(0, 1, 0)] * 3, uv0=[(0, 0), (0, 1), (1, 0)], triangles=[(0, 1, 2)],
                 subsets=[dict(first_vertex=0, num_vertices=3, first_index=0, num_indices=3,
                               uv_densities={0: 0.75})])
    splines = [dict(kind="spline", name="c", shape_id=11, points=[(0, 0, 0), (1, 2, 3), (4, 5, 6)], cubic=True),
               dict(kind="spline", name="lin", shape_id=12, points=[(0, 0, 0), (1, 0, 0)], cubic=False, closed=True)]
    for seed, items in ((255, [big, skin, merge, after] + splines), (0, [skin, merge, after])):
        path = os.path.join(workdir, "synthetic_%d.i3d.shapes" % seed)
        infos = W.write_shapes_file(path, items, seed=seed)
        header, ents = decode(R, M, path)
        chk.eq("seed %d header" % seed, (header.version, header.seed), (10, seed))
        for it, info in zip(items, infos):
            etype, ob = ents[it["shape_id"]]
            L = "seed %d %s" % (seed, it["name"])
            chk.eq(L + " unread bytes", ob.unread_bytes, 0)
            if it.get("kind") == "spline":
                chk.eq(L + " type", etype, 2 if it["cubic"] else 6)
                chk.close(L + " points", v3(ob.points), it["points"], 1e-6)
                chk.eq(L + " closed", ob.form_closed, bool(it.get("closed")))
                continue
            chk.close(L + " positions", v3(ob.positions), it["positions"], 1e-6)
            chk.eq(L + " triangles", [(t.p1 - 1, t.p2 - 1, t.p3 - 1) for t in ob.triangles],
                   [tuple(t) for t in it["triangles"]])
            chk.eq(L + " options", int(ob.options) | ob.options_high_bits, info["options"])
            for k in range(4):
                if it.get("uv%d" % k) is not None:
                    chk.close(L + " uv%d" % k, uvs_of(ob.uv_sets[k]), it["uv%d" % k], 1e-6)
            if "colors" in it:
                chk.close(L + " colors", v4(ob.vertex_colors), it["colors"], 1e-6)
            if "generic" in it:
                chk.close(L + " generic", ob.generic_data, it["generic"], 1e-6)
            if "blend_indices" in it:
                want = [tuple(b) if isinstance(b, tuple) else (b,) for b in it["blend_indices"]]
                chk.eq(L + " blend indices", [tuple(b) for b in ob.blend_indices], want)
                if "blend_weights" in it:
                    chk.close(L + " blend weights", ob.blend_weights, it["blend_weights"], 1e-6)
            if it.get("bounding_volume"):
                b = ob.bounding_volume
                chk.close(L + " bounding volume", [(b.x, b.y, b.z, b.w)], [it["bounding_volume"]], 1e-6)
            if "vertex_compression" in it:
                chk.eq(L + " vertexCompression", ob.vtx_compression, it["vertex_compression"])
            chk.eq(L + " slot names", ob.material_slot_names,
                   [s.get("material_slot_name", "") for s in it.get("subsets") or [{}]])
            if it is big:
                chk.eq(L + " uint32 indices", info["index_bytes"], 4)
                for si, s in enumerate(ob.subsets):
                    t = it["triangles"][s.first_index // 3:(s.first_index + s.num_indices) // 3]
                    got = [s.uv_density1, s.uv_density2, s.uv_density3, s.uv_density4]
                    want = [ref_uv_density(it["positions"], it["uv%d" % k], t) for k in range(4)]
                    chk.close(L + " subset %d uvDensity0-3" % si, [got], [want])
            if it is after:
                chk.close(L + " explicit uvDensity", [ob.subsets[0].uv_density1], [0.75])
    if W._np is not None:
        a = W.build_shapes_bytes([skin, merge, after] + splines, seed=77, use_numpy=True)[0]
        b = W.build_shapes_bytes([skin, merge, after] + splines, seed=77, use_numpy=False)[0]
        chk.ok(a == b, "numpy and pure-Python cipher outputs differ")
    return chk.report()


def test_reencode(R, M, path):
    """Decode a GE-saved file and rebuild it with the writer; must be byte-identical."""
    chk = Check("re-encode %s" % os.path.basename(path))
    with open(path, "rb") as f:
        raw = f.read()
    try:
        sf = R.parse_shapes_bytes(raw)
    except Exception as e:  # not a decodable .i3d.shapes file
        chk.ok(False, "reader failed: %s" % e)
        return chk.report()
    if sf.header.version != 10:
        print("[SKIP] %s is version %d (writer emits v10 only)" % (path, sf.header.version))
        return True
    items = []
    for e in sf.entities:
        if e.entity_type.name in ("SPLINE", "SPLINE_L"):
            sp = M.parse_spline_entity(e, 10)
            items.append(dict(kind="spline", name=sp.name, shape_id=sp.id, points=v3(sp.points),
                              closed=sp.form_closed, cubic=e.type == 2, attribute_flags=sp.attr_flags))
            continue
        sh = M.parse_shape_entity(e, 10)
        b = sh.bounding_volume
        d = dict(name=sh.name, shape_id=sh.id, entity_type=e.type, positions=v3(sh.positions),
                 normals=v3(sh.normals) if sh.normals is not None else None,
                 triangles=[(t.p1 - 1, t.p2 - 1, t.p3 - 1) for t in sh.triangles],
                 bounding_volume=(b.x, b.y, b.z, b.w), extra_options=sh.options_high_bits,
                 vertex_compression=sh.vtx_compression,
                 attachments=[dict(flags=a.flags, floats=a.floats, data=a.data) for a in sh.attachments],
                 tangents=v4(sh.tangents) if sh.tangents is not None else None,
                 colors=v4(sh.vertex_colors) if sh.vertex_colors is not None else None,
                 generic=sh.generic_data,
                 subsets=[dict(first_vertex=s.first_vertex, num_vertices=s.num_vertices,
                               first_index=s.first_index, num_indices=s.num_indices, material_slot_name=n,
                               uv_densities=[s.uv_density1, s.uv_density2, s.uv_density3, s.uv_density4])
                          for s, n in zip(sh.subsets, sh.material_slot_names)])
        for k in range(4):
            d["uv%d" % k] = uvs_of(sh.uv_sets[k]) if sh.uv_sets[k] is not None else None
        if sh.blend_indices is not None:
            d["blend_indices"] = [tuple(x) for x in sh.blend_indices]
            d["blend_weights"] = sh.blend_weights
        chk.eq("%r unread bytes" % sh.name, sh.unread_bytes, 0)
        items.append(d)
    out = W.build_shapes_bytes(items, seed=sf.header.seed)[0]
    first = next((i for i in range(min(len(out), len(raw))) if out[i] != raw[i]), None)
    chk.ok(out == raw, "differs from original (len %d vs %d, first diff at %s)" % (len(out), len(raw), first))
    chk.label += " (%d entities, %d bytes)" % (len(items), len(raw))
    return chk.report()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reader-path", required=True, help="directory containing i3d_shapes_reader.py")
    ap.add_argument("inputs", nargs="*", help="inline i3d files (default: test_data/*.i3d)")
    ap.add_argument("--no-synthetic", action="store_true", help="skip the in-memory synthetic test")
    ap.add_argument("--reencode", nargs="+", default=[], metavar="FILE", help="GE-saved .i3d.shapes files")
    ap.add_argument("--keep", metavar="DIR", help="write outputs to DIR instead of a temp dir")
    ap.add_argument("--no-numpy", action="store_true", help="force the pure-Python code paths")
    args = ap.parse_args(argv)
    R, M = load_reader(args.reader_path)
    if args.no_numpy:
        W._np = None
        R.USE_NUMPY = False
    inputs = args.inputs or sorted(glob.glob(os.path.join(HERE, "test_data", "*.i3d")))
    workdir = args.keep or tempfile.mkdtemp(prefix="verify_shapes_")
    os.makedirs(workdir, exist_ok=True)
    print("reader: %s | numpy: %s | work dir: %s" % (os.path.abspath(args.reader_path),
                                                     "yes" if W._np is not None else "no", workdir))
    results = []
    try:
        for path in inputs:
            sub = os.path.join(workdir, os.path.splitext(os.path.basename(path))[0])
            os.makedirs(sub, exist_ok=True)
            results.append(test_inline(R, M, path, sub))
        if not args.no_synthetic:
            results.append(test_synthetic(R, M, workdir))
        for pattern in args.reencode:
            for path in sorted(glob.glob(pattern)) or [pattern]:
                results.append(test_reencode(R, M, path))
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
    failed = results.count(False)
    print("%d/%d test groups passed" % (len(results) - failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
