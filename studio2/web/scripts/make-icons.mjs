#!/usr/bin/env node
// Draws the Studio logo mark (an accent rounded square with a white rounded
// "page" outline, the same shape as the favicon and .wordmark-mark) into the
// PNG icons the web app manifest and iOS need. No dependencies: the shapes are
// rasterised here with 4×4 supersampling and written as PNG with zlib.
//
//   node scripts/make-icons.mjs     → public/icon-*.png, apple-touch-icon.png, icon.svg

import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";

const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../public");
const ACCENT = [0x5b, 0x5b, 0xd6];
const WHITE = [255, 255, 255];

// The mark on a 32-unit grid, as in index.html's favicon.
const MARK = { size: 32, radius: 9, page: { x: 9, y: 8, w: 14, h: 16, r: 3, stroke: 2.2 } };

/** Signed distance from (px, py) to a rounded rectangle (negative inside). */
function roundedRect(px, py, x, y, w, h, r) {
  const cx = x + w / 2;
  const cy = y + h / 2;
  const qx = Math.abs(px - cx) - (w / 2 - r);
  const qy = Math.abs(py - cy) - (h / 2 - r);
  const ox = Math.max(qx, 0);
  const oy = Math.max(qy, 0);
  return Math.hypot(ox, oy) + Math.min(Math.max(qx, qy), 0) - r;
}

/**
 * size: output pixels. full: the background fills the square (maskable / Apple),
 * scale: how much of the square the mark's page outline grid occupies.
 */
function draw(size, { full = false, scale = 1 } = {}) {
  const pixels = Buffer.alloc(size * size * 4);
  const unit = (size / MARK.size) * scale;
  const offset = (size - MARK.size * unit) / 2;
  const S = 4;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let bg = 0;
      let fg = 0;
      for (let sy = 0; sy < S; sy++) {
        for (let sx = 0; sx < S; sx++) {
          const px = x + (sx + 0.5) / S;
          const py = y + (sy + 0.5) / S;
          const inBg = full
            ? true
            : roundedRect(px, py, 0, 0, size, size, (MARK.radius / MARK.size) * size) <= 0;
          if (!inBg) continue;
          bg++;
          const p = MARK.page;
          const d = roundedRect((px - offset) / unit, (py - offset) / unit, p.x, p.y, p.w, p.h, p.r);
          if (Math.abs(d) <= p.stroke / 2) fg++;
        }
      }
      const i = (y * size + x) * 4;
      const alpha = bg / (S * S);
      const mix = bg ? fg / bg : 0;
      for (let c = 0; c < 3; c++) pixels[i + c] = Math.round(ACCENT[c] * (1 - mix) + WHITE[c] * mix);
      pixels[i + 3] = Math.round(alpha * 255);
    }
  }
  return png(size, size, pixels);
}

const CRC = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buffer) {
  let c = 0xffffffff;
  for (const b of buffer) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}
function png(width, height, rgba) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 6; // RGBA
  const rows = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) rgba.copy(rows, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(rows, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#5b5bd6"/><rect x="9" y="8" width="14" height="16" rx="3" fill="none" stroke="#fff" stroke-width="2.2"/></svg>\n`;

fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(path.join(OUT, "icon.svg"), SVG);
fs.writeFileSync(path.join(OUT, "icon-192.png"), draw(192));
fs.writeFileSync(path.join(OUT, "icon-512.png"), draw(512));
// Maskable: full-bleed background, the mark inside the 80 % safe circle.
fs.writeFileSync(path.join(OUT, "icon-maskable-512.png"), draw(512, { full: true, scale: 0.72 }));
// iOS rounds the corners itself and shows black behind transparency: full bleed.
fs.writeFileSync(path.join(OUT, "apple-touch-icon.png"), draw(180, { full: true, scale: 0.86 }));
console.log("wrote icons to", OUT);
