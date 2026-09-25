# SPDX-License-Identifier: GPL-3.0-or-later
"""Writer for GIANTS ``.i3d.shapes`` files, version 10 (FS25 / GIANTS Editor 10).

Pure Python (stdlib only); numpy is used when importable, only to speed up the
cipher. Entry point: ``write_shapes_file(path, shapes, seed=0)``.

Sources and attribution (full notices in LICENSE-NOTICE.md)
  * Container framing, stream cipher, I3DPart/I3DShape/Spline base layout:
    I3DShapesTool, Daniel Hultgren (Donkie), MIT - Container/ShapesFileWriter.cs,
    Cipher/CipherStream.cs, Cipher/I3DCipher.cs, Model/I3DShape.cs, I3DPart.cs,
    I3DShapeSubset.cs, I3DShapeAttachment.cs, Spline.cs. The key table lives in
    _i3d_cipher_keys.py (copied verbatim, MIT).
  * Version-10 additions (vertexCompression slot, per-subset material slot
    names, spline attribute word): i3d-to-objx NOTES.md / Spline.cs (MIT fork of
    I3DShapesTool) and blender-i3d-importer i3d_shapes_models.py (GPL-3.0),
    which established the 2-byte padding after each slot name.
  * uvDensity heuristic: GIANTS I3D exporter util/i3d_densityUtil.py ("translated
    from the c++"), GPL-2.0-or-later, as redistributed in
    dtapgaming/GiantsExporterRework-Blender (GPL-3.0). Re-implemented here.
  * Bounding sphere = exact minimum enclosing sphere, tangents = per-face unit
    tangent sum + Gram-Schmidt: matched empirically against GE 10.0.x files.
  * Verification: re-encoding 12 GE 10.0.3-10.0.11 files (61 shapes, incl.
    skinning, colors, generic, attachments) from their decoded fields
    reproduces them byte for byte (verify_shapes.py --reencode).

Byte layout written (little-endian; "pad" = zero bytes, entity-relative)

  file   u8 version=10, u8 0, u8 seed, u8 0                (plain header)
         then the encrypted stream. The keystream block counter starts at 0
         and every write call advances it by ceil(len/64) blocks; the calls are
         int32 entityCount, then per entity: int32 type, int32 size,
         byte[size] data (one call each).

  shape entity (type 1), offsets relative to the start of ``data``:
         int32 nameLen; byte[nameLen] name; pad to 4; uint32 shapeId
         float32[4] bounding sphere (center xyz, radius)
         uint32 cornerCount (= 3 * triangles); uint32 numSubsets; uint32 vertexCount
         uint32 options: 0x1 normals, 0x2/0x4/0x8/0x10 uv0..uv3, 0x20 color,
                0x40 skinning, 0x80 tangents, 0x100 single blend weights,
                0x200 generic; bits 16-31 = meshUsage (256 -> 0x01000000 CPU mesh)
         float32 vertexCompressionRange (0.0 = "auto")
         numSubsets x { uint32 firstVertex, numVertices, firstIndex, numIndices;
                        float32 uvDensity for each present uv set (uv0..uv3) }
         numSubsets x { uint16 len; byte[len] materialSlotName; pad to 2 }
         pad to 4
         cornerCount x index (uint16 if vertexCount <= 65536, else uint32;
                0-based, absolute - not relative to firstVertex); pad to 4
         vertexCount x float32[3] position
         [vertexCount x float32[3] normal]
         [vertexCount x float32[4] tangent (xyz, w = bitangent sign)]
         [vertexCount x float32[2] uv, per set uv0..uv3, (u, v) exactly as in XML]
         [vertexCount x float32[4] color rgba]
         [skinning: vertexCount x float32[4] weights unless single blend weights;
                    then vertexCount x uint8[4] (or uint8[1] if single) indices]
         [vertexCount x float32 generic]
         uint32 attachmentCount; attachmentCount x { uint32 flags;
                [float32[3] if flags & 4]; int32 n; byte[n] }

  spline entity (type 2 = cubic, 6 = linear):
         int32 nameLen; name; pad to 4; uint32 shapeId; uint32 closed (0/1);
         int32 pointCount; pointCount x float32[3]; uint32 attributeFlags (0)
"""

import math
import random
import struct
import sys
from array import array

try:
    import numpy as _np
except Exception:  # optional
    _np = None

try:
    from ._i3d_cipher_keys import KEY_TABLE
