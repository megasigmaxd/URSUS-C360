#!/usr/bin/env python3
"""PNG -> DDS converter for GIANTS Engine (FS25) textures, numpy + Pillow only.

Formats (legacy DX9 headers, the variant GIANTS Editor/Paint.NET/NVTT all read):
  bc1   DXT1  opaque RGB (diffuse, specular)
  bc3   DXT5  RGB + smooth alpha (decals, glass)
  bc4   ATI1  single channel (masks)
  bc5   ATI2  two channels (X=R, Y=G); only valid for shaders that rebuild Z.
        GIANTS normal maps are RGB (Texture Tool: "red-green-blue channel contain
        the tangent space normal map"), so normals should be bc1: Z stays in blue
        and the map works whether the shader reads RGB or rebuilds Z from RG.
  rgba8 A8R8G8B8 uncompressed; GIANTS logs a "raw format" performance warning
        for these, so only use it for debugging.

Mip chains are generated down to 1x1. Mip filtering follows the GIANTS Texture
Tool conventions: colour maps are averaged in linear light, normal maps are
re-normalised per level, data maps are averaged as-is, and cutout alpha can be
coverage-preserved (--alpha-coverage).

The BC1 encoder fits endpoints along the principal axis with least-squares
refits, tries an exact per-channel fit for near-uniform blocks, and with
--refine (automatic for normal maps) runs a +-1 local search on the 5:6:5
endpoints, which keeps flat normal areas free of 4x4 block offsets.

Usage:
  png2dds.py in.png out.dds --format bc1|bc3|bc4|bc5|rgba8 [--no-mips]
             [--mip-mode auto|srgb|linear|normal] [--alpha-coverage 0.5]
             [--refine] [--no-verify]
"""
from __future__ import annotations

import argparse
import functools
import os
import struct
import sys

import numpy as np
from PIL import Image

FORMATS = ("bc1", "bc3", "bc4", "bc5", "rgba8")
_FOURCC = {"bc1": b"DXT1", "bc3": b"DXT5", "bc4": b"ATI1", "bc5": b"ATI2"}
_BLOCK_BYTES = {"bc1": 8, "bc3": 16, "bc4": 8, "bc5": 16}

# ----------------------------------------------------------------------------
# colour space helpers


def srgb_to_linear(x):
    x = np.asarray(x, dtype=np.float64)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x):
    x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


# ----------------------------------------------------------------------------
# mip generation


def _downsample(a):
    """2x2 box filter; axes of size 1 are left alone (non-square chains)."""
    h, w = a.shape[:2]
    if h > 1:
        a = 0.5 * (a[0::2] + a[1::2])
    if w > 1:
        a = 0.5 * (a[:, 0::2] + a[:, 1::2])
    return a


def _coverage(alpha, ref):
    return float(np.mean(alpha > ref))


def _fit_coverage(alpha, target, ref):
    """Scale alpha so the fraction above `ref` matches `target` (binary search)."""
    lo, hi = 0.0, 8.0
    for _ in range(24):
        mid = 0.5 * (lo + hi)
        if _coverage(np.clip(alpha * mid, 0, 1), ref) < target:
            lo = mid
        else:
            hi = mid
    return np.clip(alpha * hi, 0.0, 1.0)


