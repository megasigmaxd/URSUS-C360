#!/usr/bin/env python3
"""Static checks for FS25_UrsusC360 (the game cannot run here).

Checks
  * XML well-formedness of every *.xml in the mod directory.
  * Every node reference in ursusC360.xml (attributes typed g_node_index / g_node_indices in the
    official FS25 vehicle.xsd, or a built-in attribute list without the XSD) names a node from the
    contract table in docs/i3d_nodes.md and, when FS25_UrsusC360/ursusC360.i3d exists, a node of its
    <Scene>. Injected <i3dMappings> paths are resolved against the scene.
  * i3d node classes and orientations the FS25 scripts rely on (see ORIENTATION RULES below).
  * i3d physics: collision filter group/mask of the component, compound children and the fuel
    exactFillRootNode against the FS25 CollisionPreset values, rigid-body flags, wheel reprs.
  * All referenced mod files exist; $data/... paths must be in KNOWN_DATA_PATHS (with sources).
  * Sound modifier types, descVersion range, optional validation against the official XSDs
    (https://validation.gdn.giants-software.com/xml/fs25/, cached in ~/.cache/fs25-xsd).

Exit code 1 if any ERROR was reported, else 0.
"""
import argparse
import math
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MOD = os.path.join(REPO, "FS25_UrsusC360")
CONTRACT = os.path.join(REPO, "docs", "i3d_nodes.md")
VEHICLE_XML = "ursusC360.xml"
I3D_FILE = "ursusC360.i3d"
XSD_URL = "https://validation.gdn.giants-software.com/xml/fs25/"
XSD_CACHE = os.environ.get("FS25_XSD_DIR", os.path.join(os.path.expanduser("~"), ".cache", "fs25-xsd"))
MAPPING_MARKER = "<!-- I3D_MAPPINGS -->"

# FS25 1.21.1.0 dataS/scripts/main.lua: g_minModDescVersion = 90, g_maxModDescVersion = 111;
# dataS/scripts/mods.lua rejects anything outside this range ("Unsupported mod description version").
DESC_VERSION_MIN, DESC_VERSION_MAX = 90, 111

# $data paths we are allowed to reference, each with the base-game file that proves it exists.
KNOWN_DATA_PATHS = {
    "$data/effects/exhaust/exhaust.i3d":
        "FS25 base game data/vehicles/claas/arion550/arion550.xml, vehicle.motorized.exhaustEffects."
        "exhaustEffect#filename (copy: github.com/jason0611/FS25_DashboardLive_VanillaVehicles)",
}

# SoundModifierType names registered in FS25 (enumeration of modifier#type in the official vehicle.xsd).
# SoundManager:loadModifiersFromXML silently ignores unknown names, so a typo would just mute a curve.
SOUND_MODIFIER_TYPES = {
    "ACCELERATE", "BLOW_OFF_VALVE_STATE", "BOATYARD_LAUNCHING_SPEED", "BOATYARD_MOVING_SPEED", "BRAKE_TIME",
    "CARRIAGE_SPEED", "COMBINE_LOAD", "CRUISECONTROL", "DECELERATE", "DIFFERENTIAL_SPEED", "DRIVING_DIRECTION",
    "FOOTBALL_SPEED", "MOTOR_LOAD", "MOTOR_RPM", "MOTOR_RPM_REAL", "MOWER_LOAD", "ROLLERCOASTER_CURVE",
    "ROLLERCOASTER_SPEED", "SPEED", "SUSPENSION", "TURNED_ON_SPEED", "WHEEL_SUSPENSION", "WIND_TURBINE_LOAD",
}

# FS25 dataS scripts/CollisionFlag.lua (bits) and scripts/CollisionPreset.lua (presets). All FS25 vanilla
# vehicle i3ds (GIANTS Editor 10) use PRESET_VEHICLE on the component and its compound children and
# PRESET_EXACT_FILL_ROOT_NODE on the fuel fill target.
FLAG_VEHICLE, FLAG_TRIGGER, FLAG_FILLABLE = 1 << 16, 1 << 29, 1 << 30
PRESET_VEHICLE = (0x10004, 0xFE3FFB83)            # group VEHICLE|CAMERA_BLOCKING, mask all but helper volumes
PRESET_EXACT_FILL_ROOT_NODE = (0x40000000, 0x20000000)  # group FILLABLE, mask TRIGGER
RIGID_FLAGS = ("static", "dynamic", "kinematic")

# Fallback when vehicle.xsd is unavailable: attribute names that hold node ids in this mod's XML.
NODE_ATTRS = {"node", "repr", "driveNode", "linkNode", "rotationNode", "translationNode", "referenceNode",
              "referencePoint", "referenceFrame", "localReferencePoint", "outputNode", "inputNode",
              "targetNode", "rotateNode", "shadowFocusBox", "rootNode", "baseNode", "jointPositionNode",
              "rotCenterNode", "surfaceSoundLinkNode", "directionReferenceNode", "leftNode", "rightNode"}
