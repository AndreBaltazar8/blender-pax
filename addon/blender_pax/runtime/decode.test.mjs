import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import sharp from "sharp";
import { convert } from "convert-pax";
import { parseGLB, accessor, Builder } from "convert-pax/gltf";
import { decode } from "./decode.mjs";
import { decodeBasis } from "convert-pax/basis";
import { readPackets, TYPES, unpackPayload } from "spec-pax";
const hash = (a) =>
  createHash("sha256")
    .update(new Uint8Array(a.buffer, a.byteOffset, a.byteLength))
    .digest("hex");
const fixture = (name) =>
  new URL(`../../../tests/fixtures/${name}.glb`, import.meta.url);
async function verify(input, settings = {}) {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), "blender-pax-proof-"));
  try {
    const pax = path.join(temp, "asset.pax"),
      glb = path.join(temp, "decoded.glb"),
      stats = await convert(input, pax, settings),
      summary = await decode(pax, glb),
      out = parseGLB(await fs.readFile(glb));
    assert.equal(summary.version, 0);
    assert.ok(stats.streamBytes <= stats.sourceBytes * 1.05);
    stats.primitives.forEach((d, i) => {
      const p = out.json.meshes[d.mesh].primitives[d.primitive];
      d.attributes.forEach((a, j) =>
        assert.equal(
          hash(
            accessor(
              out.json,
              out.bin,
              a.morph === undefined
                ? p.attributes[a.semantic]
                : p.targets[a.morph][a.semantic],
            ),
          ),
          stats.verification.primitives[i].attributes[j].hash,
        ),
      );
      assert.equal(
        hash(Uint32Array.from(accessor(out.json, out.bin, p.indices))),
        stats.verification.primitives[i].finalIndicesHash,
      );
    });
    for (const proof of stats.verification.textures) {
      const image = out.json.images[proof.image],
        v = out.json.bufferViews[image.bufferView],
        encoded = out.bin.subarray(v.byteOffset, v.byteOffset + v.byteLength),
        pixels = await sharp(encoded).ensureAlpha().raw().toBuffer();
      assert.equal(hash(pixels), proof.hash);
    }
    const original = parseGLB(await fs.readFile(input));
    for (let i = 0; i < original.json.nodes.length; i++) {
      const a = structuredClone(original.json.nodes[i]),
        b = structuredClone(out.json.nodes[i]);
      for (const name of Object.keys(
        a.extensions?.EXT_mesh_gpu_instancing?.attributes || {},
      )) {
        assert.deepEqual(
          accessor(
            original.json,
            original.bin,
            a.extensions.EXT_mesh_gpu_instancing.attributes[name],
          ),
          accessor(
            out.json,
            out.bin,
            b.extensions.EXT_mesh_gpu_instancing.attributes[name],
          ),
        );
        b.extensions.EXT_mesh_gpu_instancing.attributes[name] =
          a.extensions.EXT_mesh_gpu_instancing.attributes[name];
      }
      assert.deepEqual(a, b);
    }
    for (let i = 0; i < (original.json.skins || []).length; i++) {
      const a = original.json.skins[i],
        b = out.json.skins[i];
      assert.deepEqual(a.joints, b.joints);
      if (a.inverseBindMatrices !== undefined)
        assert.deepEqual(
          accessor(original.json, original.bin, a.inverseBindMatrices),
          accessor(out.json, out.bin, b.inverseBindMatrices),
        );
    }
    assert.equal(
      out.json.animations?.length || 0,
      original.json.animations?.length || 0,
    );
    for (let i = 0; i < (original.json.animations || []).length; i++) {
      const a = original.json.animations[i],
        b = out.json.animations[i];
      assert.deepEqual(a.channels, b.channels);
      a.samplers.forEach((sampler, j) => {
        for (const field of ["input", "output"])
          assert.deepEqual(
            accessor(original.json, original.bin, sampler[field]),
            accessor(out.json, out.bin, b.samplers[j][field]),
          );
      });
    }
    const broken = Buffer.from(await fs.readFile(pax));
    broken[broken.length - 1] ^= 1;
    await fs.writeFile(path.join(temp, "bad.pax"), broken);
    await assert.rejects(() =>
      decode(path.join(temp, "bad.pax"), path.join(temp, "bad.glb")),
    );
    await assert.rejects(() => fs.access(path.join(temp, "bad.glb")));
    return { stats, out };
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
}
test("all primitive modes, shared meshes, skins, animations and material metadata reconstruct", async () => {
  const { stats, out } = await verify(fixture("compatibility").pathname, {
    geometryBaseRatio: 0.1,
  });
  assert.equal(out.json.scenes.length, 2);
  for (const p of stats.primitives)
    for (const ref of p.instances) {
      const a = out.json.meshes[p.mesh].primitives[p.primitive],
        b = out.json.meshes[ref.mesh].primitives[ref.primitive];
      assert.deepEqual(a.attributes, b.attributes);
      assert.equal(a.indices, b.indices);
    }
});
test("original KTX2 mip payloads decode to exact RGBA8 for Blender", async () => {
  const { stats } = await verify(fixture("basis").pathname, {
    textureBaseSize: 16,
  });
  assert.equal(stats.textures[0].codec, "ktx2-mip");
});
test("lattice and tile paths preserve exact pixels and complete geometry", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "pax-texture-proof-"));
  try {
    const b = new Builder({
      asset: { version: "2.0" },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: {}, material: 0 }] }],
      materials: [{ pbrMetallicRoughness: { baseColorTexture: { index: 0 } } }],
      textures: [{ source: 0 }],
      images: [],
    });
    const p = b.json.meshes[0].primitives[0];
    p.attributes.POSITION = b.attribute(
      new Float32Array([-1, -1, 0, 1, -1, 0, 0, 1, 0]),
      { componentType: 5126, type: "VEC3", min: [-1, -1, 0], max: [1, 1, 0] },
    );
    p.attributes.TEXCOORD_0 = b.attribute(
      new Float32Array([0, 0, 1, 0, 0.5, 1]),
      { componentType: 5126, type: "VEC2" },
    );
    let seed = 123;
    const raw = Uint8Array.from({ length: 256 * 256 * 4 }, () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed >>> 24;
    });
    const png = await sharp(raw, {
      raw: { width: 256, height: 256, channels: 4 },
    })
      .png()
      .toBuffer();
    b.json.images.push({ bufferView: b.view(png), mimeType: "image/png" });
    const file = path.join(dir, "texture.glb");
    await fs.writeFile(file, b.finish());
    for (const textureTiles of [false, true]) {
      const { stats } = await verify(file, {
        textureBaseSize: 16,
        textureTiles,
        tileSize: 128,
      });
      assert.equal(
        stats.textures[0].codec,
        textureTiles ? "tile-lattice" : "lattice",
      );
    }
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});
