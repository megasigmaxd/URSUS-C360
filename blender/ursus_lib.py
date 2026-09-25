"""Geometry toolkit for the procedural Ursus C-360 model (Blender 4.5, bmesh).

Builders take positions in *vehicle* coordinates, i.e. the i3d/GIANTS frame
(x = left, y = up, z = forward, metres); V() converts them to Blender world space
(vehicle front = Blender -Y, vehicle left = Blender +X, up = +Z).
"""
import math

import bmesh
import bpy
from mathutils import Matrix, Vector

TAU = 2.0 * math.pi

# Blender world -> i3d world (x, y, z) -> (x, z, -y); proper rotation (det = +1).
CONV = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
CONV_INV = CONV.inverted()


def V(x, y=None, z=None):
    if y is None:
        x, y, z = x
    return Vector((x, -z, y))


def D(x, y=None, z=None):
    """Direction in vehicle coordinates -> normalized Blender vector."""
    return V(x, y, z).normalized()


def i3d_matrix(pos, rot_deg=(0.0, 0.0, 0.0)):
    """Blender world matrix of a regular node given i3d position + i3d euler rotation (XYZ)."""
    rx, ry, rz = (math.radians(a) for a in rot_deg)
    m = Matrix.Translation(Vector(pos)) @ (
        Matrix.Rotation(rz, 4, "Z") @ Matrix.Rotation(ry, 4, "Y") @ Matrix.Rotation(rx, 4, "X")
    )
    return CONV_INV @ m @ CONV


def i3d_matrix_camlight(pos, rot_deg=(0.0, 0.0, 0.0)):
    """Blender world matrix for a Camera/Light node (both look down local -Z in i3d and Blender)."""
    rx, ry, rz = (math.radians(a) for a in rot_deg)
    m = Matrix.Translation(Vector(pos)) @ (
        Matrix.Rotation(rz, 4, "Z") @ Matrix.Rotation(ry, 4, "Y") @ Matrix.Rotation(rx, 4, "X")
    )
    return CONV_INV @ m


# ---------------------------------------------------------------- scene / hierarchy

WORLD = {}  # object name -> intended world matrix (avoids depsgraph round-trips)


def clear_scene():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                 bpy.data.cameras, bpy.data.images, bpy.data.curves):
        for item in list(coll):
            coll.remove(item)
    WORLD.clear()


def place(obj, world, parent=None):
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj.matrix_parent_inverse = Matrix.Identity(4)
    pw = WORLD[parent.name] if parent is not None else Matrix.Identity(4)
    obj.matrix_basis = pw.inverted() @ world
    WORLD[obj.name] = world.copy()
    return obj


def empty(name, world, parent=None, size=0.05, **props):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_size = size
    obj.empty_display_type = "ARROWS"
    for k, v in props.items():
        obj[k] = v
    return place(obj, world, parent)


def tg(name, pos, rot=(0, 0, 0), parent=None, **props):
    """TransformGroup at i3d position/rotation (vehicle coordinates)."""
    return empty(name, i3d_matrix(pos, rot), parent, **props)


# ---------------------------------------------------------------- mesh parts

class Part:
    """Geometry for one exported Shape (multi-material via face material_index)."""

    def __init__(self, name):
        self.name = name
        self.bm = bmesh.new()
        self.bm.loops.layers.uv.new("UVMap")
        self.mats = []

    def mat_index(self, mat):
        if mat not in self.mats:
            self.mats.append(mat)
        return self.mats.index(mat)

    def merge(self, tmp, mat, uv="box", uv_scale=1.0):
        """Append temporary bmesh `tmp` (geometry in Blender world space)."""
        tmp.normal_update()
        if uv == "box":
            uv_box(tmp, uv_scale)
        idx = self.mat_index(mat)
        for f in tmp.faces:
            f.material_index = idx
        me = bpy.data.meshes.new("_tmp")
        tmp.to_mesh(me)
        self.bm.from_mesh(me)
        bpy.data.meshes.remove(me)
        tmp.free()

    def build(self, parent=None, origin=None, smooth_angle=40.0, **props):
        """Create the Blender object; `origin` (Blender world matrix) becomes the object pivot."""
        origin = origin if origin is not None else Matrix.Identity(4)
        self.bm.transform(origin.inverted())
        me = bpy.data.meshes.new(self.name)
        self.bm.to_mesh(me)
        self.bm.free()
        for m in self.mats:
            me.materials.append(bpy.data.materials[m])
        me.shade_smooth()
        me.set_sharp_from_angle(angle=math.radians(smooth_angle))
        obj = bpy.data.objects.new(self.name, me)
        for k, v in props.items():
            obj[k] = v
        return place(obj, origin, parent)


def new_tmp():
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    return bm