def build_mips(img, mode="srgb", full_chain=True, alpha_coverage=None):
    """Return list of float arrays in 0..255, level 0 first.

    img: HxW or HxWxC uint8/float (0..255).
    mode: 'srgb'   colour channels averaged in linear light, alpha linear
          'linear' plain average (data textures)
          'normal' RGB treated as a unit vector (x=R, y=G, z=B), renormalised
    alpha_coverage: alpha-test reference (e.g. 0.5) to keep cutout coverage.
    """
    a = np.asarray(img, dtype=np.float64)
    squeeze = a.ndim == 2
    if squeeze:
        a = a[:, :, None]
    c = a.shape[2]
    has_alpha = c == 4 and mode != "normal"
    levels = [a.copy()]
    if not full_chain:
        return [lv[:, :, 0] if squeeze else lv for lv in levels]

    if mode == "srgb":
        work = a / 255.0
        ncol = 3 if c >= 3 else c
        work[:, :, :ncol] = srgb_to_linear(work[:, :, :ncol])
        if has_alpha:
            # premultiply so transparent texels do not bleed into edges
            work[:, :, :3] *= work[:, :, 3:4]
    elif mode == "normal":
        work = a[:, :, :3] / 127.5 - 1.0
    else:
        work = a / 255.0

    ref_cov = None
    if alpha_coverage is not None and c == 4:
        ref_cov = _coverage(a[:, :, 3] / 255.0, alpha_coverage)

    while work.shape[0] > 1 or work.shape[1] > 1:
        work = _downsample(work)
        if mode == "normal":
            n = work / np.maximum(np.linalg.norm(work, axis=2, keepdims=True), 1e-8)
            lv = np.clip((n + 1.0) * 127.5, 0, 255)
            if c == 4:  # keep an extra channel untouched if present
                lv = np.concatenate([lv, np.full(lv.shape[:2] + (1,), 255.0)], axis=2)
        elif mode == "srgb":
            out = work.copy()
            if has_alpha:
                al = out[:, :, 3:4]
                out[:, :, :3] = np.where(al > 1e-6, out[:, :, :3] / np.maximum(al, 1e-6), 0.0)
                if ref_cov is not None:
                    out[:, :, 3] = _fit_coverage(out[:, :, 3], ref_cov, alpha_coverage)
            ncol = 3 if c >= 3 else c
            out[:, :, :ncol] = linear_to_srgb(out[:, :, :ncol])
            lv = np.clip(out * 255.0, 0, 255)
        else:
            out = work.copy()
            if ref_cov is not None:
                out[:, :, 3] = _fit_coverage(out[:, :, 3], ref_cov, alpha_coverage)
            lv = np.clip(out * 255.0, 0, 255)
        levels.append(lv)
    return [lv[:, :, 0] if squeeze else lv for lv in levels]


# ----------------------------------------------------------------------------
# block helpers


def _to_blocks(img):
    """HxWxC -> (N,16,C) in DDS block order, padding to multiples of 4."""
    h, w, c = img.shape
    ph, pw = (-h) % 4, (-w) % 4
    if ph or pw:
        img = np.pad(img, ((0, ph), (0, pw), (0, 0)), mode="edge")
    h2, w2 = img.shape[:2]
    return img.reshape(h2 // 4, 4, w2 // 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(-1, 16, c)


def _from_blocks(blocks, h, w):
    c = blocks.shape[2]
    bh, bw = (h + 3) // 4, (w + 3) // 4
    img = blocks.reshape(bh, bw, 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, c)
    return img[:h, :w]


def _pack_indices(idx, bits):
    """(N,16) small ints -> (N,) uint64 bitfield, texel 0 in the lowest bits."""
    shifts = (np.arange(16, dtype=np.uint64) * np.uint64(bits))
    return np.bitwise_or.reduce(idx.astype(np.uint64) << shifts[None, :], axis=1)


def _unpack_indices(packed, bits):
    shifts = (np.arange(16, dtype=np.uint64) * np.uint64(bits))
    mask = np.uint64((1 << bits) - 1)
    return ((packed[:, None] >> shifts[None, :]) & mask).astype(np.int64)


# ----------------------------------------------------------------------------
# BC1 (colour)


def _q565(c):
    r = np.clip(np.rint(c[..., 0] * 31.0 / 255.0), 0, 31).astype(np.int64)
    g = np.clip(np.rint(c[..., 1] * 63.0 / 255.0), 0, 63).astype(np.int64)
    b = np.clip(np.rint(c[..., 2] * 31.0 / 255.0), 0, 31).astype(np.int64)
    return r, g, b


def _expand565(r, g, b):
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=-1)


def _bc1_palette(e0, e1):
    """Integer palette identical to the reference decoder (4-colour mode)."""
    p2 = (2 * e0 + e1) // 3
    p3 = (e0 + 2 * e1) // 3
    return np.stack([e0, e1, p2, p3], axis=1)  # (N,4,3)


_BC1_W = np.array([1.0, 0.0, 2.0 / 3.0, 1.0 / 3.0])
_Q_MAX = np.array([31, 63, 31])


