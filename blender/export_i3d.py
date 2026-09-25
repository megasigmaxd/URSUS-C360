"""Minimal GIANTS i3d 1.6 exporter for the procedural Ursus C-360 scene.

Writes inline IndexedTriangleSet geometry (tools/i3d/binarize_i3d.py converts it to a
binary .i3d.shapes file afterwards) plus an i3dMappings fragment (id = node name).
Custom object properties drive i3d attributes (i3d_rigidBody, i3d_compound, etc).
"""
import math
import os
from xml.sax.saxutils import quoteattr

import bpy

from ursus_lib import CONV, CONV_INV

# FS25 collision filters (group = what the node is, mask = what it collides with), as written by
# GIANTS Editor 10 on dynamic compound vehicle components and their compound children.
COLLISION = {
    "component": ("0x10004", "0xfe3ffb83"),
    "compoundChild": ("0x10004", "0xfe3ffb83"),
    "exactFillRootNode": ("0x40000000", "0x20000000"),   # FILLABLE target for fuel stations
}
NORMAL_FALLBACK = {"lensClear": "lens", "lensRed": "lens", "lensOrange": "lens"}
TEXTURE_SLOTS = (("Texture", "diffuse"), ("Normalmap", "normal"), ("Glossmap", "specular"))
ALPHA_BLENDED = ("glass", "decal_frontLogo", "needle")


def _f(v):
    s = f"{v:.6g}"
    return "0" if s in ("-0", "-0.0") else s


def _vec(values):
    return " ".join(_f(v) for v in values)


def world_i3d(obj):
    w = obj.matrix_world
    if obj.type in ("LIGHT", "CAMERA"):
        return CONV @ w
    return CONV @ w @ CONV_INV


def local_i3d(obj):
    wi = world_i3d(obj)
    return wi if obj.parent is None else world_i3d(obj.parent).inverted() @ wi


def _transform_attrs(obj):
    m = local_i3d(obj)
    t = m.to_translation()
    r = [math.degrees(a) for a in m.to_euler("XYZ")]
    out = []
    if max(abs(c) for c in t) > 1e-7:
        out.append(("translation", _vec(t)))
    if max(abs(c) for c in r) > 1e-5:
        out.append(("rotation", _vec(r)))
    return out


def _children(obj):
    return sorted(obj.children, key=lambda o: o.name)