NODE_LIST_ATTRS = {"attacherJointNodes", "referencePoints", "frontWheelNodes", "wheelNodes",
                   "disablingAttacherJointNodes", "inputAttacherJointNodes", "nodes"}
FILE_ATTRS = {"file", "filename", "xmlFilename"}

errors, warnings = [], []


def error(msg):
    errors.append(msg)
    print("ERROR:", msg)


def warn(msg):
    warnings.append(msg)
    print("WARN: ", msg)


def info(msg):
    print("OK:   ", msg)


# ------------------------------------------------------------------ contract

WHEEL_POS = ["FrontLeft", "FrontRight", "BackLeft", "BackRight"]


def parse_contract(path):
    """Node names from the first column of the markdown table ('A / B', 'X ... Y' wheel ranges)."""
    names = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("| `"):
                continue
            cell = line.split("|")[1]
            found = re.findall(r"`([^`]+)`", cell)
            if ("\u2026" in cell or "..." in cell) and len(found) == 2:
                a, b = found
                pa = next((p for p in WHEEL_POS if p in a), None)
                pb = next((p for p in WHEEL_POS if p in b), None)
                if pa and pb:
                    pre, suf = a.split(pa, 1)
                    found = [pre + p + suf for p in WHEEL_POS[WHEEL_POS.index(pa):WHEEL_POS.index(pb) + 1]]
            names.update(found)
    return names


# ------------------------------------------------------------------ XSD helpers

def get_xsd(name, offline):
    path = os.path.join(XSD_CACHE, name)
    if os.path.exists(path):
        return path
    if offline:
        return None
    try:
        os.makedirs(XSD_CACHE, exist_ok=True)
        with urllib.request.urlopen(XSD_URL + name, timeout=60) as r:
            data = r.read()
        with open(path, "wb") as fh:
            fh.write(data)
        return path
    except Exception as exc:  # network is optional
        warn(f"could not download {XSD_URL + name} ({exc}); schema checks skipped")
        return None


class XsdIndex:
    """Resolves element paths of an instance document to their XSD declarations (attribute types)."""
    NS = "{http://www.w3.org/2001/XMLSchema}"

    def __init__(self, path):
        self.root = ET.parse(path).getroot()
        self.types = {e.get("name"): e for e in self.root.findall(self.NS + "complexType")}
        self.cache = {}

    def _collect(self, node, els, atts):
        for c in node:
            tag = c.tag.replace(self.NS, "")
            if tag == "element":
                els[c.get("name")] = c
            elif tag == "attribute":
                atts[c.get("name")] = c.get("type")
            elif tag in ("sequence", "choice", "all", "complexContent", "simpleContent", "complexType"):
                self._collect(c, els, atts)
            elif tag == "extension":
                base = self.types.get(c.get("base"))
                if base is not None:
                    self._collect(base, els, atts)
                self._collect(c, els, atts)

    def info(self, decl):
        key = id(decl)
        if key not in self.cache:
            ct = self.types.get(decl.get("type")) if decl.get("type") else decl.find(self.NS + "complexType")
            els, atts = {}, {}
            if ct is not None:
                self._collect(ct, els, atts)
            self.cache[key] = (els, atts)
        return self.cache[key]

    def top(self, name):
        return self.root.find(f"{self.NS}element[@name='{name}']")


# ------------------------------------------------------------------ i3d scene

def rot_matrix(deg):
    """GIANTS/i3d euler (degrees): R = Rz * Ry * Rx (same as Blender 'XYZ')."""
    x, y, z = (math.radians(a) for a in deg)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return mat_mul(rz, mat_mul(ry, rx))


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def mat_vec(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def vec(s, default=(0.0, 0.0, 0.0)):
    return [float(t) for t in s.split()] if s else list(default)


def angle_deg(a, b):
    na, nb = math.sqrt(sum(t * t for t in a)), math.sqrt(sum(t * t for t in b))
    if na < 1e-9 or nb < 1e-9:
        return 180.0
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(p * q for p, q in zip(a, b)) / (na * nb)))))


