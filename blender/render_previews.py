"""Cycles preview renders of the built Ursus C-360 (.blend saved by build_all.py).

    blender -b /tmp/work/ursusC360.blend -P blender/render_previews.py -- --out docs/renders
    ... -- --store build/store.png   (512x512 transparent shop image, also used for the icon)
"""
import argparse
import math
import os
import sys

import bpy
from mathutils import Vector


def V(x, y, z):
    return Vector((x, -z, y))


VIEWS = {
    "front_left": (V(3.9, 1.85, 4.9), V(0.0, 0.95, 0.05), 38),
    "rear_right": (V(-4.3, 2.3, -4.6), V(0.0, 1.0, -0.2), 38),
    "side_left": (V(8.5, 1.15, 0.05), V(0.0, 1.1, 0.05), 50),
    "front": (V(0.0, 1.25, 7.0), V(0.0, 1.05, 0.0), 45),
    "engine": (V(1.35, 1.2, 1.45), V(0.0, 0.85, 0.75), 30),
    "cab_interior": (V(0.0, 1.95, -0.86), V(0.0, 1.25, 0.35), 18),
    "hitch": (V(-1.6, 1.3, -3.4), V(0.0, 0.7, -1.6), 32),
}


def look(cam, eye, target):
    cam.location = eye
    cam.rotation_euler = (target - eye).to_track_quat("-Z", "Y").to_euler()


def setup(transparent=False, samples=48):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 6
    sc.render.film_transparent = transparent
    # Filmic keeps the saturated Ursus red; AgX rolls it off towards salmon pink
    sc.view_settings.view_transform = "Filmic"
    sc.view_settings.look = "Medium High Contrast"
    sc.view_settings.exposure = -0.3
    for obj in bpy.data.objects:
        if obj.get("i3d_nonRenderable") or obj.type == "LIGHT":
            obj.hide_render = True
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    sc.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "NISHITA"
    sky.sun_elevation = math.radians(38)
    sky.sun_rotation = math.radians(145)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 0.35
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    sun = bpy.data.lights.new("previewSun", "SUN")
    sun.energy = 2.6
    sun.angle = math.radians(2.5)
    so = bpy.data.objects.new("previewSun", sun)
    so.rotation_euler = (math.radians(52), 0, math.radians(145))
    sc.collection.objects.link(so)
    if not transparent:
        me = bpy.data.meshes.new("ground")
        s = 40
        me.from_pydata([(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)], [], [(0, 1, 2, 3)])
        g = bpy.data.objects.new("ground", me)
        sc.collection.objects.link(g)
        mat = bpy.data.materials.new("groundMat")
        mat.use_nodes = True
        n = mat.node_tree.nodes
        bsdf = n["Principled BSDF"]
        noise = n.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 6.0
        ramp = n.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].color = (0.16, 0.15, 0.12, 1)
        ramp.color_ramp.elements[1].color = (0.26, 0.24, 0.19, 1)
        mat.node_tree.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        mat.node_tree.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.9
        me.materials.append(mat)
    cam = bpy.data.objects.new("previewCam", bpy.data.cameras.new("previewCam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    return sc, cam


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/renders")
    ap.add_argument("--views", default=",".join(VIEWS))
    ap.add_argument("--res", default="1280x800")
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--store", default=None, help="write a 512x512 transparent shop render here")
    args = ap.parse_args(argv)
    if args.store:
        sc, cam = setup(transparent=True, samples=args.samples)
        sc.render.resolution_x = sc.render.resolution_y = 1024
        eye, target, lens = V(4.6, 1.9, 4.9), V(0.0, 1.05, 0.0), 44
        cam.data.lens = lens
        look(cam, eye, target)
        sc.render.filepath = os.path.abspath(args.store)
        bpy.ops.render.render(write_still=True)
        return
    sc, cam = setup(samples=args.samples)
    w, h = (int(v) for v in args.res.split("x"))
    sc.render.resolution_x, sc.render.resolution_y = w, h
    os.makedirs(args.out, exist_ok=True)
    for name in args.views.split(","):
        eye, target, lens = VIEWS[name]
        cam.data.lens = lens
        look(cam, eye, target)
        sc.render.filepath = os.path.abspath(os.path.join(args.out, f"{name}.png"))
        bpy.ops.render.render(write_still=True)
        print("rendered", name)


main()