except ImportError:
    from _i3d_cipher_keys import KEY_TABLE

SHAPES_VERSION = 10

ENTITY_SHAPE = 1
ENTITY_SPLINE_CUBIC = 2
ENTITY_SPLINE_LINEAR = 6

OPT_NORMALS = 0x001
OPT_UV = (0x002, 0x004, 0x008, 0x010)
OPT_COLOR = 0x020
OPT_SKINNING = 0x040
OPT_TANGENTS = 0x080
OPT_SINGLE_BLEND_WEIGHTS = 0x100
OPT_GENERIC = 0x200
OPT_KNOWN = 0x3FF
MESH_USAGE_SHIFT = 16

MAX_UINT16_INDEX_VERTICES = 0x10000
# GIANTS ignores triangles below 64 px/m at a 4096 px texture (1 / 2**6).
UV_DENSITY_MIN = 1.0 / 64.0
# Faces with |uv determinant| below this add no tangent (GE writes (1,0,0,1)
# for float-noise uvs; any value in 1e-14..1e-8 fits the GE samples).
TANGENT_MIN_UV_DET = 1e-12

_M32 = 0xFFFFFFFF
_BLOCK_BYTES = 64
_LITTLE = sys.byteorder == "little"


# ---------------------------------------------------------------------------
# Cipher (port of I3DCipher.cs / CipherStream.cs; encrypt == decrypt)
# ---------------------------------------------------------------------------

def seed_key(seed):
    if not 0 <= int(seed) <= 255:
        raise ValueError("seed must be 0..255, got %r" % (seed,))
    seed = int(seed)
    key = list(KEY_TABLE[seed * 16:seed * 16 + 16])
    key[8] = key[9] = 0
    return key


def _keystream_py(key, first_block, nblocks):
    out = bytearray()
    pack = struct.Struct("<16I").pack
    M = _M32
    k0, k1, k2, k3, k4, k5, k6, k7, _, _, k10, k11, k12, k13, k14, k15 = key
    for blk in range(first_block, first_block + nblocks):
        k8 = blk & M
        k9 = (blk >> 32) & M
        x0, x1, x2, x3, x4, x5, x6, x7 = k0, k1, k2, k3, k4, k5, k6, k7
        x8, x9, x10, x11, x12, x13, x14, x15 = k8, k9, k10, k11, k12, k13, k14, k15
        for _ in range(10):
            # Shuffle1(0x0, 0xC, 0x4, 0x8); "ror 14" == rol 18
            t = (x12 + x0) & M; x4 ^= ((t << 7) & M) | (t >> 25)
            t = (x4 + x0) & M; x8 ^= ((t << 9) & M) | (t >> 23)
            t = (x4 + x8) & M; x12 ^= ((t << 13) & M) | (t >> 19)
            t = (x12 + x8) & M; x0 ^= ((t << 18) & M) | (t >> 14)
            # Shuffle1(0x5, 0x1, 0x9, 0xD)
            t = (x1 + x5) & M; x9 ^= ((t << 7) & M) | (t >> 25)
            t = (x9 + x5) & M; x13 ^= ((t << 9) & M) | (t >> 23)
            t = (x9 + x13) & M; x1 ^= ((t << 13) & M) | (t >> 19)
            t = (x1 + x13) & M; x5 ^= ((t << 18) & M) | (t >> 14)
            # Shuffle1(0xA, 0x6, 0xE, 0x2)
            t = (x6 + x10) & M; x14 ^= ((t << 7) & M) | (t >> 25)
            t = (x14 + x10) & M; x2 ^= ((t << 9) & M) | (t >> 23)
            t = (x14 + x2) & M; x6 ^= ((t << 13) & M) | (t >> 19)
            t = (x6 + x2) & M; x10 ^= ((t << 18) & M) | (t >> 14)
            # Shuffle1(0xF, 0xB, 0x3, 0x7)
            t = (x11 + x15) & M; x3 ^= ((t << 7) & M) | (t >> 25)
            t = (x3 + x15) & M; x7 ^= ((t << 9) & M) | (t >> 23)
            t = (x3 + x7) & M; x11 ^= ((t << 13) & M) | (t >> 19)
            t = (x11 + x7) & M; x15 ^= ((t << 18) & M) | (t >> 14)
            # Shuffle2(0x3, 0x0, 0x1, 0x2)
            t = (x0 + x3) & M; x1 ^= ((t << 7) & M) | (t >> 25)
            t = (x0 + x1) & M; x2 ^= ((t << 9) & M) | (t >> 23)
            t = (x1 + x2) & M; x3 ^= ((t << 13) & M) | (t >> 19)
            t = (x2 + x3) & M; x0 ^= ((t << 18) & M) | (t >> 14)
            # Shuffle2(0x4, 0x5, 0x6, 0x7)
            t = (x5 + x4) & M; x6 ^= ((t << 7) & M) | (t >> 25)
            t = (x5 + x6) & M; x7 ^= ((t << 9) & M) | (t >> 23)
            t = (x6 + x7) & M; x4 ^= ((t << 13) & M) | (t >> 19)
            t = (x7 + x4) & M; x5 ^= ((t << 18) & M) | (t >> 14)
            # Shuffle1(0xA, 0x9, 0xB, 0x8)
            t = (x9 + x10) & M; x11 ^= ((t << 7) & M) | (t >> 25)
            t = (x11 + x10) & M; x8 ^= ((t << 9) & M) | (t >> 23)
            t = (x11 + x8) & M; x9 ^= ((t << 13) & M) | (t >> 19)
            t = (x9 + x8) & M; x10 ^= ((t << 18) & M) | (t >> 14)
            # Shuffle2(0xE, 0xF, 0xC, 0xD)
            t = (x15 + x14) & M; x12 ^= ((t << 7) & M) | (t >> 25)
            t = (x15 + x12) & M; x13 ^= ((t << 9) & M) | (t >> 23)
            t = (x12 + x13) & M; x14 ^= ((t << 13) & M) | (t >> 19)
            t = (x13 + x14) & M; x15 ^= ((t << 18) & M) | (t >> 14)
        out += pack((x0 + k0) & M, (x1 + k1) & M, (x2 + k2) & M, (x3 + k3) & M,
                    (x4 + k4) & M, (x5 + k5) & M, (x6 + k6) & M, (x7 + k7) & M,
                    (x8 + k8) & M, (x9 + k9) & M, (x10 + k10) & M, (x11 + k11) & M,
                    (x12 + k12) & M, (x13 + k13) & M, (x14 + k14) & M, (x15 + k15) & M)
    return bytes(out)