class Scene:
    NODE_TAGS = ("TransformGroup", "Shape", "Camera", "Light", "Dynamic", "AudioSource", "Terrain")

    def __init__(self, path):
        root = ET.parse(path).getroot()
        self.root = root
        self.nodes = {}      # name -> list of node dicts
        self.top = []
        scene = root.find("Scene")
        if scene is None:
            raise ValueError("no <Scene> element")
        for el in scene:
            if el.tag in self.NODE_TAGS:
                self.top.append(self._add(el, None, [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0.0, 0.0, 0.0]))

    def _add(self, el, parent, parent_rot, parent_pos):
        local_rot = vec(el.get("rotation"))
        local_pos = vec(el.get("translation"))
        rot = mat_mul(parent_rot, rot_matrix(local_rot))
        pos = [p + d for p, d in zip(parent_pos, mat_vec(parent_rot, local_pos))]
        node = {"name": el.get("name"), "tag": el.tag, "el": el, "parent": parent, "children": [],
                "local_rot": local_rot, "local_pos": local_pos, "rot": rot, "pos": pos}
        self.nodes.setdefault(node["name"], []).append(node)
        for c in el:
            if c.tag in self.NODE_TAGS:
                node["children"].append(self._add(c, node, rot, pos))
        return node

    def get(self, name):
        found = self.nodes.get(name)
        return found[0] if found else None

    def resolve(self, index_path):
        """i3dMapping path like '0>2|1|0' (first number = top-level node index)."""
        m = re.fullmatch(r"(\d+)>([\d|]*)", index_path.strip())
        if not m or int(m.group(1)) >= len(self.top):
            return None
        node = self.top[int(m.group(1))]
        for part in filter(None, m.group(2).split("|")):
            i = int(part)
            if i >= len(node["children"]):
                return None
            node = node["children"][i]
        return node

    @staticmethod
    def is_descendant(node, ancestor):
        while node is not None:
            if node is ancestor:
                return True
            node = node["parent"]
        return False


def axis(node, i):
    return [node["rot"][r][i] for r in range(3)]


# ------------------------------------------------------------------ checks

def check_wellformed(mod):
    trees = {}
    for base, _, files in os.walk(mod):
        for f in files:
            if f.lower().endswith(".xml"):
                p = os.path.join(base, f)
                try:
                    trees[os.path.relpath(p, mod)] = ET.parse(p)
                except ET.ParseError as exc:
                    error(f"{os.path.relpath(p, REPO)} is not well-formed XML: {exc}")
    if trees:
        info(f"{len(trees)} XML file(s) well-formed: {', '.join(sorted(trees))}")
    return trees


def collect_node_refs(vroot, xsd):
    """[(xml path, attribute, node name)] for every node reference in the vehicle XML."""
    refs = []

    def walk(el, decl, path):
        els, atts = xsd.info(decl) if (xsd and decl is not None) else ({}, {})
        if el.tag != "i3dMappings":
            for attr, value in el.attrib.items():
                typ = atts.get(attr)
                if typ == "g_node_index" or (typ is None and attr in NODE_ATTRS):
                    refs.append((path, attr, value.strip()))
                elif typ == "g_node_indices" or (typ is None and attr in NODE_LIST_ATTRS):
                    refs.extend((path, attr, v) for v in value.split())
            for child in el:
                if isinstance(child.tag, str):
                    walk(child, els.get(child.tag), f"{path}.{child.tag}")

    walk(vroot, xsd.top("vehicle") if xsd else None, "vehicle")
    return refs


def check_files(mod, rel_paths):
    seen = {}
    for where, rel in rel_paths:
        seen.setdefault(rel, []).append(where)
    for rel, wheres in seen.items():
        where = ", ".join(dict.fromkeys(wheres))
        if rel.startswith("$data"):
            if rel in KNOWN_DATA_PATHS:
                info(f"{rel} ({where}) allowed: {KNOWN_DATA_PATHS[rel]}")
            else:
                error(f"{where}: base-game path {rel} is not in KNOWN_DATA_PATHS (unverified)")
            continue
        if rel.startswith("$"):
            error(f"{where}: unsupported path prefix in {rel}")
            continue
        if not os.path.isfile(os.path.join(mod, rel)):
            error(f"{where}: referenced file {rel} does not exist in {os.path.relpath(mod, REPO)}/")


def check_moddesc(mod, tree):
    root = tree.getroot()
    files = []
    dv = root.get("descVersion")
    if dv is None or not dv.isdigit():
        error("modDesc.xml: missing/invalid descVersion")
    elif not DESC_VERSION_MIN <= int(dv) <= DESC_VERSION_MAX:
        error(f"modDesc.xml: descVersion {dv} outside FS25 range {DESC_VERSION_MIN}..{DESC_VERSION_MAX}")
    else:
        info(f"descVersion {dv} within FS25 1.21.1.0 range {DESC_VERSION_MIN}..{DESC_VERSION_MAX}")
    for tag in ("author", "version", "iconFilename"):
        if root.find(tag) is None or not (root.find(tag).text or "").strip():
            error(f"modDesc.xml: <{tag}> missing")
    icon = root.find("iconFilename")
    if icon is not None and icon.text:
        files.append(("modDesc iconFilename", icon.text.strip()))
    for b in root.iterfind("brands/brand"):
        name = b.get("name", "")
        if not re.fullmatch(r"[A-Za-z0-9_.]+", name):
            error(f"modDesc.xml: brand name '{name}' rejected by ClassUtil.getIsValidIndexName")
        if not b.get("title"):
            error(f"modDesc.xml: brand '{name}' has no title")
        files.append((f"brand {name} image", b.get("image", "")))
    items = [s.get("xmlFilename", "") for s in root.iterfind("storeItems/storeItem")]
    if not items:
        error("modDesc.xml: no storeItems")
    files += [("modDesc storeItem", i) for i in items]
    mp = root.find("multiplayer")
    if mp is None or mp.get("supported") != "true":
        warn("modDesc.xml: multiplayer supported flag not set")
    check_files(mod, files)
    return {b.get("name", "").upper() for b in root.iterfind("brands/brand")}


