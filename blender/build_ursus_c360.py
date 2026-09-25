"""Procedural Ursus C-360 (1976-1994, red cab version) for Farming Simulator 25.

Run inside Blender 4.5 (GUI via blender-mcp, or `blender -b -P blender/build_all.py`).
Dimensions follow the factory data: wheelbase 2125 mm, track 1450 mm, rear 14.9-28,
front 6.00-16, bonnet/fenders red, light-grey cab with red roof, grey engine and
drivetrain castings, cream rims. Node names follow docs/i3d_nodes.md.
"""
import importlib
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ursus_lib  # noqa: E402

importlib.reload(ursus_lib)
from ursus_lib import (CONV, CONV_INV, TAU, WORLD, D, Part, V, box, circle, clear_scene, recalc,  # noqa: E402
                       copy_tmp, cylinder, disc, empty, frames_line, frames_path, i3d_matrix,
                       i3d_matrix_camlight, lathe, mirror_x, new_tmp, place, quad, rounded_rect,
                       slab, solidify, sweep, tg, tube)

TEX_PNG = os.environ.get("URSUS_TEX_PNG", "/tmp/work/tex_png")

# key dimensions (vehicle / i3d frame: x left, y up, z forward)
ZR, YR, XR = -1.0625, 0.683, 0.725      # rear axle
ZF, YF, XF = 1.0625, 0.365, 0.725       # front axle
R_REAR, W_REAR = 0.683, 0.378
R_FRONT, W_FRONT = 0.365, 0.17
HOOD_W, HOOD_TOP, HOOD_BOT, HOOD_R = 0.60, 1.31, 1.02, 0.075
CAB_X, CAB_ZF, CAB_ZR, CAB_FLOOR, CAB_TOP = 0.585, 0.03, -1.155, 0.99, 2.12
FENDER_R = 0.78
EXH_TOP = 2.02
DASH_N = Vector((0.0, 0.075, -0.28)).normalized()   # dashboard face normal (towards driver)
TACHO_XY, SMALL_XY = (0.13, 1.19), (-0.13, 1.18)


def on_dash(x, y, lift=0.0):
    """Point on the dashboard front surface (panel 1.02..1.30 m, top leaning forward)."""
    z = -0.125 + (y - HOOD_BOT) / 0.28 * 0.075
    return Vector((x, y, z)) + DASH_N * (0.01 + lift)

# name: (linear base colour RGBA, roughness, metallic)
MATERIALS = {
    "paintRed": ((0.43, 0.009, 0.006, 1), 0.32, 0.0),
    "paintGrey": ((0.155, 0.165, 0.16, 1), 0.55, 0.0),
    "paintLightGrey": ((0.58, 0.6, 0.58, 1), 0.42, 0.0),
    "paintCream": ((0.79, 0.72, 0.46, 1), 0.45, 0.0),
    "blackMetal": ((0.018, 0.018, 0.018, 1), 0.5, 0.25),
    "rubberTyre": ((0.016, 0.016, 0.016, 1), 0.85, 0.0),
    "steelBare": ((0.56, 0.56, 0.55, 1), 0.3, 1.0),
    "exhaustRust": ((0.05, 0.03, 0.022, 1), 0.8, 0.3),
    "seatVinyl": ((0.028, 0.022, 0.018, 1), 0.55, 0.0),
    "radiatorCore": ((0.02, 0.02, 0.02, 1), 0.6, 0.3),
    "glass": ((0.55, 0.6, 0.6, 0.22), 0.04, 0.0),
    "lensClear": ((0.85, 0.85, 0.82, 1), 0.05, 0.0),
    "lensRed": ((0.5, 0.01, 0.008, 1), 0.1, 0.0),
    "lensOrange": ((0.85, 0.28, 0.01, 1), 0.1, 0.0),
    "decal_hoodStrip": ((0.8, 0.8, 0.78, 1), 0.35, 0.0),
    "decal_frontLogo": ((0.9, 0.9, 0.9, 1), 0.35, 0.0),
    "gauge_tacho": ((0.02, 0.02, 0.02, 1), 0.3, 0.0),
    "gauge_small": ((0.02, 0.02, 0.02, 1), 0.3, 0.0),
    "needle": ((0.9, 0.4, 0.05, 1), 0.4, 0.0),
    "plate_ursus": ((0.7, 0.7, 0.68, 1), 0.3, 0.6),
    "collision": ((0.5, 0.0, 0.5, 1), 1.0, 0.0),
}
ALPHA_MATERIALS = {"glass", "decal_frontLogo", "needle"}


def _png(name, kind):
    for fn in (f"{name}_{kind}.png", f"{name}.png" if kind == "diffuse" else None):
        if fn and os.path.exists(os.path.join(TEX_PNG, fn)):
            return os.path.join(TEX_PNG, fn)
    if kind == "normal" and name.startswith("lens"):
        p = os.path.join(TEX_PNG, "lens_normal.png")
        return p if os.path.exists(p) else None
    return None


def setup_materials():
    for name, (col, rough, metal) in MATERIALS.items():
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        m.diffuse_color = col
        nt = m.node_tree
        bsdf = nt.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = col
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Metallic"].default_value = metal
        if name in ALPHA_MATERIALS:
            bsdf.inputs["Alpha"].default_value = col[3]
            m.surface_render_method = "BLENDED"
        diff = _png(name, "diffuse")
        if diff:
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(diff, check_existing=True)
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            if name in ALPHA_MATERIALS:
                nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        spec = _png(name, "specular")
        if spec:
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(spec, check_existing=True)
            tex.image.colorspace_settings.name = "Non-Color"
            sep = nt.nodes.new("ShaderNodeSeparateColor")
            inv = nt.nodes.new("ShaderNodeMath")
            inv.operation = "SUBTRACT"
            inv.inputs[0].default_value = 1.0
            nt.links.new(tex.outputs["Color"], sep.inputs["Color"])
            nt.links.new(sep.outputs["Red"], inv.inputs[1])
            nt.links.new(inv.outputs["Value"], bsdf.inputs["Roughness"])
            nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])   # FS25: R smooth, G AO, B metal
        nrm = _png(name, "normal")
        if nrm:
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(nrm, check_existing=True)
            tex.image.colorspace_settings.name = "Non-Color"
            nm = nt.nodes.new("ShaderNodeNormalMap")
            nm.inputs["Strength"].default_value = 0.6
            nt.links.new(tex.outputs["Color"], nm.inputs["Color"])
            nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])


# ---------------------------------------------------------------- helpers

def rail(points, w, h, r=0.006, closed=False):
    prof = rounded_rect(w, h, r, 2)
    pts = list(points)
    if closed:
        fr = frames_path(pts + [pts[0], pts[1]])[:len(pts)]
        return sweep(fr, prof, closed_path=True)
    return sweep(frames_path(pts), prof)


def rot_towards(d, up=(0.0, 1.0, 0.0)):
    """i3d euler (deg, XYZ) whose local +Z points along i3d direction d."""
    z = Vector(d).normalized()
    upv = Vector(up)
    if abs(z.dot(upv)) > 0.98:
        upv = Vector((1.0, 0.0, 0.0))
    x = upv.cross(z).normalized()
    y = z.cross(x)
    m = Matrix((x, y, z)).transposed()
    return tuple(math.degrees(a) for a in m.to_euler("XYZ"))


def tg_local(name, pos, rot_local, parent):
    """TransformGroup at world position `pos` with rotation `rot_local` relative to `parent` (i3d deg)."""
    mp = CONV @ WORLD[parent.name] @ CONV_INV
    rx, ry, rz = (math.radians(a) for a in rot_local)
    rl = Matrix.Rotation(rz, 4, "Z") @ Matrix.Rotation(ry, 4, "Y") @ Matrix.Rotation(rx, 4, "X")
    m = Matrix.Translation(Vector(pos)) @ mp.to_quaternion().to_matrix().to_4x4() @ rl
    return empty(name, CONV_INV @ m @ CONV, parent)


def fender_y(z, x_off=0.0):
    """Height of the rear fender inner surface above point z (on the arc)."""
    dz = z - ZR
    if abs(dz) >= FENDER_R:
        return YR
    return YR + math.sqrt(FENDER_R ** 2 - dz ** 2) + x_off


