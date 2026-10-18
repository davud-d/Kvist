// The orb of the voice screen: a small planet of clouds. Deep blue sky inside,
// white clouds lit warm from below, a thin bright rim and a soft glow, the way
// the reference picture (assets/orb-reference-dusk-valley.png) has it. It is
// drawn per pixel into an RGBA picture every frame, so a state is a set of
// numbers (a `Look`) and the shown look eases towards it: the sky changes its
// colour and the clouds change their pace instead of cutting.
//
// What each state shows:
//   Loading      pale grey-blue, an arc running round the rim
//   Waiting      the reference: deep blue, slow clouds, slow breathing
//   Listening    azure and cyan, rings arriving from outside: it hears the person
//   Transcribing teal, a quick arc: the words are being written down
//   Thinking     indigo and violet, clouds in a hurry
//   Working      amber, three small lights orbiting: tools are running
//   Narrating    lilac and pink, a gentle pulse in the rhythm of the line
//   Speaking     bright azure towards warm white, rings leaving with the voice
//   Approval     gold, a slow wide pulse: waiting for a yes or a no
//   Error        red, a shiver
//   Offline      grey and still
//
// The colours shimmer in every state: a thin film of changing hue lies along the
// cloud edges and the rim, faint while waiting and strong while speaking.

use std::f32::consts::TAU;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum OrbState {
    Loading,
    Waiting,
    Listening,
    Transcribing,
    Thinking,
    Working,
    Narrating,
    Speaking,
    Approval,
    Error,
    Offline,
}

pub const ALL_STATES: [OrbState; 11] = [
    OrbState::Loading,
    OrbState::Waiting,
    OrbState::Listening,
    OrbState::Transcribing,
    OrbState::Thinking,
    OrbState::Working,
    OrbState::Narrating,
    OrbState::Speaking,
    OrbState::Approval,
    OrbState::Error,
    OrbState::Offline,
];

type Rgb = [f32; 3];

/// Everything the renderer needs to know about how the planet looks and moves.
#[derive(Clone, Copy, Debug)]
struct Look {
    /// The sky at the top and at the bottom of the sphere, and the colour of rim and glow.
    sky_top: Rgb,
    sky_low: Rgb,
    glow: Rgb,
    /// Share of the sky the clouds cover (0..1) and how fast the planet turns (radians a second).
    cover: f32,
    speed: f32,
    /// Strength of the changing-hue film on cloud edges and rim.
    shimmer: f32,
    /// Breathing: size change and its rate in hertz.
    breath: f32,
    breath_hz: f32,
    /// Glow outside the sphere.
    halo: f32,
    /// Rings: strength, rate, and whether they arrive (1) or leave (0).
    rings: f32,
    ring_hz: f32,
    ring_in: f32,
    /// A running arc round the sphere, and small lights orbiting it.
    sweep: f32,
    orbit: f32,
    /// A shiver of the whole picture.
    shake: f32,
    /// How strongly the loudness of the voice swells the sphere.
    voice: f32,
}

fn rgb(hex: u32) -> Rgb {
    [(hex >> 16 & 0xFF) as f32 / 255.0, (hex >> 8 & 0xFF) as f32 / 255.0, (hex & 0xFF) as f32 / 255.0]
}

