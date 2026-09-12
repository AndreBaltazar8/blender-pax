# SPDX-License-Identifier: GPL-3.0-or-later
bl_info = {
    "name": "PAX Import / Export", "author": "André Baltazar", "version": (0, 1, 0),
    "blender": (4, 5, 0), "location": "File > Import / Export > Progressive Asset (.pax)",
    "description": "Import and export PAX version 0 with configurable progressive quality",
    "category": "Import-Export",
}

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper

ADDON_ID = __package__
_ACTIVE = set()


def preferences(context):
    entry = context.preferences.addons.get(ADDON_ID)
    return entry.preferences if entry else None


def runtime_root(context):
    override = os.environ.get('PAX_RUNTIME_ROOT')
    if override:
        return Path(override).resolve()
    root = Path(bpy.utils.user_resource('SCRIPTS', path='pax_runtime/0.1.0', create=True))
    bundled = Path(__file__).parent / 'runtime'
    for name in ('package.json', 'package-lock.json', 'bridge.mjs', 'decode.mjs'):
        source, target = bundled / name, root / name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
    return root


def executable(context, kind):
    prefs = preferences(context)
    value = getattr(prefs, kind + '_path', '') if prefs else ''
    value = bpy.path.abspath(value) if value else kind
    resolved = shutil.which(value)
    if not resolved:
        raise RuntimeError(f'{kind} was not found. Install Node.js 24+ and set its path in PAX add-on preferences.')
    return resolved


def environment(context):
    env = os.environ.copy()
    env['PATH'] = str(Path(executable(context, 'node')).parent) + os.pathsep + env.get('PATH', '')
    return env


def check_runtime(context):
    root = runtime_root(context)
    node = executable(context, 'node')
    version = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10, check=True).stdout
    if int(version.strip().lstrip('v').split('.')[0]) < 24:
        raise RuntimeError('PAX requires Node.js 24 or newer.')
    if not (root / 'node_modules' / 'convert-pax').exists():
        raise RuntimeError('PAX runtime is not installed. Open Preferences > Add-ons > PAX and click Install / Repair Runtime.')
    return root, node


class PAXPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID
    node_path: StringProperty(name='Node.js executable', subtype='FILE_PATH', description='Node.js 24+; leave empty to search PATH')
    npm_path: StringProperty(name='npm executable', subtype='FILE_PATH', description='npm supplied with Node.js; leave empty to search PATH')

    def draw(self, context):
        layout = self.layout
        layout.label(text='Requires Node.js 24+, npm and Git on this computer.')
        layout.prop(self, 'node_path')
        layout.prop(self, 'npm_path')
        layout.operator('pax.install_runtime', icon='IMPORT')
        layout.label(text='Setup downloads pinned converter dependencies. Import/export then works offline.')
        layout.label(text='Runtime is stored in your Blender user scripts directory.')