def offset_polyline(pts, d):
    """Offset an open 2D polyline to its right-hand side (inward for our U profiles)."""
    out = []
    for i, (x, y) in enumerate(pts):
        p0 = pts[max(i - 1, 0)]
        p1 = pts[min(i + 1, len(pts) - 1)]
        tx, ty = p1[0] - p0[0], p1[1] - p0[1]
        n = math.hypot(tx, ty) or 1.0
        out.append((x + d * ty / n, y - d * tx / n))
    return out


def u_profile(w, ytop, ybot, r, seg=7, crease=None, crown=0.006):
    """Inverted-U sheet profile (bottom-right -> over the top -> bottom-left)."""
    hw = w / 2
    pts = [(-hw + 0.004, ybot), (-hw, ybot + 0.08)]
    if crease:
        pts += [(-hw, crease - 0.01), (-hw - 0.003, crease), (-hw, crease + 0.01)]
    for i in range(seg + 1):
        a = math.radians(180 - 90 * i / seg)
        pts.append((-hw + r + r * math.cos(a), ytop - r + r * math.sin(a)))
    span = hw - r
    for x in (-0.6 * span, -0.2 * span, 0.2 * span, 0.6 * span):
        pts.append((x, ytop + crown * (1 - (x / span) ** 2)))
    for i in range(seg + 1):
        a = math.radians(90 - 90 * i / seg)
        pts.append((hw - r + r * math.cos(a), ytop - r + r * math.sin(a)))
    if crease:
        pts += [(hw, crease + 0.01), (hw + 0.003, crease), (hw, crease - 0.01)]
    pts += [(hw, ybot + 0.08), (hw - 0.004, ybot)]
    return pts


# ---------------------------------------------------------------- wheels

def tyre_section(r_in, r_out, half_w, n=2.8, count=40):
    """Closed (radius, axial) tyre carcass section as a rounded superellipse."""
    rc, hh = (r_in + r_out) / 2, (r_out - r_in) / 2
    pts = []
    for i in range(count):
        t = TAU * i / count
        c, s = math.cos(t), math.sin(t)
        a = half_w * math.copysign(abs(c) ** (2 / n), c)
        r = rc + hh * math.copysign(abs(s) ** (2 / n), s)
        pts.append((max(r, r_in + 0.004), a))
    return pts


def rear_wheel(part, side):
    """Tyre 14.9-28 with R-1 chevron lugs + cream disc rim; origin = wheel centre."""
    cx = side * XR
    ctr = (cx, YR, ZR)
    crown = R_REAR - 0.032
    part.merge(lathe(tyre_section(0.352, crown, W_REAR / 2 - 0.004), ctr, (1, 0, 0), 72,
                     closed_profile=True), "rubberTyre")
    # lugs: 23 per half, staggered, V pointing forward at the top of the tyre
    n_lugs, lug_w, lug_h = 23, 0.042, 0.034
    for half in (-1, 1):
        for k in range(n_lugs):
            th0 = TAU * k / n_lugs + (0.5 * TAU / n_lugs if half > 0 else 0.0)
            frames = []
            steps = 6
            for i in range(steps + 1):
                s = i / steps
                a = half * (0.01 + s * (W_REAR / 2 - 0.004))
                th = th0 - s * 0.29
                rr = crown - 0.012 - (0.018 * s ** 3)
                o = Vector((cx + a, YR + rr * math.cos(th), ZR + rr * math.sin(th)))
                radial = Vector((0.0, math.cos(th), math.sin(th)))
                tang = Vector((half * (W_REAR / 2), 0.29 * rr * math.sin(th),
                               -0.29 * rr * math.cos(th))).normalized()
                xa = tang.cross(radial).normalized()
                frames.append((V(o), D(xa), D(radial)))
            prof = [(-lug_w / 2, 0.0), (lug_w / 2, 0.0), (lug_w / 2 * 0.8, lug_h + 0.012),
                    (-lug_w / 2 * 0.8, lug_h + 0.012)]
            part.merge(sweep(frames, prof), "rubberTyre")
    # rim 28x12: flanges + bead seats + drop centre (outer surface), sheet 6 mm
    rim = [(0.388, -0.160), (0.380, -0.150), (0.360, -0.146), (0.356, -0.110), (0.335, -0.075),
           (0.333, 0.075), (0.356, 0.110), (0.360, 0.146), (0.380, 0.150), (0.388, 0.160)]
    inner = [(r - 0.007, a) for (r, a) in reversed(rim)]
    part.merge(lathe(rim + inner, ctr, (1, 0, 0), 64, closed_profile=True), "paintCream")
    # dished centre disc, offset outwards
    o = side
    disc_prof = [(0.336, 0.02 * o), (0.30, 0.03 * o), (0.22, 0.055 * o), (0.19, 0.075 * o),
                 (0.13, 0.08 * o), (0.13, 0.072 * o), (0.19, 0.067 * o), (0.22, 0.047 * o),
                 (0.30, 0.022 * o), (0.336, 0.012 * o)]
    part.merge(lathe(disc_prof, ctr, (1, 0, 0), 64, closed_profile=True), "paintCream")
    # disc-to-rim clamps and wheel nuts
    for k in range(8):
        t = TAU * k / 8 + 0.2
        c = (cx + 0.03 * o, YR + 0.315 * math.cos(t), ZR + 0.315 * math.sin(t))
        part.merge(box(c, (0.03, 0.045, 0.045), 0.006, 1, rot=(math.degrees(-t), 0, 0)), "paintCream")
        n = (cx + 0.083 * o, YR + 0.105 * math.cos(t + 0.2), ZR + 0.105 * math.sin(t + 0.2))
        part.merge(cylinder(n, (n[0] + 0.022 * o, n[1], n[2]), 0.013, 6, 0.002), "steelBare")
    # hub with red centre cap
    part.merge(cylinder((cx + 0.07 * o, YR, ZR), (cx + 0.115 * o, YR, ZR), 0.085, 28, 0.006), "paintGrey")
    part.merge(cylinder((cx + 0.115 * o, YR, ZR), (cx + 0.15 * o, YR, ZR), 0.06, 24, 0.012, r1=0.045),
               "paintRed")


def front_wheel(part, side):
    """Tyre 6.00-16 three-rib + 16x4.5 cream rim; origin = wheel centre."""
    cx = side * XF
    ctr = (cx, YF, ZF)
    hw = W_FRONT / 2
    sec = [(0.205, -0.056), (0.24, -0.08), (0.285, -0.086), (0.325, -0.078), (0.348, -0.062),
           (0.357, -0.046), (0.358, -0.036), (0.346, -0.033), (0.346, -0.019), (0.361, -0.016),
           (0.365, -0.006), (0.365, 0.006), (0.361, 0.016), (0.346, 0.019), (0.346, 0.033),
           (0.358, 0.036), (0.357, 0.046), (0.348, 0.062), (0.325, 0.078), (0.285, 0.086),
           (0.24, 0.08), (0.205, 0.056), (0.203, 0.0)]
    part.merge(lathe(sec, ctr, (1, 0, 0), 64, closed_profile=True), "rubberTyre")
    rim = [(0.222, -0.066), (0.215, -0.06), (0.203, -0.057), (0.2, -0.03), (0.185, -0.018),
           (0.185, 0.018), (0.2, 0.03), (0.203, 0.057), (0.215, 0.06), (0.222, 0.066)]
    inner = [(r - 0.005, a) for (r, a) in reversed(rim)]
    part.merge(lathe(rim + inner, ctr, (1, 0, 0), 48, closed_profile=True), "paintCream")
    o = side
    dp = [(0.186, 0.0), (0.15, 0.012 * o), (0.1, 0.03 * o), (0.065, 0.034 * o),
          (0.065, 0.028 * o), (0.1, 0.024 * o), (0.15, 0.006 * o), (0.186, -0.006 * o)]
    part.merge(lathe(dp, ctr, (1, 0, 0), 48, closed_profile=True), "paintCream")
    for k in range(6):
        t = TAU * k / 6
        n = (cx + 0.034 * o, YF + 0.075 * math.cos(t), ZF + 0.075 * math.sin(t))
        part.merge(cylinder(n, (n[0] + 0.016 * o, n[1], n[2]), 0.01, 6, 0.0015), "steelBare")
    part.merge(cylinder((cx + 0.03 * o, YF, ZF), (cx + 0.085 * o, YF, ZF), 0.05, 24, 0.01, r1=0.034),
               "paintGrey")


