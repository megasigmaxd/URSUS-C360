#!/usr/bin/env python3
"""Procedural PBR textures for the FS25 Ursus C-360 mod (deterministic).

Writes
  FS25_UrsusC360/textures/<name>.dds   (BC1 diffuse/specular/normal, BC3 for
                                       anything with alpha; full mips)
  <png-dir>/<name>.png                 (previews for Blender, same basenames)
  docs/textures_contact_sheet.jpg

Channel conventions (GIANTS Engine / FS25):
  _diffuse   sRGB albedo (+ alpha where noted)
  _normal    tangent space, OpenGL / Y+ (green = +V = image up), BC1 with
             X/Y/Z in R/G/B: correct whether the shader normalises RGB or
             rebuilds Z from RG (BC5 only works for the latter; opt-in via
             --normal-format bc5)
  _specular  linear, R = smoothness, G = ambient occlusion, B = metalness

Tiling materials: 1024 px = 1 m (radiatorCore: 1024 px = 0.25 m), seamless.
Every noise field is generated in the Fourier domain or with wrapped
distances, so all maps tile exactly.
The mod box-projects them at 1 UV = 1 m, so anything wider than ~5 cm reads
as a blotch: dirt is kept to fine grain, specks, scratches and chips, and the
remaining large-scale luminance / smoothness variation is capped (LF_*_MAX).

Usage: gen_textures.py [--only a,b] [--normal-format bc1|bc5] [--no-dds]
                       [--png-dir DIR] [--dds-dir DIR] [--sheet PATH]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True  # keep tools/textures free of __pycache__
import png2dds  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
N = 1024
# variation that survives a 15 mm gaussian (features > ~5 cm) is limited to these p1..p99 spans
LOWPASS_MM = 15.0
LF_LUM_MAX = 0.04     # relative to mean linear luminance
LF_SMOOTH_MAX = 0.04  # absolute smoothness
LUM = np.array([0.2126, 0.7152, 0.0722])

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/opentype/urw-base35",
             "/usr/share/fonts/truetype/freefont"]


def font(name, size):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.truetype("DejaVuSans.ttf", size)


# ============================================================================
# periodic noise toolkit


def _freqs(h, w):
    fy = np.fft.fftfreq(h)[:, None] * h  # cycles per tile
    fx = np.fft.rfftfreq(w)[None, :] * w
    return fx, fy


def _spectral(rng, shape, filt):
    white = rng.standard_normal(shape)
    out = np.fft.irfft2(np.fft.rfft2(white) * filt, s=shape)
    out -= out.mean()
    return out / max(out.std(), 1e-12)


def _feff(fx, fy, elong=1.0, angle=0.0):
    """Effective radial frequency; elong > 1 stretches features along `angle` (0 = image x)."""
    if angle:
        c, s = math.cos(angle), math.sin(angle)
        fx, fy = fx * c + fy * s, -fx * s + fy * c
    return np.sqrt((fx * elong) ** 2 + fy ** 2)


def band(rng, fc, bw=1.0, shape=(N, N), elong=1.0, angle=0.0):
    """Band-limited noise around fc cycles/tile, log-gaussian width bw octaves."""
    fx, fy = _freqs(*shape)
    f = _feff(fx, fy, elong, angle)
    with np.errstate(divide="ignore"):
        lf = np.log2(np.maximum(f, 1e-9) / fc)
    filt = np.exp(-0.5 * (lf / (bw * 0.5)) ** 2)
    filt[f == 0] = 0.0
    return _spectral(rng, shape, filt)


def fractal(rng, fmin, fmax, beta=2.0, shape=(N, N), elong=1.0, angle=0.0):
    """Power-law (1/f^beta) noise between fmin and fmax cycles/tile, unit std."""
    fx, fy = _freqs(*shape)
    f = _feff(fx, fy, elong, angle)
    fs = np.maximum(f, 1e-9)
    filt = fs ** (-beta / 2.0)
    filt *= 1.0 / (1.0 + (fmin / fs) ** 8) / (1.0 + (fs / fmax) ** 8)
    filt[f == 0] = 0.0
    return _spectral(rng, shape, filt)


def blur(img, sigma, axis=None):
    """Periodic gaussian blur (sigma in px); axis=0/1 blurs rows/cols only."""
    if sigma <= 0:
        return img
    h, w = img.shape[:2]
    fx, fy = _freqs(h, w)
    fxp, fyp = fx / w, fy / h  # cycles per px
    if axis == 0:
        g = np.exp(-2 * (math.pi * sigma) ** 2 * fyp ** 2) * np.ones_like(fxp)
    elif axis == 1:
        g = np.exp(-2 * (math.pi * sigma) ** 2 * fxp ** 2) * np.ones_like(fyp)
    else:
        g = np.exp(-2 * (math.pi * sigma) ** 2 * (fxp ** 2 + fyp ** 2))
    if img.ndim == 3:
        return np.stack([np.fft.irfft2(np.fft.rfft2(img[:, :, i]) * g, s=(h, w)) for i in range(img.shape[2])], 2)
    return np.fft.irfft2(np.fft.rfft2(img) * g, s=(h, w))


def smear_down(img, length):
    """One-sided exponential smear toward +row (image down), periodic."""
    h, w = img.shape
    k = np.zeros(h)
    r = np.arange(h)
    k[:] = np.exp(-r / max(length, 1e-3))
    k[r > 6 * length] = 0
    k /= k.sum()
    K = np.fft.rfft(k)
    return np.fft.irfft(np.fft.rfft(img, axis=0) * K[:, None], n=h, axis=0)


def sstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def splat(rng, count, sigma, shape=(N, N), weights=None):
    """Sum of periodic gaussian dots at random positions (peak ~1)."""
    h, w = shape
    img = np.zeros(shape)
    ys = rng.integers(0, h, count)
    xs = rng.integers(0, w, count)
    wt = np.ones(count) if weights is None else weights
    np.add.at(img, (ys, xs), wt)
    if sigma > 0:
        img = blur(img, sigma) * (2 * math.pi * sigma ** 2)
    return img


def worley(rng, cells, shape=(N, N), jitter=0.9):
    """Periodic cellular noise: (F1, F2, cell id) with distances in px."""
    h, w = shape
    gy, gx = cells if isinstance(cells, tuple) else (cells, cells)
    ch, cw = h / gy, w / gx
    jy = rng.random((gy, gx)) * jitter + (1 - jitter) / 2
    jx = rng.random((gy, gx)) * jitter + (1 - jitter) / 2
    ids = rng.random((gy, gx))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    cy = np.floor(yy / ch).astype(int)
    cx = np.floor(xx / cw).astype(int)
    f1 = np.full(shape, 1e9)
    f2 = np.full(shape, 1e9)
    idm = np.zeros(shape)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            ny, nx = cy + dy, cx + dx
            my, mx = ny % gy, nx % gx
            py = (ny + jy[my, mx]) * ch
            px = (nx + jx[my, mx]) * cw
            d = np.hypot(yy - py, xx - px)
            closer = d < f1
            f2 = np.where(closer, f1, np.minimum(f2, d))
            idm = np.where(closer, ids[my, mx], idm)
            f1 = np.where(closer, d, f1)
    return f1, f2, idm


def strokes(rng, count, length, width, shape=(N, N), curve=0.15, angle=None, spread=math.pi,
            ss=2, values=None):
    """Periodic mask of random (slightly curved) strokes, values in 0..1."""
    h, w = shape
    im = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(im)
    for i in range(count):
        L = rng.uniform(*length) * ss
        a = (rng.uniform(0, 2 * math.pi) if angle is None else angle + rng.uniform(-spread, spread) / 2)
        x0, y0 = rng.uniform(0, w * ss), rng.uniform(0, h * ss)
        bend = rng.uniform(-curve, curve)
        pts = []
        for k in range(9):
            t = k / 8.0
            aa = a + bend * (t - 0.5) * 2
            pts.append((x0 + math.cos(aa) * L * t, y0 + math.sin(aa) * L * t))
        wd = max(1, int(round(rng.uniform(*width) * ss)))
        v = int(255 * (rng.uniform(0.35, 1.0) if values is None else values[i]))
        for oy in (-h * ss, 0, h * ss):
            for ox in (-w * ss, 0, w * ss):
                d.line([(x + ox, y + oy) for x, y in pts], fill=v, width=wd, joint="curve")
    im = im.resize((w, h), Image.BOX)
    return np.asarray(im, dtype=np.float64) / 255.0


def srgb(r, g, b):
    return png2dds.srgb_to_linear(np.array([r, g, b], dtype=np.float64) / 255.0)


def mix(a, b, t):
    t = np.asarray(t)
    if t.ndim == 2:
        t = t[:, :, None]
    return a + (b - a) * t


def fill(col, shape=(N, N)):
    return np.broadcast_to(col, shape + (3,)).copy()


# ============================================================================
# output helpers


class Out:
    def __init__(self, args):
        self.args = args
        self.png_dir = args.png_dir
        self.dds_dir = args.dds_dir
        os.makedirs(self.png_dir, exist_ok=True)
        os.makedirs(self.dds_dir, exist_ok=True)
        self.report = []
        self.diffuse_previews = []  # (name, PIL image, tiling)
        self.lowfreq = []  # (name, lum span before, after, smoothness span before, after)
        self.normal_err = []  # (name, fmt, verify dict with angle_* entries)

    def _dds(self, name, levels, fmt, src_levels=None, normal=False):
        if self.args.no_dds:
            return
        path = os.path.join(self.dds_dir, name + ".dds")
        size = png2dds.save_dds(path, None, fmt, levels=levels, refine=normal)
        r = png2dds.verify(path, levels if src_levels is None else src_levels, fmt, normal=normal)
        self.report.append((name, fmt, levels[0].shape[1], levels[0].shape[0], r["mips"], size,
                            r["psnr_own"], r.get("psnr_pillow", float("nan")), r["psnr_worst_mip"]))
        if normal:
            self.normal_err.append((name, fmt, r))

    def normal_dds(self, name, nrgb):
        """Normal map DDS from the RGB preview (renormalised mips); BC1 keeps Z in blue."""
        nlev = png2dds.build_mips(nrgb, mode="normal")
        if self.args.normal_format == "bc5":
            self._dds(name, [lv[:, :, :2] for lv in nlev], "bc5", src_levels=nlev, normal=True)
        else:
            self._dds(name, nlev, "bc1", normal=True)

    def png(self, name, arr):
        a = np.clip(np.rint(arr), 0, 255).astype(np.uint8)
        Image.fromarray(a).save(os.path.join(self.png_dir, name + ".png"), optimize=False)
        return a

    def color(self, name, rgb_srgb01, alpha=None, tiling=False, coverage=None):
        """rgb in sRGB 0..1 (HxWx3); alpha 0..1 or None."""
        rgb = rgb_srgb01 * 255.0
        if alpha is not None:
            img = np.concatenate([rgb, alpha[:, :, None] * 255.0], axis=2)
            fmt = "bc3"
        else:
            img, fmt = rgb, "bc1"
        a = self.png(name, img)
        self.diffuse_previews.append((name, Image.fromarray(a), tiling))
        levels = png2dds.build_mips(a.astype(np.float64), mode="srgb", alpha_coverage=coverage)
        self._dds(name, levels, fmt)

    def material(self, name, albedo_lin, height_mm, smooth, ao, metal, px_mm, bump=1.0,
                 lf_lum=LF_LUM_MAX, lf_smooth=LF_SMOOTH_MAX):
        """Write <name>_diffuse/_normal/_specular from linear albedo and a height field."""
        albedo_lin, lum0 = limit_lowfreq(albedo_lin, px_mm, lf_lum)
        smooth, sm0 = limit_lowfreq_scalar(np.clip(smooth, 0, 1), px_mm, lf_smooth)
        self.lowfreq.append((name, lum0, min(lum0, lf_lum), sm0, min(sm0, lf_smooth)))
        diff = png2dds.linear_to_srgb(albedo_lin)
        self.color(name + "_diffuse", diff, tiling=True)

        h = height_mm * bump / px_mm  # height in px units -> slopes are dimensionless
        gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5
        gr = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5
        n = np.stack([-gx, gr, np.ones_like(h)], axis=2)  # OpenGL/Y+: green = +V = image up
        n /= np.linalg.norm(n, axis=2, keepdims=True)
        nrgb = self.png(name + "_normal", (n * 0.5 + 0.5) * 255.0).astype(np.float64)
        self.normal_dds(name + "_normal", nrgb)

        spec = np.stack([np.clip(smooth, 0, 1), np.clip(ao, 0, 1), np.clip(metal, 0, 1)], axis=2)
        s8 = self.png(name + "_specular", spec * 255.0).astype(np.float64)
        slev = png2dds.build_mips(s8, mode="linear")
        # Toksvig: lower smoothness where the normal map averages out in a mip
        nvec = nrgb / 127.5 - 1.0
        for i in range(1, len(slev)):
            nvec = png2dds._downsample(nvec)
            ln = np.clip(np.linalg.norm(nvec, axis=2), 1e-4, 1.0)
            var = (1.0 - ln) / ln
            rough = 1.0 - slev[i][:, :, 0] / 255.0
            a2 = np.clip(rough ** 4 + var, 0, 1)  # alpha = rough^2
            slev[i][:, :, 0] = (1.0 - a2 ** 0.25) * 255.0
        self._dds(name + "_specular", slev, "bc1")


# ============================================================================
# shared surface layers


def orange_peel(rng, px_mm, amp=0.004, wl_mm=3.0):
    fc = (N * px_mm) / wl_mm
    return band(rng, fc, 1.2) * amp + band(rng, fc * 0.45, 1.0) * amp * 0.6


def film(rng, amt, var=0.25):
    """Near-uniform film (dust, soot): only fine (< ~2 cm) modulation, no patches."""
    v = 0.6 * band(rng, 90, 1.4) + 0.4 * band(rng, 260, 1.4)
    return amt * np.clip(1.0 + var * v, 0.0, None)


def lowfreq_span(v, px_mm, relative=True):
    """p1..p99 span of the > ~5 cm component of v (relative to the mean if requested)."""
    lo = blur(v, LOWPASS_MM / px_mm)
    p1, p99 = np.percentile(lo, [1, 99])
    return (p99 - p1) / (v.mean() if relative else 1.0), lo


def limit_lowfreq(lin, px_mm, target):
    """Compress large-scale luminance variation of linear RGB to `target`; fine detail is kept."""
    y = lin @ LUM
    span, lo = lowfreq_span(y, px_mm)
    if span <= target:
        return lin, span
    m = y.mean()
    gain = (m + (lo - m) * (target / span)) / np.maximum(lo, 1e-6)
    return lin * gain[:, :, None], span


def limit_lowfreq_scalar(v, px_mm, target):
    span, lo = lowfreq_span(v, px_mm, relative=False)
    if span <= target:
        return v, span
    return v - (lo - lo.mean()) * (1.0 - target / span), span


def match_mean(lin, target_srgb):
    """Scale channels so the linear-light mean (what distant mips show) equals target_srgb."""
    return lin * (srgb(*target_srgb) / np.maximum(lin.reshape(-1, 3).mean(0), 1e-6))


def blobs(rng, counts_sigmas, lo, hi, rough=0.45, scale=None):
    """Sparse irregular blobs (chips, splashes, pits) from gaussian splats."""
    field = np.zeros((N, N))
    for cnt, s in counts_sigmas:
        field = np.maximum(field, np.clip(splat(rng, cnt, s), 0, 1.5))
    if scale is not None:
        field = field * scale
    field = field * (1 + rough * band(rng, 300, 1.4))
    return sstep(lo, hi, field), field


def chips(rng, count):
    """Paint chips: (primer ring, exposed core) masks."""
    per = max(1, count // 3)
    core, field = blobs(rng, [(per, 0.9), (per, 1.6), (count - 2 * per, 2.6)], 0.62, 0.70, rough=0.25)
    ring = np.clip(sstep(0.36, 0.46, field) - core, 0, 1)
    return ring, core


def specks(rng, count, sigma=0.55, lo=0.1, hi=0.6):
    return np.clip(splat(rng, count, sigma, weights=rng.uniform(lo, hi, count)), 0, 1)


# ============================================================================
# tiling materials (1 tile = 1 m unless noted)


def mat_paint(out, name, seed, base_srgb, fade_srgb, fade_amt, primer_srgb, rust_srgb, smooth0,
              dust_srgb, dust_amt, hair_n, hair_dark, chip_n, rain=0.0, extra=None):
    """Used-but-maintained enamel: even fade and dust film, fine mottling, hairlines, a few chips."""
    rng = np.random.default_rng(seed)
    px = 1000.0 / N
    lf = fractal(rng, 1, 8, 2.6)
    mf = band(rng, 45, 1.6)
    hf = band(rng, 230, 1.5)
    fade = fade_amt * (0.55 + 0.45 * sstep(-2.2, 2.2, 0.3 * lf + 0.9 * mf))
    col = mix(fill(srgb(*base_srgb)), fill(srgb(*fade_srgb)), fade)
    col *= (1 + 0.018 * mf + 0.010 * hf)[:, :, None]

    dust = film(rng, dust_amt)
    sp = specks(rng, 1800)
    hair = strokes(rng, hair_n, (12, 120), (0.45, 0.8), curve=0.35)
    deep = strokes(rng, max(3, hair_n // 30), (20, 90), (1.0, 1.5), curve=0.2)
    ring, core = chips(rng, chip_n)
    rusty = core * sstep(-0.2, 0.6, band(rng, 20, 1.0))

    if hair_dark:
        col = mix(col, col * 0.78, hair * 0.3)
    else:
        col = mix(col, np.minimum(col * 1.3 + 0.01, 1), hair * 0.22)
    primer = fill(srgb(*primer_srgb))
    col = mix(col, primer, np.clip(deep * 0.8 + ring, 0, 1))
    col = mix(col, primer * 0.8, np.clip(core - rusty, 0, 1))
    col = mix(col, fill(srgb(*rust_srgb)), rusty)
    rainm = 0.0
    if rain > 0:
        streak = sstep(1.3, 2.6, band(rng, 70, 1.3, elong=12, angle=math.pi / 2))
        rainm = streak * (0.7 + 0.3 * sstep(-1.5, 1.5, band(rng, 40, 1.2))) * rain
        col = mix(col, col * 0.9 + srgb(110, 104, 92) * 0.08, rainm)
    col = mix(col, fill(srgb(*dust_srgb)), np.clip(dust + sp * 0.22, 0, 1))
    ex_h, ex_s = 0.0, 0.0
    if extra is not None:
        col, ex_h, ex_s = extra(rng, col)

    h = orange_peel(rng, px) - hair * 0.004 - deep * 0.015
    h = h - blur(core + ring * 0.5, 0.6) * 0.035 + sp * 0.004 + ex_h
    smooth = (smooth0 + 0.025 * mf - 0.3 * fade - 0.35 * dust - 0.15 * sp - 0.14 * hair - 0.2 * deep
              - 0.3 * (core + ring) - 0.1 * rainm + ex_s)
    ao = 1.0 - 0.2 * core - 0.08 * deep - 0.04 * hair
    out.material(name, col, h, smooth, ao, np.zeros_like(smooth), px, bump=3.0)


def _cream_extra(rng, col):
    """Small dried mud splashes and rust freckles on wheel rims, evenly scattered (no clusters)."""
    mud, _ = blobs(rng, [(120, 0.8), (60, 1.4), (20, 2.2)], 0.5, 0.62)
    col = mix(col, fill(srgb(142, 120, 90)), mud * 0.75)
    fr, _ = blobs(rng, [(40, 0.9)], 0.5, 0.75, rough=0.3)
    streak = np.clip(smear_down(fr, 8) * 6.0, 0, 1)
    col = mix(col, fill(srgb(134, 74, 38)), np.clip(fr * 0.7 + streak * 0.25, 0, 1))
    return col, mud * 0.04 + fr * 0.01, -0.3 * mud - 0.25 * fr


def mat_paint_grey(out):
    """Medium-grey engine/gearbox casting paint: sand-cast grain, fine grime in the pores, thin
    dust, small chips and scuffs. Mean colour pinned to sRGB ~110,114,112 (close-up reference)."""
    rng = np.random.default_rng(202)
    px = 1000.0 / N
    cast = band(rng, 330, 1.3) * 0.022 + band(rng, 130, 1.0) * 0.02 + band(rng, 45, 1.2) * 0.015
    pits, _ = blobs(rng, [(500, 0.7), (150, 1.2)], 0.5, 0.8)
    cast = cast - pits * 0.06
    cav = np.clip(-(cast - blur(cast, 2.5)) * 14.0, 0, 1)
    grime = sstep(0.2, 2.2, band(rng, 110, 1.5) + 0.6 * band(rng, 300, 1.4))
    dust = film(rng, 0.07)
    mott = band(rng, 60, 1.5)
    grain = band(rng, 280, 1.4)
    hair = strokes(rng, 110, (8, 60), (0.45, 0.8), curve=0.4)
    ring, core = chips(rng, 45)
    col = fill(srgb(110, 114, 112)) * (1 + 0.02 * mott + 0.03 * grain)[:, :, None]
    col = mix(col, fill(srgb(52, 48, 43)), np.clip(0.18 * grime + 0.4 * cav, 0, 1))
    col = mix(col, fill(srgb(128, 120, 104)), dust)
    col = mix(col, fill(srgb(38, 36, 34)), pits * 0.45)
    col = mix(col, np.minimum(col * 1.25 + 0.01, 1), hair * 0.3)
    col = mix(col, fill(srgb(146, 144, 138)), ring * 0.7)
    col = mix(col, fill(srgb(78, 62, 50)), core)
    col = match_mean(col, (110, 114, 112))
    h = cast - hair * 0.004 - blur(core + ring * 0.5, 0.6) * 0.03
    smooth = (0.42 + 0.08 * grime - 0.25 * dust - 0.1 * pits - 0.12 * hair - 0.25 * (core + ring)
              + 0.02 * mott)
    ao = 1.0 - 0.3 * cav - 0.25 * pits - 0.15 * core
    out.material("paintGrey", col, h, smooth, ao, np.zeros_like(smooth), px, bump=3.5)


def mat_black_metal(out):
    """Satin black paint / plastic (steering wheel, levers, small parts)."""
    rng = np.random.default_rng(505)
    px = 1000.0 / N
    lf = fractal(rng, 1, 10, 2.4)
    mf = band(rng, 50, 1.5)
    col = fill(srgb(31, 31, 32)) * (1 + 0.008 * lf + 0.03 * mf)[:, :, None]
    worn = sstep(-1.0, 2.5, band(rng, 40, 1.4))
    col = mix(col, col * 1.2, worn * 0.3)
    hair = strokes(rng, 260, (10, 100), (0.45, 0.8), curve=0.4)
    col = mix(col, fill(srgb(84, 84, 84)), hair * 0.25)
    dust = film(rng, 0.06)
    sp = specks(rng, 2500)
    col = mix(col, fill(srgb(118, 110, 98)), np.clip(dust + sp * 0.35, 0, 1))
    h = band(rng, 380, 1.3) * 0.0025 + orange_peel(rng, px, 0.003, 4.0) - hair * 0.004 + sp * 0.004
    smooth = 0.50 + 0.12 * worn - 0.35 * dust - 0.15 * sp - 0.12 * hair + 0.02 * mf
    out.material("blackMetal", col, h, smooth, 1.0 - 0.04 * hair, np.zeros_like(smooth), px, bump=3.0)


def mat_rubber(out):
    """Tyre rubber: uniform dark compound, fine grain, very faint dust, a few tiny cuts."""
    rng = np.random.default_rng(606)
    px = 1000.0 / N
    micro = band(rng, 420, 1.2)
    grain = band(rng, 160, 1.3)
    col = fill(srgb(40, 40, 41)) * (1 + 0.035 * micro + 0.025 * grain)[:, :, None]
    dust = film(rng, 0.045) * sstep(-1.5, 1.0, micro)  # dust settles in the micro texture
    sp = specks(rng, 2500, 0.55, 0.1, 0.5)
    col = mix(col, fill(srgb(112, 100, 84)), np.clip(dust + sp * 0.25, 0, 1))
    cuts = strokes(rng, 25, (4, 16), (0.6, 1.0), curve=0.3)
    col = mix(col, col * 0.6, cuts)
    h = micro * 0.005 + grain * 0.006 - cuts * 0.08 + sp * 0.006
    smooth = 0.30 + 0.03 * grain - 0.2 * dust - 0.1 * sp
    ao = 1.0 - 0.3 * cuts
    out.material("rubberTyre", col, h, smooth, ao, np.zeros_like(smooth), px, bump=4.0)


def mat_steel(out):
    """Turned/brushed bare steel (PTO stub, pins) with oily grime and flash rust."""
    rng = np.random.default_rng(707)
    px = 1000.0 / N
    # machining lines run along U (tile x); several scales so they read at any distance
    brush = (band(rng, 480, 1.6, elong=60) * 0.5 + band(rng, 220, 1.6, elong=40) * 0.35
             + band(rng, 70, 1.4, elong=20) * 0.25)
    lf = fractal(rng, 1, 12, 2.4)
    col = fill(srgb(168, 166, 162)) * (1 + 0.09 * brush + 0.008 * lf)[:, :, None]
    grime = 0.2 * sstep(-1.0, 2.6, band(rng, 50, 1.4) + 0.25 * band(rng, 150, 1.4))
    col = mix(col, fill(srgb(76, 68, 58)), grime)
    fine = band(rng, 360, 1.3)
    # flash rust: sparse, soft, drawn out along the machining lines (no confetti dots)
    rn = band(rng, 70, 1.5, elong=3.0) + 0.35 * band(rng, 160, 1.4, elong=2.0)
    rust = sstep(1.9, 3.1, rn) * (0.7 + 0.3 * sstep(-1.5, 1.5, fine)) * 0.7
    col = mix(col, fill(srgb(150, 140, 128)), 0.06)  # faint overall tarnish
    rcol = mix(fill(srgb(122, 70, 38)), fill(srgb(86, 52, 34)), sstep(-1.0, 1.0, band(rng, 60, 1.5)))
    col = mix(col, rcol * (1 + 0.06 * fine)[:, :, None], rust)
    h = brush * 0.004 + rust * (0.012 + fine * 0.008)
    smooth = 0.70 + 0.06 * brush - 0.22 * grime - 0.45 * rust
    metal = np.clip(1.0 - 1.1 * rust - 0.6 * grime, 0, 1)
    out.material("steelBare", col, h, smooth, 1.0 - 0.08 * rust, metal, px, bump=2.5)


def mat_exhaust(out):
    """Black exhaust pipe: heat-darkened sooty steel, black-brown, with only a faint rust tint."""
    rng = np.random.default_rng(808)
    px = 1000.0 / N
    fine = band(rng, 320, 1.3)
    mid = band(rng, 80, 1.5)
    small = band(rng, 30, 1.2)
    col = fill(srgb(42, 36, 32)) * (1 + 0.07 * fine + 0.04 * mid)[:, :, None]
    rust = sstep(0.8, 2.4, mid + 0.6 * fine) * 0.4
    col = mix(col, fill(srgb(72, 49, 36)), rust)
    heat = sstep(0.6, 2.2, small) * 0.3  # faint bluish heat tint
    col = mix(col, fill(srgb(46, 44, 50)), heat)
    soot = film(rng, 0.4)
    col = mix(col, fill(srgb(25, 24, 23)), soot)
    flake = sstep(1.5, 1.62, band(rng, 60, 1.6) + 0.35 * band(rng, 260, 1.4))  # heat-paint remnants
    col = mix(col, fill(srgb(30, 30, 31)), flake)
    pits = blobs(rng, [(1500, 0.7)], 0.55, 0.8)[0] * (0.4 + rust)
    col = mix(col, fill(srgb(22, 20, 19)), pits * 0.5)
    h = fine * 0.012 + mid * 0.008 + blur(flake, 0.7) * 0.04 - pits * 0.04
    smooth = 0.24 + 0.12 * flake - 0.05 * rust - 0.06 * soot
    metal = np.clip(0.2 * (1 - rust) * (1 - flake) * (1 - soot), 0, 1)
    out.material("exhaustRust", col, h, smooth, 1.0 - 0.3 * pits, metal, px, bump=4.0)


def mat_vinyl(out):
    rng = np.random.default_rng(909)
    px = 1000.0 / N
    f1, f2, cid = worley(rng, 240)
    grain = sstep(0.0, 2.2, f2 - f1)  # 1 on the grain cells, 0 in the grooves
    worn = sstep(0.6, 2.4, band(rng, 22, 1.3) + 0.15 * band(rng, 180, 1.5)) * 0.4
    creases = blur(strokes(rng, 22, (80, 300), (3.0, 6.0), curve=0.6, values=rng.uniform(0.3, 0.9, 22)), 2.0)
    col = fill(srgb(47, 42, 39)) * (1 + 0.05 * (cid - 0.5) + 0.03 * band(rng, 30, 1.2))[:, :, None]
    col = mix(col, fill(srgb(62, 57, 52)), worn * 0.7)
    col = mix(col, col * 0.75, creases * 0.5)
    col = mix(col, fill(srgb(90, 84, 76)), (1 - grain) * 0.14 * (1 - worn))
    h = grain * 0.06 * (1 - 0.6 * worn) - creases * 0.2 + band(rng, 25, 1.0) * 0.04
    smooth = 0.38 + 0.16 * worn - 0.08 * (1 - grain) + 0.02 * band(rng, 60, 1.2)
    ao = 1.0 - 0.16 * (1 - grain) * (1 - worn) - 0.18 * creases
    out.material("seatVinyl", col, h, smooth, ao, np.zeros_like(smooth), px, bump=4.0)


def mat_radiator(out):
    """Plate-fin radiator core seen from the front; 1 tile = 0.25 m."""
    rng = np.random.default_rng(1010)
    px = 250.0 / N
    yy, xx = np.mgrid[0:N, 0:N].astype(np.float64)
    fins, tubes = 100, 22  # ~2.5 mm fin pitch, ~11 mm tube pitch
    bent = sstep(1.6, 2.6, band(rng, 10, 1.0))
    ph = yy * fins / N + 0.012 * band(rng, 8, 1.0) + 0.2 * band(rng, 24, 1.0) * bent
    d_fin = np.abs(ph - np.round(ph)) * (N / fins)
    fin = sstep(1.3, 0.35, d_fin)
    fin_tone = 1 + 0.12 * (rng.random(fins)[np.round(ph).astype(int) % fins] - 0.5)
    tx = xx * tubes / N
    d_tube = np.abs(tx - np.round(tx)) * (N / tubes)
    tube = sstep(6.5, 4.5, d_tube)
    fluff = sstep(0.8, 2.6, band(rng, 40, 1.4)) * (0.6 + 0.4 * sstep(-1.0, 1.0, band(rng, 220, 1.5)))
    col = mix(fill(srgb(8, 8, 8)), fill(srgb(27, 27, 27)), tube)
    fincol = fill(srgb(52, 52, 51)) * ((1 + 0.08 * band(rng, 200, 1.2)) * fin_tone)[:, :, None]
    col = mix(col, fincol, fin)
    col = mix(col, fill(srgb(96, 88, 72)), fluff * 0.16 * (1 - fin * 0.5))
    col = mix(col, fill(srgb(92, 86, 74)), fin * film(rng, 0.2))
    prof = np.cos(np.clip(d_fin / 1.6, 0, 1) * math.pi / 2)
    h = prof * 0.10 + tube * (1 - fin) * 0.05 + fluff * 0.03
    smooth = 0.10 + 0.28 * fin + 0.15 * tube * (1 - fin) - 0.08 * fluff
    ao = np.clip(0.28 + 0.72 * fin + 0.35 * tube * (1 - fin) + 0.2 * fluff, 0, 1)
    out.material("radiatorCore", col, h, smooth, ao, np.zeros_like(smooth), px)


def glass(out):
    rng = np.random.default_rng(1111)
    tint = srgb(198, 210, 206)
    dust = sstep(0.2, 2.2, fractal(rng, 2, 30, 2.2))
    st = band(rng, 70, 1.4, elong=18, angle=math.pi / 2)
    streak = sstep(1.2, 2.4, st) * sstep(-0.3, 1.2, fractal(rng, 2, 12, 2.0))
    sp = np.clip(splat(rng, 3000, 0.7, weights=rng.uniform(0.3, 1.0, 3000)), 0, 1)
    col = mix(fill(tint), fill(srgb(178, 168, 148)), np.clip(dust * 0.6 + sp, 0, 1))
    col = mix(col, fill(srgb(214, 214, 206)), streak * 0.5)
    alpha = np.clip(0.19 + 0.06 * dust + 0.035 * streak + 0.2 * sp, 0.18, 0.30)
    out.color("glass_diffuse", png2dds.linear_to_srgb(col), alpha=alpha, tiling=True)
    smooth = 0.94 - 0.22 * dust - 0.1 * streak - 0.3 * sp
    spec = np.stack([smooth, np.ones_like(smooth), np.zeros_like(smooth)], 2)
    s8 = out.png("glass_specular", spec * 255.0).astype(np.float64)
    out._dds("glass_specular", png2dds.build_mips(s8, mode="linear"), "bc1")


# ============================================================================
# lenses (256x256, UV 0..1 over the lens face)


def lens_pattern(n=256):
    yy, xx = (np.mgrid[0:n, 0:n] + 0.5) / n
    u, v = xx - 0.5, 0.5 - yy
    r = np.hypot(u, v)
    cols, rows = 16, 6
    row = np.floor(yy * rows)
    cu = (xx * cols + 0.5 * (row % 2)) % 1.0  # brick-offset flutes like period headlamps
    cv = (yy * rows) % 1.0
    xc = (cu - 0.5) * 2  # -1..1 across a flute
    flute = np.sqrt(np.clip(1 - xc ** 2, 0, 1))
    border = sstep(0.07, 0.0, np.minimum(cv, 1 - cv)) + 0.5 * sstep(0.06, 0.0, np.minimum(cu, 1 - cu))
    border = np.clip(border, 0, 1)
    rings = 0.5 + 0.5 * np.cos(r * 2 * math.pi * 26)
    center = sstep(0.15, 0.13, r)
    rim = sstep(0.445, 0.47, r)
    h = flute * 0.5 * (1 - center) * (1 - rim) - border * 0.2 * (1 - center) * (1 - rim)
    h += center * rings * 0.25 + rim * (0.6 + 0.4 * np.cos((r - 0.44) / 0.06 * math.pi))
    return h, flute, border, center, rings, rim


def lenses(out):
    n = 256
    h, flute, border, center, rings, rim = lens_pattern(n)
    hs = blur(h, 0.6) * 3.0
    gx = (np.roll(hs, -1, 1) - np.roll(hs, 1, 1)) * 0.5
    gr = (np.roll(hs, -1, 0) - np.roll(hs, 1, 0)) * 0.5
    nn = np.stack([-gx, gr, np.ones_like(hs)], 2)
    nn /= np.linalg.norm(nn, axis=2, keepdims=True)
    nrgb = out.png("lens_normal", (nn * 0.5 + 0.5) * 255).astype(np.float64)
    out.normal_dds("lens_normal", nrgb)

    hl = np.clip(flute ** 10 * (1 - center) * (1 - rim) + center * rings ** 6 * 0.5, 0, 1)
    specs = {
        "lensClear_diffuse": ((222, 226, 228), (250, 252, 252), (160, 166, 172), 0.30, 0.55),
        "lensRed_diffuse": ((150, 12, 9), (205, 48, 36), (84, 4, 3), 0.86, 0.95),
        "lensOrange_diffuse": ((224, 104, 12), (250, 164, 64), (150, 58, 4), 0.86, 0.95),
    }
    for name, (base, hi, edge, a0, a1) in specs.items():
        col = fill(srgb(*base), (n, n))
        col = mix(col, fill(srgb(*hi), (n, n)), hl * 0.6)
        col = mix(col, fill(srgb(*edge), (n, n)), np.clip(border * (1 - center) * (1 - rim) * 0.7 + rim * 0.45, 0, 1))
        alpha = np.clip(a0 + (a1 - a0) * np.clip(border * 0.7 + rim + 0.3 * hl, 0, 1), 0, 1)
        out.color(name, png2dds.linear_to_srgb(col), alpha=alpha)


# ============================================================================
# squarish 1970s badge lettering (Eurostile-like), drawn as filleted strokes


def _fillet(path, radii, samples=10):
    """Replace interior corners of a polyline with circular arcs."""
    pts = [np.asarray(path[0], float)]
    for i in range(1, len(path) - 1):
        p0, p1, p2 = (np.asarray(path[j], float) for j in (i - 1, i, i + 1))
        r = radii[i] if isinstance(radii, (list, tuple)) else radii
        u1 = (p1 - p0) / np.linalg.norm(p1 - p0)
        u2 = (p2 - p1) / np.linalg.norm(p2 - p1)
        cosang = np.clip(np.dot(u1, u2), -1, 1)
        phi = math.acos(cosang)
        if r <= 0 or phi < 1e-3:
            pts.append(p1)
            continue
        d = r * math.tan(phi / 2)
        t1, t2 = p1 - u1 * d, p1 + u2 * d
        cross = u1[0] * u2[1] - u1[1] * u2[0]
        nrm = np.array([-u1[1], u1[0]]) * (1 if cross > 0 else -1)
        c = t1 + nrm * r
        a0 = math.atan2(t1[1] - c[1], t1[0] - c[0])
        a1 = math.atan2(t2[1] - c[1], t2[0] - c[0])
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        for k in range(samples + 1):
            a = a0 + da * k / samples
            pts.append(c + r * np.array([math.cos(a), math.sin(a)]))
    pts.append(np.asarray(path[-1], float))
    return [tuple(p) for p in pts]


def glyph_paths(ch, t=0.2, rc=0.3, dash_w=0.5, space_w=0.5):
    """Centreline strokes in em units (cap height 1, y down). Returns (width, [(pts, radii)])."""
    a, top, bot, mid = t / 2, t / 2, 1 - t / 2, 0.5
    r2 = min(rc, (mid - top) / 2 - 0.005)
    if ch == "U":
        w = 1.0
        b = w - a
        return w, [([(a, -0.1), (a, bot), (b, bot), (b, -0.1)], [0, rc, rc, 0])]
    if ch == "C":
        w = 0.98
        b = w - a
        return w, [([(w + 0.1, top), (a, top), (a, bot), (w + 0.1, bot)], [0, rc, rc, 0])]
    if ch == "S":
        w = 1.0
        b = w - a
        return w, [([(w + 0.1, top), (a, top), (a, mid), (b, mid), (b, bot), (-0.1, bot)],
                    [0, r2, r2, r2, r2, 0])]
    if ch == "R":
        w = 1.0
        b = w - a
        return w, [([(a, 1.1), (a, top), (b, top), (b, mid), (a, mid)], [0, 0.04, r2, r2, 0]),
                   ([(a + 0.36, mid), (b + 0.04, 1.15)], [0, 0])]
    if ch == "3":
        w = 0.92
        b = w - a
        return w, [([(-0.1, top), (b, top), (b, bot), (-0.1, bot)], [0, rc, rc, 0]),
                   ([(0.26, mid), (b, mid)], [0, 0])]
    if ch == "6":
        w = 0.92
        b = w - a
        return w, [([(w + 0.1, top), (a, top), (a, bot), (b, bot), (b, mid), (a, mid)],
                    [0, rc, rc, r2, r2, 0])]
    if ch == "0":
        w = 0.92
        b = w - a
        return w, [([((a + b) / 2, top), (b, top), (b, bot), (a, bot), (a, top), ((a + b) / 2, top)],
                    [0, rc, rc, rc, rc, 0])]
    if ch == "-":
        return dash_w, [([(0.0, mid), (dash_w, mid)], [0, 0])]
    if ch == ".":
        return 0.22, [([(0.0, mid), (0.22, mid)], [0, 0])]
    if ch == " ":
        return space_w, []
    raise ValueError(ch)


def badge_text(text, cap_px, t=0.2, tracking=0.18, dash_scale=0.6, ss=4, xscale=1.0, dash_w=0.5,
               space_w=0.5):
    """Render squarish text to an 'L' mask; returns PIL image (height = cap_px).

    xscale condenses the glyphs horizontally while keeping the stroke weight."""
    S = cap_px * ss
    glyphs = []
    for ch in text:
        w, paths = glyph_paths(ch, t, dash_w=dash_w, space_w=space_w)
        tw = t * (dash_scale if ch in "-." else 1.0)
        gw = max(1, int(round(((w - t) * xscale + t) * S)))
        im = Image.new("L", (gw, S), 0)
        d = ImageDraw.Draw(im)
        for pts, radii in paths:
            pts = _fillet(pts, radii)
            _stroke(d, [((t / 2 + (x - t / 2) * xscale) * S, y * S) for x, y in pts], max(1.0, tw * S))
        glyphs.append(im)
    gap = int(round(tracking * S))
    total = sum(g.width for g in glyphs) + gap * (len(glyphs) - 1)
    line = Image.new("L", (total, S), 0)
    x = 0
    for g in glyphs:
        line.paste(g, (x, 0))
        x += g.width + gap
    return line.resize((max(1, total // ss), cap_px), Image.LANCZOS)


def _stroke(d, pts, width, fill=255):
    """Wide polyline: one quad per segment, round interior joins, butt ends.

    PIL's own wide lines leave hairline slivers at the many short joins of a fillet arc."""
    r = width / 2.0
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-9:
            continue
        nx, ny = -(y1 - y0) / seg * r, (x1 - x0) / seg * r
        d.polygon([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], fill=fill)
    for x, y in pts[1:-1]:
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


