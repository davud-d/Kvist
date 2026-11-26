// The sky behind the page: stars all around the phone in 3D. The phone is a window into it: its orientation turns the
// view (the sky stays where it is in the room, so turning the phone looks around), and the whole field drifts slowly
// past, near stars faster than far ones, which is what gives the depth. Drawn at the screen's own pixel density.
// Without a gyroscope (a desktop browser, permission refused) the view turns by itself, very slowly.
// The pointer (a mouse, or a finger while it touches) reaches into the sky too: the eye moves a little after it, so near
// stars slide against far ones; around it the stars part and brighten; a click or a tap sends a wave through them.
// Under reduced motion the sky is drawn once and stays still.

const NEAR = 3200; // stars in a cube of side 2 around the viewer, which drifts past
const FAR = 9000; // stars at infinity: they only turn with the view; a third of them crowd into a band, the Milky Way
const DRIFT = 0.012; // cube units per second, along a horizontal direction of the room
const FOV = 1.15; // vertical field of view, radians
const EASE = 0.12; // how much of the way to the phone's orientation the view goes each frame
const SHIFT = 0.12; // how far the eye moves after the pointer, in cube units per half of the screen's short side
const LENS = 150; // CSS px: the radius in which stars part around the pointer
const WAVE_SPEED = 900; // CSS px per second
const WAVE_LIFE = 1.6; // seconds

const still = matchMedia("(prefers-reduced-motion: reduce)");

/** A random star: a position in the cube [-1, 1]^3, a size, a brightness and a slight colour. */
function star() {
  // Temperatures as seen: most stars white, some blue-white, a few warm.
  const tint = Math.random();
  const color = tint < 0.12 ? [255, 214, 170] : tint < 0.3 ? [190, 210, 255] : [255, 255, 255];
  const bright = Math.random() ** 3; // many faint, few bright
  return { x: rnd(), y: rnd(), z: rnd(), size: 0.35 + bright * 1.5, alpha: 0.25 + bright * 0.75, color, phase: Math.random() * 6.28 };
}

/** A star at infinity: a direction, small and mostly faint. */
function farStar(_, index) {
  // A band: z close to 0 (a great circle), spread like a bell curve.
  const band = index % 3 === 0;
  const z = band ? Math.max(-1, Math.min(1, (Math.random() + Math.random() + Math.random() - 1.5) * 0.18)) : rnd();
  const angle = Math.random() * 6.2832;
  const ring = Math.sqrt(1 - z * z);
  const s = star();
  return { ...s, x: ring * Math.cos(angle), y: ring * Math.sin(angle), z, size: 0.4 + Math.random() ** 4 * 1.3, alpha: (band ? 0.25 : 0.35) + Math.random() ** 3 * 0.65 };
}

function rnd() {
  return Math.random() * 2 - 1;
}

// -- orientation --------------------------------------------------------------------------------------------------------

/** Quaternion [w, x, y, z] of a device orientation (W3C alpha/beta/gamma, Z-X'-Y'' in degrees). */
export function quaternion(alpha, beta, gamma) {
  const d = Math.PI / 360;
  const [cx, cy, cz] = [Math.cos(beta * d), Math.cos(gamma * d), Math.cos(alpha * d)];
  const [sx, sy, sz] = [Math.sin(beta * d), Math.sin(gamma * d), Math.sin(alpha * d)];
  return [
    cx * cy * cz - sx * sy * sz,
    sx * cy * cz - cx * sy * sz,
    cx * sy * cz + sx * cy * sz,
    cx * cy * sz + sx * sy * cz,
  ];
}

function slerp(a, b, t) {
  let dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  const sign = dot < 0 ? -1 : 1;
  dot *= sign;
  const out = a.map((v, i) => v + (b[i] * sign - v) * t);
  if (dot > 0.9995) return normalize(out);
  const theta = Math.acos(dot);
  const [wa, wb] = [Math.sin((1 - t) * theta) / Math.sin(theta), Math.sin(t * theta) / Math.sin(theta)];
  return a.map((v, i) => v * wa + b[i] * sign * wb);
}

function normalize(q) {
  const n = Math.hypot(...q) || 1;
  return q.map((v) => v / n);
}

/** The rotation matrix (rows) of the inverse of q: world -> device. */
function inverseMatrix([w, x, y, z]) {
  return [
    [1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)],
    [2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)],
    [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)],
  ];
}

