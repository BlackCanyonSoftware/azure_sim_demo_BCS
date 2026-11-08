"""Builds the massing model with FreeCAD: one Part box per volume, saved as a real FreeCAD document.

Runs under `freecad -c` on KPrime with LAYOUT_JSON, FC_STD and FC_OBJ in the environment (the worker sets them; spare command-line
arguments would be treated as files to open, hence the environment). The OBJ is written by hand from each shape's tessellation because
this FreeCAD snap's SMESH was built without NETGEN, so the MeshPart module raises.
"""
import json
import os

import FreeCAD
import Part

layout = json.load(open(os.environ["LAYOUT_JSON"]))
option = layout["option"]

doc = FreeCAD.newDocument("massing")
seen = set()
for v in option["volumes"]:
    shape = Part.makeBox(float(v["w"]), float(v["d"]), float(v["h"]),
                         FreeCAD.Vector(float(v["x"]), float(v["y"]), 0))
    name = (str(v.get("name") or "Volume").split()[0] or "Volume")[:24]
    while name.lower() in seen:
        name = name + "x"
    seen.add(name.lower())
    obj = doc.addObject("Part::Feature", name)
    obj.Label = name
    obj.Shape = shape
doc.recompute()
doc.saveAs(os.environ["FC_STD"])

verts, faces = [], []
for obj in doc.Objects:
    pts, tris = obj.Shape.tessellate(0.1)
    off = len(verts)
    verts.extend(pts)
    faces.extend(tuple(i + off for i in t) for t in tris)
with open(os.environ["FC_OBJ"], "w") as fh:
    fh.write("# massing model built by the Studio Assistant worker (FreeCAD)\n")
    fh.write("o massing\n")
    for p in verts:
        fh.write(f"v {p.x:.3f} {p.y:.3f} {p.z:.3f}\n")
    for f in faces:
        fh.write("f " + " ".join(str(i + 1) for i in f) + "\n")
print(f"FC OK: {len(doc.Objects)} volumes, {len(verts)} vertices, {len(faces)} faces")