def uv_box(bm, scale=1.0):
    """Tri-planar box projection in world metres (tiling materials, 1 UV = 1 m)."""
    layer = bm.loops.layers.uv.active
    for f in bm.faces:
        n = f.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        for loop in f.loops:
            c = loop.vert.co
            if ax == 0:
                u, v = c.y, c.z
            elif ax == 1:
                u, v = c.x, c.z
            else:
                u, v = c.x, c.y
            loop[layer].uv = (u * scale, v * scale)


def recalc(bm):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])


# ---------------------------------------------------------------- 2D profiles

def rounded_rect(w, h, r, seg=4, rb=None, cx=0.0, cy=0.0):
    """Closed CCW rounded rectangle (x, y) centred at (cx, cy); rb = bottom corner radius."""
    rb = r if rb is None else rb
    pts = []
    for (x, y, a0, rr) in ((w / 2 - r, h / 2 - r, 0, r), (-w / 2 + r, h / 2 - r, 90, r),
                           (-w / 2 + rb, -h / 2 + rb, 180, rb), (w / 2 - rb, -h / 2 + rb, 270, rb)):
        if rr <= 1e-6:
            pts.append((cx + x, cy + y))
            continue
        for i in range(seg + 1):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.append((cx + x + rr * math.cos(a), cy + y + rr * math.sin(a)))
    return pts


def circle(r, seg=16, cx=0.0, cy=0.0):
    return [(cx + r * math.cos(TAU * i / seg), cy + r * math.sin(TAU * i / seg)) for i in range(seg)]


# ---------------------------------------------------------------- 3D builders (temp bmesh)

def _ring(bm, frame, profile):
    o, xa, ya = frame
    return [bm.verts.new(o + xa * px + ya * py) for (px, py) in profile]


def sweep(frames, profile, closed_profile=True, closed_path=False, caps=True):
    """Sweep a 2D profile through frames [(origin, xaxis, yaxis)] (Blender vectors)."""
    bm = new_tmp()
    rings = [_ring(bm, fr, profile) for fr in frames]
    n = len(profile)
    seg_count = len(rings) if closed_path else len(rings) - 1
    for j in range(seg_count):
        a, b = rings[j], rings[(j + 1) % len(rings)]
        for i in range(n if closed_profile else n - 1):
            i2 = (i + 1) % n
            bm.faces.new((a[i], a[i2], b[i2], b[i]))
    if caps and closed_profile and not closed_path:
        bm.faces.new(rings[0][::-1])
        bm.faces.new(rings[-1])
    recalc(bm)
    return bm


def frames_line(p0, p1, xdir, ydir, steps=1):
    """Frames from vehicle point p0 to p1 with profile axes given as vehicle directions."""
    a, b = V(p0), V(p1)
    xa, ya = D(xdir), D(ydir)
    return [(a.lerp(b, i / steps), xa, ya) for i in range(steps + 1)]


def frames_path(points, up=(0, 1, 0)):
    """Parallel-transport frames along a polyline of vehicle points."""
    pts = [V(p) for p in points]
    frames = []
    upv = D(up)
    prev_x = None
    for i, p in enumerate(pts):
        if i == 0:
            t = (pts[1] - pts[0])
        elif i == len(pts) - 1:
            t = (pts[-1] - pts[-2])
        else:
            t = (pts[i + 1] - pts[i - 1])
        t.normalize()
        if prev_x is None:
            ref = upv if abs(t.dot(upv)) < 0.95 else Vector((1, 0, 0))
            xa = ref.cross(t).normalized()
        else:
            xa = (prev_x - t * prev_x.dot(t)).normalized()
        ya = t.cross(xa).normalized()
        frames.append((p, xa, ya))
        prev_x = xa
    return frames


def tube(points, r, seg=12, closed=False, caps=True):
    prof = circle(r, seg)
    pts = list(points)
    if closed:
        fr = frames_path(pts + [pts[0], pts[1]])[:len(pts)]
        return sweep(fr, prof, closed_path=True)
    return sweep(frames_path(pts), prof, caps=caps)