def _bc1_assign(px, pal):
    """Nearest palette entry per texel; exact for integer-valued px (|p|^2 - 2p.c + |c|^2)."""
    palf = pal.astype(np.float64)
    d = ((px * px).sum(2)[:, :, None] - 2.0 * np.matmul(px, palf.transpose(0, 2, 1))
         + (palf * palf).sum(2)[:, None, :])  # (N,16,4)
    idx = np.argmin(d, axis=2)
    err = np.take_along_axis(d, idx[:, :, None], axis=2)[:, :, 0].sum(1)
    return idx, err


def _quantise(c):
    """Float colours (N,3) -> 5:6:5 components (N,3) int."""
    return np.stack(_q565(np.clip(c, 0, 255)), axis=1)


def _q_expand(q):
    return _expand565(q[:, 0], q[:, 1], q[:, 2])


@functools.lru_cache(maxsize=None)
def _single_colour_table(bits):
    """Endpoint pair per 8-bit value whose palette entry 2 = (2*e0 + e1) // 3 is closest.

    A small penalty on |e0 - e1| keeps the endpoints close, so GPUs that round the
    interpolation differently from the reference decoder land on nearly the same value
    (the stb_dxt approach)."""
    q = np.arange(1 << bits)
    e = (q << (8 - bits)) | (q >> (2 * bits - 8))
    pal = (2 * e[:, None] + e[None, :]) // 3
    err = (np.abs(pal[None] - np.arange(256)[:, None, None])
           + 0.03 * np.abs(e[:, None] - e[None, :])[None])
    best = err.reshape(256, -1).argmin(axis=1)
    return best // q.size, best % q.size


class _Best:
    """Per-block best BC1 candidate (5:6:5 endpoints, indices, squared error)."""

    def __init__(self, px):
        self.px = px
        self.q0 = self.q1 = self.idx = self.err = None

    def offer(self, q0, q1):
        idx, err = _bc1_assign(self.px, _bc1_palette(_q_expand(q0), _q_expand(q1)))
        if self.err is None:
            self.q0, self.q1, self.idx, self.err = q0, q1, idx, err
            return
        b = err < self.err
        self.q0 = np.where(b[:, None], q0, self.q0)
        self.q1 = np.where(b[:, None], q1, self.q1)
        self.idx = np.where(b[:, None], idx, self.idx)
        self.err = np.where(b, err, self.err)


def _local_search(best, passes=6, exact_tol=8.0):
    """Greedy +-1 steps on each 5:6:5 endpoint component (and on both endpoints together)."""
    moves = [(w, ch, s) for w in (0, 1, 2) for ch in range(3) for s in (-1, 1)]  # w=2: shift both
    active = np.nonzero(best.err > exact_tol)[0]
    for _ in range(passes):
        if active.size == 0:
            break
        px = best.px[active]
        q0, q1 = best.q0[active], best.q1[active]
        idx, err = best.idx[active], best.err[active]
        improved = np.zeros(active.size, dtype=bool)
        for w, ch, s in moves:
            n0, n1 = q0.copy(), q1.copy()
            if w in (0, 2):
                n0[:, ch] = np.clip(n0[:, ch] + s, 0, _Q_MAX[ch])
            if w in (1, 2):
                n1[:, ch] = np.clip(n1[:, ch] + s, 0, _Q_MAX[ch])
            ni, ne = _bc1_assign(px, _bc1_palette(_q_expand(n0), _q_expand(n1)))
            b = ne < err
            if b.any():
                q0 = np.where(b[:, None], n0, q0)
                q1 = np.where(b[:, None], n1, q1)
                idx = np.where(b[:, None], ni, idx)
                err = np.where(b, ne, err)
                improved |= b
        best.q0[active], best.q1[active] = q0, q1
        best.idx[active], best.err[active] = idx, err
        active = active[improved]


