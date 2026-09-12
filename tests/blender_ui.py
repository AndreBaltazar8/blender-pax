import bpy, sys, os, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addon'))
os.environ['PAX_RUNTIME_ROOT'] = str(ROOT / 'addon/blender_pax/runtime')
import blender_pax
blender_pax.register()
def capture():
    try:
        windows = list(bpy.context.window_manager.windows)
        area = next(a for w in windows for a in w.screen.areas if a.type == 'FILE_BROWSER')
        window = next(w for w in windows if area in list(w.screen.areas))
        assert area.spaces.active.active_operator.bl_idname == 'EXPORT_SCENE_OT_pax'
        area.spaces.active.show_region_tool_props = True
        with bpy.context.temp_override(window=window, area=area):
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'docs/export-panel.png'))
        print('PAX_EXPORT_UI_OK', flush=True)
        # File-browser window shutdown crashes in this Nix Blender build;
        # this isolated capture process has no document to save.
        os._exit(0)
    except Exception:
        import traceback
        traceback.print_exc()
        os._exit(1)
def resize():
    tool = os.environ.get('XDOTOOL')
    if tool:
        ids = subprocess.check_output([tool, 'search', '--onlyvisible', '--class', 'Blender']).decode().split()
        for window in ids:
            subprocess.run([tool, 'windowsize', window, '1440', '1000'], check=True)
    bpy.app.timers.register(capture, first_interval=3)
def show():
    bpy.ops.export_scene.pax('INVOKE_DEFAULT', filepath=str(ROOT / 'artifacts/example.pax'))
    bpy.app.timers.register(resize, first_interval=2)
bpy.app.timers.register(show, first_interval=2)
