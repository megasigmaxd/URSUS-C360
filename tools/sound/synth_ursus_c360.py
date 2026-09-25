#!/usr/bin/env python3
"""Physically-modelled sound set for the Ursus C-360 (S-4003 diesel) FS25 mod.

No licensed C-360 recordings were available, so every sound is synthesised from the
engine data: 4-cylinder 4-stroke direct-injection diesel, firing order 1-3-4-2,
idle 700 rpm, rated 2200 rpm, straight vertical exhaust with a small silencer.

Sources per combustion event (placed at crank angles): exhaust blow-down pulse,
combustion knock (block ringing), injection-pump tick, valve seating clicks; plus
timing-gear whine, intake roar and fan noise. Paths are filtered in the frequency
domain; loops use circular filtering over an integer number of engine cycles, so
they repeat sample-exactly without clicks.

    python3 tools/sound/synth_ursus_c360.py [--out FS25_UrsusC360/sounds] [--demo docs/sound_demo.ogg]
"""
import argparse
import json
import math
import os
import subprocess
import tempfile
import wave

import numpy as np

FS = 44100
RNG_SEED = 360
FIRING_ORDER = (1, 3, 4, 2)
CYL_GAIN = {1: 1.0, 2: 0.94, 3: 1.07, 4: 0.97}      # worn engine: slight imbalance
CYL_PHASE_DEG = {1: 0.0, 2: 1.2, 3: -0.8, 4: 0.6}
EXHAUST_L, SOUND_C = 1.45, 470.0                     # pipe length (m), hot-gas speed of sound


# ---------------------------------------------------------------- frequency-domain filters

def _f(n):
    return np.fft.rfftfreq(n, 1.0 / FS)


def resonator(f, f0, q):
    x = np.where(f > 0, f / f0 - f0 / np.maximum(f, 1e-9), -1e9)
    return 1.0 / (1.0 + 1j * q * x)


def lowpass2(f, fc):
    s = 1j * f / fc
    return 1.0 / (1.0 + math.sqrt(2) * s + s * s)


def highpass2(f, fc):
    s = 1j * f / fc
    return (s * s) / (1.0 + math.sqrt(2) * s + s * s)


def exhaust_response(f, load):
    """Closed-open pipe (odd resonances) + silencer + open-end radiation."""
    d = 2.0 * EXHAUST_L / SOUND_C
    g = 0.72 * np.exp(-f / 1400.0)
    pipe = (1.0 - 0.3) / (1.0 + g * np.exp(-2j * np.pi * f * d))
    silencer = lowpass2(f, 700.0 + 500.0 * load) * (0.35 + 0.65 * resonator(f, 380.0, 1.4))
    radiation = (1j * f / 90.0) / (1.0 + 1j * f / 90.0)
    return pipe * silencer * radiation * highpass2(f, 22.0)


def block_response(f):
    """Engine block / head / bonnet radiation for knock and mechanical noise."""
    h = (0.6 * resonator(f, 780.0, 3.0) + 1.0 * resonator(f, 1650.0, 4.0)
         + 0.8 * resonator(f, 2500.0, 5.0) + 0.45 * resonator(f, 3600.0, 6.0))
    return h * highpass2(f, 300.0) * lowpass2(f, 6500.0)


def apply(x, h_fn, circular):
    """Filter x with response h_fn(f); circular for loops, zero-padded for one-shots."""
    if circular:
        n = len(x)
        return np.fft.irfft(np.fft.rfft(x) * h_fn(_f(n)), n)
    n = len(x) + FS
    y = np.fft.irfft(np.fft.rfft(x, n) * h_fn(_f(n)), n)
    return y[:len(x)]


# ---------------------------------------------------------------- excitation

class Buffer:
    """Sample buffer with event stamping (wraps for loops)."""

    def __init__(self, n, loop):
        self.n, self.loop = n, loop
        self.x = np.zeros(n)

    def stamp(self, t0, wave_fn, length=None):
        if isinstance(wave_fn, tuple):
            wave_fn, length = wave_fn
        start = int(math.floor(t0 * FS))
        m = int(length * FS) + 2
        tau = (np.arange(m) + start - t0 * FS) / FS
        w = wave_fn(tau)
        idx = np.arange(start, start + m)
        if self.loop:
            np.add.at(self.x, idx % self.n, w)
        else:
            ok = (idx >= 0) & (idx < self.n)
            np.add.at(self.x, idx[ok], w[ok])