def encode_bc1_blocks(px, refine=False):
    """px: (N,16,3) float 0..255 -> (N,) c0, c1 uint16 and (N,16) indices."""
    px = px.astype(np.float64)
    n = px.shape[0]
    mean = px.mean(axis=1, keepdims=True)
    cen = px - mean
    cov = np.einsum("nki,nkj->nij", cen, cen)
    v = np.tile(np.array([0.577, 0.577, 0.577]), (n, 1))
    # start from the channel with the largest variance to avoid degenerate init
    diag = np.einsum("nii->ni", cov)
    v = np.where((diag.max(1, keepdims=True) > 0), np.eye(3)[np.argmax(diag, 1)] + 0.1, v)
    for _ in range(8):
        v = np.einsum("nij,nj->ni", cov, v)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
    t = np.einsum("nki,ni->nk", cen, v)
    e0f = mean[:, 0] + v * t.max(1, keepdims=True)
    e1f = mean[:, 0] + v * t.min(1, keepdims=True)

    best = _Best(px)
    for it in range(4):
        best.offer(_quantise(e0f), _quantise(e1f))
        if it == 3:
            break
        # least-squares endpoint refit for the current index assignment
        w = _BC1_W[best.idx]
        a = (w * w).sum(1)
        b = (w * (1 - w)).sum(1)
        cc = ((1 - w) ** 2).sum(1)
        det = a * cc - b * b
        x0 = np.einsum("nk,nkc->nc", w, px)
        x1 = np.einsum("nk,nkc->nc", 1 - w, px)
        ok = np.abs(det) > 1e-6
        sd = np.where(ok, det, 1.0)[:, None]
        n0 = (cc[:, None] * x0 - b[:, None] * x1) / sd
        n1 = (a[:, None] * x1 - b[:, None] * x0) / sd
        e0f = np.where(ok[:, None], n0, _q_expand(best.q0).astype(np.float64))
        e1f = np.where(ok[:, None], n1, _q_expand(best.q1).astype(np.float64))

    # near-uniform blocks: all texels on palette entry 2, fitted per channel to the block mean
    m = np.clip(np.rint(px.mean(axis=1)), 0, 255).astype(np.int64)
    t5, t6 = _single_colour_table(5), _single_colour_table(6)
    best.offer(np.stack([t5[0][m[:, 0]], t6[0][m[:, 1]], t5[0][m[:, 2]]], axis=1),
               np.stack([t5[1][m[:, 0]], t6[1][m[:, 1]], t5[1][m[:, 2]]], axis=1))
    if refine:
        _local_search(best)

    q0, q1, idx = best.q0, best.q1, best.idx
    c0 = (q0[:, 0] << 11) | (q0[:, 1] << 5) | q0[:, 2]
    c1 = (q1[:, 0] << 11) | (q1[:, 1] << 5) | q1[:, 2]
    # enforce 4-colour mode (c0 > c1); equal endpoints -> all indices 0
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    remap = np.array([1, 0, 3, 2])
    idx = np.where(swap[:, None], remap[idx], idx)
    idx = np.where((c0 == c1)[:, None], 0, idx)
    return c0.astype(np.uint16), c1.astype(np.uint16), idx


def encode_bc1(img, refine=False):
    blocks = _to_blocks(np.asarray(img, dtype=np.float64)[:, :, :3])
    c0, c1, idx = encode_bc1_blocks(blocks, refine=refine)
    out = np.zeros((blocks.shape[0], 8), dtype=np.uint8)
    out[:, 0:2] = c0.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = c1.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 4:8] = _pack_indices(idx, 2).astype("<u4").view(np.uint8).reshape(-1, 4)
    return out.tobytes()


# ----------------------------------------------------------------------------
# BC4 (single channel), BC3 alpha, BC5