class Exporter:
    def __init__(self, mod_dir):
        self.mod_dir = mod_dir
        self.files = []
        self.materials = {}
        self.shapes = []
        self.node_id = 0
        self.mappings = []
        self.stats = {"shapes": 0, "tris": 0, "verts": 0}

    def _file_id(self, rel):
        if rel not in self.files:
            self.files.append(rel)
        return self.files.index(rel) + 1

    def _texture(self, mat_name, kind):
        base = NORMAL_FALLBACK.get(mat_name, mat_name) if kind == "normal" else mat_name
        cands = [f"textures/{base}_{kind}.dds", f"textures/{mat_name}_{kind}.dds"]
        if kind == "diffuse":
            cands.append(f"textures/{mat_name}.dds")
        for cand in cands:
            if os.path.exists(os.path.join(self.mod_dir, cand)):
                return cand
        return None

    def material_id(self, mat):
        if mat.name in self.materials:
            return self.materials[mat.name][0]
        mid = len(self.materials) + 1
        attrs = [("name", mat.name), ("materialId", str(mid))]
        children = []
        if self._texture(mat.name, "diffuse") is None:
            attrs.append(("diffuseColor", _vec(mat.diffuse_color)))
        if mat.name in ALPHA_BLENDED:
            attrs.append(("alphaBlending", "true"))
        for tag, kind in TEXTURE_SLOTS:
            rel = self._texture(mat.name, kind)
            if rel:
                children.append(f'      <{tag} fileId="{self._file_id(rel)}"/>')
        if self._texture(mat.name, "specular") is None:
            bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.use_nodes else None
            rough = bsdf.inputs["Roughness"].default_value if bsdf else 0.5
            metal = bsdf.inputs["Metallic"].default_value if bsdf else 0.0
            attrs.append(("specularColor", _vec((1.0 - rough, 0.5, metal))))
        head = "    <Material " + " ".join(f"{k}={quoteattr(v)}" for k, v in attrs)
        xml = head + (">\n" + "\n".join(children) + "\n    </Material>" if children else "/>")
        self.materials[mat.name] = (mid, xml)
        return mid

    def shape(self, obj):
        me = obj.data
        me.calc_loop_triangles()
        uvl = me.uv_layers.active.data if me.uv_layers.active else None
        cn = me.corner_normals
        c3 = CONV.to_3x3()
        loops, verts_co = me.loops, me.vertices
        per_mat = {}
        for tri in me.loop_triangles:
            per_mat.setdefault(tri.material_index, []).append(tri)
        vlines, tlines, subsets, mat_ids = [], [], [], []
        first_vertex = first_index = 0
        for mi in sorted(per_mat):
            vmap, local, tris_out = {}, [], []
            for tri in per_mat[mi]:
                ids = []
                for li in tri.loops:
                    vi = loops[li].vertex_index
                    n = cn[li].vector
                    uv = uvl[li].uv if uvl else (0.0, 0.0)
                    key = (vi, round(n.x, 3), round(n.y, 3), round(n.z, 3), round(uv[0], 5), round(uv[1], 5))
                    j = vmap.get(key)
                    if j is None:
                        j = vmap[key] = len(local)
                        local.append((c3 @ verts_co[vi].co, (c3 @ n).normalized(), (uv[0], uv[1])))
                    ids.append(first_vertex + j)
                tris_out.append(ids)
            for p, n, uv in local:
                vlines.append(f'        <v p="{_vec(p)}" n="{_vec(n)}" t0="{_f(uv[0])} {_f(uv[1])}"/>')
            for a, b, c in tris_out:
                tlines.append(f'        <t vi="{a} {b} {c}"/>')
            subsets.append(f'        <Subset firstVertex="{first_vertex}" numVertices="{len(local)}" '
                           f'firstIndex="{first_index}" numIndices="{3 * len(tris_out)}"/>')
            mat = me.materials[mi] if mi < len(me.materials) else bpy.data.materials["collision"]
            mat_ids.append(self.material_id(mat))
            first_vertex += len(local)
            first_index += 3 * len(tris_out)
        shape_id = len(self.shapes) + 1
        self.shapes.append("\n".join([
            f'    <IndexedTriangleSet name={quoteattr(obj.name)} shapeId="{shape_id}">',
            f'      <Vertices count="{first_vertex}" normal="true" uv0="true">', *vlines, "      </Vertices>",
            f'      <Triangles count="{first_index // 3}">', *tlines, "      </Triangles>",
            f'      <Subsets count="{len(subsets)}">', *subsets, "      </Subsets>",
            "    </IndexedTriangleSet>"]))
        self.stats["shapes"] += 1
        self.stats["tris"] += first_index // 3
        self.stats["verts"] += first_vertex
        return shape_id, mat_ids

    def node(self, obj, path, depth):
        self.node_id += 1
        self.mappings.append((obj.name, path))
        attrs = [("name", obj.name)] + _transform_attrs(obj)
        pad = "  " * depth
        if obj.type == "MESH":
            tag = "Shape"
            shape_id, mat_ids = self.shape(obj)
            attrs.append(("shapeId", str(shape_id)))
            if obj.get("i3d_rigidBody") == "dynamic":
                g, m = COLLISION["component"]
                attrs += [("dynamic", "true"), ("compound", "true"), ("collisionFilterGroup", g),
                          ("collisionFilterMask", m), ("clipDistance", "300")]
            elif obj.get("i3d_rigidBody") == "kinematic":
                g, m = COLLISION["exactFillRootNode"]
                attrs += [("kinematic", "true"), ("compound", "true"), ("collisionFilterGroup", g),
                          ("collisionFilterMask", m)]
            elif obj.get("i3d_compoundChild"):
                g, m = COLLISION["compoundChild"]
                attrs += [("compoundChild", "true"), ("collisionFilterGroup", g), ("collisionFilterMask", m),
                          ("density", "0.001")]
            attrs.append(("nodeId", str(self.node_id)))
            nonrender = bool(obj.get("i3d_nonRenderable"))
            shadows = "false" if (nonrender or obj.get("i3d_castsShadows") is False) else "true"
            attrs += [("castsShadows", shadows), ("receiveShadows", "false" if nonrender else "true")]
            if nonrender:
                attrs.append(("nonRenderable", "true"))
            attrs.append(("materialIds", " ".join(str(i) for i in mat_ids)))
        elif obj.type == "LIGHT":
            tag = "Light"
            ld = obj.data
            attrs += [("type", "spot" if ld.type == "SPOT" else "point"), ("color", _vec(ld.color)),
                      ("emitDiffuse", "true"), ("emitSpecular", "true"), ("castShadowMap", "false"),
                      ("decayRate", "4"), ("range", _f(obj.get("i3d_range", 10.0)))]
            if ld.type == "SPOT":
                attrs += [("coneAngle", _f(obj.get("i3d_coneAngle", 60.0))), ("dropOff", "3")]
            attrs.append(("nodeId", str(self.node_id)))
        elif obj.type == "CAMERA":
            tag = "Camera"
            attrs += [("fov", _f(math.degrees(obj.data.angle_y))), ("nearClip", "0.05"), ("farClip", "5000"),
                      ("nodeId", str(self.node_id))]
        else:
            tag = "TransformGroup"
            attrs.append(("nodeId", str(self.node_id)))
        head = f"{pad}<{tag} " + " ".join(f"{k}={quoteattr(v)}" for k, v in attrs)
        kids = _children(obj)
        if not kids:
            return [head + "/>"]
        lines = [head + ">"]
        for i, child in enumerate(kids):
            child_path = f"{path}{i}" if path.endswith(">") else f"{path}|{i}"
            lines += self.node(child, child_path, depth + 1)
        lines.append(f"{pad}</{tag}>")
        return lines

    def write(self, roots, i3d_path, mappings_path=None):
        scene = []
        for ri, root in enumerate(roots):
            scene += self.node(root, f"{ri}>", 2)
        name = os.path.splitext(os.path.basename(i3d_path))[0]
        out = ['<?xml version="1.0" encoding="iso-8859-1"?>',
               f'<i3D name="{name}" version="1.6" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
               'xsi:noNamespaceSchemaLocation="http://i3d.giants.ch/schema/i3d-1.6.xsd">',
               '  <Asset>', '    <Export program="Hoplite Blender pipeline (blender/export_i3d.py)" version="1.0"/>',
               '  </Asset>', '  <Files>']
        out += [f'    <File fileId="{i + 1}" filename={quoteattr(f)}/>' for i, f in enumerate(self.files)]
        out += ['  </Files>', '  <Materials>']
        out += [xml for (_, xml) in sorted(self.materials.values())]
        out += ['  </Materials>', '  <Shapes>', *self.shapes, '  </Shapes>', '  <Dynamics>', '  </Dynamics>',
                '  <Scene>', *scene, '  </Scene>', '</i3D>', '']
        with open(i3d_path, "w", encoding="iso-8859-1", newline="\n") as fh:
            fh.write("\n".join(out))
        if mappings_path:
            os.makedirs(os.path.dirname(mappings_path), exist_ok=True)
            with open(mappings_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("    <i3dMappings>\n")
                for nm, p in self.mappings:
                    fh.write(f'        <i3dMapping id="{nm}" node="{p}"/>\n')
                fh.write("    </i3dMappings>\n")
        return self.stats


def export(root, i3d_path, mappings_path=None, mod_dir=None):
    """Write the inline i3d; texture paths are resolved relative to `mod_dir`."""
    bpy.context.view_layer.update()
    os.makedirs(os.path.dirname(os.path.abspath(i3d_path)), exist_ok=True)
    exp = Exporter(mod_dir or os.path.dirname(os.path.abspath(i3d_path)))
    return exp.write([root], i3d_path, mappings_path)