def check_vehicle(mod, vtree, contract, scene, xsd, mod_brands):
    vroot = vtree.getroot()
    if vroot.tag != "vehicle":
        error(f"{VEHICLE_XML}: root element is <{vroot.tag}>")
        return
    raw = open(os.path.join(mod, VEHICLE_XML), encoding="utf-8").read()
    has_marker = MAPPING_MARKER in raw
    mappings = vroot.find("i3dMappings")
    if not has_marker and mappings is None:
        error(f"{VEHICLE_XML}: neither {MAPPING_MARKER} nor <i3dMappings> present")

    # node references
    refs = collect_node_refs(vroot, xsd)
    mapping_ids = {}
    if mappings is not None:
        for m in mappings.iterfind("i3dMapping"):
            if m.get("id") in mapping_ids:
                error(f"duplicate i3dMapping id {m.get('id')}")
            mapping_ids[m.get("id")] = m.get("node")
    bad = 0
    for path, attr, name in refs:
        if re.fullmatch(r"\d+>[\d|]*", name):
            warn(f"{path}#{attr} uses raw index path {name}; use contract names")
            continue
        if name not in contract:
            error(f"{path}#{attr}: node '{name}' is not in docs/i3d_nodes.md")
            bad += 1
        if scene is not None and scene.get(name) is None:
            error(f"{path}#{attr}: node '{name}' not found in {I3D_FILE}")
            bad += 1
        if mappings is not None and name not in mapping_ids:
            error(f"{path}#{attr}: node '{name}' has no <i3dMapping>")
            bad += 1
    used = sorted({n for _, _, n in refs})
    if not bad:
        info(f"{len(refs)} node references ({len(used)} distinct nodes) all in the contract"
             + (" and the i3d scene" if scene is not None else " (i3d not built yet, scene check skipped)"))
    if scene is not None and mappings is not None:
        for mid, path in mapping_ids.items():
            node = scene.resolve(path or "")
            if node is None:
                error(f"i3dMapping {mid}: path {path} does not resolve in {I3D_FILE}")
            elif node["name"] != mid:
                error(f"i3dMapping {mid}: path {path} points to '{node['name']}'")

    # brand
    brand = (vroot.findtext("storeData/brand") or "").strip().upper()
    if brand and brand not in mod_brands:
        warn(f"storeData brand {brand} is not defined in modDesc (must be a base-game brand)")

    # files
    files = [("storeData/image", (vroot.findtext("storeData/image") or "").strip()),
             ("base/filename", (vroot.findtext("base/filename") or "").strip())]
    for el in vroot.iter():
        for attr in FILE_ATTRS & set(el.attrib):
            files.append((f"<{el.tag} {attr}>", el.get(attr)))
    check_files(mod, [f for f in files if f[1]])

    # sound modifiers
    mods = [m.get("type") for m in vroot.iter("modifier")]
    unknown = sorted({t for t in mods if t not in SOUND_MODIFIER_TYPES})
    if unknown:
        error(f"unknown sound modifier types: {', '.join(unknown)}")
    elif mods:
        info(f"{len(mods)} sound modifiers use valid FS25 types ({', '.join(sorted(set(mods)))})")

    # Motorized:onLoad warns "Missing exactFillRootNode for fuel fill unit"; FillTrigger (fuel stations,
    # fuel tanks) only overlaps shapes whose collision group has FILLABLE, and FillUnit maps only
    # exactFillRootNodes to fill units, so without one the tractor cannot be refuelled
    for fu in vroot.iterfind("fillUnit/fillUnitConfigurations/fillUnitConfiguration/fillUnits/fillUnit"):
        if "diesel" in (fu.get("fillTypes") or "").lower().split() and fu.find("exactFillRootNode") is None:
            warn("diesel fillUnit has no <exactFillRootNode>: fuel stations cannot refuel the tractor "
                 "(needs a FILLABLE target shape, see docs/xml_notes.md 'i3d physics attributes')")

    if scene is not None:
        check_scene_rules(vroot, scene)
        check_physics(vroot, scene)