fn look(state: OrbState) -> Look {
    let base = Look {
        sky_top: rgb(0x1A4DF2), sky_low: rgb(0x73B3FF), glow: rgb(0x73BFFF), cover: 0.50, speed: 0.05,
        shimmer: 0.25, breath: 0.03, breath_hz: 0.25, halo: 0.55, rings: 0.0, ring_hz: 0.4, ring_in: 0.0,
        sweep: 0.0, orbit: 0.0, shake: 0.0, voice: 0.0,
    };
    match state {
        OrbState::Loading => Look { sky_top: rgb(0x475580), sky_low: rgb(0x8590B3), glow: rgb(0x99A8CC), cover: 0.35, speed: 0.015, shimmer: 0.10, halo: 0.30, sweep: 0.9, breath: 0.012, ..base },
        OrbState::Waiting => base,
        OrbState::Listening => Look { sky_top: rgb(0x0A8CE6), sky_low: rgb(0x59E6FF), glow: rgb(0x80FFFF), cover: 0.40, speed: 0.07, shimmer: 0.45, halo: 0.95, rings: 0.85, ring_hz: 0.9, ring_in: 1.0, breath: 0.04, breath_hz: 1.1, ..base },
        OrbState::Transcribing => Look { sky_top: rgb(0x00808C), sky_low: rgb(0x59D9B3), glow: rgb(0x80FFCC), cover: 0.45, speed: 0.13, shimmer: 0.40, halo: 0.75, sweep: 1.0, breath: 0.02, breath_hz: 2.0, ..base },
        OrbState::Thinking => Look { sky_top: rgb(0x401FBF), sky_low: rgb(0xA673F2), glow: rgb(0xBF8CFF), cover: 0.66, speed: 0.15, shimmer: 0.55, halo: 0.85, breath: 0.05, breath_hz: 0.5, ..base },
        OrbState::Working => Look { sky_top: rgb(0xCC4D0D), sky_low: rgb(0xFFB34D), glow: rgb(0xFFA640), cover: 0.50, speed: 0.09, shimmer: 0.30, halo: 0.85, orbit: 1.0, breath: 0.03, breath_hz: 0.9, ..base },
        OrbState::Narrating => Look { sky_top: rgb(0x7340BF), sky_low: rgb(0xF2A6D9), glow: rgb(0xFFB3E6), cover: 0.55, speed: 0.07, shimmer: 0.55, halo: 0.75, rings: 0.30, ring_hz: 0.5, breath: 0.02, voice: 0.08, ..base },
        OrbState::Speaking => Look { sky_top: rgb(0x2673FF), sky_low: rgb(0xD9EBFF), glow: rgb(0xCCE6FF), cover: 0.55, speed: 0.09, shimmer: 0.90, halo: 1.0, rings: 0.70, ring_hz: 0.7, breath: 0.02, voice: 0.16, ..base },
        OrbState::Approval => Look { sky_top: rgb(0xBF730D), sky_low: rgb(0xFFD966), glow: rgb(0xFFCC4D), cover: 0.45, speed: 0.03, shimmer: 0.30, halo: 1.0, rings: 0.55, ring_hz: 0.35, breath: 0.07, breath_hz: 0.45, ..base },
        OrbState::Error => Look { sky_top: rgb(0xA60D14), sky_low: rgb(0xFF6659), glow: rgb(0xFF4D40), cover: 0.40, speed: 0.12, shimmer: 0.10, halo: 0.80, shake: 1.0, breath: 0.03, breath_hz: 3.0, ..base },
        OrbState::Offline => Look { sky_top: rgb(0x33333D), sky_low: rgb(0x73737F), glow: rgb(0x80808C), cover: 0.30, speed: 0.0, shimmer: 0.0, halo: 0.12, breath: 0.0, ..base },
    }
}

fn mix(a: f32, b: f32, k: f32) -> f32 {
    a + (b - a) * k
}

fn mix3(a: Rgb, b: Rgb, k: f32) -> Rgb {
    [mix(a[0], b[0], k), mix(a[1], b[1], k), mix(a[2], b[2], k)]
}

fn mix_look(a: Look, b: Look, k: f32) -> Look {
    Look {
        sky_top: mix3(a.sky_top, b.sky_top, k), sky_low: mix3(a.sky_low, b.sky_low, k), glow: mix3(a.glow, b.glow, k),
        cover: mix(a.cover, b.cover, k), speed: mix(a.speed, b.speed, k), shimmer: mix(a.shimmer, b.shimmer, k),
        breath: mix(a.breath, b.breath, k), breath_hz: mix(a.breath_hz, b.breath_hz, k),
        halo: mix(a.halo, b.halo, k), rings: mix(a.rings, b.rings, k), ring_hz: mix(a.ring_hz, b.ring_hz, k),
        ring_in: mix(a.ring_in, b.ring_in, k), sweep: mix(a.sweep, b.sweep, k), orbit: mix(a.orbit, b.orbit, k),
        shake: mix(a.shake, b.shake, k), voice: mix(a.voice, b.voice, k),
    }
}

/// How quickly the look follows a new state, per second (about 0.25 s to settle).
const FOLLOW_RATE: f32 = 4.0;
/// How quickly the pop at a state change fades, per second.
const POP_RATE: f32 = 5.0;

pub struct Orb {
    state: OrbState,
    look: Look,
    /// A swell at the moment the state changes, fading out.
    pop: f32,
    /// Loudness of what is being said now, 0..1 (set by the screen).
    pub level: f32,
    /// Turn of the planet and of the rings' arc, advanced by `step` at the look's own pace,
    /// so a faster state does not make the picture jump.
    turn: f32,
    spin: f32,
}