_ROUND = ((1, 0x0, 0xC, 0x4, 0x8), (1, 0x5, 0x1, 0x9, 0xD), (1, 0xA, 0x6, 0xE, 0x2),
          (1, 0xF, 0xB, 0x3, 0x7), (2, 0x3, 0x0, 0x1, 0x2), (2, 0x4, 0x5, 0x6, 0x7),
          (1, 0xA, 0x9, 0xB, 0x8), (2, 0xE, 0xF, 0xC, 0xD))


def _keystream_np(key, first_block, nblocks):
    np = _np
    base = np.tile(np.asarray(key, dtype=np.uint32), (nblocks, 1))
    ctr = np.arange(first_block, first_block + nblocks, dtype=np.uint64)
    base[:, 8] = (ctr & np.uint64(_M32)).astype(np.uint32)
    base[:, 9] = (ctr >> np.uint64(32)).astype(np.uint32)
    x = [base[:, i].copy() for i in range(16)]

    def rol(v, n):
        return (v << np.uint32(n)) | (v >> np.uint32(32 - n))

    for _ in range(10):
        for kind, a, b, c, d in _ROUND:
            if kind == 1:
                x[c] ^= rol(x[b] + x[a], 7)
                x[d] ^= rol(x[c] + x[a], 9)
                x[b] ^= rol(x[c] + x[d], 13)
                x[a] ^= rol(x[b] + x[d], 18)
            else:
                x[c] ^= rol(x[b] + x[a], 7)
                x[d] ^= rol(x[b] + x[c], 9)
                x[a] ^= rol(x[c] + x[d], 13)
                x[b] ^= rol(x[d] + x[a], 18)
    out = np.empty((nblocks, 16), dtype="<u4")
    for i in range(16):
        out[:, i] = x[i] + base[:, i]
    return out.tobytes()


def keystream(key, first_block, nblocks, use_numpy=True):
    if use_numpy and _np is not None:
        return _keystream_np(key, first_block, nblocks)
    return _keystream_py(key, first_block, nblocks)


def _xor(data, ks):
    n = len(data)
    if _np is not None:
        a = _np.frombuffer(data, dtype=_np.uint8)
        b = _np.frombuffer(ks, dtype=_np.uint8, count=n)
        return (a ^ b).tobytes()
    return (int.from_bytes(data, "little") ^ int.from_bytes(ks[:n], "little")).to_bytes(n, "little")