def exhaust_pulse(amp, rpm, load, rng=None):
    """Blow-down pressure pulse plus turbulent flow noise (the 'bark' under load)."""
    tr = 0.0024 * (1000.0 / rpm) ** 0.5 * (1.0 - 0.25 * load)
    td = 0.016 * (1000.0 / rpm) ** 0.7
    length = 6 * td + 5 * tr
    noise = rng.standard_normal(int(length * FS) + 4) if rng is not None else None
    turb = 0.22 + 0.5 * load

    def fn(t):
        t = np.maximum(t, 0.0)
        env = np.exp(-t / td) * (1.0 - np.exp(-t / tr))
        y = (t / tr) * np.exp(1.0 - t / tr) + 0.35 * env
        if noise is not None:
            y = y + turb * env * noise[np.minimum((t * FS).astype(int), len(noise) - 1)]
        return amp * y
    return fn, length


def knock_burst(amp, rng):
    # broadband combustion 'clatter': mostly noise, with short, detuned block modes
    freqs = np.array([1250.0, 1900.0, 2650.0, 3400.0, 4700.0]) * rng.uniform(0.86, 1.14, 5)
    decays = np.array([0.0016, 0.0013, 0.001, 0.0008, 0.0006]) * rng.uniform(0.7, 1.3, 5)
    amps = np.array([0.5, 0.6, 0.5, 0.35, 0.25]) * rng.uniform(0.5, 1.5, 5)
    phases = rng.uniform(0, 2 * np.pi, 5)
    noise = rng.standard_normal(160)

    def fn(t):
        t = np.maximum(t, 0.0)
        y = np.zeros_like(t)
        for f0, d, a, p in zip(freqs, decays, amps, phases):
            y += a * np.exp(-t / d) * np.sin(2 * np.pi * f0 * t + p)
        k = np.minimum((t * FS).astype(int), 159)
        y += 1.1 * noise[k] * np.exp(-t / 0.0011)
        return amp * y * (t < 0.03)
    return fn, 0.03


def click(amp, f0, decay, rng):
    p = rng.uniform(0, 2 * np.pi)

    def fn(t):
        t = np.maximum(t, 0.0)
        return amp * np.exp(-t / decay) * np.sin(2 * np.pi * f0 * t + p)
    return fn, 8 * decay


def thump(amp, f0, decay):
    def fn(t):
        t = np.maximum(t, 0.0)
        return amp * np.exp(-t / decay) * np.sin(2 * np.pi * f0 * t) * (1 - np.exp(-t / 0.002))
    return fn, 8 * decay


# ---------------------------------------------------------------- engine renderer

def crank_angle(rpm_curve):
    """Crank angle (deg) per sample for an rpm array."""
    return np.cumsum(rpm_curve) * 360.0 / 60.0 / FS