impl Orb {
    pub fn new(state: OrbState) -> Self {
        Orb { state, look: look(state), pop: 0.0, level: 0.0, turn: 0.0, spin: 0.0 }
    }

    pub fn state(&self) -> OrbState {
        self.state
    }

    pub fn set_state(&mut self, state: OrbState) {
        if state != self.state {
            self.state = state;
            self.pop = 1.0;
        }
    }

    /// Moves the look towards the state's look and advances the turning clocks.
    pub fn step(&mut self, dt: f32) {
        let k = 1.0 - (-dt * FOLLOW_RATE).exp();
        self.look = mix_look(self.look, look(self.state), k);
        self.pop *= (-dt * POP_RATE).exp();
        self.turn += dt * self.look.speed;
        self.spin += dt * (0.4 + self.look.speed * 8.0);
    }

    /// An RGBA picture (straight alpha) of `side` x `side` pixels; time `t`
    /// only drives the periodic motions (breathing, rings, shimmer).
    pub fn render(&self, t: f32, side: usize) -> Vec<u8> {
        let l = &self.look;
        let mut out = vec![0u8; side * side * 4];
        let breath = 1.0 + l.breath * (t * l.breath_hz * TAU).sin() + l.voice * self.level;
        let core = 0.50 * breath * (1.0 + 0.10 * self.pop);
        let (shake_x, shake_y) = (
            l.shake * 0.012 * (t * 37.0).sin() * (t * 11.0).cos(),
            l.shake * 0.012 * (t * 29.0).cos() * (t * 13.0).sin(),
        );
        let bright_glow = mix3(l.glow, [1.0; 3], 0.35);
        let inv = 2.0 / side as f32;
        for py in 0..side {
            for px in 0..side {
                let u = (px as f32 + 0.5) * inv - 1.0 - shake_x;
                let v = (py as f32 + 0.5) * inv - 1.0 - shake_y;
                let r = (u * u + v * v).sqrt();
                let mut layer = Pixel::CLEAR;

                if r > core * 0.9 {
                    // The glow reaches zero before the picture's edge, so no square shows.
                    let d = r - core;
                    let glow = ((-(d * d) / 0.020).exp() * 0.40 + (-d.max(0.0) / 0.16).exp() * 0.22)
                        * l.halo
                        * smoothstep(0.97, 0.70, r);
                    layer = layer.over(Pixel::new(l.glow, glow.min(0.65)));
                }
                if l.rings > 0.01 {
                    layer = layer.over(self.rings(r, core, t, bright_glow));
                }
                if l.sweep > 0.01 {
                    layer = layer.over(self.sweep(u, v, r, core, bright_glow));
                }
                if r < core * 1.02 {
                    layer = layer.over(self.planet(u, v, r, core, t, bright_glow));
                }
                if l.orbit > 0.01 {
                    layer = layer.over(self.lights(u, v, core, bright_glow));
                }
                let at = (py * side + px) * 4;
                out[at..at + 4].copy_from_slice(&layer.straight());
            }
        }
        out
    }

    fn planet(&self, u: f32, v: f32, r: f32, core: f32, t: f32, glow: Rgb) -> Pixel {
        let l = &self.look;
        let rr = r / core;
        let (sx, sy) = (u / core, v / core);
        let z = (1.0 - (rr * rr).min(1.0)).sqrt();
        // The picture is wrapped on the sphere: clouds slide across it as the planet turns.
        let longitude = sx.atan2(z) + self.turn;
        let latitude = sy.clamp(-1.0, 1.0).asin();
        let (a, b) = (longitude * 1.9, latitude * 2.6);
        let warp = noise(a * 0.8 + 7.0, b * 0.8 + t * 0.02) * 1.2;
        // Value noise hovers near its middle; stretching it makes clouds and gaps.
        let density = 0.5 + (fbm(a + warp, b - warp * 0.5) - 0.5) * 1.8;
        // Clouds gather in the lower part and thin out towards the top, as in the reference.
        let edge = 1.0 - l.cover;
        let cloud = smoothstep(edge, edge + 0.30, density + 0.55 * sy);
        // Light from the upper left: a cloud is bright where it thins towards the light.
        let towards_light = 0.5 + (fbm(a + warp - 0.12, b - warp * 0.5 - 0.12) - 0.5) * 1.8;
        let lit = (0.70 + (density - towards_light) * 3.0).clamp(0.0, 1.0);
        let underlit = smoothstep(-0.2, 0.9, sy);
        let shade = mix3(mix3(l.sky_low, [0.50, 0.58, 0.80], 0.5), [1.0, 0.97, 0.90], lit);
        let cloud_colour = mix3(shade, [1.0, 0.90, 0.74], underlit * 0.32 * lit);
        let sky = mix3(l.sky_top, l.sky_low, smoothstep(-0.7, 1.0, sy - sx * 0.15));
        let mut colour = mix3(sky, cloud_colour, cloud * 0.96);

        // A thin film of changing hue along the cloud edges and the rim.
        let on_edge = cloud * (1.0 - cloud) * 4.0;
        let rim = smoothstep(0.86, 1.0, rr);
        let film = hsv(t * 0.04 + a * 0.10 + density * 0.5, 0.55, 1.0);
        for c in 0..3 {
            colour[c] += film[c] * (on_edge * 0.30 + rim * 0.22) * l.shimmer;
        }
        colour = mix3(colour, glow, rim * 0.75);
        let sheen = (-(((sx + 0.40).powi(2) + (sy + 0.45).powi(2)) / 0.05)).exp() * 0.12;
        for channel in &mut colour {
            *channel = (*channel + sheen).min(1.0);
        }
        Pixel::new(colour, smoothstep(1.0, 0.985, rr))
    }

