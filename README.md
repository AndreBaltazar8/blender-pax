# blender-pax

Import and export **PAX v0** in Blender, with progressive geometry and texture
settings in the export browser.

## Install

Requires **Blender 4.5+**, **Node.js 24+**, **npm** and **Git**.
Tested on Linux with Blender 4.5.3; other platforms are unverified.

1. [Download `blender_pax-0.1.0.zip`](https://github.com/AndreBaltazar8/blender-pax/releases/latest).
2. In **Preferences → Add-ons → menu → Install from Disk**, select the ZIP and
   enable **PAX Import / Export**.
3. Expand its preferences and click **Install / Repair PAX Runtime**. Set Node/npm
   executable paths there if Blender cannot find them.
4. Use **File → Import/Export → Progressive Asset (.pax)**.

Runtime setup downloads pinned codecs into Blender's user scripts directory.
Import/export then works offline. Add-on version `0.1.0` is separate from file
format version `0`.

## Export settings

| Control | Converter setting | Default |
| --- | --- | --- |
| Initial Quality: Fast / Balanced / Sharp | `initialQuality` | Balanced |
| Initial Texture Size: 16–1024 px | `textureBaseSize` | 128 px |
| Initial Geometry Fraction | `geometryBaseRatio` | 0.015 (1.5%) |
| Adaptive Detail Stages | `adaptive` | Enabled |
| Geometry Codec: Auto / Plain Gzip | `geometryCodec` | Auto |
| Stream Texture Tiles | `textureTiles` | Enabled |
| Texture Tile Size: 128 / 256 / 512 px | `tileSize` | 256 px |

Settings control previews and refinement stages, not a fixed LOD count. The
converter may retain extra geometry or change the texture layout to meet the budget.

The **5% overhead cap** is measured against Blender's temporary GLB, not the
`.blend` file. The optional `.stats.json` report records sizes, stages, codecs,
settings and fidelity; it is separate from the PAX asset.

Scene controls include selection, visibility, active scene, animations, skinning,
shape keys, cameras, lights, custom properties and modifiers. **Apply Modifiers**
and **Shape Keys** cannot both be enabled. Operator presets are supported.

[View the export panel](docs/export-panel.png).

## Import and fidelity

Export runs Blender's glTF exporter, then `convert-pax`. Import validates and
reconstructs the complete asset before calling Blender's glTF importer; textures
are packed. Import does not stream detail into the Blender viewport.

Conversion runs in a background process; Escape cancels it. Blender's glTF stages
run on its main thread. Failed conversion leaves an existing output PAX intact.

- Final glTF attribute data and topology are preserved; previews are approximate.
  This is not a lossless `.blend` archive. Bake unsupported procedural materials.
- Geometry, lattice/tiled textures, source images and KTX2 mip streams are
  reconstructed. KTX2 becomes RGBA8 pixels; re-export does not preserve its bitstream.
- Native scene and extension support follows Blender's glTF importer. Required
  unsupported extensions may reject import. Interactivity graphs and Gaussian
  splats do not gain native Blender behavior. Unknown extension refinement packets
  fail explicitly.

## Development

```sh
npm ci --prefix addon/blender_pax/runtime
node --test addon/blender_pax/runtime/decode.test.mjs
blender --background --factory-startup --python-exit-code 1 --python tests/blender_roundtrip.py
python3 scripts/package.py
blender --factory-startup --command extension validate dist/blender_pax-0.1.0.zip
```

Tests cover geometry/pixel reconstruction, animations, skinning, shape keys,
scene content, the size cap and failed exports. `tests/blender_ui.py` captures
the export panel; `tests/blender_modal.py` tests asynchronous import/export.
Run either with `blender --factory-startup --python <script>` and a display.

To load from source, add `addon` to Blender's Python path, import `blender_pax`
and call `register()`. `PAX_RUNTIME_ROOT` overrides the runtime directory;
installed copies use `SCRIPTS/pax_runtime/0.1.0` in Blender's user resources.

## Related projects and license

[spec-pax](https://github.com/AndreBaltazar8/spec-pax) ·
[convert-pax](https://github.com/AndreBaltazar8/convert-pax) ·
[three-pax](https://github.com/AndreBaltazar8/three-pax)

[GPL-3.0-or-later](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md).