class CipherStream:
    """Encrypting sink with CipherStream.Write semantics (one segment per call)."""

    def __init__(self, seed, use_numpy=True):
        self._key = seed_key(seed)
        self._block = 0
        self._parts = []
        self._use_numpy = use_numpy

    def write(self, data):
        data = bytes(data)
        if not data:
            return
        nblocks = (len(data) + _BLOCK_BYTES - 1) // _BLOCK_BYTES
        ks = keystream(self._key, self._block, nblocks, self._use_numpy)
        self._parts.append(_xor(data, ks))
        self._block += nblocks

    def getvalue(self):
        return b"".join(self._parts)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

class _Buf:
    def __init__(self):
        self.b = bytearray()

    def raw(self, data):
        self.b += data

    def u16(self, v):
        self.b += struct.pack("<H", v)

    def u32(self, v):
        self.b += struct.pack("<I", v)

    def i32(self, v):
        self.b += struct.pack("<i", v)

    def f32(self, *vals):
        self.b += struct.pack("<%df" % len(vals), *vals)

    def align(self, n):
        self.b += b"\0" * (-len(self.b) % n)


def _encode_name(s):
    if isinstance(s, (bytes, bytearray)):
        return bytes(s)
    try:
        return str(s).encode("latin-1")  # i3d files are iso-8859-1
    except UnicodeEncodeError:
        return str(s).encode("utf-8")


def _f32(v):
    return struct.unpack("<f", struct.pack("<f", v))[0]


def _f32_up(v):
    """Smallest float32 >= v (v >= 0)."""
    f = _f32(v)
    if f < v:
        bits = struct.unpack("<I", struct.pack("<f", f))[0]
        f = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
    return f


def _tolist(data):
    return data.tolist() if hasattr(data, "tolist") else data


def _f32_rows(data, ncomp, label, count):
    """Validate (count, ncomp) numeric data; return float32-rounded tuples."""
    data = _tolist(data)
    flat = []
    n = 0
    for row in data:
        if ncomp == 1 and not isinstance(row, (list, tuple)):
            row = (row,)
        if len(row) != ncomp:
            raise ValueError("%s: row %d has %d components, expected %d" % (label, n, len(row), ncomp))
        flat.extend(float(v) for v in row)
        n += 1
    if count is not None and n != count:
        raise ValueError("%s: %d rows, expected %d" % (label, n, count))
    vals = array("f", flat).tolist()
    return [tuple(vals[i:i + ncomp]) for i in range(0, len(vals), ncomp)]


def _pack_f32(rows):
    a = array("f", [v for r in rows for v in r])
    if not _LITTLE:
        a.byteswap()
    return a.tobytes()


def _pack_index(values, typecode):
    a = array(typecode, values)
    if not _LITTLE:
        a.byteswap()
    return a.tobytes()


# ---------------------------------------------------------------------------
# Derived data: bounding sphere, uvDensity, tangents
# ---------------------------------------------------------------------------

def _d2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _sphere2(a, b):
    c = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5)
    return c, _d2(a, c)


def _sphere3(a, b, c):
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
    nn = n[0] * n[0] + n[1] * n[1] + n[2] * n[2]
    lab = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
    lac = ac[0] ** 2 + ac[1] ** 2 + ac[2] ** 2
    if nn <= 1e-24 * max(lab * lac, 1e-300):  # collinear: widest pair
        return max((_sphere2(a, b), _sphere2(a, c), _sphere2(b, c)), key=lambda s: s[1])
    # o = ((n x ab) |ac|^2 + (ac x n) |ab|^2) / (2 |n|^2)
    nab = (n[1] * ab[2] - n[2] * ab[1], n[2] * ab[0] - n[0] * ab[2], n[0] * ab[1] - n[1] * ab[0])
    acn = (ac[1] * n[2] - ac[2] * n[1], ac[2] * n[0] - ac[0] * n[2], ac[0] * n[1] - ac[1] * n[0])
    o = tuple((nab[i] * lac + acn[i] * lab) / (2.0 * nn) for i in range(3))
    return (a[0] + o[0], a[1] + o[1], a[2] + o[2]), o[0] ** 2 + o[1] ** 2 + o[2] ** 2