def engine(rpm_curve, load_curve, loop=False, firing=None, seed=0, knock_gain=1.0):
    """Render the engine for per-sample rpm/load arrays; firing[i] in 0..1 = fuel on/off."""
    n = len(rpm_curve)
    rng = np.random.default_rng(RNG_SEED + seed)
    theta = crank_angle(rpm_curve)
    total_deg = theta[-1]
    exh, blk, mech = Buffer(n, loop), Buffer(n, loop), Buffer(n, loop)
    firing = np.ones(n) if firing is None else firing
    cycles = int(math.ceil(total_deg / 720.0)) + 1
    for c in range(cycles):
        for k, cyl in enumerate(FIRING_ORDER):
            tdc = c * 720.0 + k * 180.0 + CYL_PHASE_DEG[cyl]
            if tdc > total_deg + 720:
                break
            if loop and tdc >= total_deg - 1e-6:
                continue      # belongs to the next repetition of the loop
            i = int(np.searchsorted(theta, tdc))
            if i >= n:
                continue
            rpm, load, fuel = rpm_curve[i], load_curve[i], firing[i]
            t_tdc = i / FS
            dt_deg = 60.0 / (rpm * 360.0)
            jitter = rng.normal(0.0, 0.00012 + 0.00025 * (1.0 - load))
            if fuel > 0.05 and rng.random() < fuel:
                var = 1.0 + rng.normal(0.0, 0.05 + 0.06 * (1.0 - load))
                a_exh = CYL_GAIN[cyl] * var * (0.32 + 0.68 * load) * (rpm / 1400.0) ** 0.55
                fn, ln = exhaust_pulse(a_exh, rpm, load, rng)
                exh.stamp(t_tdc + 135.0 * dt_deg + jitter, fn, ln)
                a_k = knock_gain * CYL_GAIN[cyl] * (1.1 - 0.6 * load) * (rpm / 1400.0) ** 0.35 \
                    * (1.0 + rng.normal(0.0, 0.15))
                fn, ln = knock_burst(a_k, rng)
                blk.stamp(t_tdc + 4.0 * dt_deg + jitter, fn, ln)
            else:
                # motoring (no fuel): compression/expansion only, a soft chuff at EVO
                fn, ln = exhaust_pulse(0.08 * (rpm / 400.0) ** 0.5, max(rpm, 120.0), 0.0)
                exh.stamp(t_tdc + 135.0 * dt_deg, fn, ln)
            fn, ln = click(0.05, 6200.0, 0.0005, rng)                      # injection pump tick
            mech.stamp(t_tdc - 16.0 * dt_deg, fn, ln)
            for ang, a in ((-160.0, 0.035), (230.0, 0.03)):                 # valve seating
                fn, ln = click(a, rng.uniform(3000, 4200), 0.0004, rng)
                mech.stamp(t_tdc + ang * dt_deg, fn, ln)
    exh_y = apply(exh.x, lambda f: exhaust_response(f, float(np.mean(load_curve))), loop)
    blk_y = apply(blk.x, block_response, loop)
    mech_y = apply(mech.x, lambda f: highpass2(f, 1500.0) * lowpass2(f, 9000.0), loop)
    # timing gears (38 teeth) + fan + intake roar
    crank_hz = rpm_curve / 60.0
    ph = 2 * np.pi * np.cumsum(38.0 * crank_hz) / FS
    firing_mod = 0.6 + 0.4 * np.cos(np.deg2rad(theta * 2.0))          # one firing per 180 deg
    gear = (0.02 * np.sin(ph) + 0.008 * np.sin(2 * ph)) * (rpm_curve / 2200.0) * firing_mod
    noise = np.random.default_rng(RNG_SEED + 1000 + seed).standard_normal(n)
    fan = apply(noise, lambda f: lowpass2(f, 2500.0) * highpass2(f, 200.0), loop) * 0.05 * (rpm_curve / 2200.0) ** 2
    noise2 = np.random.default_rng(RNG_SEED + 2000 + seed).standard_normal(n)
    intake = apply(noise2, lambda f: resonator(f, 320.0, 1.3), loop) * firing_mod
    intake *= 0.09 * load_curve * (rpm_curve / 2200.0) ** 1.5
    mix = 1.0 * exh_y + 0.3 * blk_y + 0.3 * mech_y + gear + fan + intake
    return np.tanh(mix * 0.9) / 0.9                                      # gentle saturation


# ---------------------------------------------------------------- sound set

def loop_len(rpm, seconds=3.0):
    """Integer engine cycles and integer samples; returns (samples, effective rpm)."""
    cycles = max(4, round(seconds * rpm / 120.0))
    samples = int(round(cycles * 120.0 * FS / rpm))
    return samples, cycles * 120.0 * FS / samples


def motor_loop(rpm, load, seed):
    n, rpm_eff = loop_len(rpm)
    y = engine(np.full(n, rpm_eff), np.full(n, float(load)), loop=True, seed=seed)
    return y, rpm_eff


def starter_whine(rpm_curve, active):
    """12 V starter: armature hum + pinion/ring-gear mesh (113 teeth) + commutator hiss."""
    n = len(rpm_curve)
    crank_hz = rpm_curve / 60.0
    arm = 2 * np.pi * np.cumsum(crank_hz * 11.3) / FS
    mesh = 2 * np.pi * np.cumsum(crank_hz * 113.0) / FS
    rng = np.random.default_rng(RNG_SEED + 77)
    hiss = apply(rng.standard_normal(n), lambda f: resonator(f, 5200.0, 2.0), False)
    y = 0.10 * np.sin(arm) + 0.05 * np.sin(2 * arm) + 0.06 * np.sin(mesh) + 0.03 * np.sin(2 * mesh) + 0.006 * hiss
    return y * active