def check_scene_rules(vroot, scene):
    """ORIENTATION RULES (FS25 script behaviour; see docs/xml_notes.md for the source lines)."""
    def node(name):
        return scene.get(name) if name else None

    def local_zero(n, what, fatal):
        if n and max(abs(a) for a in n["local_rot"]) > 0.01:
            (error if fatal else warn)(f"{n['name']}: local rotation {n['local_rot']} must be 0 0 0 ({what})")

    # Drivable:updateSteeringWheel does setRotation(node, 0, rot, 0): tilt must live on a parent node
    local_zero(node(vroot.find("drivable/steeringWheel").get("node")
                    if vroot.find("drivable/steeringWheel") is not None else None),
               "Drivable resets X/Z every frame; put the column tilt on a parent node", True)
    # Wheel:loadFromXML warns when the drive node is rotated
    for ph in vroot.iter("physics"):
        local_zero(node(ph.get("driveNode")), "wheel driveNode", True)
        repr_n, drive_n = node(ph.get("repr")), node(ph.get("driveNode"))
        if repr_n and drive_n and not Scene.is_descendant(drive_n, repr_n):
            error(f"{drive_n['name']} must be a child of wheel repr {repr_n['name']}")
    # attacher joints: vanilla FS25 tractors use rotation 0 90 0 (local X backward, Y up, Z left)
    comp = scene.top[0] if scene.top else None
    for aj in vroot.iterfind("attacherJoints/attacherJoint"):
        n = node(aj.get("node"))
        if n and comp:
            x_ang, y_ang = angle_deg(axis(n, 0), [0, 0, -1]), angle_deg(axis(n, 1), [0, 1, 0])
            if x_ang > 2.0 or y_ang > 2.0:
                error(f"attacher joint {n['name']}: local X must point backward and Y up in the rest pose "
                      f"(vanilla rotation 0 90 0); X off by {x_ang:.1f} deg, Y off by {y_ang:.1f} deg")
        top = aj.find("topArm")
        if top is not None and top.get("rotationNode"):
            tr, tt, tref = node(top.get("rotationNode")), node(top.get("translationNode")), node(top.get("referenceNode"))
            z_sign = -1 if int(top.get("zScale", "-1")) < 0 else 1
            if tr and angle_deg(axis(tr, 2), [0, 0, z_sign]) > 2.0:
                error(f"topArm {tr['name']}: local +Z must point {'backward' if z_sign < 0 else 'forward'} "
                      f"(AttacherJointTopArm resets it to 0 {180 if z_sign < 0 else 0} 0 when idle)")
            for child, parent in ((tt, tr), (tref, tt)):
                if child and parent:
                    lx, ly, lz = child["local_pos"]
                    if abs(lx) > 0.005 or abs(ly) > 0.005 or lz <= 0 or child["parent"] is not parent:
                        error(f"topArm chain: {child['name']} must be a child of {parent['name']} on its +Z axis "
                              f"(local translation {child['local_pos']})")
        rn2 = aj.find("rotationNode2")
        if rn2 is not None:
            arm = node(rn2.get("node"))
            for mp in vroot.iterfind("cylindered/movingParts/movingPart"):
                part = node(mp.get("node"))
                if arm and part and part["name"].startswith("liftRod") and not Scene.is_descendant(part, arm):
                    warn(f"{part['name']} should be a child of {arm['name']} (rotationNode2) so its top follows the lift arm")
    # moving parts: +Z must aim at the reference point in the rest pose
    for mp in vroot.iterfind("cylindered/movingParts/movingPart"):
        part, ref = node(mp.get("node")), node(mp.get("referencePoint"))
        if part and ref:
            d = [r - p for r, p in zip(ref["pos"], part["pos"])]
            a = angle_deg(axis(part, 2), d)
            if a > 3.0:
                warn(f"movingPart {part['name']}: local +Z is {a:.1f} deg off the direction to {ref['name']}")
    check_hitch_geometry(vroot, scene)
    # PTO: shaft is linked along +Z of the output node
    for out in vroot.iterfind("powerTakeOffs/output"):
        n = node(out.get("outputNode"))
        if n and angle_deg(axis(n, 2), [0, 0, -1]) > 10.0:
            error(f"PTO output {n['name']}: local +Z must point backward (towards the implement)")
    # exhaust flap is driven with absolute setRotation around rotationAxis
    flap = vroot.find("motorized/exhaustFlap")
    if flap is not None:
        local_zero(node(flap.get("node")), "exhaustFlap rest pose is overwritten", False)
    ex = vroot.find("motorized/exhaustEffects/exhaustEffect")
    if ex is not None and node(ex.get("node")) and angle_deg(axis(node(ex.get("node")), 1), [0, 1, 0]) > 60:
        warn("exhaustNode: local +Y (pipe axis) points far from up")
    # ROT dashboards with rotAxis only replace one euler component (R = Rz*Ry*Rx)
    for d in vroot.iter("dashboard"):
        if d.get("displayType") == "ROT" and d.get("rotAxis") == "3":
            n = node(d.get("node"))
            if n and (abs(n["local_rot"][0]) > 0.01 or abs(n["local_rot"][1]) > 0.01):
                warn(f"{n['name']}: rotAxis 3 turns about the parent's Z; give the needle rotation 0 0 0 "
                     f"under a parent that carries the dial orientation (local {n['local_rot']})")
    # node classes
    for cam in vroot.iterfind("enterable/cameras/camera"):
        n = node(cam.get("node"))
        if n and n["tag"] != "Camera":
            error(f"camera node {n['name']} is a {n['tag']}, must be a Camera")
    for tag in ("light", "brakeLight", "turnLightLeft", "turnLightRight", "reverseLight", "topLight", "bottomLight"):
        for lt in vroot.iter(tag):
            n = node(lt.get("node"))
            if n and n["tag"] != "Light":
                error(f"real light node {n['name']} is a {n['tag']}, must be a Light")
    info("i3d orientation/class rules evaluated")