def _bc4_palette(a0, a1):
    a0 = a0.astype(np.int64)
    a1 = a1.astype(np.int64)
    p8 = np.stack([a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)], axis=1)
    p6 = np.stack([a0, a1] + [((5 - k) * a0 + k * a1) // 5 for k in range(1, 5)]
                  + [np.zeros_like(a0), np.full_like(a0, 255)], axis=1)
    return np.where((a0 > a1)[:, None], p8, p6)


def _bc4_assign(v, pal):
    d = (v[:, :, None] - pal[:, None, :].astype(np.float64)) ** 2
    idx = np.argmin(d, axis=2)
    err = np.take_along_axis(d, idx[:, :, None], axis=2)[:, :, 0].sum(1)
    return idx, err


_W8 = np.array([1.0, 0.0] + [(7 - k) / 7.0 for k in range(1, 7)])


def encode_bc4_blocks(v):
    """v: (N,16) float 0..255 -> a0, a1 uint8 and (N,16) indices (3 bit)."""
    v = v.astype(np.float64)
    vmax, vmin = v.max(1), v.min(1)
    # mode A: 8 interpolated values, a0 > a1
    a0 = np.clip(np.rint(vmax), 0, 255)
    a1 = np.clip(np.rint(vmin), 0, 255)
    flat = a0 <= a1
    a0 = np.where(flat, np.minimum(a1 + 1, 255), a0)
    a1 = np.where(flat & (a0 == a1), a1 - 1, a1)
    best_a0, best_a1 = a0.copy(), a1.copy()
    best_idx, best_err = _bc4_assign(v, _bc4_palette(a0, a1))
    for _ in range(2):
        w = _W8[best_idx]
        a = (w * w).sum(1)
        b = (w * (1 - w)).sum(1)
        cc = ((1 - w) ** 2).sum(1)
        det = a * cc - b * b
        x0 = (w * v).sum(1)
        x1 = ((1 - w) * v).sum(1)
        ok = np.abs(det) > 1e-6
        sd = np.where(ok, det, 1.0)
        n0 = np.clip(np.rint((cc * x0 - b * x1) / sd), 0, 255)
        n1 = np.clip(np.rint((a * x1 - b * x0) / sd), 0, 255)
        ok &= n0 > n1
        n0 = np.where(ok, n0, best_a0)
        n1 = np.where(ok, n1, best_a1)
        idx, err = _bc4_assign(v, _bc4_palette(n0, n1))
        better = err < best_err
        best_a0 = np.where(better, n0, best_a0)
        best_a1 = np.where(better, n1, best_a1)
        best_idx = np.where(better[:, None], idx, best_idx)
        best_err = np.where(better, err, best_err)
    # mode B: 6 interpolated values + explicit 0 and 255 (a0 <= a1)
    inner = (v > 0.5) & (v < 254.5)
    big, small = np.where(inner, v, -1).max(1), np.where(inner, v, 256).min(1)
    has = inner.any(1)
    b0 = np.where(has, np.clip(np.rint(small), 0, 255), 0)
    b1 = np.where(has, np.clip(np.rint(big), 0, 255), 0)
    idx, err = _bc4_assign(v, _bc4_palette(b0, b1))
    better = err < best_err
    best_a0 = np.where(better, b0, best_a0)
    best_a1 = np.where(better, b1, best_a1)
    best_idx = np.where(better[:, None], idx, best_idx)
    return best_a0.astype(np.uint8), best_a1.astype(np.uint8), best_idx


def _bc4_bytes(v):
    a0, a1, idx = encode_bc4_blocks(v)
    out = np.zeros((v.shape[0], 8), dtype=np.uint8)
    out[:, 0] = a0
    out[:, 1] = a1
    out[:, 2:8] = _pack_indices(idx, 3).astype("<u8").view(np.uint8).reshape(-1, 8)[:, :6]
    return out


def encode_bc4(img):
    a = np.asarray(img, dtype=np.float64)
    if a.ndim == 3:
        a = a[:, :, 0]
    return _bc4_bytes(_to_blocks(a[:, :, None])[:, :, 0]).tobytes()


def encode_bc3(img, refine=False):
    a = np.asarray(img, dtype=np.float64)
    blocks = _to_blocks(a[:, :, :4])
    alpha = _bc4_bytes(blocks[:, :, 3])
    c0, c1, idx = encode_bc1_blocks(blocks[:, :, :3], refine=refine)
    col = np.zeros((blocks.shape[0], 8), dtype=np.uint8)
    col[:, 0:2] = c0.astype("<u2").view(np.uint8).reshape(-1, 2)
    col[:, 2:4] = c1.astype("<u2").view(np.uint8).reshape(-1, 2)
    col[:, 4:8] = _pack_indices(idx, 2).astype("<u4").view(np.uint8).reshape(-1, 4)
    return np.concatenate([alpha, col], axis=1).tobytes()


def encode_bc5(img):
    a = np.asarray(img, dtype=np.float64)
    blocks = _to_blocks(a[:, :, :2])
    return np.concatenate([_bc4_bytes(blocks[:, :, 0]), _bc4_bytes(blocks[:, :, 1])], axis=1).tobytes()


def encode_rgba8(img):
    a = np.clip(np.rint(np.asarray(img, dtype=np.float64)), 0, 255).astype(np.uint8)
    if a.shape[2] == 3:
        a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], axis=2)
    return a[:, :, [2, 1, 0, 3]].tobytes()  # A8R8G8B8 = BGRA in memory


