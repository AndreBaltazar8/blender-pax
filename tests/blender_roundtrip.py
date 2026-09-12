import bpy
import sys
import os
import json
import math
import random
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.environ.get('PAX_TEST_INSTALLED'):
    import importlib, addon_utils
    blender_pax = importlib.import_module('bl_ext.user_default.blender_pax')
    addon_utils.enable(blender_pax.ADDON_ID, default_set=False)
    assert bpy.ops.pax.install_runtime() == {'FINISHED'}
else:
    sys.path.insert(0, str(ROOT / 'addon'))
    os.environ['PAX_RUNTIME_ROOT'] = str(ROOT / 'addon/blender_pax/runtime')
    import blender_pax
    blender_pax.register()
OUT = ROOT / 'artifacts'
OUT.mkdir(exist_ok=True)

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
mesh = bpy.context.object
mesh.name = 'PaxSphere'
mesh['test_property'] = 'preserved'
mesh.shape_key_add(name='Basis')
shape = mesh.shape_key_add(name='Stretch')
for vertex in shape.data:
    vertex.co.z *= 1.2
shape.value = 0
shape.keyframe_insert(data_path='value', frame=1)
shape.value = 1
shape.keyframe_insert(data_path='value', frame=20)
shape.value = 0
image = bpy.data.images.new('PaxNoise', width=128, height=128, alpha=True)
rng = random.Random(123)
image.pixels.foreach_set([v for _ in range(128*128) for v in [rng.random(), rng.random(), rng.random(), 1.0]])
image.pack()
material = bpy.data.materials.new('PaxMaterial')
material.use_nodes = True
tex = material.node_tree.nodes.new('ShaderNodeTexImage')
tex.image = image
material.node_tree.links.new(tex.outputs['Color'], material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
mesh.data.materials.append(material)
bpy.ops.object.armature_add()
armature = bpy.context.object
armature.name = 'PaxRig'
group = mesh.vertex_groups.new(name=armature.data.bones[0].name)
group.add(list(range(len(mesh.data.vertices))), 1.0, 'REPLACE')
modifier = mesh.modifiers.new('PaxSkin', 'ARMATURE')
modifier.object = armature
mesh.parent = armature
bone = armature.pose.bones[0]
bone.rotation_mode = 'XYZ'
bone.rotation_euler.z = 0
bone.keyframe_insert(data_path='rotation_euler', frame=1)
bone.rotation_euler.z = 0.3
bone.keyframe_insert(data_path='rotation_euler', frame=20)
bpy.ops.object.camera_add(location=(0,-5,3))
bpy.context.object.name = 'PaxCamera'
bpy.ops.object.light_add(type='POINT', location=(2,-3,4))
bpy.context.object.name = 'PaxLight'
bpy.context.scene.frame_end = 20
bpy.context.scene.frame_set(1)
settings = dict(initial_quality='sharp', texture_base_size='32', geometry_base_ratio=0.1,
                adaptive=False, geometry_codec='auto', texture_tiles=True, tile_size='128')
path = OUT / 'roundtrip.pax'
result = bpy.ops.export_scene.pax(filepath=str(path), **settings)
assert result == {'FINISHED'}, result
assert struct.unpack_from('<I', path.read_bytes(), 4)[0] == 0
stats = json.loads(path.with_suffix('.stats.json').read_text())
assert stats['streamBytes'] <= stats['sourceBytes'] * 1.05
assert stats['conversionSettings']['initialQuality'] == 'sharp'
assert stats['conversionSettings']['textureBaseSize'] == 32
assert abs(stats['conversionSettings']['geometryBaseRatio'] - 0.1) < 1e-6
assert stats['conversionSettings']['adaptive'] is False
assert stats['conversionSettings']['tileSize'] == 128
assert stats['animations'] >= 2
source_triangles = sum(len(p.vertices)-2 for p in mesh.data.polygons)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
result = bpy.ops.import_scene.pax(filepath=str(path))
assert result == {'FINISHED'}, result
holder = bpy.data.objects['PaxSphere']
loaded = next(o for o in bpy.context.scene.objects if o.type == 'MESH' and len(o.data.materials) and o.data.materials[0].name.startswith('PaxMaterial'))
assert sum(len(p.vertices)-2 for p in loaded.data.polygons) == source_triangles
assert len(loaded.data.uv_layers) >= 1
assert holder['test_property'] == 'preserved'
assert loaded.data.shape_keys and 'Stretch' in loaded.data.shape_keys.key_blocks
assert any(m.type == 'ARMATURE' for m in loaded.modifiers)
assert any(o.type == 'ARMATURE' for o in bpy.context.scene.objects)
assert any(o.type == 'CAMERA' for o in bpy.context.scene.objects)
assert any(o.type == 'LIGHT' for o in bpy.context.scene.objects)
assert any(n.type == 'TEX_IMAGE' and n.image and n.image.packed_file for n in loaded.data.materials[0].node_tree.nodes)
assert len(bpy.data.actions) >= 2
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'roundtrip.blend'))
# Failed conversion must not clobber an existing destination.
existing = OUT / 'keep.pax'
existing.write_bytes(b'keep-me')
try:
    result = bpy.ops.export_scene.pax(filepath=str(existing), apply_modifiers=True, export_morphs=True)
except RuntimeError:
    result = {'CANCELLED'}
assert result == {'CANCELLED'}
assert existing.read_bytes() == b'keep-me'
# All PAX knobs are real operator properties, available in the export browser/presets.
keys = bpy.ops.export_scene.pax.get_rna_type().properties.keys()
for key in settings:
    assert key in keys
blender_pax.unregister()
blender_pax.register()
if os.environ.get('PAX_TEST_INSTALLED'):
    addon_utils.disable(blender_pax.ADDON_ID, default_set=False)
else:
    blender_pax.unregister()
print('PAX_BLENDER_ROUNDTRIP_OK', json.dumps({'triangles':source_triangles,'bytes':stats['streamBytes'],'sourceBytes':stats['sourceBytes'],'animations':stats['animations']}))
