import fs from "node:fs/promises";
import sharp from "sharp";
import {
  readPackets,
  unpackPayload,
  TYPES,
  COMPONENTS,
  VERSION,
} from "spec-pax";
import { mergeAxis, unfilterRows } from "spec-pax/textures";
import { expandPixels, refineTile } from "spec-pax/texture-tiles";
import { mipContainer } from "spec-pax/ktx-mips";
import { parseGLB, makeGLB, accessor } from "convert-pax/gltf";
import { decodeBasis } from "convert-pax/basis";

/** Decode a complete PAX scene into a self-contained GLB for Blender's importer. */
export async function decode(input, output) {
  let manifest,
    base,
    states,
    images,
    complete = false;
  const bytes = await fs.readFile(input);
  for await (const { type, data, entry } of readPackets(
    new Blob([bytes]).stream(),
  )) {
    if (complete) throw new Error("Data after end packet");
    if (type === TYPES.manifest) {
      if (manifest) throw new Error("Duplicate manifest");
      manifest = JSON.parse(new TextDecoder().decode(data));
      if (manifest.version !== VERSION)
        throw new Error("Unsupported PAX version");
      for (const feature of manifest.features || [])
        if (
          !["primitive-modes", "final-order", "extension-packets"].includes(
            feature,
          )
        )
          throw new Error(`Unsupported PAX feature ${feature}`);
    } else if (type === TYPES.base) {
      if (!manifest || base) throw new Error("Unexpected bootstrap");
      const p = unpackPayload(data);
      base = parseGLB(p.take(Uint8Array, p.meta.glbLength));
      images = [];
      for (const t of p.meta.textures) {
        const spec = manifest.textures[t.image];
        if (!spec) throw new Error("Unknown bootstrap image");
        const pixels = p.take(Uint8Array, t.width * t.height * 4).slice();
        const image = { width: t.width, height: t.height, pixels };
        if (spec.codec === "tile-lattice") {
          Object.assign(image, {
            baseWidth: t.width,
            baseHeight: t.height,
            basePixels: pixels,
            fullWidth: spec.width,
            fullHeight: spec.height,
            tiles: new Map(),
            finished: new Set(),
          });
          image.pixels = expandPixels(
            pixels,
            t.width,
            t.height,
            spec.width,
            spec.height,
          );
        }
        images[t.image] = image;
      }
      states = manifest.primitives.map((d) => {
        const primitive = base.json.meshes[d.mesh].primitives[d.primitive];
        const attrs = d.attributes.map((a) => {
          const id =
              a.morph === undefined
                ? primitive.attributes[a.semantic]
                : primitive.targets[a.morph][a.semantic],
            definition = base.json.accessors[id],
            initial = accessor(base.json, base.bin, id),
            values = new COMPONENTS[a.componentType](
              d.finalVertices * a.itemSize,
            );
          values.set(initial);
          return { values, definition };
        });
        const indices = accessor(base.json, base.bin, primitive.indices),
          active = new Map();
        for (let i = 0; i < indices.length; i += d.indexWidth)
          active.set(
            i / d.indexWidth,
            Array.from(indices.subarray(i, i + d.indexWidth)),
          );
        return { attrs, active, count: d.baseVertices, level: 0 };
      });
    } else if (type === TYPES.geometry) {
      if (!base) throw new Error("Geometry before bootstrap");
      const p = unpackPayload(data);
      if (
        p.meta.primitives.length !== 1 ||
        entry.level !== p.meta.level ||
        entry.resource !== p.meta.primitives[0].id
      )
        throw new Error("Geometry directory mismatch");
      for (const part of p.meta.primitives) {
        const d = manifest.primitives[part.id],
          s = states[part.id];
        if (
          !s ||
          p.meta.level !== s.level + 1 ||
          part.start !== s.count ||
          part.start + part.count > d.finalVertices
        )
          throw new Error("Invalid geometry append");
        d.attributes.forEach((a, i) =>
          s.attrs[i].values.set(
            p.take(COMPONENTS[a.componentType], part.count * a.itemSize),
            part.start * a.itemSize,
          ),
        );
        const removed = p.take(Uint32Array, part.removed),
          added = p.take(Uint32Array, part.added),
          order = p.take(Uint32Array, part.order || 0);
        for (const id of removed)
          if (!s.active.delete(id))
            throw new Error("Unknown removed primitive");
        if (added.length % (d.indexWidth + 1))
          throw new Error("Invalid primitive patch length");
        for (let i = 0; i < added.length; i += d.indexWidth + 1) {
          const id = added[i],
            indices = Array.from(added.subarray(i + 1, i + 1 + d.indexWidth));
          if (
            s.active.has(id) ||
            indices.some((v) => v >= part.start + part.count)
          )
            throw new Error("Invalid primitive indices");
          s.active.set(id, indices);
        }
        if (s.active.size !== part.triangles)
          throw new Error("Primitive count mismatch");
        if (order.length) {
          if (
            order.length !== s.active.size ||
            new Set(order).size !== order.length ||
            Array.from(order).some((id) => !s.active.has(id))
          )
            throw new Error("Invalid final primitive order");
          s.active = new Map(Array.from(order, (id) => [id, s.active.get(id)]));
        }
        s.count += part.count;
        s.level = p.meta.level;
      }
    } else if (type === TYPES.texture) {
      if (!base) throw new Error("Texture before bootstrap");
      const p = unpackPayload(data),
        m = p.meta,
        s = images[m.image],
        d = manifest.textures[m.image];
      if (!s || entry.resource !== m.image)
        throw new Error("Unknown texture resource");
      if (m.codec === "lattice") {
        if (
          m.width !== s.width * (m.axis === "x" ? 2 : 1) ||
          m.height !== s.height * (m.axis === "y" ? 2 : 1)
        )
          throw new Error("Out-of-order lattice");
        s.pixels = mergeAxis(
          s.pixels,
          s.width,
          s.height,
          unfilterRows(p.take(Uint8Array, m.filteredLength), s.width, s.height),
          m.axis,
        );
        s.width = m.width;
        s.height = m.height;
      } else if (m.codec === "tile-lattice") {
        if (entry.tile !== m.tile || !s.tiles)
          throw new Error("Tile directory mismatch");
        const patch = refineTile(s, m, p.take(Uint8Array, m.filteredLength));
        for (let y = 0; y < m.tileHeight; y++)
          s.pixels.set(
            patch.subarray(y * m.tileWidth * 4, (y + 1) * m.tileWidth * 4),
            ((m.y + y) * s.fullWidth + m.x) * 4,
          );
        if (m.final) s.finished.add(m.tile);
        if (s.finished.size === d.tileCount) {
          s.width = d.width;
          s.height = d.height;
        }
      } else if (m.codec === "source") {
        s.encoded = p.take(Uint8Array, m.byteLength).slice();
        s.mimeType = m.mimeType;
        s.width = m.width;
        s.height = m.height;
        s.pixels = null;
      } else if (m.codec === "ktx2" || m.codec === "ktx2-mip") {
        let encoded;
        if (m.codec === "ktx2") encoded = p.take(Uint8Array, m.byteLength);
        else {
          if (m.templateLength) {
            if (s.template) throw new Error("Duplicate KTX template");
            s.template = p.take(Uint8Array, m.templateLength).slice();
            s.nextMip = d.levels - 2;
          }
          if (m.mip !== s.nextMip--) throw new Error("Out-of-order KTX mip");
          const level = p.take(Uint8Array, m.byteLength);
          if (m.mip === 0) encoded = mipContainer(s.template, 0, level);
        }
        if (encoded) {
          const decoded = await decodeBasis(encoded);
          s.pixels = new Uint8Array(decoded.data);
          s.width = decoded.info.width;
          s.height = decoded.info.height;
        }
      } else throw new Error(`Unsupported texture codec ${m.codec}`);
    } else if (type === TYPES.extension) {
      const p = unpackPayload(data);
      throw new Error(
        `Cannot import custom progressive packets for ${p.meta.extension}; a Blender reconstruction adapter is required`,
      );
    } else if (type === TYPES.end) {
      if (
        !base ||
        JSON.parse(new TextDecoder().decode(data)).complete !== true ||
        states.some(
          (s, i) =>
            s.level !== manifest.primitives[i].levels - 1 ||
            s.count !== manifest.primitives[i].finalVertices ||
            s.active.size !== manifest.primitives[i].finalTriangles,
        ) ||
        images.some(
          (s, i) =>
            s.width !== manifest.textures[i].width ||
            s.height !== manifest.textures[i].height,
        )
      )
        throw new Error("Incomplete asset");
      complete = true;
    }
  }
  if (!complete) throw new Error("Missing end packet");
  const json = base.json,
    parts = [Buffer.from(base.bin)];
  let offset = base.bin.length;
  const append = (bytes) => {
    const pad = (4 - (offset % 4)) % 4;
    if (pad) {
      parts.push(Buffer.alloc(pad));
      offset += pad;
    }
    const id = (json.bufferViews ||= []).length;
    json.bufferViews.push({
      buffer: 0,
      byteOffset: offset,
      byteLength: bytes.byteLength,
    });
    parts.push(Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength));
    offset += bytes.byteLength;
    return id;
  };
  const addAccessor = (values, definition) => {
    const next = {
      ...definition,
      bufferView: append(values),
      byteOffset: 0,
      count:
        values.length /
        { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[definition.type],
    };
    delete next.sparse;
    const id = json.accessors.length;
    json.accessors.push(next);
    return id;
  };
  states.forEach((s, i) => {
    const d = manifest.primitives[i],
      attributes = {},
      targets = [];
    d.attributes.forEach((a, k) => {
      const id = addAccessor(s.attrs[k].values, s.attrs[k].definition);
      if (a.morph === undefined) attributes[a.semantic] = id;
      else (targets[a.morph] ||= {})[a.semantic] = id;
    });
    const indices = addAccessor(
      Uint32Array.from(Array.from(s.active.values()).flat()),
      { componentType: 5125, type: "SCALAR" },
    );
    for (const ref of d.instances || [
      { mesh: d.mesh, primitive: d.primitive },
    ]) {
      const primitive = json.meshes[ref.mesh].primitives[ref.primitive];
      primitive.attributes = { ...attributes };
      primitive.indices = indices;
      if (targets.length) primitive.targets = structuredClone(targets);
    }
  });
  for (let i = 0; i < images.length; i++) {
    const s = images[i];
    let encoded = s.encoded,
      mimeType = s.mimeType;
    if (s.pixels) {
      encoded = await sharp(s.pixels, {
        raw: { width: s.width, height: s.height, channels: 4 },
      })
        .png()
        .toBuffer();
      mimeType = "image/png";
    } else if (!["image/png", "image/jpeg"].includes(mimeType)) {
      encoded = await sharp(encoded).png().toBuffer();
      mimeType = "image/png";
    }
    json.images[i] = {
      ...json.images[i],
      bufferView: append(encoded),
      mimeType,
    };
    delete json.images[i].uri;
  }
  await fs.writeFile(output, makeGLB(json, Buffer.concat(parts)));
  return {
    version: VERSION,
    primitives: states.length,
    vertices: states.reduce((n, s) => n + s.count, 0),
    images: images.length,
    animations: json.animations?.length || 0,
    extensions: json.extensionsUsed || [],
    conversionSettings: manifest.conversionSettings,
  };
}