# ============================================================================
# decals and gauges


def _rounded_mask(w, h, r, inset=0, ss=4):
    im = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(im).rounded_rectangle([inset * ss, inset * ss, (w - inset) * ss - 1, (h - inset) * ss - 1],
                                         radius=r * ss, fill=255)
    return np.asarray(im.resize((w, h), Image.LANCZOS), dtype=np.float64) / 255.0


def _paste_mask(dst, mask_img, x, y):
    m = np.asarray(mask_img, dtype=np.float64) / 255.0
    h, w = m.shape
    sub = dst[y:y + h, x:x + w]
    np.maximum(sub, m[:sub.shape[0], :sub.shape[1]], out=sub)


def decal_hood_strip(out):
    """Bonnet-side emblem: light silver plate, thin dark rim, black '-U-R-S-U-S- C-360'."""
    W, H = 2048, 256
    rng = np.random.default_rng(1201)
    inset, rim_w, margin = 6, 7, 70
    shape = _rounded_mask(W, H, 34, inset=inset)
    rim = np.clip(shape - _rounded_mask(W, H, 28, inset=inset + rim_w), 0, 1)
    cap = int(H * 0.62)
    while True:  # largest letters that fit between the margins
        txt = badge_text("-U-R-S-U-S- C-360", cap, tracking=0.12, dash_scale=0.9, xscale=0.92,
                         dash_w=0.42, space_w=0.3)
        if txt.width <= W - 2 * margin or cap <= 80:
            break
        cap -= 2
    text = np.zeros((H, W))
    _paste_mask(text, txt, (W - txt.width) // 2, (H - cap) // 2)
    brushed = (band(rng, 300, 1.6, shape=(H, W), elong=30) * 0.6
               + band(rng, 60, 1.4, shape=(H, W), elong=10) * 0.4)
    col = np.broadcast_to(np.array([222, 224, 220]) / 255.0, (H, W, 3)) * (1 + 0.025 * brushed)[:, :, None]
    near_rim = 1 - _rounded_mask(W, H, 30, inset=inset + rim_w + 10)
    col = col * (1 - 0.06 * near_rim * sstep(-1.0, 2.0, band(rng, 40, 1.2, shape=(H, W))))[:, :, None]
    col = mix(col, np.broadcast_to(np.array([38, 38, 38]) / 255.0, (H, W, 3)), rim)
    col = mix(col, np.broadcast_to(np.array([20, 20, 21]) / 255.0, (H, W, 3)), text)
    out.color("decal_hoodStrip", np.clip(col, 0, 1), alpha=shape, coverage=0.5)


def decal_front_logo(out):
    W, H = 1024, 256
    cap = 150
    txt = badge_text("-U-R-S-U-S-", cap, tracking=0.2)
    if txt.width > W - 40:
        txt = txt.resize((W - 40, cap), Image.LANCZOS)
    alpha = np.zeros((H, W))
    _paste_mask(alpha, txt, (W - txt.width) // 2, (H - cap) // 2)
    col = np.full((H, W, 3), 0.93)
    out.color("decal_frontLogo", col, alpha=alpha, coverage=0.5)


def _draw_ring_arc(d, cx, cy, r0, r1, a0, a1, fill, steps=240):
    """Filled annular sector; angles in degrees clockwise from +V (up)."""
    pts = []
    for k in range(steps + 1):
        a = math.radians(a0 + (a1 - a0) * k / steps)
        pts.append((cx + r1 * math.sin(a), cy - r1 * math.cos(a)))
    for k in range(steps, -1, -1):
        a = math.radians(a0 + (a1 - a0) * k / steps)
        pts.append((cx + r0 * math.sin(a), cy - r0 * math.cos(a)))
    d.polygon(pts, fill=fill)


def _tick(d, cx, cy, ang, r0, r1, width, fill):
    a = math.radians(ang)
    d.line([(cx + r0 * math.sin(a), cy - r0 * math.cos(a)), (cx + r1 * math.sin(a), cy - r1 * math.cos(a))],
           fill=fill, width=width)


def _text_c(d, xy, s, fnt, fill):
    x0, y0, x1, y1 = d.textbbox((0, 0), s, font=fnt)
    d.text((xy[0] - (x1 + x0) / 2, xy[1] - (y1 + y0) / 2), s, font=fnt, fill=fill)


def gauge_tacho(out):
    n, ss = 512, 4
    S = n * ss
    c = S / 2
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([c - 250 * ss, c - 250 * ss, c + 250 * ss, c + 250 * ss], fill=(20, 20, 21, 255))
    white = (236, 236, 230, 255)
    a0, a1 = -135.0, 135.0
    ang = lambda v: a0 + (a1 - a0) * v / 30.0  # noqa: E731
    _draw_ring_arc(d, c, c, 231 * ss, 241 * ss, ang(15), ang(22), (46, 160, 72, 255))
    for i in range(0, 61):
        v = i / 2
        if i % 10 == 0:
            _tick(d, c, c, ang(v), 196 * ss, 229 * ss, 7 * ss, white)
        elif i % 2 == 0:
            _tick(d, c, c, ang(v), 210 * ss, 229 * ss, 3 * ss, white)
        else:
            _tick(d, c, c, ang(v), 219 * ss, 229 * ss, 2 * ss, white)
    fnum = font("DejaVuSans-Bold.ttf", 38 * ss)
    for v in range(0, 31, 5):
        a = math.radians(ang(v))
        _text_c(d, (c + 160 * ss * math.sin(a), c - 160 * ss * math.cos(a)), str(v), fnum, white)
    _text_c(d, (c, c - 78 * ss), "x100", font("DejaVuSans-Bold.ttf", 26 * ss), white)
    _text_c(d, (c, c + 70 * ss), "obr/min", font("DejaVuSans.ttf", 30 * ss), white)
    # hour counter window (kept clear of the "30" numeral)
    wx0, wy0, dw, dh = c - 60 * ss, c + 100 * ss, 20 * ss, 32 * ss
    d.rounded_rectangle([wx0 - 6 * ss, wy0 - 6 * ss, wx0 + 6 * dw + 6 * ss, wy0 + dh + 6 * ss], radius=5 * ss,
                        fill=(70, 70, 70, 255))
    fd = font("DejaVuSansMono-Bold.ttf", 25 * ss)
    for k, digit in enumerate("004731"):
        x = wx0 + k * dw
        last = k == 5
        d.rectangle([x + ss, wy0, x + dw - ss, wy0 + dh], fill=(232, 230, 222, 255) if last else (14, 14, 14, 255))
        _text_c(d, (x + dw / 2, wy0 + dh / 2), digit, fd, (14, 14, 14, 255) if last else white)
    _text_c(d, (c, wy0 + dh + 24 * ss), "h", font("DejaVuSans-Bold.ttf", 20 * ss), white)
    im = im.resize((n, n), Image.LANCZOS)
    a = np.asarray(im, dtype=np.float64) / 255.0
    rgb = a[:, :, :3] / np.maximum(a[:, :, 3:4], 1e-6)
    rgb = np.where(a[:, :, 3:4] > 0.01, rgb, 20 / 255.0)
    out.color("gauge_tacho", np.clip(rgb, 0, 1), alpha=a[:, :, 3], coverage=0.5)


def gauge_small(out):
    n, ss = 256, 4
    S = n * ss
    c = S / 2
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([c - 124 * ss, c - 124 * ss, c + 124 * ss, c + 124 * ss], fill=(20, 20, 21, 255))
    white = (236, 236, 230, 255)
    red = (200, 40, 30, 255)
    # upper arc: coolant temperature 40..120 C, needle pointing +V at 80 C
    _draw_ring_arc(d, c, c, 100 * ss, 112 * ss, 37.5, 60, red)
    for k in range(9):
        a = -60 + 15 * k
        _tick(d, c, c, a, (96 if k % 2 == 0 else 104) * ss, 114 * ss, (5 if k % 2 == 0 else 2) * ss, white)
    f = font("DejaVuSans-Bold.ttf", 20 * ss)
    for val, a in ((40, -60), (80, 0), (120, 60)):
        r = 78 * ss
        _text_c(d, (c + r * math.sin(math.radians(a)), c - r * math.cos(math.radians(a))), str(val), f, white)
    _text_c(d, (c, c - 40 * ss), "\u00b0C", font("DejaVuSans-Bold.ttf", 18 * ss), white)
    # lower arc: fuel 0..1, needle pointing -V at 1/2
    _draw_ring_arc(d, c, c, 100 * ss, 112 * ss, 180 + 37.5, 180 + 60, red)
    for k in range(5):
        a = 180 - 60 + 30 * k
        _tick(d, c, c, a, (96 if k % 2 == 0 else 104) * ss, 114 * ss, (5 if k % 2 == 0 else 2) * ss, white)
    for lab, a in (("1", 180 - 60), ("\u00bd", 180), ("0", 180 + 60)):
        r = 78 * ss
        _text_c(d, (c + r * math.sin(math.radians(a)), c - r * math.cos(math.radians(a))), lab, f, white)
    # fuel pump icon
    ix, iy = c - 10 * ss, c + 30 * ss
    d.rectangle([ix, iy, ix + 16 * ss, iy + 20 * ss], outline=white, width=3 * ss)
    d.rectangle([ix + 3 * ss, iy + 3 * ss, ix + 13 * ss, iy + 8 * ss], fill=white)
    d.line([(ix + 16 * ss, iy + 5 * ss), (ix + 22 * ss, iy + 9 * ss), (ix + 22 * ss, iy + 18 * ss)], fill=white,
           width=2 * ss)
    im = im.resize((n, n), Image.LANCZOS)
    a = np.asarray(im, dtype=np.float64) / 255.0
    rgb = np.where(a[:, :, 3:4] > 0.01, a[:, :, :3] / np.maximum(a[:, :, 3:4], 1e-6), 20 / 255.0)
    out.color("gauge_small", np.clip(rgb, 0, 1), alpha=a[:, :, 3], coverage=0.5)


def needle(out):
    w, h, ss = 64, 256, 8
    im = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx, py = 32 * ss, 216 * ss  # pivot
    body = [(cx - 4 * ss, py), (cx - 1.2 * ss, 10 * ss), (cx + 1.2 * ss, 10 * ss), (cx + 4 * ss, py),
            (cx + 5 * ss, py + 34 * ss), (cx - 5 * ss, py + 34 * ss)]
    d.polygon(body, fill=(240, 238, 230, 255))
    d.polygon([(cx - 2.4 * ss, 70 * ss), (cx - 1.2 * ss, 10 * ss), (cx + 1.2 * ss, 10 * ss), (cx + 2.4 * ss, 70 * ss)],
              fill=(236, 110, 24, 255))
    d.ellipse([cx - 13 * ss, py - 13 * ss, cx + 13 * ss, py + 13 * ss], fill=(30, 30, 30, 255))
    d.ellipse([cx - 6 * ss, py - 6 * ss, cx + 6 * ss, py + 6 * ss], fill=(70, 70, 70, 255))
    im = im.resize((w, h), Image.LANCZOS)
    a = np.asarray(im, dtype=np.float64) / 255.0
    rgb = np.where(a[:, :, 3:4] > 0.01, a[:, :, :3] / np.maximum(a[:, :, 3:4], 1e-6), 0.9)
    out.color("needle", np.clip(rgb, 0, 1), alpha=a[:, :, 3], coverage=0.5)


def plate_ursus(out):
    W, H, ss = 512, 256, 4
    rng = np.random.default_rng(1301)
    alpha = _rounded_mask(W, H, 18, inset=3)
    brushed = band(rng, 180, 1.6, shape=(H, W), elong=25) * 0.5 + band(rng, 30, 1.2, shape=(H, W)) * 0.4
    alu = np.full((H, W), 0.74) * (1 + 0.04 * brushed)
    ink = Image.new("L", (W * ss, H * ss), 0)  # 255 = black enamel field
    d = ImageDraw.Draw(ink)
    d.rounded_rectangle([22 * ss, 22 * ss, (W - 22) * ss, (H - 22) * ss], radius=10 * ss, fill=255)
    txt = Image.new("L", (W * ss, H * ss), 0)  # 255 = raised aluminium lettering
    dt = ImageDraw.Draw(txt)
    small = font("NimbusSansNarrow-Bold.otf", 17 * ss)
    mid = font("NimbusSansNarrow-Bold.otf", 21 * ss)
    _text_c(dt, (W * ss / 2, 42 * ss), "ZAK\u0141ADY PRZEMYS\u0141U CI\u0104GNIKOWEGO \u00abURSUS\u00bb", small, 255)
    big = badge_text("URSUS C-360", 60 * ss, tracking=0.16)
    if big.width > (W - 90) * ss:
        big = big.resize(((W - 90) * ss, int(big.height * (W - 90) * ss / big.width)), Image.LANCZOS)
    bx = (W * ss - big.width) // 2
    txt.paste(Image.new("L", big.size, 255), (bx, 60 * ss + (60 * ss - big.height) // 2), big)
    rows = [("TYP", "C-360"), ("NR FABR.", "0 4 7 3 1 2"), ("ROK PROD.", "1979"), ("MOC", "38 KM")]
    for i, (k, v) in enumerate(rows):
        x = (60 if i % 2 == 0 else 280) * ss
        y = (160 if i < 2 else 192) * ss
        dt.text((x, y), k, font=small, fill=255)
        dt.text((x + 88 * ss, y - 2 * ss), v, font=mid, fill=255)
    _text_c(dt, (W * ss / 2, 222 * ss), "WARSZAWA \u2014 POLSKA", small, 255)
    for (rx, ry) in ((12, 12), (W - 12, 12), (12, H - 12), (W - 12, H - 12)):
        d.ellipse([(rx - 5) * ss, (ry - 5) * ss, (rx + 5) * ss, (ry + 5) * ss], fill=0)
    ink_a = np.asarray(ink.resize((W, H), Image.LANCZOS), dtype=np.float64) / 255.0
    txt_a = np.asarray(txt.resize((W, H), Image.LANCZOS), dtype=np.float64) / 255.0
    field = ink_a * (1 - txt_a)
    lum = alu * (1 - field) + 0.05 * field
    grime = sstep(0.5, 2.2, fractal(rng, 2, 30, 2.0, shape=(H, W))) * 0.3
    edge = 1 - _rounded_mask(W, H, 18, inset=14)
    lum *= 1 - 0.35 * np.clip(grime + edge * 0.6, 0, 1)
    col = np.stack([lum, lum * 0.995, lum * 0.975], 2)
    rim_holes = np.ones((H, W))
    for (rx, ry) in ((12, 12), (W - 12, 12), (12, H - 12), (W - 12, H - 12)):
        yy, xx = np.mgrid[0:H, 0:W]
        rr = np.hypot(xx - rx, yy - ry)
        col = mix(col, np.full((H, W, 3), 0.62), sstep(6.5, 5.0, rr))  # rivet head
        col = mix(col, np.full((H, W, 3), 0.85), sstep(3.5, 1.5, rr) * 0.8)
    out.color("plate_ursus", np.clip(col, 0, 1), alpha=alpha * rim_holes, coverage=0.5)


# ============================================================================
# contact sheet


def _checker(w, h, s=16):
    yy, xx = np.mgrid[0:h, 0:w]
    c = ((xx // s + yy // s) % 2) * 40 + 90
    return Image.fromarray(np.stack([c, c, c], 2).astype(np.uint8))


def contact_sheet(out, path):
    cell, cols, lab = 320, 5, 26
    items = out.diffuse_previews
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + lab)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    f = font("DejaVuSans.ttf", 15)
    for i, (name, im, tiling) in enumerate(items):
        x, y = (i % cols) * cell, (i // cols) * (cell + lab)
        if tiling:
            t = im.resize((cell // 2, cell // 2), Image.LANCZOS)
            thumb = Image.new(im.mode, (cell, cell))
            for oy in (0, cell // 2):
                for ox in (0, cell // 2):
                    thumb.paste(t, (ox, oy))
        else:
            thumb = im.copy()
            thumb.thumbnail((cell - 8, cell - 8), Image.LANCZOS)
        if thumb.mode == "RGBA":
            if "hood" in name or "Logo" in name:
                bg = Image.new("RGB", thumb.size, (150, 22, 18))
            elif "glass" in name:
                g = np.linspace(20, 70, thumb.width)[None, :].repeat(thumb.height, 0)
                bg = Image.fromarray(np.stack([g, g, g], 2).astype(np.uint8))
            else:
                bg = _checker(thumb.width, thumb.height)
            bg.paste(thumb, (0, 0), thumb)
            thumb = bg
        sheet.paste(thumb.convert("RGB"), (x + (cell - thumb.width) // 2, y + (cell - thumb.height) // 2))
        d.text((x + 6, y + cell + 4), name + ("  (2x2 tiled)" if tiling else ""), font=f, fill=(220, 220, 220))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.save(path, quality=88)


# ============================================================================


def build_all(out, only=None):
    jobs = [
        ("paintRed", lambda: mat_paint(out, "paintRed", 101, (175, 22, 18), (180, 40, 34), 0.07,
                                       (118, 114, 106), (86, 47, 30), 0.62, (160, 140, 114), 0.025,
                                       160, False, 30)),
        ("paintGrey", lambda: mat_paint_grey(out)),
        ("paintLightGrey", lambda: mat_paint(out, "paintLightGrey", 303, (200, 203, 200), (188, 187, 178), 0.14,
                                             (96, 92, 86), (122, 72, 42), 0.58, (168, 156, 136), 0.07,
                                             150, True, 26, rain=0.5)),
        ("paintCream", lambda: mat_paint(out, "paintCream", 404, (230, 220, 180), (221, 206, 162), 0.15,
                                         (104, 98, 88), (132, 72, 38), 0.56, (164, 138, 104), 0.10,
                                         110, True, 30, extra=_cream_extra)),
        ("blackMetal", lambda: mat_black_metal(out)),
        ("rubberTyre", lambda: mat_rubber(out)),
        ("steelBare", lambda: mat_steel(out)),
        ("exhaustRust", lambda: mat_exhaust(out)),
        ("seatVinyl", lambda: mat_vinyl(out)),
        ("radiatorCore", lambda: mat_radiator(out)),
        ("glass", lambda: glass(out)),
        ("lenses", lambda: lenses(out)),
        ("decal_hoodStrip", lambda: decal_hood_strip(out)),
        ("decal_frontLogo", lambda: decal_front_logo(out)),
        ("gauge_tacho", lambda: gauge_tacho(out)),
        ("gauge_small", lambda: gauge_small(out)),
        ("needle", lambda: needle(out)),
        ("plate_ursus", lambda: plate_ursus(out)),
    ]
    for name, fn in jobs:
        if only and name not in only:
            continue
        t = time.time()
        fn()
        print(f"  {name:18s} {time.time() - t:6.1f}s", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="comma separated job names")
    ap.add_argument("--normal-format", default="bc1", choices=("bc1", "bc5"),
                    help="bc1 (default): RGB normal, works for RGB-reading and Z-rebuilding shaders; "
                         "bc5: RG only, needs a shader that rebuilds Z")
    ap.add_argument("--no-dds", action="store_true", help="PNG previews only")
    ap.add_argument("--png-dir", default="/tmp/work/tex_png")
    ap.add_argument("--dds-dir", default=os.path.join(REPO, "FS25_UrsusC360", "textures"))
    ap.add_argument("--sheet", default=os.path.join(REPO, "docs", "textures_contact_sheet.jpg"))
    args = ap.parse_args(argv)
    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    out = Out(args)
    t0 = time.time()
    build_all(out, only)
    if not only:
        contact_sheet(out, args.sheet)
    total = 0
    for name, fmt, w, h, mips, size, p_own, p_pil, p_worst in out.report:
        total += size
        print(f"{name:28s} {fmt:5s} {w:4d}x{h:<4d} mips={mips:2d} {size / 1024:8.1f} KiB "
              f"PSNR own={p_own:5.1f} pillow={p_pil:5.1f} worst-mip={p_worst:5.1f}")
    if out.report:
        print(f"total DDS: {total / 1048576:.2f} MiB in {len(out.report)} files; {time.time() - t0:.1f}s")
    if out.normal_err:
        print("normal maps, angular error vs source (level 0, degrees mean / p99):")
        for name, fmt, r in out.normal_err:
            rgb = (f"RGB read {r['angle_rgb_mean']:4.2f} / {r['angle_rgb_p99']:4.2f}   "
                   if "angle_rgb_mean" in r else "RGB read   n/a (no blue)   ")
            print(f"  {name:22s} {fmt}  {rgb}Z from RG {r['angle_rg_mean']:4.2f} / {r['angle_rg_p99']:4.2f}")
    if out.lowfreq:
        print("large-scale (> ~5 cm) variation, p1..p99 span, designed -> written:")
        for name, l0, l1, s0, s1 in out.lowfreq:
            print(f"  {name:16s} luminance {l0 * 100:5.1f}% -> {l1 * 100:4.1f}%   smoothness {s0:.3f} -> {s1:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