_ENCODERS = {"bc1": encode_bc1, "bc3": encode_bc3, "bc4": encode_bc4, "bc5": encode_bc5, "rgba8": encode_rgba8}


# ----------------------------------------------------------------------------
# DDS container

DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PITCH = 0x1, 0x2, 0x4, 0x8
DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT, DDSD_LINEARSIZE = 0x1000, 0x20000, 0x80000
DDPF_ALPHAPIXELS, DDPF_FOURCC, DDPF_RGB = 0x1, 0x4, 0x40
DDSCAPS_COMPLEX, DDSCAPS_TEXTURE, DDSCAPS_MIPMAP = 0x8, 0x1000, 0x400000


def _level_size(fmt, w, h):
    if fmt == "rgba8":
        return w * h * 4
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * _BLOCK_BYTES[fmt]


def dds_header(fmt, w, h, mip_count):
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT
    caps = DDSCAPS_TEXTURE
    if mip_count > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps |= DDSCAPS_COMPLEX | DDSCAPS_MIPMAP
    if fmt == "rgba8":
        flags |= DDSD_PITCH
        pitch = w * 4
        pf = struct.pack("<II4sIIIII", 32, DDPF_RGB | DDPF_ALPHAPIXELS, b"\0\0\0\0", 32,
                         0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    else:
        flags |= DDSD_LINEARSIZE
        pitch = _level_size(fmt, w, h)
        pf = struct.pack("<II4sIIIII", 32, DDPF_FOURCC, _FOURCC[fmt], 0, 0, 0, 0, 0)
    hdr = struct.pack("<IIIIIII", 124, flags, h, w, pitch, 0, mip_count if mip_count > 1 else 0)
    hdr += b"\0" * 44 + pf + struct.pack("<IIIII", caps, 0, 0, 0, 0)
    assert len(hdr) == 124
    return b"DDS " + hdr


def encode_levels(levels, fmt, refine=False):
    """levels: list of HxWxC float/uint8 arrays (0..255), level 0 first -> DDS bytes."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt}")
    h, w = levels[0].shape[:2]
    parts = [dds_header(fmt, w, h, len(levels))]
    for lv in levels:
        lv = np.asarray(lv, dtype=np.float64)
        if lv.ndim == 2:
            lv = lv[:, :, None]
        if fmt == "bc1" and lv.shape[2] < 3:
            lv = np.repeat(lv[:, :, :1], 3, axis=2)
        if fmt in ("bc3", "rgba8") and lv.shape[2] == 3:
            lv = np.concatenate([lv, np.full(lv.shape[:2] + (1,), 255.0)], axis=2)
        # quantise to 8 bit first so the encoder sees exactly what a PNG would hold
        lv = np.clip(np.rint(lv), 0, 255)
        data = _ENCODERS[fmt](lv, refine=refine) if fmt in ("bc1", "bc3") else _ENCODERS[fmt](lv)
        assert len(data) == _level_size(fmt, lv.shape[1], lv.shape[0]), (fmt, lv.shape)
        parts.append(data)
    return b"".join(parts)


def save_dds(path, img, fmt, mips=True, mip_mode="srgb", alpha_coverage=None, levels=None, refine=False):
    """Encode an image (or a prebuilt mip list) and write it to `path`."""
    if levels is None:
        levels = build_mips(img, mode=mip_mode, full_chain=mips, alpha_coverage=alpha_coverage)
    data = encode_levels(levels, fmt, refine=refine)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


# ----------------------------------------------------------------------------
# decoder (verification)


def _decode_bc1_blocks(raw, alpha_mode=True):
    c0 = raw[:, 0:2].copy().view("<u2")[:, 0].astype(np.int64)
    c1 = raw[:, 2:4].copy().view("<u2")[:, 0].astype(np.int64)
    idx = _unpack_indices(raw[:, 4:8].copy().view("<u4")[:, 0].astype(np.uint64), 2)
    e0 = _expand565(c0 >> 11, (c0 >> 5) & 63, c0 & 31)
    e1 = _expand565(c1 >> 11, (c1 >> 5) & 63, c1 & 31)
    pal4 = _bc1_palette(e0, e1)
    pal3 = np.stack([e0, e1, (e0 + e1) // 2, np.zeros_like(e0)], axis=1)
    three = (c0 <= c1) & alpha_mode
    pal = np.where(three[:, None, None], pal3, pal4)
    rgb = np.take_along_axis(pal, idx[:, :, None].repeat(3, axis=2), axis=1)
    a = np.where(three[:, None] & (idx == 3), 0, 255)
    return rgb, a


def _decode_bc4_blocks(raw):
    a0, a1 = raw[:, 0], raw[:, 1]
    packed = np.zeros((raw.shape[0], 8), dtype=np.uint8)
    packed[:, :6] = raw[:, 2:8]
    idx = _unpack_indices(packed.view("<u8")[:, 0].astype(np.uint64), 3)
    pal = _bc4_palette(a0, a1)
    return np.take_along_axis(pal, idx, axis=1)


def read_dds(path):
    """Return (fmt, [levels]) with levels as uint8 HxWxC arrays."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"DDS ":
        raise ValueError("not a DDS file")
    _, flags, h, w, _, _, mips = struct.unpack("<IIIIIII", data[4:32])
    pf_flags, fourcc, bits = struct.unpack("<I4sI", data[80:92])
    mips = max(1, mips)
    if pf_flags & DDPF_FOURCC:
        fmt = {v: k for k, v in _FOURCC.items()}.get(fourcc)
        if fmt is None:
            raise ValueError(f"unsupported fourcc {fourcc!r}")
    elif bits == 32:
        fmt = "rgba8"
    else:
        raise ValueError("unsupported pixel format")
    off = 128
    levels = []
    lw, lh = w, h
    for _ in range(mips):
        size = _level_size(fmt, lw, lh)
        chunk = np.frombuffer(data[off:off + size], dtype=np.uint8)
        off += size
        if fmt == "rgba8":
            img = chunk.reshape(lh, lw, 4)[:, :, [2, 1, 0, 3]]
        else:
            raw = chunk.reshape(-1, _BLOCK_BYTES[fmt])
            if fmt == "bc1":
                rgb, _ = _decode_bc1_blocks(raw, alpha_mode=True)
                img = _from_blocks(rgb, lh, lw)
            elif fmt == "bc3":
                a = _decode_bc4_blocks(raw[:, :8])
                rgb, _ = _decode_bc1_blocks(raw[:, 8:], alpha_mode=False)
                img = _from_blocks(np.concatenate([rgb, a[:, :, None]], axis=2), lh, lw)
            elif fmt == "bc4":
                img = _from_blocks(_decode_bc4_blocks(raw)[:, :, None], lh, lw)
            else:
                r = _decode_bc4_blocks(raw[:, :8])
                g = _decode_bc4_blocks(raw[:, 8:])
                img = _from_blocks(np.stack([r, g], axis=2), lh, lw)
        levels.append(np.ascontiguousarray(img).astype(np.uint8))
        lw, lh = max(1, lw // 2), max(1, lh // 2)
    if off != len(data):
        raise ValueError(f"size mismatch: parsed {off} of {len(data)} bytes")
    return fmt, levels


def psnr(a, b):
    mse = np.mean((np.asarray(a, np.float64) - np.asarray(b, np.float64)) ** 2)
    return float("inf") if mse == 0 else 10.0 * np.log10(255.0 ** 2 / mse)


def channels_for(fmt):
    return {"bc1": [0, 1, 2], "bc3": [0, 1, 2, 3], "bc4": [0], "bc5": [0, 1], "rgba8": [0, 1, 2, 3]}[fmt]


def normal_angle_error(dec, src):
    """Angular error (degrees, mean and p99) of decoded vs source tangent-space normals.

    'rgb': shader normalises the sampled RGB; 'rg': shader rebuilds Z from RG (ignores B)."""
    s = np.asarray(src, np.float64)[:, :, :3] / 127.5 - 1.0
    s /= np.maximum(np.linalg.norm(s, axis=2, keepdims=True), 1e-8)
    d = np.asarray(dec, np.float64) / 127.5 - 1.0
    xy = d[:, :, :2]
    cands = {"rg": np.concatenate([xy, np.sqrt(np.clip(1.0 - (xy ** 2).sum(2), 0, 1))[:, :, None]], axis=2)}
    if d.shape[2] >= 3:
        cands["rgb"] = d[:, :, :3]
    res = {}
    for k, v in cands.items():
        v = v / np.maximum(np.linalg.norm(v, axis=2, keepdims=True), 1e-8)
        ang = np.degrees(np.arccos(np.clip((v * s).sum(2), -1.0, 1.0)))
        res[f"angle_{k}_mean"] = float(ang.mean())
        res[f"angle_{k}_p99"] = float(np.percentile(ang, 99))
    return res


def verify(dds_path, source_levels, fmt, normal=False):
    """Decode with our decoder and Pillow; return a dict of PSNR figures (level 0)."""
    res = {}
    _, levels = read_dds(dds_path)
    res["mips"] = len(levels)
    ch = channels_for(fmt)
    src0 = np.asarray(source_levels[0], np.float64)
    if src0.ndim == 2:
        src0 = src0[:, :, None]
    src0 = np.clip(np.rint(src0), 0, 255)
    if fmt in ("bc3", "rgba8") and src0.shape[2] == 3:
        src0 = np.concatenate([src0, np.full(src0.shape[:2] + (1,), 255.0)], axis=2)
    if fmt == "bc1" and src0.shape[2] == 1:
        src0 = np.repeat(src0, 3, axis=2)
    res["psnr_own"] = psnr(levels[0][:, :, :len(ch)], src0[:, :, ch])
    worst = float("inf")
    for dec, src in zip(levels, source_levels):
        s = np.asarray(src, np.float64)
        if s.ndim == 2:
            s = s[:, :, None]
        if s.shape[2] < len(ch):
            s = np.concatenate([s, np.full(s.shape[:2] + (len(ch) - s.shape[2],), 255.0)], axis=2)
        worst = min(worst, psnr(dec[:, :, :len(ch)], np.clip(np.rint(s[:, :, ch]), 0, 255)))
    res["psnr_worst_mip"] = worst
    if normal:
        res.update(normal_angle_error(levels[0], np.clip(np.rint(np.asarray(source_levels[0], np.float64)), 0, 255)))
    try:
        with Image.open(dds_path) as im:
            im.load()
            arr = np.asarray(im)
        if arr.ndim == 2:
            arr = arr[:, :, None]
        pil = arr[:, :, :len(ch)].astype(np.float64)
        res["psnr_pillow"] = psnr(pil, src0[:, :, ch])
        res["max_diff_pillow_vs_own"] = int(np.abs(pil - levels[0][:, :, :len(ch)]).max())
    except Exception as exc:  # pragma: no cover - diagnostic only
        res["pillow_error"] = str(exc)
    return res


# ----------------------------------------------------------------------------
# CLI


def auto_mip_mode(path):
    name = os.path.basename(path).lower()
    if "_normal" in name:
        return "normal"
    if any(k in name for k in ("_specular", "_mask", "_vmask", "_height", "_alpha")):
        return "linear"
    return "srgb"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--format", required=True, choices=FORMATS)
    ap.add_argument("--no-mips", action="store_true", help="write level 0 only")
    ap.add_argument("--mip-mode", default="auto", choices=("auto", "srgb", "linear", "normal"))
    ap.add_argument("--alpha-coverage", type=float, default=None,
                    help="preserve alpha-test coverage at this reference (e.g. 0.5)")
    ap.add_argument("--refine", action="store_true",
                    help="BC1/BC3 local endpoint search (always on for normal maps)")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)

    im = Image.open(args.input)
    if args.format in ("bc3", "rgba8"):
        im = im.convert("RGBA")
    elif args.format == "bc4":
        im = im.convert("L")
    else:
        im = im.convert("RGB")
    img = np.asarray(im, dtype=np.float64)
    mode = auto_mip_mode(args.input) if args.mip_mode == "auto" else args.mip_mode
    if args.format == "bc5" and mode != "normal":
        img = img[:, :, :2]
    levels = build_mips(img, mode=mode, full_chain=not args.no_mips, alpha_coverage=args.alpha_coverage)
    if args.format == "bc5":
        levels = [lv[:, :, :2] for lv in levels]
    refine = args.refine or mode == "normal"
    size = save_dds(args.output, None, args.format, levels=levels, refine=refine)
    print(f"{args.output}: {args.format} {img.shape[1]}x{img.shape[0]} mips={len(levels)} "
          f"mip-mode={mode} refine={refine} bytes={size}")
    if not args.no_verify:
        r = verify(args.output, levels, args.format, normal=mode == "normal")
        print("  verify: " + ", ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                                        for k, v in r.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