def motor_start():
    dur = 4.6
    t = np.arange(int(dur * FS)) / FS
    rpm = np.zeros_like(t)
    crank = (t > 0.12) & (t < 1.55)
    theta_approx = np.cumsum(np.where(crank, 180.0, 0.0)) * 6.0 / FS
    # starter slows on every compression (4 per 720 deg)
    rpm[crank] = 175.0 * (1.0 + 0.22 * np.sin(np.deg2rad(theta_approx[crank] * 2.0)))
    rpm[t <= 0.12] = np.linspace(0, 60, np.sum(t <= 0.12))
    catch = (t >= 1.55)
    tc = t[catch] - 1.55
    rpm[catch] = 700.0 + 380.0 * np.exp(-((tc - 0.35) / 0.3) ** 2) - (700.0 - 190.0) * np.exp(-tc / 0.09)
    rpm = np.maximum(rpm, 1.0)
    load = np.where(catch, 0.35 * np.exp(-np.maximum(t - 1.8, 0) / 0.6), 0.0)
    fuel = np.where(t > 1.3, np.clip((t - 1.3) / 0.35, 0, 1), 0.0)
    y = engine(rpm, load, firing=fuel, seed=11, knock_gain=1.5)
    active = np.clip(np.where(t < 1.62, 1.0, 1.0 - (t - 1.62) / 0.05), 0, 1) * np.clip(t / 0.05, 0, 1)
    y += starter_whine(np.maximum(rpm, 60.0), active)
    b = Buffer(len(t), False)
    b.stamp(0.02, thump(0.25, 140.0, 0.012), 0.1)                       # solenoid clack
    b.stamp(0.02, click(0.2, 2300.0, 0.004, np.random.default_rng(5)), 0.05)
    b.stamp(1.64, click(0.12, 1800.0, 0.006, np.random.default_rng(6)), 0.06)  # pinion throws out
    return y + apply(b.x, lambda f: highpass2(f, 60.0), False)


def motor_stop():
    dur = 2.4
    t = np.arange(int(dur * FS)) / FS
    rpm = np.maximum(700.0 * np.clip(1.0 - t / 1.25, 0.0, 1.0) ** 1.6, 1.0)
    fuel = np.where(t < 0.08, 1.0, 0.0)
    y = engine(rpm, np.zeros_like(t), firing=fuel, seed=21)
    y *= np.clip((1.45 - t) / 0.35, 0.0, 1.0)
    b = Buffer(len(t), False)
    b.stamp(1.28, thump(0.45, 55.0, 0.05), 0.4)                          # rock-back
    rng = np.random.default_rng(9)
    for k in range(5):
        b.stamp(1.3 + 0.03 * k, click(0.05 / (k + 1), rng.uniform(900, 1600), 0.01, rng), 0.1)
    return y + apply(b.x, lambda f: highpass2(f, 30.0), False)


def periodic_noise(n, seed, h_fn):
    return apply(np.random.default_rng(RNG_SEED + seed).standard_normal(n), h_fn, True)


def gearbox_whine():
    n = int(3.0 * FS)
    base = round(3.0 * 610.0) / 3.0            # mesh frequency snapped to loop periodicity
    t = np.arange(n) / FS
    shaft = round(3.0 * 14.0) / 3.0
    am = 1.0 + 0.25 * np.sin(2 * np.pi * shaft * t)
    y = (0.5 * np.sin(2 * np.pi * base * t) + 0.22 * np.sin(2 * np.pi * 2 * base * t + 1.0)
         + 0.1 * np.sin(2 * np.pi * 3 * base * t + 2.0)) * am
    y += 0.18 * np.sin(2 * np.pi * (round(3.0 * base * 1.515) / 3.0) * t + 0.3) * am   # second gear pair
    y += periodic_noise(n, 31, lambda f: resonator(f, 1200.0, 1.5)) * 0.25
    return y * 0.5


def gear_shift():
    n = int(0.45 * FS)
    b = Buffer(n, False)
    rng = np.random.default_rng(41)
    b.stamp(0.01, thump(0.6, 95.0, 0.03), 0.3)
    for f0, a in ((1150.0, 0.35), (2300.0, 0.25), (3700.0, 0.15)):
        b.stamp(0.012, click(a, f0, 0.012, rng), 0.12)
    b.stamp(0.09, click(0.12, 1700.0, 0.01, rng), 0.1)
    return apply(b.x, lambda f: highpass2(f, 50.0), False)