    fn rings(&self, r: f32, core: f32, t: f32, rgb: Rgb) -> Pixel {
        let l = &self.look;
        let mut alpha = 0.0f32;
        for i in 0..3 {
            let phase = (t * l.ring_hz + i as f32 / 3.0).fract();
            let progress = mix(phase, 1.0 - phase, l.ring_in);
            let radius = core * (1.08 + progress * 0.62);
            let width = 0.016 + 0.014 * phase;
            let fade = mix((1.0 - phase).powf(1.5), phase.powf(1.2) * (1.0 - phase * 0.4), l.ring_in);
            let d = (r - radius) / width;
            alpha += l.rings * fade * (-(d * d)).exp();
        }
        Pixel::new(rgb, alpha.min(0.85))
    }

    fn sweep(&self, u: f32, v: f32, r: f32, core: f32, rgb: Rgb) -> Pixel {
        let l = &self.look;
        let angle = v.atan2(u);
        let head = self.spin * 2.2;
        let behind = (head - angle).rem_euclid(TAU) / TAU;
        let d = (r - core * 1.22) / 0.014;
        Pixel::new(rgb, l.sweep * (1.0 - behind).powi(4) * (-(d * d)).exp())
    }

    fn lights(&self, u: f32, v: f32, core: f32, rgb: Rgb) -> Pixel {
        let l = &self.look;
        let mut alpha = 0.0f32;
        for i in 0..3 {
            let angle = self.spin * 1.9 + i as f32 * TAU / 3.0;
            let (sin, cos) = angle.sin_cos();
            let radius = core * 1.36;
            let (dx, dy) = (u - cos * radius, v - sin * radius);
            alpha += (-(dx * dx + dy * dy) / 0.0009).exp();
        }
        Pixel::new(rgb, (l.orbit * alpha).min(1.0))
    }
}

/// A premultiplied pixel, so layers compose with one rule.
#[derive(Clone, Copy)]
struct Pixel {
    rgb: Rgb,
    a: f32,
}

impl Pixel {
    const CLEAR: Pixel = Pixel { rgb: [0.0; 3], a: 0.0 };

    fn new(colour: Rgb, alpha: f32) -> Self {
        let a = alpha.clamp(0.0, 1.0);
        Pixel { rgb: [colour[0] * a, colour[1] * a, colour[2] * a], a }
    }

    /// `top` drawn over `self`.
    fn over(self, top: Pixel) -> Pixel {
        let keep = 1.0 - top.a;
        Pixel {
            rgb: [top.rgb[0] + self.rgb[0] * keep, top.rgb[1] + self.rgb[1] * keep, top.rgb[2] + self.rgb[2] * keep],
            a: top.a + self.a * keep,
        }
    }

    fn straight(self) -> [u8; 4] {
        if self.a <= 0.002 {
            return [0, 0, 0, 0];
        }
        let channel = |c: f32| ((c / self.a).clamp(0.0, 1.0) * 255.0 + 0.5) as u8;
        [channel(self.rgb[0]), channel(self.rgb[1]), channel(self.rgb[2]), (self.a * 255.0 + 0.5) as u8]
    }
}

fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    let t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// Hue (any real number, wrapped), saturation and value to RGB, all 0..1.
fn hsv(h: f32, s: f32, v: f32) -> Rgb {
    let h = h.rem_euclid(1.0) * 6.0;
    let channel = |offset: f32| {
        let k = (offset + h) % 6.0;
        v - v * s.clamp(0.0, 1.0) * k.min(4.0 - k).clamp(0.0, 1.0)
    };
    [channel(5.0), channel(3.0), channel(1.0)]
}

fn hash(x: i32, y: i32) -> f32 {
    let mut h = (x as u32).wrapping_mul(374_761_393) ^ (y as u32).wrapping_mul(668_265_263);
    h = (h ^ (h >> 13)).wrapping_mul(1_274_126_177);
    (h ^ (h >> 16)) as f32 / u32::MAX as f32
}

/// Smooth value noise, 0..1.
fn noise(x: f32, y: f32) -> f32 {
    let (xi, yi) = (x.floor(), y.floor());
    let (fx, fy) = (x - xi, y - yi);
    let (ux, uy) = (fx * fx * (3.0 - 2.0 * fx), fy * fy * (3.0 - 2.0 * fy));
    let (ix, iy) = (xi as i32, yi as i32);
    let top = mix(hash(ix, iy), hash(ix + 1, iy), ux);
    let bottom = mix(hash(ix, iy + 1), hash(ix + 1, iy + 1), ux);
    mix(top, bottom, uy)
}

fn fbm(x: f32, y: f32) -> f32 {
    noise(x, y) * 0.62 + noise(x * 2.03 + 5.2, y * 2.03 + 1.3) * 0.28 + noise(x * 4.1 + 9.7, y * 4.1 + 3.1) * 0.10
}