def lathe(profile, center, axis, seg=32, closed_profile=False, phase=0.0):
    """Revolve (radius, axial) profile around vehicle axis through vehicle point `center`."""
    bm = new_tmp()
    c, ax = V(center), D(axis)
    ref = Vector((0, 0, 1)) if abs(ax.z) < 0.9 else Vector((1, 0, 0))
    u = ax.cross(ref).normalized()
    w = ax.cross(u).normalized()
    rings = []
    for (r, a) in profile:
        if r <= 1e-7:
            rings.append([bm.verts.new(c + ax * a)])
            continue
        ring = []
        for i in range(seg):
            t = TAU * i / seg + phase
            ring.append(bm.verts.new(c + ax * a + (u * math.cos(t) + w * math.sin(t)) * r))
        rings.append(ring)
    count = len(rings) if closed_profile else len(rings) - 1
    for j in range(count):
        r0, r1 = rings[j], rings[(j + 1) % len(rings)]
        for i in range(seg):
            i2 = (i + 1) % seg
            if len(r0) == 1 and len(r1) == 1:
                continue
            if len(r0) == 1:
                bm.faces.new((r0[0], r1[i], r1[i2]))
            elif len(r1) == 1:
                bm.faces.new((r0[i], r1[0], r0[i2]))
            else:
                bm.faces.new((r0[i], r1[i], r1[i2], r0[i2]))
    if not closed_profile:
        for ring in (rings[0], rings[-1]):
            if len(ring) > 2:
                bm.faces.new(ring)
    recalc(bm)
    return bm


def cylinder(p0, p1, r, seg=20, bevel=0.0, r1=None):
    r1 = r if r1 is None else r1
    a, b = Vector(p0), Vector(p1)
    length = (b - a).length
    ax = tuple((b - a).normalized())
    bv = min(bevel, r * 0.5, length * 0.3)
    if bv > 0:
        prof = [(0, 0), (r - bv, 0), (r, bv), (r1, length - bv), (r1 - bv, length), (0, length)]
    else:
        prof = [(0, 0), (r, 0), (r1, length), (0, length)]
    return lathe(prof, tuple(a), ax, seg)


def box(center, size, bevel=0.0, seg=2, rot=None):
    """Rounded box; center/size in vehicle coordinates, optional i3d euler rotation (deg)."""
    bm = new_tmp()
    bmesh.ops.create_cube(bm, size=1.0)
    sx, sy, sz = size
    bm.transform(Matrix.Diagonal((sx, sy, sz, 1.0)))
    if bevel > 0:
        bv = min(bevel, 0.49 * min(size))
        bmesh.ops.bevel(bm, geom=bm.verts[:] + bm.edges[:], offset=bv, segments=seg,
                        profile=0.5, affect="EDGES", clamp_overlap=True)
    # vertices are in i3d-local axes: place with the i3d transform, then convert to Blender
    bm.transform(i3d_matrix_camlight(center, rot or (0, 0, 0)))
    recalc(bm)
    return bm


def slab(points, thickness):
    """Closed polygon (vehicle points, planar) extruded by thickness along its normal."""
    bm = new_tmp()
    vs = [bm.verts.new(V(p)) for p in points]
    f = bm.faces.new(vs)
    f.normal_update()
    n = f.normal.copy()
    ret = bmesh.ops.extrude_face_region(bm, geom=[f])
    new_verts = [e for e in ret["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=new_verts, vec=n * thickness)
    recalc(bm)
    return bm


def solidify(bm, thickness):
    bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=thickness)
    recalc(bm)
    return bm


def quad(corners, uvs=((0, 0), (1, 0), (1, 1), (0, 1))):
    """Single-sided quad (vehicle points, CCW seen from the visible side) with explicit UVs."""
    bm = new_tmp()
    layer = bm.loops.layers.uv.active
    f = bm.faces.new([bm.verts.new(V(p)) for p in corners])
    for loop, uv in zip(f.loops, uvs):
        loop[layer].uv = uv
    return bm


def disc(center, normal, r, seg=24, uv=True):
    """Single-sided disc facing `normal` (vehicle), UV 0..1 across the disc."""
    bm = new_tmp()
    layer = bm.loops.layers.uv.active
    c, n = V(center), D(normal)
    ref = Vector((0, 0, 1)) if abs(n.z) < 0.9 else Vector((0, 1, 0))
    u = ref.cross(n).normalized()
    w = n.cross(u).normalized()
    vs = [bm.verts.new(c + (u * math.cos(TAU * i / seg) + w * math.sin(TAU * i / seg)) * r)
          for i in range(seg)]
    f = bm.faces.new(vs)
    if uv:
        for loop, i in zip(f.loops, range(seg)):
            loop[layer].uv = (0.5 + 0.5 * math.cos(TAU * i / seg), 0.5 + 0.5 * math.sin(TAU * i / seg))
    f.normal_update()
    if f.normal.dot(n) < 0:
        f.normal_flip()
    return bm


def mirror_x(bm):
    """Mirror a temp bmesh across the vehicle YZ plane (Blender X) and fix winding."""
    bm.transform(Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0)))
    for f in bm.faces:
        f.normal_flip()
    bm.normal_update()
    return bm


def copy_tmp(bm):
    me = bpy.data.meshes.new("_copy")
    bm.to_mesh(me)
    out = new_tmp()
    out.from_mesh(me)
    bpy.data.meshes.remove(me)
    return out


def transform_tmp(bm, blender_matrix):
    bm.transform(blender_matrix)
    return bm