def _sphere4(a, b, c, d):
    rows = []
    rhs = []
    for p in (b, c, d):
        v = (p[0] - a[0], p[1] - a[1], p[2] - a[2])
        rows.append(v)
        rhs.append(0.5 * (v[0] ** 2 + v[1] ** 2 + v[2] ** 2))

    def det3(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))

    det = det3(rows)
    scale = max(abs(v) for r in rows for v in r) or 1.0
    if abs(det) <= 1e-14 * scale ** 3:
        return None
    o = []
    for col in range(3):
        m = [list(r) for r in rows]
        for i in range(3):
            m[i][col] = rhs[i]
        o.append(det3(m) / det)
    return (a[0] + o[0], a[1] + o[1], a[2] + o[2]), o[0] ** 2 + o[1] ** 2 + o[2] ** 2


def _inside(p, c, r2):
    return _d2(p, c) <= r2 * (1.0 + 1e-10) + 1e-30


def compute_bounding_sphere(positions):
    """Exact minimum enclosing sphere (iterative Welzl) -> (cx, cy, cz, r) float32.

    GE 10 stores this sphere for plain shapes (verified on 55 GE-saved shapes,
    radius within 1e-7 relative).
    """
    pts = sorted(set(tuple(p) for p in positions))
    if not pts:
        return (0.0, 0.0, 0.0, 0.0)
    random.Random(0x13D).shuffle(pts)
    c, r2 = pts[0], 0.0
    for i in range(1, len(pts)):
        pi = pts[i]
        if _inside(pi, c, r2):
            continue
        c, r2 = pi, 0.0
        for j in range(i):
            pj = pts[j]
            if _inside(pj, c, r2):
                continue
            c, r2 = _sphere2(pi, pj)
            for k in range(j):
                pk = pts[k]
                if _inside(pk, c, r2):
                    continue
                c, r2 = _sphere3(pi, pj, pk)
                for m in range(k):
                    pm = pts[m]
                    if _inside(pm, c, r2):
                        continue
                    s = _sphere4(pi, pj, pk, pm)
                    if s is None:  # near-coplanar: smallest 3-point sphere holding all four
                        quad = (pi, pj, pk, pm)
                        cands = [_sphere3(*t) for t in ((pi, pj, pm), (pi, pk, pm), (pj, pk, pm))]
                        cands = [q for q in cands if all(_inside(p, q[0], q[1]) for p in quad)]
                        s = min(cands, key=lambda q: q[1]) if cands else (c, max(_d2(p, c) for p in quad))
                    c, r2 = s
    cf = (_f32(c[0]), _f32(c[1]), _f32(c[2]))
    r = math.sqrt(max(_d2(p, cf) for p in pts))  # exact containment after rounding
    return (cf[0], cf[1], cf[2], _f32_up(r))


def _tri_uv_density(pa, pb, pc, ua, ub, uc):
    # Mean of |uv edge| / |world edge| over the three edges; 0 if any edge degenerates.
    s = 0.0
    for p0, p1, u0, u1 in ((pa, pb, ua, ub), (pb, pc, ub, uc), (pc, pa, uc, ua)):
        dp = _d2(p0, p1)
        if dp == 0.0:
            return 0.0
        du = (u0[0] - u1[0]) ** 2 + (u0[1] - u1[1]) ** 2
        if du == 0.0:
            return 0.0
        s += math.sqrt(du / dp)
    return s / 3.0


def compute_uv_density(positions, uvs, triangles):
    """GIANTS uvDensity of one subset / uv set.

    Per triangle: min(1, mean edge ratio); triangles below 1/64 are ignored.
    Result: max(min, max(mean - stddev, 0.75 * mean)) (sample stddev), 0 if none.
    """
    n = 0
    mean = 0.0
    m2 = 0.0
    lo = float("inf")
    for a, b, c in triangles:
        d = min(1.0, _tri_uv_density(positions[a], positions[b], positions[c], uvs[a], uvs[b], uvs[c]))
        if d < UV_DENSITY_MIN:
            continue
        n += 1
        delta = d - mean
        mean += delta / n
        m2 += delta * (d - mean)
        lo = min(lo, d)
    if n == 0:
        return 0.0
    std = math.sqrt(m2 / (n - 1)) if n > 1 else 0.0
    return max(lo, max(mean - std, 0.75 * mean))


def _norm(v):
    ln = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / ln, v[1] / ln, v[2] / ln) if ln > 0.0 else None


