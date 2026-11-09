// The map's camera: `geometry.WorldView` with the game's free clamp (`room`).
// Fits the world box into a screen rectangle at zoom 1, then zooms about a
// screen point and pans in screen pixels, keeping `1 - room` of the map (or of
// the view, once the map is the bigger) on screen on each axis.

export const ZOOM_MAX = 6;          // config.ZOOM_MAX
export const LIVE_ROOM = 0.5;       // config.LIVE_ROOM
export const WHEEL_STEP = 1.15;     // config.ZOOM_WHEEL_STEP
export const BUTTON_STEP = 1.25;    // config.ZOOM_BUTTON_STEP

/** geometry.room_zoom_min: the zoom at which `room` of the fitted map shows past each side. */
export function roomZoomMin(room) {
  return 1 / (1 + 2 * room);
}

export class Camera {
  /** `world` [x0, y0, x1, y1]; `screen` [x, y, w, h]; `padding` px around the fit. */
  constructor(world, screen, padding, room = LIVE_ROOM) {
    this.world = world;
    this.room = room;
    this.padding = padding;
    this.resize(screen);
  }

  /** A new screen rectangle: refit, keeping the zoom and the world point at the centre. */
  resize(screen) {
    const centre = this.screen ? this.toWorld(this.screen[0] + this.screen[2] / 2, this.screen[1] + this.screen[3] / 2)
      : null;
    this.screen = screen;
    const [wx0, wy0, wx1, wy1] = this.world;
    const [, , sw, sh] = screen;
    const ww = Math.max(1e-6, wx1 - wx0), wh = Math.max(1e-6, wy1 - wy0);
    this.fitScale = Math.min(Math.max(1, sw - 2 * this.padding) / ww, Math.max(1, sh - 2 * this.padding) / wh);
    if (centre === null) {
      this.reset();
    } else {
      this.offX = screen[0] + sw / 2 - centre[0] * this.scale;
      this.offY = screen[1] + sh / 2 - centre[1] * this.scale;
      this.clamp();
    }
  }

  get scale() {
    return this.fitScale * (this.zoom ?? 1);
  }

  /** Back to zoom 1, the map centred as at construction. */
  reset() {
    this.zoom = 1;
    const [wx0, wy0, wx1, wy1] = this.world;
    const [sx, sy, sw, sh] = this.screen;
    this.offX = sx + sw / 2 - ((wx0 + wx1) / 2) * this.scale;
    this.offY = sy + sh / 2 - ((wy0 + wy1) / 2) * this.scale;
  }

  toScreen(x, y) {
    return [x * this.scale + this.offX, y * this.scale + this.offY];
  }

  toWorld(px, py) {
    return [(px - this.offX) / this.scale, (py - this.offY) / this.scale];
  }

  /** Multiply the zoom by `factor`, keeping the world point under (px, py) fixed. */
  zoomAt(px, py, factor) {
    const [wx, wy] = this.toWorld(px, py);
    this.zoom = Math.max(roomZoomMin(this.room), Math.min(ZOOM_MAX, this.zoom * factor));
    this.offX = px - wx * this.scale;
    this.offY = py - wy * this.scale;
    this.clamp();
  }

  pan(dx, dy) {
    this.offX += dx;
    this.offY += dy;
    this.clamp();
  }

  /** `WorldView._clamp_room`. */
  clamp() {
    const s = this.scale;
    const [wx0, wy0, wx1, wy1] = this.world;
    const [sx, sy, sw, sh] = this.screen;
    const keep = 1 - this.room;
    const keepX = keep * Math.min(Math.max(1e-6, wx1 - wx0) * s, sw);
    const keepY = keep * Math.min(Math.max(1e-6, wy1 - wy0) * s, sh);
    this.offX = Math.max(sx + keepX - wx1 * s, Math.min(sx + sw - keepX - wx0 * s, this.offX));
    this.offY = Math.max(sy + keepY - wy1 * s, Math.min(sy + sh - keepY - wy0 * s, this.offY));
  }
}
