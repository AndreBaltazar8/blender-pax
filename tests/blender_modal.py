"""Exercise the actual asynchronous operators in a Blender window."""
import bpy, os, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addon'))
os.environ['PAX_RUNTIME_ROOT'] = str(ROOT / 'addon/blender_pax/runtime')
import blender_pax
blender_pax.register()
OUT = ROOT / 'artifacts/modal.pax'
OUT.parent.mkdir(exist_ok=True)
OUT.unlink(missing_ok=True)
started = time.monotonic()
phase = 'export'
def check():
    global phase
    try:
        assert time.monotonic() - started < 60, 'Modal operation timed out'
        if blender_pax._ACTIVE:
            return .1
        if phase == 'export':
            assert OUT.exists(), 'Modal export failed'
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete(use_global=False)
            assert bpy.ops.import_scene.pax(filepath=str(OUT)) == {'RUNNING_MODAL'}
            phase = 'import'
            return .1
        assert any(o.type == 'MESH' for o in bpy.context.scene.objects)
        assert 'pax_last_import' in bpy.context.scene
        blender_pax.unregister()
        print('PAX_MODAL_ROUNDTRIP_OK', flush=True)
        os._exit(0)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
def run():
    assert bpy.ops.export_scene.pax(filepath=str(OUT)) == {'RUNNING_MODAL'}
    bpy.app.timers.register(check, first_interval=.1)
bpy.app.timers.register(run, first_interval=1)