class Job:
    """Poll the external codec process without blocking Blender's interface."""
    _process = None
    _timer = None
    _temp = None
    _log = None

    @classmethod
    def poll(cls, context):
        return not _ACTIVE

    def start_job(self, context, command, cwd):
        self._log = open(Path(self._temp.name) / 'process.log', 'w+b')
        self._process = subprocess.Popen(command, cwd=str(cwd), stdout=self._log,
                                         stderr=subprocess.STDOUT, env=environment(context))
        _ACTIVE.add(self)
        context.window_manager.progress_begin(0, 100)
        if bpy.app.background:
            self._process.wait()
            return self.finish_job(context)
        self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self.cancel(context)
            self.report({'WARNING'}, 'PAX operation cancelled; existing destination kept.')
            return {'CANCELLED'}
        if event.type == 'TIMER':
            if self._process.poll() is not None:
                return self.finish_job(context)
            context.workspace.status_text_set('PAX processing… Press Esc to cancel')
        return {'PASS_THROUGH'}

    def finish_job(self, context):
        try:
            self._log.flush()
            if self._process.returncode:
                self._log.seek(0)
                detail = self._log.read().decode('utf-8', errors='replace')
                print(detail)
                raise RuntimeError(next((line for line in detail.splitlines() if 'Error:' in line), detail[-1200:] or 'PAX process failed'))
            self.on_success(context)
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        finally:
            self.cleanup(context)

    def cancel(self, context):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self.cleanup(context)

    def cleanup(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.window_manager.progress_end()
        if context.workspace:
            context.workspace.status_text_set(None)
        if self._log:
            self._log.close()
            self._log = None
        if self._temp:
            self._temp.cleanup()
            self._temp = None
        _ACTIVE.discard(self)

    def bridge(self, context, request):
        root, node = check_runtime(context)
        request['result'] = str(Path(self._temp.name) / 'result.json')
        self._request = request
        request_path = Path(self._temp.name) / 'request.json'
        request_path.write_text(json.dumps(request), encoding='utf-8')
        return self.start_job(context, [node, str(root / 'bridge.mjs'), str(request_path)], root)


class PAX_OT_install_runtime(Job, bpy.types.Operator):
    bl_idname = 'pax.install_runtime'
    bl_label = 'Install / Repair PAX Runtime'
    bl_description = 'Download the pinned PAX converter and codecs using npm into Blender user storage'

    def execute(self, context):
        try:
            if not bpy.app.online_access:
                raise RuntimeError('Online access is disabled in Blender preferences. Enable it for runtime setup, or install the runtime offline with npm ci.')
            root = runtime_root(context)
            executable(context, 'node')
            npm = executable(context, 'npm')
            self._temp = tempfile.TemporaryDirectory(prefix='pax-setup-')
            return self.start_job(context, [npm, 'ci', '--omit=dev', '--no-audit', '--no-fund'], root)
        except Exception as error:
            self.cleanup(context)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

    def on_success(self, context):
        check_runtime(context)
        self.report({'INFO'}, 'PAX runtime installed. Import and export are ready.')


def atomic_copy(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.pax-', dir=str(destination.parent))
    try:
        with os.fdopen(fd, 'wb') as output, open(source, 'rb') as input_file:
            shutil.copyfileobj(input_file, output)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class PAX_OT_export(Job, bpy.types.Operator, ExportHelper):
    bl_idname = 'export_scene.pax'
    bl_label = 'Export PAX'
    bl_options = {'PRESET'}
    filename_ext = '.pax'
    filter_glob: StringProperty(default='*.pax', options={'HIDDEN'})

    initial_quality: EnumProperty(name='Initial Quality', items=[('fast', 'Fast', 'Smaller preview, more approximation'), ('balanced', 'Balanced', 'Balance preview detail and startup bytes'), ('sharp', 'Sharp', 'Prioritize initial detail')], default='balanced')
    texture_base_size: EnumProperty(name='Initial Texture Size', items=[(str(n), str(n) + ' px', 'Maximum preview dimension') for n in (16, 32, 64, 128, 256, 512, 1024)], default='128')
    geometry_base_ratio: FloatProperty(name='Initial Geometry Fraction', description='Target fraction of source primitives; simplification may retain more', default=0.015, min=0.0001, max=1.0, precision=4)
    adaptive: BoolProperty(name='Adaptive Detail Stages', default=True, description='Choose useful stages using error reduction and estimated byte cost')
    geometry_codec: EnumProperty(name='Geometry Codec', items=[('auto', 'Auto (Lossless)', 'Choose prediction, meshopt or raw blocks by size'), ('gzip', 'Plain Gzip', 'Reference encoding without inner block prediction')], default='auto')
    texture_tiles: BoolProperty(name='Stream Texture Tiles', default=True, description='Refine independently addressable image regions where they fit the budget')
    tile_size: EnumProperty(name='Texture Tile Size', items=[(str(n), str(n) + ' px', 'Full-resolution tile width/height') for n in (128, 256, 512)], default='256')
    selected_only: BoolProperty(name='Selected Objects Only', default=False)
    visible_only: BoolProperty(name='Visible Objects Only', default=False)
    active_scene_only: BoolProperty(name='Active Scene Only', default=True)
    export_animations: BoolProperty(name='Animations', default=True)
    export_skins: BoolProperty(name='Skinning', default=True)
    export_morphs: BoolProperty(name='Shape Keys', default=True)
    export_cameras: BoolProperty(name='Cameras', default=True)
    export_lights: BoolProperty(name='Lights', default=True)
    export_extras: BoolProperty(name='Custom Properties', default=True)
    apply_modifiers: BoolProperty(name='Apply Modifiers', default=False, description='Blender cannot export shape keys when modifiers are applied')
    write_stats: BoolProperty(name='Write Conversion Report', default=True, description='Save size, codec and fidelity metadata beside the PAX file')

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text='PAX Format Version 0')
        box.label(text='+5% maximum vs Blender GLB', icon='LOCKED')
        box.label(text='Final detail preserved')
        box.label(text='Approximate previews')
        box = layout.box()
        box.label(text='Progressive Quality')
        for prop, label in (('initial_quality', 'Initial Quality'), ('texture_base_size', 'Initial Texture Size'), ('geometry_base_ratio', 'Initial Geometry Fraction'), ('geometry_codec', 'Geometry Codec')):
            column = box.column(align=True)
            column.label(text=label)
            column.prop(self, prop, text='')
        box.prop(self, 'adaptive')
        box.prop(self, 'texture_tiles')
        row = box.column(align=True)
        row.enabled = self.texture_tiles
        row.label(text='Texture Tile Size')
        row.prop(self, 'tile_size', text='')
        box = layout.box()
        box.label(text='Scene Content')
        for prop in ('selected_only', 'visible_only', 'active_scene_only', 'export_animations', 'export_skins', 'export_morphs', 'export_cameras', 'export_lights', 'export_extras', 'apply_modifiers', 'write_stats'):
            box.prop(self, prop)
        if self.apply_modifiers and self.export_morphs:
            box.label(text='Disable Apply Modifiers to export shape keys.', icon='ERROR')

    def converter_settings(self):
        return dict(initialQuality=self.initial_quality, textureBaseSize=int(self.texture_base_size),
                    geometryBaseRatio=self.geometry_base_ratio, adaptive=self.adaptive,
                    geometryCodec=self.geometry_codec, textureTiles=self.texture_tiles, tileSize=int(self.tile_size))

    def execute(self, context):
        try:
            check_runtime(context)
            if self.apply_modifiers and self.export_morphs:
                raise RuntimeError('Apply Modifiers and Shape Keys cannot both be enabled. Choose which representation to export.')
            self._temp = tempfile.TemporaryDirectory(prefix='pax-export-')
            self.filepath = bpy.path.ensure_ext(self.filepath, '.pax')
            source = str(Path(self._temp.name) / 'scene.glb')
            options = dict(filepath=source, check_existing=False, export_format='GLB',
                           export_image_format='AUTO', use_selection=self.selected_only,
                           use_visible=self.visible_only, use_active_scene=self.active_scene_only,
                           export_animations=self.export_animations, export_skins=self.export_skins,
                           export_morph=self.export_morphs, export_morph_normal=self.export_morphs,
                           export_cameras=self.export_cameras, export_lights=self.export_lights,
                           export_extras=self.export_extras, export_apply=self.apply_modifiers,
                           export_all_influences=True, export_tangents=True,
                           export_texcoords=True, export_normals=True, export_materials='EXPORT')
            supported = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
            result = bpy.ops.export_scene.gltf(**{k: v for k, v in options.items() if k in supported})
            if 'FINISHED' not in result:
                raise RuntimeError('Blender glTF export did not finish.')
            return self.bridge(context, dict(operation='export', input=source,
                               output=str(Path(self._temp.name) / 'scene.pax'), settings=self.converter_settings()))
        except Exception as error:
            self.cleanup(context)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

    def on_success(self, context):
        stats = json.loads(Path(self._request['result']).read_text())
        if stats['streamBytes'] > stats['sourceBytes'] * 1.05:
            raise RuntimeError('Converter violated the 5% size budget.')
        atomic_copy(self._request['output'], self.filepath)
        if self.write_stats:
            atomic_copy(self._request['result'], str(Path(self.filepath).with_suffix('.stats.json')))
        context.scene['pax_last_export_settings'] = json.dumps(self.converter_settings())
        self.report({'INFO'}, f"PAX exported: {stats['streamBytes'] / 1048576:.2f} MiB ({stats['overheadPercent']:+.2f}% versus Blender GLB)")


class PAX_OT_import(Job, bpy.types.Operator, ImportHelper):
    bl_idname = 'import_scene.pax'
    bl_label = 'Import PAX'
    bl_options = {'UNDO'}
    filename_ext = '.pax'
    filter_glob: StringProperty(default='*.pax', options={'HIDDEN'})
    merge_vertices: BoolProperty(name='Merge Vertices', default=False, description='Merge equivalent Blender vertices; disable to retain separate attribute tuples')

    def draw(self, context):
        self.layout.label(text='Reconstructs full detail before import.')
        self.layout.prop(self, 'merge_vertices')
        self.layout.label(text='Textures are packed into the blend file.')

    def execute(self, context):
        try:
            check_runtime(context)
            self._temp = tempfile.TemporaryDirectory(prefix='pax-import-')
            return self.bridge(context, dict(operation='import', input=str(Path(self.filepath).resolve()),
                               output=str(Path(self._temp.name) / 'scene.glb')))
        except Exception as error:
            self.cleanup(context)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

    def on_success(self, context):
        summary = json.loads(Path(self._request['result']).read_text())
        result = bpy.ops.import_scene.gltf(filepath=self._request['output'], merge_vertices=self.merge_vertices,
                                          import_pack_images=True)
        if 'FINISHED' not in result:
            raise RuntimeError('Blender glTF import did not finish.')
        # Temporary GLB removal requires embedded/packed images. Keep them packed even
        # if the user later chooses Image > Unpack; no dangling temporary paths.
        context.scene['pax_last_import'] = json.dumps(summary)
        if any(e in summary['extensions'] for e in ('KHR_interactivity', 'KHR_gaussian_splatting')):
            self.report({'WARNING'}, 'PAX data reconstructed. Blender does not execute interactivity graphs or render Gaussian splats as Three.js does; see import metadata.')
        else:
            self.report({'INFO'}, f"PAX imported at full detail: {summary['vertices']} source vertex tuples")


def menu_export(self, context):
    self.layout.operator(PAX_OT_export.bl_idname, text='Progressive Asset (.pax)')


def menu_import(self, context):
    self.layout.operator(PAX_OT_import.bl_idname, text='Progressive Asset (.pax)')


CLASSES = (PAXPreferences, PAX_OT_install_runtime, PAX_OT_export, PAX_OT_import)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)


def unregister():
    for job in list(_ACTIVE):
        job.cancel(bpy.context)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