def _x_rotated(pivot, point, deg):
    """Rotate a world point about the world X axis through pivot (rotation nodes sit under the
    unrotated component and turn about X)."""
    t = math.radians(deg)
    y, z = point[1] - pivot[1], point[2] - pivot[2]
    return [point[0], pivot[1] + y * math.cos(t) - z * math.sin(t), pivot[2] + y * math.sin(t) + z * math.cos(t)]


def check_hitch_geometry(vroot, scene):
    """Recompute the hitch heights and the lift-arm linkage from the scene and compare them with
    distanceToGround and rotationNode2 (the XML values were solved from the modelled geometry)."""
    for aj in vroot.iterfind("attacherJoints/attacherJoint"):
        rn, dist = aj.find("rotationNode"), aj.find("distanceToGround")
        if rn is None or dist is None:
            continue
        pivot, joint = scene.get(rn.get("node")), scene.get(aj.get("node"))
        if pivot is None or joint is None:
            continue
        rn2 = aj.find("rotationNode2")
        arm = scene.get(rn2.get("node")) if rn2 is not None else None
        for n in (pivot, arm):
            if n and max(abs(a) for a in n["local_rot"]) > 0.01:
                warn(f"{n['name']}: rest rotation {n['local_rot']} is not 0 0 0; the XML hitch angles assume it is")
        lo, up = vec(rn.get("lowerRotation"))[0], vec(rn.get("upperRotation"))[0]
        if Scene.is_descendant(joint, pivot):
            # AttacherJoints:updateAttacherJointRotation turns the joint about its local Z by
            # -lerp(upper/lowerRotationOffset, moveAlpha); matching the arm angles keeps implements level
            off_lo, off_up = float(aj.get("lowerRotationOffset", "0")), float(aj.get("upperRotationOffset", "0"))
            if abs(off_lo - lo) > 0.5 or abs(off_up - up) > 0.5:
                warn(f"{joint['name']} rotates with {pivot['name']}: lower/upperRotationOffset ({off_lo}/{off_up}) "
                     f"should equal the rotationNode X angles ({lo}/{up}) or implements pitch with the arms")
        for label, rot, want in (("lower", lo, float(dist.get("lower", "0.7"))), ("upper", up, float(dist.get("upper", "1")))):
            h = _x_rotated(pivot["pos"], joint["pos"], rot)[1]
            if abs(h - want) > 0.03:
                warn(f"{joint['name']}: height at {label}Rotation {rot} deg is {h:.3f} m, distanceToGround#{label} is {want}")
        if arm is None:
            continue
        rod = next((mp for mp in vroot.iterfind("cylindered/movingParts/movingPart")
                    if scene.get(mp.get("node")) and scene.get(mp.get("referencePoint"))
                    and Scene.is_descendant(scene.get(mp.get("referencePoint")), pivot)), None)
        if rod is None:
            continue
        top, ref = scene.get(rod.get("node"))["pos"], scene.get(rod.get("referencePoint"))["pos"]
        length = math.dist(top, ref)
        for label, rot, rot2 in (("lower", lo, vec(rn2.get("lowerRotation"))[0]), ("upper", up, vec(rn2.get("upperRotation"))[0])):
            ref_r = _x_rotated(pivot["pos"], ref, rot)
            best = min((abs(math.dist(_x_rotated(arm["pos"], top, p / 10.0), ref_r) - length), p / 10.0)
                       for p in range(-900, 901))[1]
            if abs(best - rot2) > 1.5:
                warn(f"{arm['name']}: linkage needs {best:.1f} deg at {label} position, rotationNode2 has {rot2}")
        info(f"hitch geometry of {joint['name']} checked against distanceToGround/rotationNode2")