def compute_tangents(positions, normals, uvs, triangles):
    """Per-vertex (x, y, z, w) tangents from uv set 0.

    Each face adds its unit tangent/bitangent to its corners; the sum is
    Gram-Schmidt orthogonalised against the normal; w = +1 when
    dot(cross(n, t), bitangent) >= 0, else -1. Vertices without usable uvs get
    (1, 0, 0, 1) like GE. Matches GE 10 output for >99% of vertices.
    """
    nv = len(positions)
    tan = [[0.0, 0.0, 0.0] for _ in range(nv)]
    bit = [[0.0, 0.0, 0.0] for _ in range(nv)]
    if uvs is not None:
        for a, b, c in triangles:
            pa, pb, pc = positions[a], positions[b], positions[c]
            e1 = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
            e2 = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
            d1u, d1v = uvs[b][0] - uvs[a][0], uvs[b][1] - uvs[a][1]
            d2u, d2v = uvs[c][0] - uvs[a][0], uvs[c][1] - uvs[a][1]
            det = d1u * d2v - d2u * d1v
            if abs(det) <= TANGENT_MIN_UV_DET:
                continue
            r = 1.0 / det
            s = _norm(tuple((e1[i] * d2v - e2[i] * d1v) * r for i in range(3)))
            t = _norm(tuple((e2[i] * d1u - e1[i] * d2u) * r for i in range(3)))
            for vi in (a, b, c):
                if s is not None:
                    acc = tan[vi]
                    acc[0] += s[0]; acc[1] += s[1]; acc[2] += s[2]
                if t is not None:
                    acc = bit[vi]
                    acc[0] += t[0]; acc[1] += t[1]; acc[2] += t[2]
    out = []
    for i in range(nv):
        t = tan[i]
        n = _norm(normals[i]) if normals is not None else None
        if n is not None:
            d = n[0] * t[0] + n[1] * t[1] + n[2] * t[2]
            t = (t[0] - n[0] * d, t[1] - n[1] * d, t[2] - n[2] * d)
        tn = math.sqrt(t[0] * t[0] + t[1] * t[1] + t[2] * t[2])
        if tn <= 1e-12:
            out.append((1.0, 0.0, 0.0, 1.0))
            continue
        t = (t[0] / tn, t[1] / tn, t[2] / tn)
        w = 1.0
        if n is not None:
            cx = (n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0])
            b = bit[i]
            if cx[0] * b[0] + cx[1] * b[1] + cx[2] * b[2] < 0.0:
                w = -1.0
        out.append((t[0], t[1], t[2], w))
    return out


# ---------------------------------------------------------------------------
# Entity serialisation
# ---------------------------------------------------------------------------

def _part_header(buf, name, shape_id):
    nb = _encode_name(name)
    buf.i32(len(nb))
    buf.raw(nb)
    buf.align(4)
    buf.u32(int(shape_id) & _M32)


def _uv_sets(shape, nv):
    uvs = shape.get("uvs")
    sets = list(uvs) + [None] * (4 - len(uvs)) if uvs is not None else [shape.get("uv%d" % i) for i in range(4)]
    if len(sets) != 4:
        raise ValueError("at most 4 uv sets are supported")
    return [None if s is None else _f32_rows(s, 2, "uv%d" % i, nv) for i, s in enumerate(sets)]


