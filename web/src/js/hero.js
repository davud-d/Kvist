/**
 * hero.js — draws the ported banner animation into the hero's 2D canvas.
 *
 * Two phases of one canvas, as the hero calls for:
 *   1. intro   — up to INTRO_MS, the word is typed at full strength inside its
 *                terminal plate;
 *   2. settled — brightness drops to --hero-settle, the plate dissolves, and
 *                the raster stays as the field the hero sits on, running the
 *                same cycle at real speed from then on.
 *
 * No WebGL: the source is a raster on upper-half blocks, which is 2D by
 * nature, and a second renderer would only add a failure class on weak and
 * virtualised GPUs.
 *
 * Colour never appears here. Roles come out of hero-frames.js and are resolved
 * against tokens.css at runtime, re-read whenever the theme changes.
 */
import {
  CELL_ROWS,
  FONT,
  GLYPH_H,
  GLYPH_W,
  SLOT_COUNT,
  SLOT_W,
  WORD_COMPLETE_MS,
  frameAt,
} from "./hero-frames.js";

/* The intro must be over before the first screen settles; the word itself
   lands at WORD_COMPLETE_MS (1435 ms in the Python timeline), so the clock is
   run fast enough to fit the brief and then eased back to real time. */
const INTRO_MS = 1200;
const INTRO_RATE = WORD_COMPLETE_MS / (INTRO_MS * 0.82);
const SETTLE_MS = 420;
const MAX_DPR = 2;
const MAX_AREA = 2_400_000; // device pixels; a 4K hero is not worth the fill