def _hexint(value):
    try:
        return int(value, 0) if value is not None else None
    except ValueError:
        return None


def _check_filter(name, el, preset, required_group, required_mask=None):
    group, mask = _hexint(el.get("collisionFilterGroup")), _hexint(el.get("collisionFilterMask"))
    if group is None or mask is None:
        error(f"{name}: collisionFilterGroup/collisionFilterMask missing (FS25 attributes; expected "
              f"0x{preset[0]:x}/0x{preset[1]:x})")
        return
    if not group & required_group:
        error(f"{name}: collisionFilterGroup 0x{group:x} lacks bit {required_group.bit_length() - 1} "
              f"(expected 0x{preset[0]:x})")
    if required_mask is not None and not mask & required_mask:
        error(f"{name}: collisionFilterMask 0x{mask:x} lacks bit {required_mask.bit_length() - 1} "
              f"(expected 0x{preset[1]:x})")
    if (group, mask) != preset:
        warn(f"{name}: collision filter 0x{group:x}/0x{mask:x} differs from the FS25 preset 0x{preset[0]:x}/0x{preset[1]:x}")


def check_physics(vroot, scene):
    """FS25 rigid-body setup (Vehicle:loadComponentFromXML, WheelPhysics, FillUnit, vanilla GE10 exports)."""
    all_nodes = [n for lst in scene.nodes.values() for n in lst]
    comp = scene.top[0] if scene.top else None
    if comp is None:
        error("i3d scene has no component node")
        return
    el = comp["el"]
    if len(scene.top) > 1:
        warn(f"{len(scene.top)} top-level nodes; this XML defines one <component>")
    if comp["tag"] != "Shape" or el.get("dynamic") != "true" or el.get("compound") != "true":
        error(f"{comp['name']}: component must be a Shape with dynamic=\"true\" compound=\"true\"")
    if abs(comp["local_pos"][1]) > 1e-4:
        error(f"{comp['name']}: Y translation of the first component must be 0 (Vehicle:loadComponentFromXML)")
    _check_filter(comp["name"], el, PRESET_VEHICLE, FLAG_VEHICLE, FLAG_VEHICLE)
    if el.get("clipDistance") is None:
        warn(f"{comp['name']}: no clipDistance (the game warns and sets 300; vanilla components use clipDistance=\"300\")")
    lin, ang = el.get("linearDamping"), el.get("angularDamping")
    if (lin is not None and float(lin) > 0.01) or (ang is not None and not 0.0001 <= float(ang) <= 0.05):
        warn(f"{comp['name']}: non-default damping (linear {lin}, angular {ang}); Vehicle.lua dev-warns, leave the defaults")
    if any(el.get(a) for a in ("density", "solverIterationCount")):
        warn(f"{comp['name']}: density/solverIterationCount in the i3d are overridden by XML component#mass/#solverIterationCount")

    exact = {e.get("node") for e in vroot.iter("exactFillRootNode")}
    children = 0
    for n in all_nodes:
        e = n["el"]
        if "collisionMask" in e.attrib:
            warn(f"{n['name']}: FS22 attribute collisionMask; FS25 uses collisionFilterGroup/collisionFilterMask")
        if e.get("trigger") == "true":
            warn(f"{n['name']}: trigger shape in a vehicle i3d (not needed: AI box via ai.collisionTrigger#useSize, "
                 f"entering via enterReferenceNode#interactionRadius)")
        if e.get("compoundChild") == "true":
            children += 1
            if not Scene.is_descendant(n, comp) or any(e.get(f) == "true" for f in RIGID_FLAGS):
                error(f"{n['name']}: compound child must sit under the component and carry no rigid-body flag")
            _check_filter(n["name"], e, PRESET_VEHICLE, FLAG_VEHICLE)
            dens = e.get("density")
            if dens is None or float(dens) > 0.01:
                warn(f"{n['name']}: density {dens}; FS25 vanilla compound children use density=\"0.001\"")
        elif n is not comp and any(e.get(f) == "true" for f in RIGID_FLAGS) and n["name"] not in exact:
            warn(f"{n['name']}: extra rigid body ({', '.join(f for f in RIGID_FLAGS if e.get(f) == 'true')}) "
                 f"is not referenced as a component or exactFillRootNode")
    for name in exact:
        n = scene.get(name)
        if n is None:
            continue
        e = n["el"]
        if n["tag"] != "Shape" or e.get("kinematic") != "true" or e.get("compound") != "true":
            warn(f"{name}: vanilla exactFillRootNodes are Shapes with kinematic=\"true\" compound=\"true\"")
        if not Scene.is_descendant(n, comp):
            error(f"{name}: exactFillRootNode must be linked under the component so it moves with the tractor")
        _check_filter(name, e, PRESET_EXACT_FILL_ROOT_NODE, FLAG_FILLABLE, FLAG_TRIGGER)

    # wheels: physics wheels are created by script (createWheelShape on the component, group VEHICLE,
    # WheelPhysics.COLLISION_MASK); the i3d only provides repr/drive nodes and visual meshes
    for ph in vroot.iter("physics"):
        repr_n, drive_n = scene.get(ph.get("repr") or ""), scene.get(ph.get("driveNode") or "")
        if repr_n and not Scene.is_descendant(repr_n, comp):
            error(f"{repr_n['name']}: wheel repr must be inside the dynamic component (Wheel:loadFromXML)")
        stack = [drive_n] if drive_n else []
        while stack:
            v = stack.pop()
            stack.extend(v["children"])
            ve = v["el"]
            if any(ve.get(f) == "true" for f in RIGID_FLAGS + ("compoundChild",)):
                error(f"{v['name']}: visual wheel geometry must not be a rigid body or compound child")
    info(f"physics setup evaluated (component + {children} compound children"
         + (f", exactFillRootNode {', '.join(sorted(exact))}" if exact else "") + ")")