def serialize_shape(shape):
    """Shape dict -> (entity data bytes, info dict). See module docstring."""
    name = shape.get("name", "")
    shape_id = int(shape["shape_id"])
    label = "shape %r (id %d)" % (name, shape_id)

    pos = _f32_rows(shape["positions"], 3, label + " positions", None)
    nv = len(pos)
    normals = shape.get("normals")
    normals = None if normals is None else _f32_rows(normals, 3, label + " normals", nv)
    uvs = _uv_sets(shape, nv)
    colors = shape.get("colors")
    if colors is not None:
        colors = [tuple(c) + (1.0,) * (4 - len(c)) for c in _tolist(colors)]
        colors = _f32_rows(colors, 4, label + " colors", nv)

    tris = []
    for t in _tolist(shape["triangles"]):
        t = tuple(int(i) for i in t)
        if len(t) != 3:
            raise ValueError("%s: triangles must have 3 indices" % label)
        if min(t) < 0 or max(t) >= nv:
            raise ValueError("%s: triangle %r references a vertex outside 0..%d" % (label, t, nv - 1))
        tris.append(t)
    corners = 3 * len(tris)

    tangents = shape.get("tangents")
    if tangents is True or isinstance(tangents, str):
        tangents = compute_tangents(pos, normals, uvs[0], tris)
    if tangents is not None:
        tangents = _f32_rows(tangents, 4, label + " tangents", nv)

    blend_weights = shape.get("blend_weights")
    blend_indices = shape.get("blend_indices")
    single_blend = False
    if blend_indices is not None:
        rows = [(int(r),) if not isinstance(r, (list, tuple)) else tuple(int(v) for v in r)
                for r in _tolist(blend_indices)]
        if len(rows) != nv:
            raise ValueError("%s: %d blend index rows, expected %d" % (label, len(rows), nv))
        single_blend = blend_weights is None
        width = 1 if single_blend else 4
        if any(len(r) != width for r in rows):
            raise ValueError("%s: blend indices need %d value(s) per vertex" % (label, width))
        if any(v < 0 or v > 255 for r in rows for v in r):
            raise ValueError("%s: blend indices must be 0..255" % label)
        blend_indices = bytes(v for r in rows for v in r)
        if blend_weights is not None:
            blend_weights = _f32_rows(blend_weights, 4, label + " blend weights", nv)
    elif blend_weights is not None:
        raise ValueError("%s: blend weights without blend indices" % label)

    generic = shape.get("generic")
    generic = None if generic is None else _f32_rows(generic, 1, label + " generic", nv)

    subsets = shape.get("subsets")
    if not subsets:
        subsets = [dict(first_vertex=0, num_vertices=nv, first_index=0, num_indices=corners)]

    options = 0
    if normals is not None:
        options |= OPT_NORMALS
    for i, uv in enumerate(uvs):
        if uv is not None:
            options |= OPT_UV[i]
    if colors is not None:
        options |= OPT_COLOR
    if blend_indices is not None:
        options |= OPT_SKINNING | (OPT_SINGLE_BLEND_WEIGHTS if single_blend else 0)
    if tangents is not None:
        options |= OPT_TANGENTS
    if generic is not None:
        options |= OPT_GENERIC
    mesh_usage = int(shape.get("mesh_usage", 0) or 0)
    if not 0 <= mesh_usage <= 0xFFFF:
        raise ValueError("%s: meshUsage %d does not fit 16 bits" % (label, mesh_usage))
    options |= (mesh_usage << MESH_USAGE_SHIFT) | (int(shape.get("extra_options", 0)) & _M32)

    bv = shape.get("bounding_volume")
    bv = compute_bounding_sphere(pos) if bv is None else tuple(float(v) for v in bv)
    if len(bv) != 4:
        raise ValueError("%s: bounding_volume needs 4 values" % label)

    buf = _Buf()
    _part_header(buf, name, shape_id)
    buf.f32(*bv)
    buf.u32(corners)
    buf.u32(len(subsets))
    buf.u32(nv)
    buf.u32(options & _M32)
    buf.f32(float(shape.get("vertex_compression", 0.0) or 0.0))

    info_subsets = []
    for si, s in enumerate(subsets):
        fv, nsv = int(s["first_vertex"]), int(s["num_vertices"])
        fi, ni = int(s["first_index"]), int(s["num_indices"])
        if fv < 0 or nsv < 0 or fv + nsv > nv:
            raise ValueError("%s: subset %d vertex range outside 0..%d" % (label, si, nv))
        if fi < 0 or ni < 0 or fi % 3 or ni % 3 or fi + ni > corners:
            raise ValueError("%s: subset %d index range invalid (corners=%d)" % (label, si, corners))
        given = s.get("uv_densities") or {}
        if isinstance(given, (list, tuple)):
            given = dict(enumerate(given))
        sub_tris = tris[fi // 3:(fi + ni) // 3]
        dens = []
        for k, uv in enumerate(uvs):
            if uv is None:
                continue
            d = given.get(k)
            dens.append(float(d) if d is not None else compute_uv_density(pos, uv, sub_tris))
        buf.u32(fv)
        buf.u32(nsv)
        buf.u32(fi)
        buf.u32(ni)
        if dens:
            buf.f32(*dens)
        info_subsets.append(dict(first_vertex=fv, num_vertices=nsv, first_index=fi, num_indices=ni,
                                 uv_densities=[_f32(d) for d in dens],
                                 material_slot_name=s.get("material_slot_name") or ""))
    for s in info_subsets:
        nb = _encode_name(s["material_slot_name"])
        if len(nb) > 0xFFFF:
            raise ValueError("%s: material slot name too long" % label)
        buf.u16(len(nb))
        buf.raw(nb)
        buf.align(2)
    buf.align(4)

    flat_idx = [i for t in tris for i in t]
    buf.raw(_pack_index(flat_idx, "H" if nv <= MAX_UINT16_INDEX_VERTICES else "I"))
    buf.align(4)

    buf.raw(_pack_f32(pos))
    if normals is not None:
        buf.raw(_pack_f32(normals))
    if tangents is not None:
        buf.raw(_pack_f32(tangents))
    for uv in uvs:
        if uv is not None:
            buf.raw(_pack_f32(uv))
    if colors is not None:
        buf.raw(_pack_f32(colors))
    if blend_indices is not None:
        if blend_weights is not None:
            buf.raw(_pack_f32(blend_weights))
        buf.raw(blend_indices)
    if generic is not None:
        buf.raw(_pack_f32(generic))

    attachments = shape.get("attachments") or []
    buf.u32(len(attachments))
    for att in attachments:
        flags = int(att["flags"])
        floats = att.get("floats")
        if bool(flags & 4) != (floats is not None):
            raise ValueError("%s: attachment floats must be present iff flags & 4" % label)
        buf.u32(flags)
        if floats is not None:
            buf.f32(*[float(v) for v in floats])
        data = bytes(att.get("data", b""))
        buf.i32(len(data))
        buf.raw(data)

    info = dict(kind="shape", name=name, shape_id=shape_id, entity_type=int(shape.get("entity_type", ENTITY_SHAPE)),
                options=options & _M32, vertices=nv, triangles=len(tris), bounding_volume=tuple(_f32(v) for v in bv),
                subsets=info_subsets, index_bytes=2 if nv <= MAX_UINT16_INDEX_VERTICES else 4)
    return bytes(buf.b), info


def serialize_spline(spline):
    name = spline.get("name", "")
    shape_id = int(spline["shape_id"])
    pts = _f32_rows(spline["points"], 3, "spline %r points" % name, None)
    buf = _Buf()
    _part_header(buf, name, shape_id)
    buf.u32(1 if spline.get("closed") else 0)
    buf.i32(len(pts))
    buf.raw(_pack_f32(pts))
    buf.u32(int(spline.get("attribute_flags", 0)))
    etype = ENTITY_SPLINE_CUBIC if spline.get("cubic", True) else ENTITY_SPLINE_LINEAR
    info = dict(kind="spline", name=name, shape_id=shape_id, entity_type=etype, points=len(pts),
                closed=bool(spline.get("closed")))
    return bytes(buf.b), info


def serialize_entity(item):
    if item.get("kind", "shape") == "spline":
        return serialize_spline(item)
    return serialize_shape(item)


def build_shapes_bytes(shapes, seed=0, use_numpy=True):
    """-> (file bytes, list of per-entity info dicts)."""
    seed = int(seed)
    seed_key(seed)
    cs = CipherStream(seed, use_numpy=use_numpy)
    infos = []
    blobs = [serialize_entity(s) for s in shapes]
    cs.write(struct.pack("<i", len(blobs)))
    for data, info in blobs:
        cs.write(struct.pack("<i", info["entity_type"]))
        cs.write(struct.pack("<i", len(data)))
        cs.write(data)
        info["size"] = len(data)
        infos.append(info)
    header = bytes((SHAPES_VERSION, 0, seed, 0))
    return header + cs.getvalue(), infos


def write_shapes_file(path, shapes, seed=0):
    """Write ``shapes`` (list of dicts) to ``path`` as a v10 .i3d.shapes file.

    Shape dict keys: name, shape_id, positions (N,3), normals (N,3)|None,
    uv0..uv3 (N,2)|None, colors (N,4)|None, triangles (M,3), subsets
    [dict(first_vertex, num_vertices, first_index, num_indices,
    material_slot_name='', uv_densities={set: value})]. Optional: tangents
    ((N,4) or True = compute), blend_weights (N,4), blend_indices (N,4)|(N,),
    generic (N,), bounding_volume (cx, cy, cz, r), mesh_usage, vertex_compression,
    extra_options, attachments. Spline dict: kind='spline', name, shape_id,
    points (K,3), closed, cubic. Returns the per-entity info list.
    """
    data, infos = build_shapes_bytes(shapes, seed)
    with open(path, "wb") as f:
        f.write(data)
    return infos