def gear_grind():
    n = int(0.65 * FS)
    t = np.arange(n) / FS
    env = np.clip(t / 0.05, 0, 1) * np.clip((0.62 - t) / 0.2, 0, 1)
    rng = np.random.default_rng(51)
    b = Buffer(n, False)
    tt = 0.0
    while tt < 0.6:
        b.stamp(tt, click(rng.uniform(0.2, 0.5), rng.uniform(1800, 3600), 0.0015, rng), 0.012)
        tt += 1.0 / rng.uniform(320, 420)
    y = apply(b.x, lambda f: highpass2(f, 400.0), False)
    y += apply(rng.standard_normal(n), lambda f: resonator(f, 2600.0, 2.0), False) * 0.15
    return y * env


def hydraulic_pump():
    n = int(2.5 * FS)
    t = np.arange(n) / FS
    f0 = 2.5 * 250.0 / 2.5
    y = 0.4 * np.sin(2 * np.pi * f0 * t) + 0.2 * np.sin(2 * np.pi * 2 * f0 * t + 0.7) \
        + 0.1 * np.sin(2 * np.pi * 3 * f0 * t + 1.3)
    y *= 1.0 + 0.15 * np.sin(2 * np.pi * 24.0 * t)
    y += periodic_noise(n, 61, lambda f: resonator(f, 3500.0, 1.2)) * 0.12
    return y * 0.5


def horn():
    n = int(1.2 * FS)
    t = np.arange(n) / FS
    f0 = 420.0
    ph = (t * f0) % 1.0
    raw = np.where(ph < 0.32, 1.0, -0.45) + 0.3 * np.sin(2 * np.pi * f0 * t)
    y = apply(raw, lambda f: 0.8 * resonator(f, 900.0, 4.0) + 1.0 * resonator(f, 2200.0, 5.0)
              + 0.5 * resonator(f, 3500.0, 6.0) + 0.3, True)
    return y * 0.35


# ---------------------------------------------------------------- output

def write_wav(path, y):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


def to_ogg(path, y):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        write_wav(tmp.name, y)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp.name, "-c:a", "libvorbis", "-q:a", "6",
                    path], check=True)
    os.unlink(tmp.name)


def db(x):
    return 20.0 * math.log10(max(x, 1e-9))