# ---------------------------------------------------------------- body assemblies

def build_hood(body):
    prof = u_profile(HOOD_W, HOOD_TOP, HOOD_BOT, HOOD_R, crease=1.225)
    shell = prof + offset_polyline(prof, 0.004)[::-1]      # sheet thickness goes inwards
    body.merge(sweep(frames_line((0, 0, -0.07), (0, 0, 1.43), (1, 0, 0), (0, 1, 0), 6), shell), "paintRed")
    # radiator shell: straight part + rounded front edge + front face with grille opening
    mask = u_profile(HOOD_W + 0.004, HOOD_TOP + 0.002, 0.60, HOOD_R, crown=0.006)
    rf, z0, z1 = 0.05, 1.40, 1.575
    frames = []
    rings = []
    for z in (z0, z1):
        rings.append((z, mask))
    for k in range(1, 6):
        phi = math.radians(90 * k / 5)
        rings.append((z1 + rf * math.sin(phi), offset_polyline(mask, rf * (1 - math.cos(phi)))))
    bm = new_tmp()
    vrings = [[bm.verts.new(V(x, y, z)) for (x, y) in pr + offset_polyline(pr, 0.004)[::-1]] for (z, pr) in rings]
    for a, b in zip(vrings, vrings[1:]):
        n = len(a)
        for i in range(n):
            bm.faces.new((a[i], a[(i + 1) % n], b[(i + 1) % n], b[i]))
    bm.faces.new(vrings[0][::-1])
    bm.faces.new(vrings[-1])
    recalc(bm)
    body.merge(bm, "paintRed")
    zf = z1 + rf
    inset = offset_polyline(mask, rf)
    gx, gy0, gy1 = 0.215, 0.80, 1.25
    edge_x = (HOOD_W + 0.004) / 2 - rf
    top = [(x, y) for (x, y) in inset if y > gy1]
    top_poly = [(-edge_x, gy1)] + top + [(edge_x, gy1)]
    panels = [top_poly,
              [(-edge_x, 0.60), (edge_x, 0.60), (edge_x, gy0), (-edge_x, gy0)],
              [(gx, gy0), (edge_x, gy0), (edge_x, gy1), (gx, gy1)],
              [(-edge_x, gy0), (-gx, gy0), (-gx, gy1), (-edge_x, gy1)]]
    for poly in panels:
        body.merge(slab([(x, y, zf - 0.004) for (x, y) in poly], 0.004), "paintRed")
    # grille: frame, louvres and centre bar (cream), radiator core behind
    for (p0, p1) in (((-gx, gy0, zf - 0.01), (gx, gy0, zf - 0.01)), ((-gx, gy1, zf - 0.01), (gx, gy1, zf - 0.01)),
                     ((gx, gy0, zf - 0.01), (gx, gy1, zf - 0.01)), ((-gx, gy0, zf - 0.01), (-gx, gy1, zf - 0.01))):
        body.merge(rail([p0, p1], 0.02, 0.02, 0.004), "paintCream")
    n = 17
    for i in range(n):
        y = gy0 + 0.02 + (gy1 - gy0 - 0.04) * i / (n - 1)
        if abs(y - 1.025) < 0.012:
            continue
        body.merge(box((0, y, zf - 0.022), (2 * gx - 0.01, 0.006, 0.03), 0.002, 1, rot=(28, 0, 0)), "paintCream")
    body.merge(box((0, 1.025, zf - 0.014), (2 * gx, 0.028, 0.03), 0.006, 2), "paintCream")
    body.merge(box((0, 1.02, 1.525), (0.46, 0.50, 0.06), 0.01, 1), "radiatorCore")
    body.merge(box((0, 1.29, 1.51), (0.44, 0.05, 0.10), 0.012, 2), "paintGrey")
    body.merge(box((0, 0.74, 1.51), (0.44, 0.05, 0.10), 0.012, 2), "paintGrey")
    # URSUS badge above the grille (seen from the front, vehicle -x is on the viewer's left)
    body.merge(quad([(-0.10, 1.262, zf + 0.0015), (0.10, 1.262, zf + 0.0015),
                     (0.10, 1.292, zf + 0.0015), (-0.10, 1.292, zf + 0.0015)],
                    uvs=((0, 0), (1, 0), (1, 1), (0, 1))), "decal_frontLogo", uv=None)
    # hood side plates "-U-R-S-U-S- C-360" (text reads front->rear on the left side)
    y0, y1, za, zb = 1.132, 1.1945, 0.24, 0.74
    xs = HOOD_W / 2 + 0.0015
    body.merge(quad([(xs, y0, zb), (xs, y0, za), (xs, y1, za), (xs, y1, zb)]), "decal_hoodStrip", uv=None)
    body.merge(quad([(-xs, y0, za), (-xs, y0, zb), (-xs, y1, zb), (-xs, y1, za)]), "decal_hoodStrip", uv=None)
    # fuel filler cap on the bonnet (right rear) and dashboard cowl inside the cab
    body.merge(cylinder((-0.14, HOOD_TOP - 0.01, 0.30), (-0.14, HOOD_TOP + 0.035, 0.30), 0.045, 20, 0.01), "blackMetal")
    # headlamps in black buckets on the radiator shell sides + small turn lamps
    for s in (1, -1):
        c = (s * 0.388, 1.0, 1.54)
        body.merge(box((s * 0.33, 1.0, 1.52), (0.07, 0.035, 0.05), 0.008, 2), "blackMetal")
        body.merge(lathe([(0.0, -0.075), (0.045, -0.072), (0.07, -0.055), (0.083, -0.025),
                          (0.086, 0.03), (0.0, 0.03)], c, (0, 0, 1), 32), "blackMetal")
        body.merge(lathe([(0.079, 0.028), (0.09, 0.03), (0.09, 0.042), (0.082, 0.046), (0.079, 0.04)],
                         c, (0, 0, 1), 32, closed_profile=True), "steelBare")
        body.merge(disc((c[0], c[1], c[2] + 0.041), (0, 0, 1), 0.08, 32), "lensClear", uv=None)
        t = (s * 0.335, 1.165, 1.545)
        body.merge(cylinder((t[0], t[1], t[2] - 0.03), t, 0.028, 16, 0.004), "blackMetal")
        body.merge(disc((t[0], t[1], t[2] + 0.001), (0, 0, 1), 0.024, 16), "lensOrange", uv=None)