// iOS asks before it gives the page the gyroscope, and only from a touch; Android and other browsers just give it.
function askForMotion() {
  const ask = window.DeviceOrientationEvent?.requestPermission;
  if (typeof ask !== "function") return;
  const once = () => {
    window.removeEventListener("pointerup", once, true);
    ask.call(DeviceOrientationEvent).catch(() => {});
  };
  window.addEventListener("pointerup", once, true);
}

// -- the sky ------------------------------------------------------------------------------------------------------------

export function startStars() {
  const canvas = document.createElement("canvas");
  canvas.id = "stars";
  canvas.setAttribute("aria-hidden", "true");
  document.body.prepend(canvas);
  const ctx = canvas.getContext("2d", { alpha: false });
  const near = Array.from({ length: NEAR }, star);
  const far = Array.from({ length: FAR }, farStar);

  let [width, height, dpr, focal] = [0, 0, 1, 1];
  const resize = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 4);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    focal = (Math.max(width, height) / 2 / Math.tan(FOV / 2)) * dpr;
  };
  resize();
  window.addEventListener("resize", resize);

  // The phone held upright in front of the face looks at the horizon: beta 90 is the starting view.
  let target = quaternion(0, 90, 0);
  let view = target;
  let gyro = false;
  window.addEventListener("deviceorientation", (event) => {
    if (event.alpha === null || event.beta === null) return;
    gyro = true;
    target = quaternion(event.alpha, event.beta, event.gamma ?? 0);
  });
  askForMotion();

  // The pointer in CSS px; `hover` eases to `present` (1 while a mouse is over the page or a finger is down).
  const pointer = { x: 0, y: 0, sx: 0, sy: 0, present: 0, hover: 0 };
  const waves = [];
  const place = (event) => {
    if (!pointer.hover) [pointer.sx, pointer.sy] = [event.clientX, event.clientY];
    [pointer.x, pointer.y] = [event.clientX, event.clientY];
  };
  window.addEventListener("pointermove", (event) => {
    if (event.pointerType === "touch" && !event.isPrimary) return;
    place(event);
    if (event.pointerType === "mouse") pointer.present = 1;
  }, { passive: true });
  window.addEventListener("pointerdown", (event) => {
    place(event);
    pointer.present = 1;
    waves.push({ x: event.clientX, y: event.clientY, born: performance.now() / 1000 });
  }, { passive: true });
  const lift = (event) => {
    if (event.pointerType !== "mouse") pointer.present = 0;
  };
  window.addEventListener("pointerup", lift, { passive: true });
  window.addEventListener("pointercancel", lift, { passive: true });
  document.documentElement.addEventListener("pointerleave", () => (pointer.present = 0));
  window.addEventListener("blur", () => (pointer.present = 0));

  let offset = 0;
  let last = performance.now();
  let frame = 0;

  const draw = (now) => {
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    if (!still.matches) {
      offset = (offset + DRIFT * dt) % 2;
      if (!gyro) target = quaternion((now / 1000) * 1.2, 90, 0);
      view = slerp(view, target, gyro ? 1 - (1 - EASE) ** (dt * 60) : 1);
      const follow = 1 - 0.9 ** (dt * 60);
      pointer.sx += (pointer.x - pointer.sx) * follow;
      pointer.sy += (pointer.y - pointer.sy) * follow;
      pointer.hover += (pointer.present - pointer.hover) * (1 - 0.94 ** (dt * 60));
      while (waves.length && now / 1000 - waves[0].born > WAVE_LIFE) waves.shift();
    }
    render(now / 1000);
    if (!still.matches) frame = requestAnimationFrame(draw);
  };

  const render = (seconds) => {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // The screen's own axes in the device frame: x right, y up, the camera looks along -z (out of the back).
    const m = inverseMatrix(view);
    const [cx, cy] = [canvas.width / 2, canvas.height / 2];
    const [mx, my, hover] = [pointer.sx * dpr, pointer.sy * dpr, pointer.hover];
    const lens = LENS * dpr;
    const rings = waves.map((w) => ({ x: w.x * dpr, y: w.y * dpr, radius: (seconds - w.born) * WAVE_SPEED * dpr, fade: 1 - (seconds - w.born) / WAVE_LIFE }));
    if (hover > 0.01) {
      const glow = ctx.createRadialGradient(mx, my, 0, mx, my, lens * 1.3);
      glow.addColorStop(0, `rgba(170,190,255,${0.07 * hover})`);
      glow.addColorStop(1, "rgba(170,190,255,0)");
      ctx.fillStyle = glow;
      ctx.fillRect(mx - lens * 1.3, my - lens * 1.3, lens * 2.6, lens * 2.6);
    }
    for (const ring of rings) {
      ctx.strokeStyle = `rgba(190,205,255,${0.09 * ring.fade * ring.fade})`;
      ctx.lineWidth = 1.5 * dpr;
      ctx.beginPath();
      ctx.arc(ring.x, ring.y, ring.radius, 0, 6.2832);
      ctx.stroke();
    }
    const point = (s, x, y, z, a, radius) => {
      if (z > -0.02) return;
      let px = cx + (x / -z) * focal;
      let py = cy + (y / z) * focal;
      if (px < -8 || py < -8 || px > canvas.width + 8 || py > canvas.height + 8) return;
      let alpha = a * (0.85 + 0.15 * Math.sin(seconds * 1.7 + s.phase));
      let r = radius * dpr;
      if (hover > 0.01) {
        // Stars part around the pointer, as if it were a lens, and the ones it passes light up.
        const [dx, dy] = [px - mx, py - my];
        const d = Math.hypot(dx, dy) || 1;
        if (d < lens) {
          const k = (1 - d / lens) ** 2 * hover;
          px += (dx / d) * k * 22 * dpr;
          py += (dy / d) * k * 22 * dpr;
          alpha = Math.min(1, alpha * (1 + k * 2.5));
          r *= 1 + k * 0.8;
        }
      }
      for (const ring of rings) {
        const k = Math.max(0, 1 - Math.abs(Math.hypot(px - ring.x, py - ring.y) - ring.radius) / (36 * dpr)) * ring.fade;
        if (k <= 0) continue;
        alpha = Math.min(1, alpha * (1 + k * 3));
        r *= 1 + k * 0.9;
      }
      const [red, green, blue] = s.color;
      if (r < 1.2) {
        ctx.fillStyle = `rgba(${red},${green},${blue},${alpha * Math.max(r, 0.7)})`;
        ctx.fillRect(px - 0.75, py - 0.75, 1.5, 1.5);
        return;
      }
      if (radius > 0.9) {
        // A soft halo around the bright ones, then the point itself.
        ctx.fillStyle = `rgba(${red},${green},${blue},${alpha * 0.1})`;
        ctx.beginPath();
        ctx.arc(px, py, r * 3, 0, 6.2832);
        ctx.fill();
      }
      ctx.fillStyle = `rgba(${red},${green},${blue},${alpha})`;
      ctx.beginPath();
      ctx.arc(px, py, r, 0, 6.2832);
      ctx.fill();
    };
    for (const s of far) {
      const x = m[0][0] * s.x + m[0][1] * s.y + m[0][2] * s.z;
      const y = m[1][0] * s.x + m[1][1] * s.y + m[1][2] * s.z;
      const z = m[2][0] * s.x + m[2][1] * s.y + m[2][2] * s.z;
      point(s, x, y, z, s.alpha, s.size * 0.5);
    }
    // The eye moves after the pointer: near stars slide against the far ones, which stay put.
    const half = Math.min(width, height) / 2;
    const tx = ((pointer.sx - width / 2) / half) * SHIFT * hover;
    const ty = ((height / 2 - pointer.sy) / half) * SHIFT * hover;
    for (const s of near) {
      let wy = s.y - offset;
      if (wy < -1) wy += 2;
      const x = m[0][0] * s.x + m[0][1] * wy + m[0][2] * s.z - tx;
      const y = m[1][0] * s.x + m[1][1] * wy + m[1][2] * s.z - ty;
      const z = m[2][0] * s.x + m[2][1] * wy + m[2][2] * s.z;
      // Near stars are larger; each fades in at the far edge of the cube and out as it passes the viewer.
      const distance = Math.hypot(x, y, z);
      const edge = Math.min(1, (1.6 - distance) * 3, distance * 6);
      if (edge <= 0) continue;
      const close = Math.min(1, 0.3 / distance);
      point(s, x, y, z, s.alpha * edge, s.size * (0.45 + close * 1.4) * 0.5);
    }
  };

  const resume = () => {
    cancelAnimationFrame(frame);
    last = performance.now();
    if (document.hidden) return;
    frame = requestAnimationFrame(draw);
  };
  document.addEventListener("visibilitychange", resume);
  still.addEventListener("change", resume);
  window.addEventListener("resize", () => still.matches && render(performance.now() / 1000));
  resume();
}