/// All states side by side on a dark ground, for a visual check
/// (`kvist-voice-screen --orb-preview FILE`). Returns (width, height, RGB).
pub fn contact_sheet(t: f32, side: usize) -> (u32, u32, Vec<u8>) {
    let columns = 4usize;
    let rows = ALL_STATES.len().div_ceil(columns);
    let (width, height) = (columns * side, rows * side);
    let mut rgb = vec![0u8; width * height * 3];
    for (index, state) in ALL_STATES.iter().enumerate() {
        let mut orb = Orb::new(*state);
        orb.level = 0.6;
        // Let the clocks run so the picture is not the very first frame.
        for _ in 0..(t * 30.0) as usize {
            orb.step(1.0 / 30.0);
        }
        let rgba = orb.render(t, side);
        let (ox, oy) = ((index % columns) * side, (index / columns) * side);
        for y in 0..side {
            for x in 0..side {
                let from = (y * side + x) * 4;
                let alpha = rgba[from + 3] as f32 / 255.0;
                let ground = [10.0, 14.0, 26.0];
                let to = ((oy + y) * width + ox + x) * 3;
                for c in 0..3 {
                    rgb[to + c] = (rgba[from + c] as f32 * alpha + ground[c] * (1.0 - alpha)) as u8;
                }
            }
        }
    }
    (width as u32, height as u32, rgb)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SIDE: usize = 96;

    fn pixel(picture: &[u8], x: usize, y: usize) -> [u8; 4] {
        let at = (y * SIDE + x) * 4;
        [picture[at], picture[at + 1], picture[at + 2], picture[at + 3]]
    }

    /// The mean colour of the solid part of a picture.
    fn mean_colour(picture: &[u8]) -> [f32; 3] {
        let mut sum = [0.0f32; 3];
        let mut count = 0.0;
        for p in picture.chunks(4).filter(|p| p[3] > 240) {
            for c in 0..3 {
                sum[c] += p[c] as f32;
            }
            count += 1.0;
        }
        [sum[0] / count, sum[1] / count, sum[2] / count]
    }

    #[test]
    fn every_state_renders_a_sphere_in_the_middle_and_clear_corners() {
        for state in ALL_STATES {
            let orb = Orb::new(state);
            let picture = orb.render(1.7, SIDE);
            assert_eq!(picture.len(), SIDE * SIDE * 4);
            assert!(pixel(&picture, SIDE / 2, SIDE / 2)[3] > 200, "{state:?}: the middle is solid");
            assert_eq!(pixel(&picture, 0, 0)[3], 0, "{state:?}: the corner is clear");
        }
    }

    #[test]
    fn the_states_look_different() {
        let sheet: Vec<Vec<u8>> = ALL_STATES.iter().map(|state| Orb::new(*state).render(2.0, SIDE)).collect();
        for a in 0..sheet.len() {
            for b in a + 1..sheet.len() {
                assert_ne!(sheet[a], sheet[b], "{:?} and {:?}", ALL_STATES[a], ALL_STATES[b]);
            }
        }
    }

    #[test]
    fn the_waiting_orb_is_the_blue_planet_of_the_reference() {
        let [r, g, b] = mean_colour(&Orb::new(OrbState::Waiting).render(2.0, SIDE));
        assert!(b > r + 40.0 && b > g + 15.0, "blue dominates: {r} {g} {b}");
    }

    #[test]
    fn states_keep_the_colour_they_mean() {
        let [r, g, b] = mean_colour(&Orb::new(OrbState::Error).render(2.0, SIDE));
        assert!(r > g + 60.0 && r > b + 60.0, "error is red: {r} {g} {b}");
        let [r, g, b] = mean_colour(&Orb::new(OrbState::Working).render(2.0, SIDE));
        assert!(r > b + 50.0 && g > b, "working is amber: {r} {g} {b}");
        let [r, g, b] = mean_colour(&Orb::new(OrbState::Offline).render(2.0, SIDE));
        assert!((r - g).abs() < 20.0 && (g - b).abs() < 25.0, "offline is grey: {r} {g} {b}");
    }

    #[test]
    fn the_picture_moves_with_time() {
        let orb = Orb::new(OrbState::Thinking);
        assert_ne!(orb.render(1.0, SIDE), orb.render(1.4, SIDE));
    }

    #[test]
    fn the_clouds_keep_their_look_over_an_hour() {
        let mut orb = Orb::new(OrbState::Thinking);
        let before = mean_colour(&orb.render(1.0, SIDE));
        for _ in 0..(3600 * 30) {
            orb.step(1.0 / 30.0);
        }
        let after = mean_colour(&orb.render(1.0, SIDE));
        for c in 0..3 {
            assert!((before[c] - after[c]).abs() < 40.0, "channel {c}: {before:?} -> {after:?}");
        }
    }

    #[test]
    fn a_change_of_state_eases_instead_of_cutting() {
        let mut orb = Orb::new(OrbState::Waiting);
        let before = orb.look.sky_top[0];
        orb.set_state(OrbState::Error);
        orb.step(0.016);
        let target = look(OrbState::Error).sky_top[0];
        assert!((orb.look.sky_top[0] - before).abs() < (target - before).abs() * 0.2);
        for _ in 0..120 {
            orb.step(0.016);
        }
        assert!((orb.look.sky_top[0] - target).abs() < 0.01);
    }

    #[test]
    fn a_new_state_pops_and_the_pop_fades() {
        let mut orb = Orb::new(OrbState::Waiting);
        orb.set_state(OrbState::Listening);
        assert!(orb.pop > 0.99);
        for _ in 0..100 {
            orb.step(0.016);
        }
        assert!(orb.pop < 0.01);
        // The same state again is not a change.
        orb.set_state(OrbState::Listening);
        assert!(orb.pop < 0.01);
    }

    #[test]
    fn voice_level_swells_the_speaking_sphere() {
        let mut quiet = Orb::new(OrbState::Speaking);
        let mut loud = Orb::new(OrbState::Speaking);
        quiet.level = 0.0;
        loud.level = 1.0;
        let solid = |picture: &[u8]| picture.chunks(4).filter(|p| p[3] > 200).count();
        assert!(solid(&loud.render(1.0, SIDE)) > solid(&quiet.render(1.0, SIDE)));
    }

    #[test]
    fn colour_conversion_matches_the_primaries() {
        let close = |a: Rgb, b: Rgb| a.iter().zip(b).all(|(x, y)| (x - y).abs() < 1e-4);
        assert!(close(hsv(0.0, 1.0, 1.0), [1.0, 0.0, 0.0]));
        assert!(close(hsv(1.0 / 3.0, 1.0, 1.0), [0.0, 1.0, 0.0]));
        assert!(close(hsv(2.0 / 3.0, 1.0, 1.0), [0.0, 0.0, 1.0]));
        assert!(close(hsv(1.5, 0.0, 0.5), [0.5, 0.5, 0.5]));
        assert!(close(rgb(0xFF8000), [1.0, 128.0 / 255.0, 0.0]));
    }

    #[test]
    fn the_contact_sheet_holds_every_state() {
        let (width, height, rgb) = contact_sheet(1.0, 32);
        assert_eq!((width, height), (128, 96));
        assert_eq!(rgb.len(), 128 * 96 * 3);
    }
}