def build_engine(body):
    g = "paintGrey"
    body.merge(box((0, 0.74, 0.83), (0.33, 0.48, 1.06), 0.025, 2), g)                 # block
    body.merge(box((0, 1.03, 0.81), (0.31, 0.10, 0.98), 0.012, 2), g)                 # head
    body.merge(box((0, 1.1, 0.81), (0.24, 0.06, 0.86), 0.022, 3), g)                  # valve cover
    body.merge(box((0, 0.46, 0.85), (0.30, 0.13, 0.92), 0.035, 3), g)                 # sump
    for s in (1, -1):                                                                  # casting details
        body.merge(box((s * 0.17, 0.83, 0.83), (0.03, 0.035, 1.02), 0.012, 2), g)       # deck ridge
        body.merge(box((s * 0.168, 0.555, 0.85), (0.03, 0.03, 0.96), 0.01, 2), g)       # sump flange
        for i in range(3):
            z = 0.52 + i * 0.3
            body.merge(cylinder((s * 0.165, 0.7, z), (s * 0.176, 0.7, z), 0.024, 16, 0.003), g)   # core plugs
        for i in range(9):
            z = 0.4 + i * 0.115
            body.merge(cylinder((s * 0.172, 0.555, z), (s * 0.19, 0.555, z), 0.007, 6), "steelBare")
    # tappet side cover with bolts (right) and oil filter + dipstick (left)
    body.merge(box((-0.175, 0.69, 0.82), (0.02, 0.14, 0.62), 0.02, 3), g)
    for i in range(6):
        body.merge(cylinder((-0.183, 0.64 + 0.1 * (i % 2), 0.56 + 0.26 * (i // 2)),
                            (-0.195, 0.64 + 0.1 * (i % 2), 0.56 + 0.26 * (i // 2)), 0.007, 6), "steelBare")
    body.merge(cylinder((0.2, 0.56, 1.0), (0.2, 0.76, 1.0), 0.052, 20, 0.012), "blackMetal")
    body.merge(box((0.175, 0.78, 1.0), (0.05, 0.05, 0.1), 0.01, 1), g)
    body.merge(tube([(0.17, 0.62, 0.7), (0.19, 0.8, 0.7), (0.2, 0.98, 0.72)], 0.005, 6), "steelBare")
    body.merge(lathe([(0.008, -0.012), (0.018, 0.0), (0.008, 0.012), (0.003, 0.0)], (0.2, 0.995, 0.72), (1, 0, 0), 12,
                     closed_profile=True), "paintRed")
    # injection pump P24 + injector pipes + fuel filter (right side, -x)
    body.merge(box((-0.225, 0.72, 0.78), (0.10, 0.17, 0.42), 0.02, 2), g)
    body.merge(box((-0.225, 0.83, 0.62), (0.08, 0.07, 0.12), 0.015, 2), g)
    for i in range(4):
        z = 0.62 + i * 0.1
        zc = 0.44 + i * 0.255
        body.merge(tube([(-0.225, 0.81, z), (-0.23, 0.95, z), (-0.17, 1.06, zc + 0.08), (-0.10, 1.07, zc + 0.08)],
                        0.004, 6), "steelBare")
    body.merge(cylinder((-0.24, 0.84, 1.12), (-0.24, 1.0, 1.12), 0.045, 18, 0.01), "paintGrey")
    body.merge(cylinder((-0.24, 1.0, 1.12), (-0.24, 1.03, 1.12), 0.05, 18, 0.008), "blackMetal")
    # alternator, water pump, fan and belt (front)
    body.merge(cylinder((-0.2, 0.86, 1.23), (-0.2, 0.86, 1.4), 0.07, 20, 0.012), "blackMetal")
    body.merge(cylinder((-0.2, 0.86, 1.4), (-0.2, 0.86, 1.42), 0.045, 20, 0.004), "steelBare")
    body.merge(cylinder((0, 0.84, 1.36), (0, 0.84, 1.42), 0.06, 20, 0.01), g)
    body.merge(cylinder((0, 0.66, 1.36), (0, 0.66, 1.42), 0.085, 24, 0.01), g)
    belt = [(0.0 + 0.09 * math.cos(t), 0.66 + 0.09 * math.sin(t), 1.415) for t in
            [math.radians(a) for a in range(200, 341, 20)]]
    belt += [(-0.2 + 0.05 * math.cos(math.radians(a)), 0.86 + 0.05 * math.sin(math.radians(a)), 1.415)
             for a in range(-20, 181, 25)]
    body.merge(tube(belt, 0.008, 6, closed=True), "rubberTyre")
    fan = new_tmp()
    body.merge(cylinder((0, 0.84, 1.42), (0, 0.84, 1.45), 0.035, 16), "blackMetal")
    for k in range(6):
        a = TAU * k / 6
        c = (0.13 * math.cos(a), 0.84 + 0.13 * math.sin(a), 1.44)
        body.merge(box(c, (0.07, 0.19, 0.008), 0.003, 1, rot=(0, 25, math.degrees(a) - 90)), "blackMetal")
    fan.free()
    # hoses to radiator
    body.merge(tube([(0, 1.06, 1.3), (0, 1.12, 1.4), (0, 1.25, 1.47)], 0.022, 10), "rubberTyre")
    body.merge(tube([(0, 0.72, 1.42), (0.05, 0.72, 1.47), (0.1, 0.78, 1.5)], 0.022, 10), "rubberTyre")
    # exhaust manifold (left, +x) into the vertical pipe; starter motor
    for i in range(4):
        z = 0.44 + i * 0.255
        body.merge(tube([(0.155, 1.0, z), (0.2, 1.0, z), (0.21, 1.02, 0.3 + 0.02 * i)], 0.022, 10), "exhaustRust")
    body.merge(tube([(0.21, 1.02, 1.2), (0.21, 1.02, 0.24), (0.2, 1.12, 0.24), (0.2, 1.3, 0.24)], 0.03, 12),
               "exhaustRust")
    body.merge(cylinder((0.22, 0.58, 0.18), (0.22, 0.58, 0.42), 0.055, 18, 0.01), "blackMetal")
    # bell housing, gearbox, rear axle / final drives, lift housing
    body.merge(lathe([(0.0, 0.3), (0.2, 0.3), (0.26, 0.26), (0.26, 0.1), (0.23, 0.06), (0.0, 0.06)],
                     (0, 0.72, 0), (0, 0, 1), 32), g)
    body.merge(box((0, 0.67, -0.36), (0.42, 0.52, 0.86), 0.035, 3), g)
    for i in range(5):
        body.merge(box((0, 0.46, -0.72 + i * 0.17), (0.43, 0.03, 0.02), 0.006, 1), g)
    body.merge(box((0, 0.70, -1.07), (0.60, 0.60, 0.60), 0.06, 3), g)
    body.merge(box((0, 1.05, -1.15), (0.50, 0.14, 0.58), 0.03, 2), g)
    for s in (1, -1):
        body.merge(cylinder((s * 0.28, YR, ZR), (s * 0.40, YR, ZR), 0.17, 32, 0.02), g)
        body.merge(cylinder((s * 0.40, YR, ZR), (s * 0.56, YR, ZR), 0.11, 28, 0.01, r1=0.1), g)
        body.merge(cylinder((s * 0.56, YR, ZR), (s * 0.64, YR, ZR), 0.13, 28, 0.01), g)
        for k in range(6):
            t = TAU * k / 6
            c = (s * 0.39, YR + 0.15 * math.cos(t), ZR + 0.15 * math.sin(t))
            body.merge(cylinder(c, (s * 0.415, c[1], c[2]), 0.012, 6), "steelBare")
    # front support casting + weight carrier + 8 front weights
    body.merge(box((0, 0.52, 1.52), (0.34, 0.2, 0.5), 0.03, 2), g)
    body.merge(cylinder((-0.31, 0.66, 1.79), (0.31, 0.66, 1.79), 0.022, 12, 0.004), "steelBare")
    for i in range(8):
        x = -0.2625 + i * 0.075
        wprof = rounded_rect(0.26, 0.33, 0.05, 3, rb=0.03)
        bm = sweep(frames_line((x - 0.034, 0.55, 1.79), (x + 0.034, 0.55, 1.79), (0, 0, 1), (0, 1, 0)), wprof)
        body.merge(bm, "paintGrey")
        body.merge(box((x, 0.735, 1.79), (0.05, 0.03, 0.09), 0.012, 2), "paintGrey")
    # steps under the doors
    for s in (1, -1):
        body.merge(box((s * 0.66, 0.60, -0.18), (0.16, 0.012, 0.26), 0.004, 1), "steelBare")
        body.merge(rail([(s * 0.58, 0.95, -0.08), (s * 0.66, 0.60, -0.08)], 0.03, 0.012), "blackMetal")
        body.merge(rail([(s * 0.58, 0.95, -0.30), (s * 0.66, 0.60, -0.30)], 0.03, 0.012), "blackMetal")


def build_fenders(body):
    prof = [(0.50, FENDER_R), (0.905, FENDER_R), (0.925, FENDER_R - 0.008), (0.935, FENDER_R - 0.03),
            (0.936, FENDER_R - 0.075)]
    for s in (1, -1):
        frames = []
        for i in range(41):
            phi = math.radians(12 + 156 * i / 40)
            o = V(0.0, YR, ZR)
            frames.append((o, D(1, 0, 0), D(0, math.sin(phi), -math.cos(phi))))
        bm = solidify(sweep(frames, prof, closed_profile=False, caps=False), 0.004)
        if s < 0:
            mirror_x(bm)
        body.merge(bm, "paintRed")
        pts = []
        pa, pb = math.asin((CAB_FLOOR - YR) / FENDER_R), math.pi - math.asin((CAB_FLOOR - YR) / FENDER_R)
        for i in range(25):
            phi = pa + (pb - pa) * i / 24
            pts.append((s * 0.502, YR + FENDER_R * math.sin(phi), ZR - FENDER_R * math.cos(phi)))
        body.merge(slab(pts, 0.004), "paintRed")
        # rear lamp cluster (tail/brake red, indicator orange) + reflector
        phi = math.radians(38)
        c = (s * 0.82, YR + (FENDER_R + 0.03) * math.sin(phi), ZR - (FENDER_R + 0.03) * math.cos(phi))
        body.merge(box(c, (0.16, 0.075, 0.05), 0.01, 2), "blackMetal")
        zq = c[2] - 0.0255
        body.merge(quad([(c[0] + s * 0.07, c[1] - 0.03, zq), (c[0] + s * 0.005, c[1] - 0.03, zq),
                         (c[0] + s * 0.005, c[1] + 0.03, zq), (c[0] + s * 0.07, c[1] + 0.03, zq)][::s]),
                   "lensRed", uv=None)
        body.merge(quad([(c[0] - s * 0.005, c[1] - 0.03, zq), (c[0] - s * 0.07, c[1] - 0.03, zq),
                         (c[0] - s * 0.07, c[1] + 0.03, zq), (c[0] - s * 0.005, c[1] + 0.03, zq)][::s]),
                   "lensOrange", uv=None)
        body.merge(disc((s * 0.86, 0.96, -1.772), (0, 0, -1), 0.03, 16), "lensRed", uv=None)


def build_cab(body, glass):
    lg = "paintLightGrey"
    x = CAB_X
    zb = -0.50                                  # door rear / B-pillar
    for s in (1, -1):
        for z in (CAB_ZF, zb, CAB_ZR):
            y0 = CAB_FLOOR if z != zb else fender_y(zb, 0.02)
            body.merge(rail([(s * x, y0, z), (s * x, CAB_TOP, z)], 0.045, 0.045), lg)
        body.merge(rail([(s * x, CAB_TOP, CAB_ZF), (s * x, CAB_TOP, CAB_ZR)], 0.05, 0.05), lg)
        body.merge(rail([(s * x, 1.36, CAB_ZF), (s * x, 1.36, zb)], 0.035, 0.03), lg)
        # door: frame follows the fender at its lower rear corner
        door = [(s * (x + 0.012), 1.03, CAB_ZF - 0.03)]
        for i in range(8):
            z = -0.28 - (zb + 0.03 + 0.28) * -i / 7
            door.append((s * (x + 0.012), max(1.03, fender_y(z, 0.03)), z))
        door += [(s * (x + 0.012), CAB_TOP - 0.03, zb + 0.03), (s * (x + 0.012), CAB_TOP - 0.03, CAB_ZF - 0.03)]
        body.merge(rail(door, 0.03, 0.028, 0.006, closed=True), lg)
        glass.merge(slab([(p[0] - s * 0.004, p[1], p[2]) for p in door][::s], 0.005), "glass")
        body.merge(box((s * (x + 0.03), 1.42, zb + 0.07), (0.02, 0.025, 0.11), 0.006, 1), "steelBare")
        for yh in (1.25, 1.85):
            body.merge(cylinder((s * (x + 0.005), yh, CAB_ZF - 0.02), (s * (x + 0.005), yh + 0.07, CAB_ZF - 0.02),
                                0.012, 8), "blackMetal")
        # rear side window above the fender
        side = [(s * x, fender_y(z, 0.035), z) for z in [zb - 0.03 - i * (CAB_ZR - zb + 0.06) / -8 for i in range(9)]]
        side += [(s * x, CAB_TOP - 0.03, CAB_ZR + 0.03), (s * x, CAB_TOP - 0.03, zb - 0.03)]
        glass.merge(slab(side[::s], 0.005), "glass")
        # lower front panels beside the bonnet
        body.merge(slab([(s * 0.30, CAB_FLOOR, CAB_ZF), (s * x, CAB_FLOOR, CAB_ZF), (s * x, 1.36, CAB_ZF),
                         (s * 0.30, 1.36, CAB_ZF)][::s], 0.004), lg)
        # mirror
        body.merge(tube([(s * x, 1.86, CAB_ZF), (s * (x + 0.12), 1.88, CAB_ZF + 0.02), (s * (x + 0.2), 1.89, CAB_ZF)],
                        0.009, 8), "blackMetal")
        body.merge(box((s * (x + 0.22), 1.89, CAB_ZF - 0.005), (0.13, 0.19, 0.025), 0.01, 2), "blackMetal")
        body.merge(box((s * (x + 0.22), 1.89, CAB_ZF - 0.019), (0.115, 0.175, 0.004), 0.001, 1), "steelBare")
    # front: rails, top strip over the bonnet, windscreen, wiper
    for y in (1.36, CAB_TOP):
        body.merge(rail([(x, y, CAB_ZF), (-x, y, CAB_ZF)], 0.045, 0.045), lg)
    body.merge(slab([(0.30, 1.30, CAB_ZF), (-0.30, 1.30, CAB_ZF), (-0.30, 1.36, CAB_ZF), (0.30, 1.36, CAB_ZF)][::-1],
                    0.004), lg)
    glass.merge(slab([(-x + 0.02, 1.38, CAB_ZF), (x - 0.02, 1.38, CAB_ZF), (x - 0.02, CAB_TOP - 0.02, CAB_ZF),
                      (-x + 0.02, CAB_TOP - 0.02, CAB_ZF)], 0.005), "glass")
    body.merge(rail([(0.02, 1.4, CAB_ZF + 0.012), (0.3, 1.78, CAB_ZF + 0.012)], 0.012, 0.006, 0.002), "blackMetal")
    # rear wall + rear window + rear rails
    body.merge(slab([(x, CAB_FLOOR, CAB_ZR), (-x, CAB_FLOOR, CAB_ZR), (-x, 1.28, CAB_ZR), (x, 1.28, CAB_ZR)], 0.004), lg)
    for y in (1.28, CAB_TOP):
        body.merge(rail([(x, y, CAB_ZR), (-x, y, CAB_ZR)], 0.045, 0.045), lg)
    glass.merge(slab([(x - 0.02, 1.3, CAB_ZR), (-x + 0.02, 1.3, CAB_ZR), (-x + 0.02, CAB_TOP - 0.02, CAB_ZR),
                      (x - 0.02, CAB_TOP - 0.02, CAB_ZR)], 0.005), "glass")
    # floor with rubber mat
    body.merge(box((0, CAB_FLOOR - 0.01, (CAB_ZF + CAB_ZR) / 2), (2 * x, 0.02, CAB_ZF - CAB_ZR), 0.004, 1), lg)
    body.merge(box((0, CAB_FLOOR + 0.004, -0.35), (0.9, 0.008, 0.62), 0.002, 1), "rubberTyre")
    # roof: red shell with rolled edges, light-grey lining, rear work lamp + front lamps
    body.merge(box((0, CAB_TOP + 0.055, (CAB_ZF + CAB_ZR) / 2), (2 * x + 0.1, 0.1, CAB_ZF - CAB_ZR + 0.12),
                   0.045, 4), "paintRed")
    body.merge(box((0, CAB_TOP + 0.108, (CAB_ZF + CAB_ZR) / 2), (2 * x - 0.1, 0.02, CAB_ZF - CAB_ZR - 0.1),
                   0.01, 2), "paintRed")
    body.merge(box((0, CAB_TOP - 0.005, (CAB_ZF + CAB_ZR) / 2), (2 * x - 0.02, 0.01, CAB_ZF - CAB_ZR - 0.02),
                   0.003, 1), lg)
    wl = (0.0, CAB_TOP + 0.03, CAB_ZR - 0.1)
    body.merge(box((0, CAB_TOP + 0.01, CAB_ZR - 0.07), (0.05, 0.03, 0.06), 0.008, 1), "blackMetal")
    body.merge(lathe([(0, 0.0), (0.04, 0.005), (0.055, 0.03), (0.058, 0.07), (0, 0.07)], wl, (0, -0.4, -1), 24),
               "blackMetal")
    c = Vector(wl) + Vector((0, -0.4, -1)).normalized() * 0.071
    body.merge(disc(tuple(c), (0, -0.4, -1), 0.052, 24), "lensClear", uv=None)
    for s in (1, -1):
        f = (s * 0.48, CAB_TOP + 0.02, CAB_ZF + 0.12)
        body.merge(lathe([(0, -0.06), (0.035, -0.055), (0.045, -0.02), (0.046, 0.01), (0, 0.01)], f, (0, -0.2, 1), 20),
                   "blackMetal")
        c = Vector(f) + Vector((0, -0.2, 1)).normalized() * 0.011
        body.merge(disc(tuple(c), (0, -0.2, 1), 0.04, 20), "lensClear", uv=None)
    # slow-moving-vehicle triangle on the rear wall
    tri = [(0.0, 1.27, CAB_ZR - 0.012), (-0.17, 0.98, CAB_ZR - 0.012), (0.17, 0.98, CAB_ZR - 0.012)]
    body.merge(slab(tri, 0.004), "paintRed")
    inner = [(0.0, 1.225, CAB_ZR - 0.018), (-0.12, 1.012, CAB_ZR - 0.018), (0.12, 1.012, CAB_ZR - 0.018)]
    body.merge(slab(inner, 0.003), "lensOrange", uv="box")


def build_interior(body, seat, root):
    # dashboard panel = rear face of the bonnet/tank inside the cab, gauges beside the column
    dash_n = DASH_N
    body.merge(slab([(0.29, HOOD_BOT, -0.125), (-0.29, HOOD_BOT, -0.125), (-0.29, 1.3, -0.05), (0.29, 1.3, -0.05)],
                    0.01), "blackMetal")
    tacho = on_dash(*TACHO_XY, 0.004)
    body.merge(lathe([(0.062, -0.012), (0.066, 0.0), (0.066, 0.012), (0.058, 0.014)],
                     tuple(tacho), tuple(dash_n), 32, closed_profile=True), "steelBare")
    body.merge(disc(tuple(tacho + dash_n * 0.002), tuple(dash_n), 0.058, 32), "gauge_tacho", uv=None)
    small = on_dash(*SMALL_XY, 0.004)
    body.merge(lathe([(0.04, -0.01), (0.043, 0.0), (0.043, 0.01), (0.037, 0.012)],
                     tuple(small), tuple(dash_n), 24, closed_profile=True), "steelBare")
    body.merge(disc(tuple(small + dash_n * 0.002), tuple(dash_n), 0.037, 24), "gauge_small", uv=None)
    for i, mat in enumerate(("lensRed", "lensOrange", "lensRed", "lensClear")):
        p = on_dash(-0.035 + 0.07 * (i % 2), 1.08 + 0.035 * (i // 2), 0.002)
        body.merge(disc(tuple(p), tuple(dash_n), 0.009, 12), mat, uv=None)
    key = on_dash(-0.22, 1.1)
    body.merge(cylinder(tuple(key - dash_n * 0.005), tuple(key + dash_n * 0.02), 0.014, 12, 0.003), "steelBare")
    # steering column (37 deg from vertical) + steering wheel node
    d = Vector((0.0, 0.8, -0.6))
    hub = Vector((0.0, 1.58, -0.33))
    body.merge(cylinder(tuple(hub - d * 0.58), tuple(hub - d * 0.05), 0.028, 16, 0.004), "blackMetal")
    body.merge(cylinder(tuple(hub - d * 0.58), tuple(hub - d * 0.45), 0.05, 16, 0.01), "blackMetal")
    col = tg("steeringWheelColumn", tuple(hub), (-36.87, 0, 0), root)
    sw = tg("steeringWheel", tuple(hub), (-36.87, 0, 0), col)        # local rotation 0 0 0
    e1 = Vector((1.0, 0.0, 0.0))
    e2 = e1.cross(d.normalized())
    swp = Part("steeringWheelMesh")
    rim = [tuple(hub + (e1 * math.cos(t) + e2 * math.sin(t)) * 0.205) for t in [TAU * i / 36 for i in range(36)]]
    swp.merge(tube(rim, 0.016, 10, closed=True), "blackMetal")
    for a in (math.radians(-90), math.radians(30), math.radians(150)):
        dirv = e1 * math.cos(a) + e2 * math.sin(a)
        swp.merge(tube([tuple(hub + dirv * 0.03 - d * 0.02), tuple(hub + dirv * 0.12 - d * 0.035),
                        tuple(hub + dirv * 0.195)], 0.009, 8), "blackMetal")
    swp.merge(cylinder(tuple(hub - d * 0.05), tuple(hub + d * 0.012), 0.042, 20, 0.01), "blackMetal")
    swp.build(sw, WORLD[sw.name])
    for name, a, rot in (("leftHandTarget", math.radians(30), (-34.0, -20.6, -130.0)),
                         ("rightHandTarget", math.radians(150), (-34.0, 20.6, 130.0))):
        p = hub + (e1 * math.cos(a) + e2 * math.sin(a)) * 0.205
        tg_local(name, tuple(p), rot, sw)
    # seat (named node) with suspension base
    seat.merge(box((0, 1.205, -0.80), (0.46, 0.09, 0.42), 0.04, 3), "seatVinyl")
    for s in (1, -1):
        seat.merge(box((s * 0.2, 1.235, -0.80), (0.06, 0.05, 0.40), 0.022, 2), "seatVinyl")
    seat.merge(box((0, 1.44, -1.03), (0.44, 0.40, 0.08), 0.035, 3, rot=(-12, 0, 0)), "seatVinyl")
    seat.merge(box((0, 1.12, -0.80), (0.36, 0.06, 0.34), 0.01, 1), "blackMetal")
    seat.merge(box((0, 1.05, -0.82), (0.12, 0.10, 0.12), 0.01, 1), "blackMetal")
    # pedals, gear levers, hydraulic control quadrant
    for (px, mat) in ((0.2, "clutch"), (-0.15, "brakeL"), (-0.22, "brakeR")):
        body.merge(tube([(px, 0.9, 0.05), (px, 1.05, -0.02), (px, 1.1, -0.08)], 0.01, 8), "blackMetal")
        body.merge(box((px, 1.11, -0.09), (0.06, 0.012, 0.09), 0.004, 1, rot=(-35, 0, 0)), "rubberTyre")
    body.merge(tube([(-0.05, CAB_FLOOR, -0.33), (-0.08, 1.25, -0.4), (-0.11, 1.47, -0.46)], 0.011, 8), "blackMetal")
    body.merge(cylinder((-0.11, 1.46, -0.46), (-0.11, 1.51, -0.47), 0.022, 12, 0.01), "blackMetal")
    body.merge(lathe([(0.0, 0.0), (0.05, 0.0), (0.035, 0.06), (0.012, 0.1), (0.0, 0.1)], (-0.05, CAB_FLOOR, -0.33),
                     (-0.02, 1, -0.05), 16), "rubberTyre")
    body.merge(tube([(0.09, CAB_FLOOR, -0.45), (0.11, 1.2, -0.5), (0.13, 1.35, -0.55)], 0.009, 8), "blackMetal")
    body.merge(cylinder((0.13, 1.345, -0.55), (0.13, 1.385, -0.56), 0.018, 12, 0.008), "paintRed")
    body.merge(box((-0.33, 1.2, -0.82), (0.04, 0.14, 0.2), 0.01, 1), "blackMetal")
    for i in range(2):
        z = -0.78 - i * 0.07
        body.merge(tube([(-0.33, 1.26, z), (-0.33, 1.38, z - 0.03)], 0.007, 8), "blackMetal")
        body.merge(cylinder((-0.33, 1.375, z - 0.03), (-0.33, 1.41, z - 0.035), 0.016, 12, 0.007), "paintRed")
    # nodes for the character
    tg("playerSkin", (0.0, 1.25, -0.82), (0, 0, 0), root)
    tg("leftFootTarget", (0.2, 1.12, -0.1), (0, 0, 0), root)
    tg("rightFootTarget", (-0.15, 1.12, -0.1), (0, 0, 0), root)


def build_exhaust(body, root):
    x, z = 0.20, 0.24
    body.merge(cylinder((x, HOOD_TOP - 0.02, z), (x, EXH_TOP, z), 0.031, 20, 0.004), "exhaustRust")
    body.merge(cylinder((x, 1.40, z), (x, 1.82, z), 0.058, 28, 0.012), "steelBare")
    for y in (1.46, 1.61, 1.76):
        body.merge(lathe([(0.059, -0.006), (0.064, -0.004), (0.064, 0.004), (0.059, 0.006)], (x, y, z), (0, 1, 0), 28,
                         closed_profile=True), "steelBare")
    body.merge(lathe([(0.03, -0.012), (0.042, -0.006), (0.042, 0.006), (0.03, 0.012)], (x, HOOD_TOP + 0.005, z),
                     (0, 1, 0), 20, closed_profile=True), "blackMetal")
    hinge = (x, EXH_TOP + 0.003, z + 0.036)
    flap = Part("exhaustFlap")
    flap.merge(cylinder((x, EXH_TOP + 0.001, z), (x, EXH_TOP + 0.007, z), 0.037, 20, 0.001), "exhaustRust")
    flap.merge(cylinder((x - 0.01, EXH_TOP + 0.004, z + 0.036), (x + 0.01, EXH_TOP + 0.004, z + 0.036), 0.005, 8), "exhaustRust")
    flap.build(root, i3d_matrix(hinge))
    tg("exhaustNode", (x, EXH_TOP + 0.005, z), (0, 0, 0), root)
    tg("soundExhaust", (x, EXH_TOP - 0.05, z), (0, 0, 0), root)


def build_front_axle(root, wheels_tg):
    fa = tg("frontAxle", (0.0, 0.47, ZF), (0, 0, 0), root)
    axle = Part("frontAxleMesh")
    axle.merge(box((0, 0.44, ZF), (1.16, 0.085, 0.08), 0.012, 2), "paintGrey")
    axle.merge(cylinder((0, 0.47, ZF - 0.12), (0, 0.47, ZF + 0.12), 0.05, 20, 0.008), "paintGrey")
    for s in (1, -1):
        axle.merge(cylinder((s * 0.6, 0.34, ZF), (s * 0.6, 0.53, ZF), 0.04, 18, 0.006), "paintGrey")
    axle.merge(tube([(0.56, 0.37, ZF - 0.14), (-0.56, 0.37, ZF - 0.14)], 0.015, 10), "steelBare")
    axle.build(fa, WORLD[fa.name])
    # knuckles + front fenders follow steering (children of the wheel repr nodes)
    for s, side in ((1, "Left"), (-1, "Right")):
        repr_node = WORLD[f"wheelFront{side}"]
        f = Part(f"frontFender{side}")
        f.merge(cylinder((s * 0.6, YF, ZF), (s * 0.66, YF, ZF), 0.03, 16, 0.004), "paintGrey")
        f.merge(tube([(s * 0.6, 0.36, ZF), (s * 0.6, 0.37, ZF - 0.13), (s * 0.56, 0.37, ZF - 0.14)], 0.018, 10),
                "paintGrey")
        prof = [(s * 0.63, 0.445), (s * 0.82, 0.445), (s * 0.835, 0.435), (s * 0.84, 0.41)]
        if s < 0:
            prof = prof[::-1]
        frames = []
        for i in range(25):
            phi = math.radians(28 + 124 * i / 24)
            frames.append((V(0.0, YF, ZF), D(1, 0, 0), D(0, math.sin(phi), -math.cos(phi))))
        f.merge(solidify(sweep(frames, prof, closed_profile=False, caps=False), 0.004), "paintRed")
        f.merge(rail([(s * 0.62, YF + 0.02, ZF - 0.03), (s * 0.62, YF + 0.25, ZF - 0.1), (s * 0.68, YF + 0.38, ZF - 0.16)],
                     0.02, 0.035), "blackMetal")
        f.build(bpy.data.objects[f"wheelFront{side}"], repr_node)


def build_hitch(body, root):
    g = "paintGrey"
    # lift arms
    la = tg("liftArm", (0.0, 1.13, -1.26), (0, 0, 0), root)
    lam = Part("liftArmMesh")
    lam.merge(cylinder((-0.33, 1.13, -1.26), (0.33, 1.13, -1.26), 0.03, 16, 0.004), g)
    for s in (1, -1):
        lam.merge(rail([(s * 0.3, 1.13, -1.26), (s * 0.3, 1.06, -1.66)], 0.035, 0.06, 0.01), g)
    lam.build(la, WORLD[la.name])
    # bottom arms (rotation about X, arms extend along -Z) + attacher joint
    ba = tg("bottomArm", (0.0, 0.48, -1.12), (0, 0, 0), root)
    for s, side in ((1, "Left"), (-1, "Right")):
        arm = Part(f"bottomArm{side}")
        p0, p1 = (s * 0.27, 0.48, -1.12), (s * 0.41, 0.40, -1.93)
        arm.merge(rail([p0, p1], 0.028, 0.075, 0.008), g)
        arm.merge(lathe([(0.03, -0.02), (0.045, -0.018), (0.045, 0.018), (0.03, 0.02)], p1, (1, 0, 0), 20,
                        closed_profile=True), "steelBare")
        arm.merge(cylinder((p0[0] - 0.03, p0[1], p0[2]), (p0[0] + 0.03, p0[1], p0[2]), 0.035, 16, 0.005), g)
        att = (s * 0.345, 0.44, -1.55)
        arm.merge(box(att, (0.02, 0.06, 0.05), 0.004, 1), g)
        obj = arm.build(ba, WORLD[ba.name])
        tg(f"liftRod{side}Ref", att, (0, 0, 0), obj)
        # lift rods: node at lift-arm end, local +Z towards the reference point
        top = Vector((s * 0.3, 1.06, -1.66))
        dvec = Vector(att) - top
        rod = tg(f"liftRod{side}", tuple(top), rot_towards(dvec), la)
        rp = Part(f"liftRod{side}Mesh")
        L = dvec.length
        dn = dvec.normalized()
        rp.merge(cylinder(tuple(top), tuple(top + dn * L), 0.014, 10, 0.003), "steelBare")
        rp.merge(cylinder(tuple(top + dn * 0.2), tuple(top + dn * 0.34), 0.022, 6, 0.004), "paintGrey")
        rp.merge(box(tuple(top), (0.05, 0.04, 0.04), 0.006, 1), "paintGrey")
        rp.build(rod, WORLD[rod.name])
    tg("attacherJointBack", (0.0, 0.40, -1.93), (0, 90, 0), ba)
    # top link: rotation node, sliding part, reference eye (all along -Z)
    ta = tg("topArm", (0.0, 0.95, -1.42), (0, 180, 0), root)
    tp = Part("topArmTube")
    tp.merge(cylinder((0, 0.95, -1.42), (0, 0.95, -1.8), 0.024, 12, 0.004), "blackMetal")
    tp.merge(cylinder((0, 0.95, -1.56), (0, 0.95, -1.68), 0.03, 6, 0.004), "blackMetal")
    tp.merge(lathe([(0.018, -0.03), (0.035, -0.028), (0.035, 0.028), (0.018, 0.03)], (0, 0.95, -1.42), (1, 0, 0), 16,
                   closed_profile=True), "steelBare")
    tp.build(ta, WORLD[ta.name])
    tt = tg("topArmTranslation", (0.0, 0.95, -1.78), (0, 180, 0), ta)
    te = Part("topArmEnd")
    te.merge(cylinder((0, 0.95, -1.7), (0, 0.95, -1.93), 0.016, 10, 0.003), "steelBare")
    te.merge(lathe([(0.02, -0.025), (0.038, -0.022), (0.038, 0.022), (0.02, 0.025)], (0, 0.95, -1.96), (1, 0, 0), 16,
                   closed_profile=True), "steelBare")
    te.build(tt, WORLD[tt.name])
    tg("topArmReference", (0.0, 0.95, -1.96), (0, 180, 0), tt)
    # top link bracket, PTO with shield, drawbar, upper hitch, hydraulic couplers
    body.merge(box((0, 0.95, -1.38), (0.12, 0.12, 0.1), 0.01, 1), g)
    body.merge(lathe([(0.0, 0.0), (0.05, 0.0), (0.05, 0.03), (0.03, 0.04), (0.0, 0.04)], (0, 0.655, -1.37),
                     (0, 0, -1), 24), g)
    body.merge(cylinder((0, 0.655, -1.41), (0, 0.655, -1.56), 0.0175, 6, 0.003), "steelBare")
    shield = [(-0.14, 0.60), (-0.14, 0.73), (-0.10, 0.76), (0.10, 0.76), (0.14, 0.73), (0.14, 0.60)]
    bm = sweep(frames_line((0, 0, -1.38), (0, 0, -1.58), (1, 0, 0), (0, 1, 0)), shield, closed_profile=False,
               caps=False)
    body.merge(solidify(bm, 0.004), "paintRed")
    body.merge(box((0, 0.40, -1.38), (0.09, 0.03, 0.9), 0.006, 1), "steelBare")
    body.merge(box((0, 0.38, -0.95), (0.3, 0.05, 0.12), 0.01, 1), g)
    body.merge(cylinder((0, 0.36, -1.8), (0, 0.46, -1.8), 0.014, 10, 0.002), "steelBare")
    body.merge(box((0, 0.80, -1.43), (0.1, 0.12, 0.12), 0.01, 1), g)
    body.merge(cylinder((-0.07, 0.80, -1.5), (0.07, 0.80, -1.5), 0.012, 10, 0.002), "steelBare")
    for i in range(2):
        c = (0.18 + 0.07 * i, 1.08, -1.43)
        body.merge(cylinder(c, (c[0], c[1], c[2] - 0.06), 0.016, 12, 0.003), "steelBare")
        body.merge(cylinder((c[0], c[1], c[2] - 0.06), (c[0], c[1], c[2] - 0.08), 0.02, 12, 0.005), "paintRed")
    tg("ptoBack", (0.0, 0.655, -1.56), (0, 180, 0), root)
    tg("attacherJointTrailerLow", (0.0, 0.42, -1.80), (0, 90, 0), root)
    tg("attacherJointTrailer", (0.0, 0.80, -1.50), (0, 90, 0), root)


def build_needles(root):
    dash_n = DASH_N
    rot = (-(90.0 - math.degrees(math.acos(dash_n.y))), 180.0, 0.0)   # local +Z = dash normal
    for name, c, L in (("rpmNeedle", on_dash(*TACHO_XY, 0.009), 0.05),
                       ("fuelNeedle", on_dash(*SMALL_XY, 0.009), 0.032)):
        par = tg(name + "Rot", tuple(c), rot, root)
        m = WORLD[par.name]
        p = Part(name)
        # needle drawn along local +Y in the local XY plane (dial plane), pivot at origin
        h = (L + 0.01) / 0.844               # needle texture pivot sits at v = 0.156
        loc = [(-h / 8, -0.156 * h), (h / 8, -0.156 * h), (h / 8, 0.844 * h), (-h / 8, 0.844 * h)]
        bm = new_tmp()
        layer = bm.loops.layers.uv.active
        vs = [bm.verts.new(m @ (CONV_INV @ Vector((px, py, 0.0)))) for (px, py) in loc]
        f = bm.faces.new(vs)
        for loop, uv in zip(f.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
            loop[layer].uv = uv
        f.normal_update()
        if f.normal.dot(V(tuple(dash_n))) < 0:
            f.normal_flip()
        p.merge(bm, "needle", uv=None)
        p.build(par, m)


def build_lights_cameras(root):
    lights = empty("lights", i3d_matrix((0, 0, 0)), root)

    def light(name, kind, pos, rot, color, rng, cone=60.0, energy=40.0):
        ld = bpy.data.lights.new(name, kind)
        ld.color = color
        ld.energy = energy
        if kind == "SPOT":
            ld.spot_size = math.radians(cone)
            ld.spot_blend = 0.4
        obj = bpy.data.objects.new(name, ld)
        obj["i3d_range"] = rng
        obj["i3d_coneAngle"] = cone
        place(obj, i3d_matrix_camlight(pos, rot), lights)

    for s, side in ((1, "Left"), (-1, "Right")):
        light(f"frontLight{side}", "SPOT", (s * 0.388, 1.0, 1.60), (-5, 180, 0), (1.0, 0.94, 0.82), 45.0, 70.0)
        rl = (s * 0.82, YR + (FENDER_R + 0.03) * math.sin(math.radians(38)),
              ZR - (FENDER_R + 0.03) * math.cos(math.radians(38)) - 0.05)
        light(f"tailLight{side}", "POINT", rl, (0, 0, 0), (1.0, 0.04, 0.02), 1.5, energy=2.0)
        light(f"brakeLight{side}", "POINT", rl, (0, 0, 0), (1.0, 0.03, 0.02), 3.0, energy=5.0)
        light(f"turnLight{side}Front", "POINT", (s * 0.335, 1.165, 1.58), (0, 0, 0), (1.0, 0.45, 0.0), 2.0, energy=3.0)
        light(f"turnLight{side}Back", "POINT", (rl[0] - s * 0.04, rl[1], rl[2]), (0, 0, 0), (1.0, 0.45, 0.0), 2.0,
              energy=3.0)
    light("workLightBack", "SPOT", (0.0, CAB_TOP + 0.03, CAB_ZR - 0.18), (-25, 0, 0), (1.0, 0.95, 0.85), 25.0, 90.0)
    # cameras
    oct_ = tg("outdoorCameraTarget", (0.0, 1.5, 0.0), (0, 0, 0), root)
    cam = bpy.data.objects.new("outdoorCamera", bpy.data.cameras.new("outdoorCamera"))
    cam.data.lens_unit = "FOV"
    cam.data.angle = math.radians(60)
    place(cam, i3d_matrix_camlight((0.0, 1.5, -8.0), (0, 180, 0)), oct_)
    head = (0.0, 1.9, -0.84)
    ict = tg("indoorCameraTarget", head, (0, 0, 0), root)
    cam = bpy.data.objects.new("indoorCamera", bpy.data.cameras.new("indoorCamera"))
    cam.data.lens_unit = "FOV"
    cam.data.angle = math.radians(70)
    place(cam, i3d_matrix_camlight(head, (0, 180, 0)), ict)


def build_collisions(root):
    col = tg("collisions", (0, 0, 0), (0, 0, 0), root)
    props = dict(i3d_collision=True, i3d_compoundChild=True, i3d_nonRenderable=True)
    for name, c, sz in (("collisionCab", (0, 1.645, -0.56), (1.24, 1.33, 1.25)),
                        ("collisionFenderLeft", (0.73, 1.16, -1.06), (0.46, 0.62, 1.5)),
                        ("collisionFenderRight", (-0.73, 1.16, -1.06), (0.46, 0.62, 1.5)),
                        ("collisionRear", (0, 0.8, -1.3), (0.7, 0.7, 0.62))):
        p = Part(name)
        p.merge(box(c, sz), "collision")
        p.build(col, **props)


# ---------------------------------------------------------------- main

def build():
    clear_scene()
    setup_materials()
    comp = Part("ursusC360_main_component1")
    comp.merge(box((0, 0.87, 0.24), (0.62, 0.92, 3.3)), "collision")
    root = comp.build(None, i3d_matrix((0, 0, 0)), i3d_rigidBody="dynamic", i3d_compound=True,
                      i3d_collision=True, i3d_nonRenderable=True)
    build_collisions(root)
    fill = Part("exactFillRootNodeFuel")                 # refuelling target above the filler cap
    fill.merge(box((-0.14, 1.40, 0.30), (0.30, 0.20, 0.30)), "collision")
    fill.build(root, i3d_rigidBody="kinematic", i3d_nonRenderable=True)
    # wheels (repr + visual drive nodes)
    wheels = tg("wheels", (0, 0, 0), (0, 0, 0), root)
    for s, side in ((1, "Left"), (-1, "Right")):
        for front in (True, False):
            nm = f"wheel{'Front' if front else 'Back'}{side}"
            c = (s * XF, YF, ZF) if front else (s * XR, YR, ZR)
            rep = tg(nm, c, (0, 0, 0), wheels)
            vis = tg(nm + "Visual", c, (0, 0, 0), rep)
            p = Part(nm + "Mesh")
            (front_wheel if front else rear_wheel)(p, s)
            p.build(vis, WORLD[vis.name], smooth_angle=50.0)
    visuals = tg("visuals", (0, 0, 0), (0, 0, 0), root)
    body, glass, seat = Part("body"), Part("glass"), Part("seat")
    build_hood(body)
    build_engine(body)
    build_fenders(body)
    build_cab(body, glass)
    build_interior(body, seat, root)
    build_exhaust(body, root)
    build_hitch(body, root)
    build_front_axle(root, wheels)
    build_needles(root)
    body.build(visuals)
    glass.build(visuals, i3d_castsShadows=False)
    seat.build(root)
    build_lights_cameras(root)
    for name, pos in (("exitPoint", (1.3, 0.05, -0.45)), ("enterReferenceNode", (0.75, 1.3, -0.25)),
                      ("soundMotor", (0.0, 0.8, 0.9)), ("soundCabin", (0.0, 1.88, -0.84))):
        tg(name, pos, (0, 0, 0), root)
    bpy.context.view_layer.update()
    return root


if __name__ == "__main__":
    build()
    print("built objects:", len(bpy.data.objects))