def render_demo(sounds, meta):
    """In-game style mix: rpm/load automation, pitch = rpm/native, crossfades between loops."""
    dur = 28.0
    n = int(dur * FS)
    t = np.arange(n) / FS
    key = [(0, 700, 0), (6, 700, 0), (7.5, 2300, 0), (9.5, 2300, 0), (11, 700, 0), (12.5, 700, 0),
           (13, 1000, 1), (17, 2200, 1), (21, 2200, 0.6), (23, 700, 0), (25.5, 700, 0), (28, 700, 0)]
    kt = [k[0] for k in key]
    rpm = np.interp(t, kt, [k[1] for k in key])
    load = np.interp(t, kt, [k[2] for k in key])
    running = (t >= 4.4) & (t < 25.6)
    out = np.zeros(n)
    loops = [(nm, m) for nm, m in meta.items() if m["type"] == "loop" and nm.startswith("motor")]
    for nm, m in loops:
        src = sounds[nm]
        native = m["native_rpm"]
        w_rpm = np.clip(1.0 - np.abs(rpm - native) / 520.0, 0.0, 1.0)
        w_load = load if m["load"] else (1.0 - load)
        gain = w_rpm * w_load * running
        pos = np.cumsum(rpm / native) % len(src)
        i0 = pos.astype(int)
        frac = pos - i0
        out += gain * (src[i0] * (1 - frac) + src[(i0 + 1) % len(src)] * frac)
    norm = np.zeros(n)
    for nm, m in loops:
        norm += np.clip(1.0 - np.abs(rpm - m["native_rpm"]) / 520.0, 0.0, 1.0) * \
            (load if m["load"] else (1.0 - load))
    out = out / np.maximum(norm, 1e-3) * running
    start = sounds["motorStart"]
    s0 = int(0.3 * FS)
    seg = start[:min(len(start), n - s0)]
    fade = np.clip((4.6 - (np.arange(len(seg)) / FS + 0.3)) / 0.3, 0, 1)
    out[s0:s0 + len(seg)] += seg * fade
    a0 = int(4.3 * FS)
    out[a0:a0 + int(0.3 * FS)] *= np.linspace(0, 1, int(0.3 * FS))
    stop = sounds["motorStop"]
    s1 = int(25.5 * FS)
    out[s1:s1 + len(stop[:n - s1])] += stop[:n - s1]
    whine = sounds["gearboxWhine"]
    speed_w = np.clip((t - 13.0) / 2.0, 0, 1) * np.clip((23.0 - t) / 1.5, 0, 1)
    pos = np.cumsum(0.4 + 0.6 * np.clip((rpm - 700) / 1500, 0, 1)) % len(whine)
    i0 = pos.astype(int)
    fr = pos - i0
    out += 0.25 * speed_w * (whine[i0] * (1 - fr) + whine[(i0 + 1) % len(whine)] * fr)
    for ts in (12.8, 20.9):
        g = sounds["gearShift"]
        s = int(ts * FS)
        out[s:s + len(g)] += 0.8 * g
    return out / max(1e-9, np.max(np.abs(out))) * 0.89


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(repo, "FS25_UrsusC360", "sounds"))
    ap.add_argument("--demo", default=os.path.join(repo, "docs", "sound_demo.ogg"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    sounds, meta = {}, {}
    motor = [("motorIdle", 700, 0), ("motorNoLoad_1200", 1200, 0), ("motorNoLoad_1700", 1700, 0),
             ("motorNoLoad_2300", 2300, 0), ("motorLoad_1000", 1000, 1), ("motorLoad_1500", 1500, 1),
             ("motorLoad_2000", 2000, 1), ("motorLoad_2200", 2200, 1)]
    for i, (name, rpm, load) in enumerate(motor):
        y, rpm_eff = motor_loop(rpm, load, seed=i)
        sounds[name] = y
        meta[name] = {"type": "loop", "native_rpm": round(rpm_eff, 3), "load": load}
    sounds["motorStart"] = motor_start()
    meta["motorStart"] = {"type": "oneshot", "native_rpm": 700, "load": 0}
    sounds["motorStop"] = motor_stop()
    meta["motorStop"] = {"type": "oneshot", "native_rpm": 700, "load": 0}
    for name, fn, typ, extra in (("gearboxWhine", gearbox_whine, "loop", {"native_speed_kmh": 20}),
                                 ("gearShift", gear_shift, "oneshot", {}), ("gearGrind", gear_grind, "oneshot", {}),
                                 ("hydraulicPump", hydraulic_pump, "loop", {}), ("horn", horn, "loop", {})):
        sounds[name] = fn()
        meta[name] = {"type": typ, **extra}

    # one calibrated gain for all engine sounds keeps load/no-load/idle levels meaningful
    engine_names = [nm for nm in sounds if nm.startswith("motor")]
    g_engine = 0.89 / max(np.max(np.abs(sounds[nm])) for nm in engine_names)
    for nm, y in sounds.items():
        g = g_engine if nm in engine_names else 0.8 / max(np.max(np.abs(y)), 1e-9)
        if nm == "gearboxWhine":
            g *= 0.6
        sounds[nm] = y * g
        m = meta[nm]
        m.update({"file": f"{nm}.ogg", "duration_s": round(len(y) / FS, 4),
                  "peak_dbfs": round(db(np.max(np.abs(sounds[nm]))), 2),
                  "rms_dbfs": round(db(float(np.sqrt(np.mean(sounds[nm] ** 2)))), 2)})
        if m["type"] == "loop":
            m["loop_samples"] = len(y)
            tiled = np.concatenate([sounds[nm]] * 3)
            step = np.max(np.abs(np.diff(tiled)))
            seam = np.max(np.abs(np.diff(tiled[len(y) - 2:len(y) + 2])))
            m["seam_step_ratio"] = round(float(seam / max(step, 1e-9)), 3)
        to_ogg(os.path.join(args.out, f"{nm}.ogg"), sounds[nm])
        print(f"{nm:18s} {m['duration_s']:6.2f}s peak {m['peak_dbfs']:6.1f} dBFS rms {m['rms_dbfs']:6.1f} dBFS")
    with open(os.path.join(args.out, "sounds.json"), "w", encoding="utf-8") as fh:
        json.dump({"sample_rate": FS, "engine": "S-4003 4-cyl diesel, firing order 1-3-4-2", "files": meta},
                  fh, indent=2)
    if args.demo:
        os.makedirs(os.path.dirname(args.demo), exist_ok=True)
        to_ogg(args.demo, render_demo(sounds, meta))
        print("demo:", args.demo)


if __name__ == "__main__":
    main()