def xsd_validate(mod, offline):
    try:
        from lxml import etree
    except ImportError:
        warn("lxml not installed; official XSD validation skipped (pip install lxml)")
        return None
    vxsd = None
    for xml_name, xsd_name in (("modDesc.xml", "modDesc.xsd"), (VEHICLE_XML, "vehicle.xsd")):
        xsd_path = get_xsd(xsd_name, offline)
        if not xsd_path:
            continue
        if xsd_name == "vehicle.xsd":
            vxsd = xsd_path
        src = open(os.path.join(mod, xml_name), encoding="utf-8").read()
        if MAPPING_MARKER in src and "<i3dMappings" not in src:
            # simulate the build's injection so the document is complete
            src = src.replace(MAPPING_MARKER, "<i3dMappings><i3dMapping id=\"x\" node=\"0>\"/></i3dMappings>")
        try:
            schema = etree.XMLSchema(etree.parse(xsd_path))
            doc = etree.fromstring(src.encode("utf-8"))
        except Exception as exc:
            error(f"XSD validation of {xml_name} failed to run: {exc}")
            continue
        if schema.validate(doc):
            info(f"{xml_name} is valid against the official FS25 {xsd_name}")
        else:
            for e in schema.error_log:
                error(f"{xml_name}:{e.line}: {e.message}")
    return vxsd


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mod", default=DEFAULT_MOD, help="mod directory (default: FS25_UrsusC360)")
    ap.add_argument("--no-xsd", action="store_true", help="skip official XSD validation")
    ap.add_argument("--offline", action="store_true", help="never download XSDs")
    args = ap.parse_args()
    mod = os.path.abspath(args.mod)

    contract = parse_contract(CONTRACT)
    info(f"contract: {len(contract)} node names from {os.path.relpath(CONTRACT, REPO)}")
    trees = check_wellformed(mod)
    for need in ("modDesc.xml", VEHICLE_XML):
        if need not in trees:
            error(f"{need} missing or unreadable")
    if errors:
        return 1

    vxsd_path = None if args.no_xsd else xsd_validate(mod, args.offline)
    xsd = XsdIndex(vxsd_path) if vxsd_path else None
    if xsd is None:
        warn("vehicle.xsd unavailable: node attributes detected by built-in name list")

    scene = None
    i3d_path = os.path.join(mod, I3D_FILE)
    if os.path.isfile(i3d_path):
        try:
            scene = Scene(i3d_path)
            info(f"{I3D_FILE}: {sum(len(v) for v in scene.nodes.values())} scene nodes")
            dup = sorted(n for n, v in scene.nodes.items() if len(v) > 1 and n in contract)
            if dup:
                error(f"{I3D_FILE}: contract node names used more than once: {', '.join(dup)}")
            shapes = scene.root.find("Shapes")
            ext = shapes.get("externalShapesFile") if shapes is not None else None
            if ext and not os.path.isfile(os.path.join(mod, ext)):
                error(f"{I3D_FILE}: externalShapesFile {ext} missing")
        except (ET.ParseError, ValueError) as exc:
            warn(f"{I3D_FILE} not readable as XML i3d ({exc}); scene checks skipped")
    else:
        warn(f"{I3D_FILE} not built yet; scene checks skipped")

    brands = check_moddesc(mod, trees["modDesc.xml"])
    check_vehicle(mod, trees[VEHICLE_XML], contract, scene, xsd, brands)

    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
