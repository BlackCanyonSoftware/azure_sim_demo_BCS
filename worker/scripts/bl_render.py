"""Renders the massing model: imports the OBJ the FreeCAD step built, puts it on a ground plane with a sun and a plain sky, and renders a
3/4 view from a slightly elevated camera (the classic massing-study look). Runs on KPrime:

    blender --background --python bl_render.py -- IN.obj OUT.png

(paths absolute; Blender's own working directory is not the caller's). CPU Cycles, low samples: the whole render takes seconds.
"""
import math
import sys
import time

import bpy
import mathutils

args = sys.argv[sys.argv.index("--") + 1:]
obj_path, out_png = args[0], args[1]
t0 = time.time()

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete()  # start from an empty scene (the startup file has a cube, a light and a camera)

bpy.ops.wm.obj_import(filepath=obj_path, forward_axis="Y", up_axis="Z")  # FreeCAD writes the OBJ Z-up; Blender's default import would rotate the model 90 deg
imported = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not imported:
    raise SystemExit(f"FAIL: nothing imported from {obj_path}")

# world bounds of the imported model
mins = mathutils.Vector((1e9,) * 3)
maxs = mathutils.Vector((-1e9,) * 3)
for o in imported:
    for corner in o.bound_box:
        w = o.matrix_world @ mathutils.Vector(corner)
        for i in range(3):
            mins[i] = min(mins[i], w[i])
            maxs[i] = max(maxs[i], w[i])
center = (mins + maxs) / 2
size = max(maxs[i] - mins[i] for i in range(3)) or 10.0

# ground plane
bpy.ops.mesh.primitive_plane_add(size=max(size * 6, 60), location=(center.x, center.y, 0))
ground = bpy.context.object
ground.name = "Ground"

# materials: pale plaster for the mass, dark slate for the ground, so the volumes read against the site
mat_mass = bpy.data.materials.new("Mass")
mat_mass.diffuse_color = (0.90, 0.89, 0.86, 1)
mat_ground = bpy.data.materials.new("Ground")
mat_ground.diffuse_color = (0.15, 0.16, 0.18, 1)
for o in imported:
    o.data.materials.clear()
    o.data.materials.append(mat_mass)
ground.data.materials.append(mat_ground)

# sun behind and above the subject, so the shadows are cast toward the camera; a plain cool sky for fill
sun_data = bpy.data.lights.new("Sun", type="SUN")
sun_data.energy = 4.5
sun_data.angle = math.radians(1.5)
sun = bpy.data.objects.new("Sun", sun_data)
bpy.context.collection.objects.link(sun)
sun_from = mathutils.Vector((-0.30, 0.55, 0.95)).normalized()
sun.location = center + sun_from * size * 6
sun.rotation_euler = (center - sun.location).to_track_quat("-Z", "Y").to_euler()
world = bpy.data.worlds.new("World")
bpy.context.scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.48, 0.60, 0.78, 1)
bg.inputs[1].default_value = 0.45

# camera: a 3/4 view from the south-east, far enough that the WHOLE model fits the frame with a margin (the old
# distance cropped the blocks). The model's bounding sphere is framed against the narrower of the two field-of-view
# angles; the ground plane may run past the edges.
cam_data = bpy.data.cameras.new("Camera")
cam_data.lens = 50
radius = (maxs - mins).length / 2 or 5.0
margin = 1.15
res_x, res_y = 1280, 720
h_fov = 2 * math.atan(cam_data.sensor_width / 2 / cam_data.lens)
v_fov = 2 * math.atan(cam_data.sensor_width * res_y / res_x / 2 / cam_data.lens)
dist = (radius * margin) / math.tan(min(h_fov, v_fov) / 2)
from_dir = mathutils.Vector((1.0, -1.1, 0.55)).normalized()
cam_pos = center + from_dir * dist
print(f"CAMERA fit: bounding radius {radius:.1f} m, lens {cam_data.lens} mm, distance {dist:.1f} m")
cam = bpy.data.objects.new("Camera", cam_data)
bpy.context.collection.objects.link(cam)
cam.location = cam_pos
cam.rotation_euler = (center - cam_pos).to_track_quat("-Z", "Y").to_euler()
bpy.context.scene.camera = cam

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "AgX - Medium High Contrast"
scene.cycles.samples = 24
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = res_x, res_y
scene.render.filepath = out_png
scene.render.image_settings.file_format = "PNG"
bpy.ops.render.render(write_still=True)
print(f"RENDER OK in {time.time() - t0:.1f} s -> {out_png}")