export function mountHero(canvas, { reducedMotion }) {
  const context = canvas.getContext("2d", { alpha: true });
  if (!context) return () => {};

  let palette = readPalette();
  let width = 0;
  let height = 0;
  let dpr = 1;
  let raf = 0;
  let start = 0;
  let clock = 0; // warped animation time, milliseconds
  let last = 0;
  let running = false;

  function readPalette() {
    const style = getComputedStyle(document.documentElement);
    const get = (name) => style.getPropertyValue(name).trim();
    return {
      glyph: parseColor(get("--hero-glyph")),
      flash: parseColor(get("--hero-flash")),
      plate: parseColor(get("--hero-plate")),
      settle: Number(get("--hero-settle")) || 0.34,
    };
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    const area = rect.width * rect.height * dpr * dpr;
    if (area > MAX_AREA) dpr *= Math.sqrt(MAX_AREA / area);
    width = Math.round(rect.width * dpr);
    height = Math.round(rect.height * dpr);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return true;
  }

  /** Pixel size and origin for a given phase progress (0 intro, 1 settled). */
  function geometry(settled) {
    const columns = SLOT_COUNT * SLOT_W;
    const wide = width / dpr >= 980;
    /* The word lives in a zone the text never enters, and the zone is the
       mirror of the scrim stops in components.css: wide puts it to the right
       of the text column, narrow puts it above. Move one, move the other. */
    /* Narrow has no column to sit beside, so the word takes the empty half of
       the badge row above the headline instead of landing on the prose. */
    const zone = wide ? [0.60, 0.97] : [0.42, 0.97];
    const zoneX = width * zone[0];
    const zoneW = width * (zone[1] - zone[0]);
    const pixel = Math.max(
      2 * dpr,
      Math.min(
        zoneW / columns,
        (height * (wide ? 0.34 : 0.10)) / (CELL_ROWS * 2),
      ),
    );
    const blockW = columns * pixel;
    const blockH = CELL_ROWS * 2 * pixel;
    const centreY = height * (wide ? 0.42 : 0.055 + 0.005 * settled);
    return {
      pixel,
      x: zoneX + (zoneW - blockW) / 2,
      y: centreY - blockH / 2,
      blockW,
      blockH,
    };
  }

  function mix(a, b, t) {
    return [
      Math.round(a[0] + (b[0] - a[0]) * t),
      Math.round(a[1] + (b[1] - a[1]) * t),
      Math.round(a[2] + (b[2] - a[2]) * t),
    ];
  }

  function colorOf(role) {
    const [from, to, t] = role;
    return rgb(mix(palette[from], palette[to], t));
  }

  function draw(atMs, settled) {
    const frame = frameAt(atMs);
    const geo = geometry(settled);
    const alpha = 1 - (1 - palette.settle) * settled;

    context.clearRect(0, 0, width, height);

    /* The plate is the terminal block the word is typed into. It belongs to
       the intro; once the word has settled into the page there is no terminal
       around it any more, so the plate dissolves and the raster stays. */
    if (settled < 1) {
      context.globalAlpha = alpha * (1 - settled);
      const pad = geo.pixel * 3;
      context.fillStyle = rgb(palette.plate);
      roundRect(
        context,
        geo.x - pad,
        geo.y - pad,
        geo.blockW + pad * 2,
        geo.blockH + pad * 2,
        Math.max(2, geo.pixel * 1.2),
      );
      context.fill();
    }

    context.globalAlpha = alpha;
    for (const [slot, [glyph, role]] of frame) {
      const bitmap = FONT[glyph];
      if (!bitmap) continue;
      context.fillStyle = colorOf(role);
      const slotX = geo.x + slot * SLOT_W * geo.pixel;
      for (let cellRow = 0; cellRow < CELL_ROWS; cellRow += 1) {
        const top = 2 * cellRow;
        const bottom = top + 1;
        for (let column = 0; column < GLYPH_W; column += 1) {
          const x = slotX + column * geo.pixel;
          // Upper half block: the top pixel row is the cell's foreground, the
          // bottom row its background. Painted as two rects, same as the cell.
          if (top < GLYPH_H && bitmap[top][column] === "#") {
            context.fillRect(x, geo.y + top * geo.pixel, geo.pixel, geo.pixel);
          }
          if (bottom < GLYPH_H && bitmap[bottom][column] === "#") {
            context.fillRect(x, geo.y + bottom * geo.pixel, geo.pixel, geo.pixel);
          }
        }
      }
    }
    context.globalAlpha = 1;
  }

  /** Settle progress at an instant, 0 during the intro, 1 once settled. */
  function clockSettled(now = performance.now()) {
    const wall = now - start;
    return wall <= INTRO_MS ? 0 : Math.min(1, (wall - INTRO_MS) / SETTLE_MS);
  }

  function tick(now) {
    if (!running) return;
    if (last === 0) last = now;
    const delta = Math.min(now - last, 64);
    last = now;

    const settled = clockSettled(now);
    const rate = INTRO_RATE + (1 - INTRO_RATE) * settled;
    clock += delta * rate;

    draw(clock, easeOut(settled));
    raf = requestAnimationFrame(tick);
  }

  function stop() {
    running = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    last = 0;
  }

  function play() {
    if (running || reducedMotion()) return;
    running = true;
    // Coming back to a hidden tab resumes settled, not back at the intro.
    start = start === 0
      ? performance.now()
      : performance.now() - (INTRO_MS + SETTLE_MS);
    raf = requestAnimationFrame(tick);
  }

  function still() {
    // One frame of the settled state: the word standing, nothing moving.
    if (!resize()) return;
    clock = WORD_COMPLETE_MS + 400;
    draw(clock, 1);
  }

  function restart() {
    palette = readPalette();
    if (!resize()) return;
    if (reducedMotion()) {
      stop();
      still();
      return;
    }
    if (running) draw(clock, easeOut(clockSettled()));
    else play();
  }

  const onVisibility = () => {
    if (document.hidden) stop();
    else if (!reducedMotion()) play();
  };

  const onResize = () => {
    if (!resize()) return;
    if (reducedMotion()) still();
    else draw(clock, easeOut(clockSettled()));
  };

  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("kvist:theme", restart);

  /* The hero grows when the webfont lands, so one measurement at mount is not
     enough; an observer keeps the raster matched to the box it lives in. */
  const observer = new ResizeObserver(onResize);
  observer.observe(canvas);

  restart();

  return () => {
    stop();
    observer.disconnect();
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("kvist:theme", restart);
  };
}

/* -- small helpers --------------------------------------------------------- */

function easeOut(t) {
  return 1 - (1 - t) ** 3;
}

function rgb([r, g, b]) {
  return `rgb(${r},${g},${b})`;
}

export function parseColor(value) {
  const text = String(value).trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text);
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].replace(/./g, (c) => c + c) : hex[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  }
  const parts = /rgba?\(([^)]+)\)/i.exec(text);
  if (parts) {
    const n = parts[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [n[0] | 0, n[1] | 0, n[2] | 0];
  }
  return [0, 0, 0];
}

function roundRect(context, x, y, w, h, r) {
  context.beginPath();
  if (context.roundRect) {
    context.roundRect(x, y, w, h, r);
    return;
  }
  context.moveTo(x + r, y);
  context.arcTo(x + w, y, x + w, y + h, r);
  context.arcTo(x + w, y + h, x, y + h, r);
  context.arcTo(x, y + h, x, y, r);
  context.arcTo(x, y, x + w, y, r);
  context.closePath();
}
