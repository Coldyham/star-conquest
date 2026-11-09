// The map camera against `geometry.WorldView`'s rules: the fit, zooming about
// a point, and the free clamp's room.

import assert from "node:assert/strict";
import { test } from "node:test";

import { Camera, LIVE_ROOM, roomZoomMin, ZOOM_MAX } from "../js/camera.mjs";

const WORLD = [0, 0, 1000, 500];
const SCREEN = [0, 0, 1200, 900];

test("zoom 1 fits the world inside the padding, centred", () => {
  const cam = new Camera(WORLD, SCREEN, 50);
  assert.equal(cam.scale, 1.1);   // min(1100 / 1000, 800 / 500)
  assert.deepEqual(cam.toScreen(500, 250), [600, 450]);
  const [x0] = cam.toScreen(0, 0);
  assert.equal(x0, 50);
});

test("zooming keeps the world point under the cursor fixed", () => {
  const cam = new Camera(WORLD, SCREEN, 50);
  const under = cam.toWorld(400, 300);
  cam.zoomAt(400, 300, 2);
  const [x, y] = cam.toScreen(...under);
  assert.ok(Math.abs(x - 400) < 1e-9 && Math.abs(y - 300) < 1e-9);
});

test("zoom is held between the room's floor and ZOOM_MAX", () => {
  const cam = new Camera(WORLD, SCREEN, 50);
  cam.zoomAt(600, 450, 1e-6);
  assert.equal(cam.zoom, roomZoomMin(LIVE_ROOM));
  cam.zoomAt(600, 450, 1e6);
  assert.equal(cam.zoom, ZOOM_MAX);
});

test("a pan never takes more than the room's share of the map off screen", () => {
  const cam = new Camera(WORLD, SCREEN, 50);
  cam.pan(100000, 100000);
  const [x0, y0] = cam.toScreen(0, 0);
  const keep = (1 - LIVE_ROOM) * Math.min(1000 * cam.scale, 1200);
  assert.ok(Math.abs(x0 - (1200 - keep)) < 1e-9);
  assert.ok(y0 < 900);
  cam.reset();
  assert.deepEqual(cam.toScreen(500, 250), [600, 450]);
});
