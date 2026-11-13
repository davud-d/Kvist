// The orb of the voice screen: a ball of glass hanging in the photograph, with a
// light inside. The glass bends the photograph behind it the way a real glass
// ball does (upside down, squeezed towards the rim), so the orb belongs to the
// scene it hangs in and is as sharp as the photograph itself. What Kvist is doing
// is the light: its colour, how it moves, and what leaves the ball.
//
// It is drawn per pixel into an RGBA picture every frame, at the size it is shown
// at, so nothing is scaled up. A state is a set of numbers (a `Look`) and the shown
// look eases towards it: the light changes colour and pace instead of cutting.
//
// What each state shows:
//   Loading      a pale ember, an arc running round the ball
//   Waiting      a warm ember breathing slowly: a lantern left on
//   Listening    cyan; a ring of light inside moves with the person's voice, and a
//                thin ring outside empties while they are silent: when it is empty
//                the phrase is over, so they can see how long they may think
//   Transcribing teal, a quick arc: the words are being written down
//   Thinking     violet into pink, ribbons of light turning inside
//   Working      amber, one slow ribbon; twigs grow from the ball to the modules
//   Narrating    lilac, the ring inside moves with the voice
//   Speaking     warm white, the ring inside moves with the voice, rings leave
//   Approval     gold, a slow wide pulse: waiting for a yes or a no
//   Error        red, a shiver
//   Offline      no light at all: plain grey glass

use std::f32::consts::TAU;
use std::sync::OnceLock;
use std::thread;

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

/// The ball's radius in the picture's units: the picture is 2 high, so the ball is half as
/// tall as its picture and the rest is room for the glow, the rings and the twigs.
pub const BALL: f32 = 0.5;
/// How far around its centre the ball sees the photograph, in the picture's units.
pub const SCENERY_REACH: f32 = 1.5;
/// The colour glass takes on where it is thick.
const GLASS: Rgb = [0.035, 0.065, 0.125];
/// The thin outer ring that shows the pause, as a multiple of the ball's radius.
const TIMER_RADIUS: f32 = 1.17;
/// Points a twig's curve is measured at.
const TWIG_STEPS: usize = 28;

/// A picture that can be read between its pixels.
struct Plane {
    width: usize,
    height: usize,
    rgb: Vec<f32>,
}

impl Plane {
    fn at(&self, x: usize, y: usize) -> Rgb {
        let i = (y * self.width + x) * 3;
        [self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]]
    }

    /// The colour at (u, v), each -1..=1 across the picture; outside it the edge repeats.
    fn sample(&self, u: f32, v: f32) -> Rgb {
        let x = ((u * 0.5 + 0.5) * self.width as f32 - 0.5).clamp(0.0, (self.width - 1) as f32);
        let y = ((v * 0.5 + 0.5) * self.height as f32 - 0.5).clamp(0.0, (self.height - 1) as f32);
        let (x0, y0) = (x as usize, y as usize);
        let (x1, y1) = ((x0 + 1).min(self.width - 1), (y0 + 1).min(self.height - 1));
        let (fx, fy) = (x - x0 as f32, y - y0 as f32);
        mix3(mix3(self.at(x0, y0), self.at(x1, y0), fx), mix3(self.at(x0, y1), self.at(x1, y1), fx), fy)
    }

    /// The same picture with every `by` x `by` pixels averaged into one.
    fn smaller(&self, by: usize) -> Plane {
        let (width, height) = (self.width.div_ceil(by), self.height.div_ceil(by));
        let mut rgb = Vec::with_capacity(width * height * 3);
        for y in 0..height {
            for x in 0..width {
                let (mut sum, mut count) = ([0.0f32; 3], 0.0);
                for sy in y * by..((y + 1) * by).min(self.height) {
                    for sx in x * by..((x + 1) * by).min(self.width) {
                        let colour = self.at(sx, sy);
                        for c in 0..3 {
                            sum[c] += colour[c];
                        }
                        count += 1.0;
                    }
                }
                rgb.extend(sum.map(|channel| channel / count));
            }
        }
        Plane { width, height, rgb }
    }
}

/// How much smaller the scenery's coarse copy is.
const COARSE: usize = 6;

/// The patch of the photograph around the orb, which its glass bends. The ball squeezes
/// the photograph more and more towards its rim; read pixel by pixel there, fine detail
/// (trees, stars) would turn into grit, so the rim is read from an averaged copy.
pub struct Scenery {
    fine: Plane,
    coarse: Plane,
    /// What a picture of the orb keeps from frame to frame, made when the first one is drawn.
    still: OnceLock<Still>,
}

impl Scenery {
    /// `bytes` are RGB rows; the patch reaches `SCENERY_REACH` from the orb's centre each way.
    pub fn new(width: usize, height: usize, bytes: &[u8]) -> Self {
        assert_eq!(bytes.len(), width * height * 3, "the scenery is RGB");
        let fine = Plane { width, height, rgb: bytes.iter().map(|&b| b as f32 / 255.0).collect() };
        let coarse = fine.smaller(COARSE);
        Scenery { fine, coarse, still: OnceLock::new() }
    }

    /// One colour everywhere: for a screen without a photograph, and for tests.
    pub fn flat(colour: [u8; 3]) -> Self {
        Scenery::new(1, 1, &colour)
    }

    /// The colour at (u, v), each -1..=1 across the patch, as sharp as it is.
    fn sample(&self, u: f32, v: f32) -> Rgb {
        self.fine.sample(u, v)
    }

    /// The colour at (u, v) with `blur` 0..=1 of it read from the averaged copy.
    fn sample_blurred(&self, u: f32, v: f32, blur: f32) -> Rgb {
        if blur <= 0.0 {
            return self.fine.sample(u, v);
        }
        mix3(self.fine.sample(u, v), self.coarse.sample(u, v), blur.min(1.0))
    }
}

/// A twig from the ball to a module that came out of it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Twig {
    /// Where it ends, in the picture's units (centre 0,0; the picture is 2 high).
    pub to: (f32, f32),
    pub colour: [u8; 3],
    /// How much of its length is drawn, 0..=1.
    pub grown: f32,
    /// Light running along it while its module works, 0..=1.
    pub flow: f32,
}

/// Everything the renderer needs to know about how the orb looks and moves.
#[derive(Clone, Copy, Debug)]
struct Look {
    /// The light inside, the colour its ribbons lean to, and the colour of rim and glow.
    light: Rgb,
    light2: Rgb,
    glow: Rgb,
    /// The ember in the middle, the turning ribbons, and the ring that moves with a voice.
    ember: f32,
    swirl: f32,
    wave: f32,
    /// How fast the ribbons turn (radians a second).
    speed: f32,
    /// Breathing: size change and its rate in hertz.
    breath: f32,
    breath_hz: f32,
    /// Glow outside the ball.
    halo: f32,
    /// Rings leaving the ball: strength and rate.
    rings: f32,
    ring_hz: f32,
    /// A running arc round the ball, and the ring that empties during a pause.
    sweep: f32,
    timer: f32,
    /// A shiver of the whole picture.
    shake: f32,
    /// How strongly the loudness of a voice moves the light.
    voice: f32,
    /// How much colour the glass lets through (0 for a dead orb: grey glass).
    colour: f32,
}

fn rgb(hex: u32) -> Rgb {
    [(hex >> 16 & 0xFF) as f32 / 255.0, (hex >> 8 & 0xFF) as f32 / 255.0, (hex & 0xFF) as f32 / 255.0]
}

fn look(state: OrbState) -> Look {
    let base = Look {
        light: rgb(0xFFE4B8), light2: rgb(0xFFC98A), glow: rgb(0xCFE0FF), ember: 0.50, swirl: 0.0, wave: 0.0,
        speed: 0.35, breath: 0.05, breath_hz: 0.20, halo: 0.34, rings: 0.0, ring_hz: 0.4, sweep: 0.0, timer: 0.0,
        shake: 0.0, voice: 0.0, colour: 1.0,
    };
    match state {
        OrbState::Loading => Look { light: rgb(0xA9B8DC), light2: rgb(0xC9D4EE), glow: rgb(0xB5C4E6), ember: 0.26, halo: 0.22, sweep: 0.9, breath: 0.02, ..base },
        OrbState::Waiting => base,
        OrbState::Listening => Look { light: rgb(0x5FDDFF), light2: rgb(0xB8F4FF), glow: rgb(0x8FE9FF), ember: 0.34, wave: 1.0, halo: 0.62, timer: 1.0, breath: 0.02, breath_hz: 0.5, voice: 0.55, ..base },
        OrbState::Transcribing => Look { light: rgb(0x62E6C4), light2: rgb(0xC6FFE9), glow: rgb(0x8FF3D6), ember: 0.40, swirl: 0.45, speed: 1.5, halo: 0.48, sweep: 1.0, breath: 0.02, breath_hz: 1.2, ..base },
        OrbState::Thinking => Look { light: rgb(0xA98BFF), light2: rgb(0xFF9FDD), glow: rgb(0xC8B0FF), ember: 0.28, swirl: 1.0, speed: 1.05, halo: 0.56, breath: 0.03, breath_hz: 0.4, ..base },
        OrbState::Working => Look { light: rgb(0xFFAE52), light2: rgb(0xFFE18F), glow: rgb(0xFFC37A), ember: 0.52, swirl: 0.40, speed: 0.6, halo: 0.56, breath: 0.03, breath_hz: 0.6, ..base },
        OrbState::Narrating => Look { light: rgb(0xD3A4FF), light2: rgb(0xFFC4EC), glow: rgb(0xE2C2FF), ember: 0.42, wave: 0.65, halo: 0.52, rings: 0.22, ring_hz: 0.5, breath: 0.02, voice: 0.7, ..base },
        OrbState::Speaking => Look { light: rgb(0xFFF0D4), light2: rgb(0xA8E6FF), glow: rgb(0xE9F1FF), ember: 0.50, wave: 1.0, halo: 0.74, rings: 0.50, ring_hz: 0.7, breath: 0.02, voice: 1.0, ..base },
        OrbState::Approval => Look { light: rgb(0xFFCF5C), light2: rgb(0xFFF0B0), glow: rgb(0xFFD877), ember: 0.62, halo: 0.84, rings: 0.45, ring_hz: 0.35, breath: 0.08, breath_hz: 0.45, ..base },
        OrbState::Error => Look { light: rgb(0xFF5548), light2: rgb(0xFFA08F), glow: rgb(0xFF7466), ember: 0.72, halo: 0.70, shake: 1.0, breath: 0.03, breath_hz: 3.0, ..base },
        OrbState::Offline => Look { light: rgb(0x8C8C98), light2: rgb(0x8C8C98), glow: rgb(0x9A9AA6), ember: 0.0, speed: 0.0, halo: 0.05, breath: 0.0, colour: 0.0, ..base },
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
        light: mix3(a.light, b.light, k), light2: mix3(a.light2, b.light2, k), glow: mix3(a.glow, b.glow, k),
        ember: mix(a.ember, b.ember, k), swirl: mix(a.swirl, b.swirl, k), wave: mix(a.wave, b.wave, k),
        speed: mix(a.speed, b.speed, k), breath: mix(a.breath, b.breath, k), breath_hz: mix(a.breath_hz, b.breath_hz, k),
        halo: mix(a.halo, b.halo, k), rings: mix(a.rings, b.rings, k), ring_hz: mix(a.ring_hz, b.ring_hz, k),
        sweep: mix(a.sweep, b.sweep, k), timer: mix(a.timer, b.timer, k), shake: mix(a.shake, b.shake, k),
        voice: mix(a.voice, b.voice, k), colour: mix(a.colour, b.colour, k),
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
    /// Loudness of the voice heard or spoken now, 0..1 (set by the screen).
    pub level: f32,
    /// How much of the pause that ends the person's phrase has passed, 0..1 (set by the screen).
    pub pause: f32,
    /// Turn of the ribbons and of the running arc, advanced by `step` at the look's own
    /// pace, so a faster state does not make the picture jump.
    turn: f32,
    spin: f32,
    /// Seconds since the state last changed.
    settled: f32,
}

/// After this long in a state the look has stopped changing towards it.
const SETTLE_SECONDS: f32 = 1.5;

impl Orb {
    pub fn new(state: OrbState) -> Self {
        Orb { state, look: look(state), pop: 0.0, level: 0.0, pause: 0.0, turn: 0.0, spin: 0.0, settled: SETTLE_SECONDS }
    }

    pub fn state(&self) -> OrbState {
        self.state
    }

    pub fn set_state(&mut self, state: OrbState) {
        if state != self.state {
            self.state = state;
            self.pop = 1.0;
            self.settled = 0.0;
        }
    }

    /// Nothing in the picture moves faster than a slow breath: it waits (or is dead), has
    /// arrived at that look, and no voice moves its light. Such a picture need not be
    /// drawn thirty times a second, and waiting is what the orb does most of the day.
    pub fn calm(&self) -> bool {
        matches!(self.state, OrbState::Waiting | OrbState::Offline) && self.settled >= SETTLE_SECONDS && self.level < 0.02
    }

    /// Moves the look towards the state's look and advances the turning clocks.
    pub fn step(&mut self, dt: f32) {
        let k = 1.0 - (-dt * FOLLOW_RATE).exp();
        self.look = mix_look(self.look, look(self.state), k);
        self.pop *= (-dt * POP_RATE).exp();
        self.settled += dt;
        self.turn += dt * self.look.speed;
        self.spin += dt * (0.4 + self.look.speed * 1.2);
    }

    /// An RGBA picture (straight alpha), `width` x `height` pixels with the ball in the
    /// middle, half as tall as the picture; `scenery` is the photograph round it, and
    /// `twigs` reach from the ball to the modules. Time `t` drives only the periodic
    /// motions (breathing, rings, the voice's ring).
    pub fn render(&self, t: f32, width: usize, height: usize, scenery: &Scenery, twigs: &[Twig]) -> Vec<u8> {
        let l = self.look;
        let px = 2.0 / height as f32;
        // Three rings leave one after another, widening and fading on the way.
        let rings = [0.0f32, 1.0, 2.0].map(|i| {
            let phase = (t * l.ring_hz + i / 3.0).fract();
            (BALL * (1.08 + phase * 0.62), 0.010 + 0.012 * phase, l.rings * (1.0 - phase).powf(1.5))
        });
        let level = self.level.clamp(0.0, 1.0);
        let ribbons = if l.swirl > 0.01 {
            (0..ANGLES)
                .map(|index| {
                    let theta = direction(index);
                    [0.0f32, 1.0, 2.0].map(|k| {
                        let path = 0.42 + 0.19 * wave(2.0 * theta + k * 2.1 + self.turn * (0.9 + 0.35 * k));
                        let along = 0.55 + 0.45 * wave(theta * (1.0 + k) - self.turn * 1.7 + k * 2.3);
                        (path, along * l.swirl * 0.75)
                    })
                })
                .collect()
        } else {
            Vec::new()
        };
        // A ring that a voice shakes: still and thin in silence, wide and restless when loud.
        // The second, fainter one turns the other way.
        let voice_rings = if l.wave > 0.01 {
            (0..ANGLES)
                .map(|index| {
                    let theta = direction(index);
                    [(0.0f32, 1.0f32), (1.0, -1.0)].map(|(k, turn)| {
                        let shape = 0.060 * wave(2.0 * theta * turn + t * (1.9 + k) + k * 1.7)
                            + 0.040 * wave(3.0 * theta * turn - t * (2.7 - k))
                            + 0.022 * wave(5.0 * theta + t * 4.9 + k * 0.9);
                        0.57 + 0.04 * level * l.voice + shape * (0.08 + 0.80 * level)
                    })
                })
                .collect()
        } else {
            Vec::new()
        };
        let frame = Frame {
            l,
            t,
            rings,
            ribbons,
            voice_rings,
            breath: 1.0 + l.breath * (t * l.breath_hz * TAU).sin(),
            flash: self.pop,
            level,
            pause: self.pause.clamp(0.0, 1.0),
            spin: self.spin,
            px,
            width,
        };
        // The glass and its surroundings do not change from frame to frame: only the light is drawn anew.
        let kept = scenery.still.get_or_init(|| Still::new(scenery, width, height));
        let made;
        let still = if (kept.width, kept.height) == (width, height) {
            kept
        } else {
            made = Still::new(scenery, width, height);
            &made
        };
        // An error's shiver moves the whole picture, by whole pixels.
        let shiver = |a: f32, b: f32| (l.shake * 0.012 * (t * a).sin() * (t * b).cos() / px).round() as isize;
        let shift = (shiver(37.0, 11.0), shiver(13.0, 29.0));
        let mut out = vec![0u8; width * height * 4];
        let workers = thread::available_parallelism().map_or(1, |n| n.get()).min(4);
        let band = height.div_ceil(workers).max(1);
        thread::scope(|scope| {
            for (index, rows) in out.chunks_mut(band * width * 4).enumerate() {
                let frame = &frame;
                scope.spawn(move || {
                    for (row, line) in rows.chunks_mut(width * 4).enumerate() {
                        let y = (index * band + row) as isize - shift.1;
                        if y < 0 || y >= height as isize {
                            continue;
                        }
                        for (column, pixel) in line.chunks_mut(4).enumerate() {
                            let x = column as isize - shift.0;
                            if x >= 0 && x < width as isize {
                                pixel.copy_from_slice(&frame.pixel(still, x as usize, y as usize));
                            }
                        }
                    }
                });
            }
        });
        for twig in twigs {
            frame.twig(&mut out, width, height, twig);
        }
        out
    }
}

/// Nothing of the orb is drawn further than this from the centre, in the picture's units.
const REACH: f32 = 0.98;
/// Directions round the ball that the ribbons and the voice's rings are worked out for:
/// more than the ball has pixels round its rim, so no step shows.
const ANGLES: usize = 2048;

/// The direction that step `index` of `ANGLES` stands for, in radians.
fn direction(index: usize) -> f32 {
    (index as f32 + 0.5) / ANGLES as f32 * TAU - TAU / 2.0
}
/// The shadow the ball casts on the photograph.
const SHADOW: Rgb = [0.0, 0.01, 0.04];

/// A pixel of the picture: how far it is from the centre, and the shadow and the glow
/// (at full strength) that fall on it.
#[derive(Clone, Copy)]
struct Around {
    r: f32,
    shadow: f32,
    glow: f32,
}

/// A pixel of the ball: the glass itself, without the light of a state.
#[derive(Clone, Copy)]
struct Glass {
    /// The photograph as the glass bends it, and how much the glass darkens it here.
    scene: Rgb,
    body: f32,
    /// Where the pixel is on the ball: distance from the middle (0..1), and its direction
    /// as one of `ANGLES` steps round the ball.
    rr: f32,
    angle: u16,
    /// How much of a light inside reaches the eye from here.
    depth: f32,
    /// How much of the state's colour the rim takes here, and its thin closing line.
    rim: f32,
    line: f32,
    /// What the sky's reflection leaves of the darkness here, per channel.
    shine: Rgb,
    /// The light the ball gathers on its far side.
    gathered: f32,
    /// How much of the pixel the ball covers (less than 1 on its edge).
    edge: f32,
}

/// Everything about a picture of one size that is the same in every frame: the glass with
/// the photograph in it, the reflections, the shadow, the shape of the glow. Working it
/// out takes most of the arithmetic of a frame, and the orb is drawn thirty times a second
/// for as long as the screen is open, so it is worked out once.
struct Still {
    width: usize,
    height: usize,
    around: Vec<Around>,
    /// The ball's pixels: the corner and the size of the square that holds them.
    left: usize,
    top: usize,
    side: usize,
    glass: Vec<Glass>,
}

impl Still {
    fn new(scenery: &Scenery, width: usize, height: usize) -> Self {
        let px = 2.0 / height as f32;
        let at = |x: usize, y: usize| ((x as f32 + 0.5 - width as f32 * 0.5) * px, (y as f32 + 0.5) * px - 1.0);
        let mut around = Vec::with_capacity(width * height);
        for y in 0..height {
            for x in 0..width {
                let (u, v) = at(x, y);
                let r = (u * u + v * v).sqrt();
                // Everything reaches zero before the picture's edge, so no rectangle shows.
                let inside = smoothstep(REACH, 0.72, r);
                // A soft shadow, a little below the ball, lifts it off a bright part of the photograph.
                let low = ((u * u + (v - 0.05) * (v - 0.05)).sqrt() - BALL) / BALL;
                let d = r - BALL;
                around.push(Around {
                    r,
                    shadow: 0.34 * bell(low * low / 0.10) * inside,
                    glow: (bell(d * d / 0.012) * 0.42 + bell(d.max(0.0) / 0.20) * 0.20) * inside,
                });
            }
        }
        let side = (((BALL + px) * 2.0 / px).ceil() as usize + 2).min(width).min(height);
        let (left, top) = ((width - side) / 2, (height - side) / 2);
        let mut glass = Vec::with_capacity(side * side);
        for y in top..top + side {
            for x in left..left + side {
                let (u, v) = at(x, y);
                glass.push(Still::glass(scenery, u, v, px));
            }
        }
        Still { width, height, around, left, top, side, glass }
    }

    fn glass(scenery: &Scenery, u: f32, v: f32, px: f32) -> Glass {
        let r = (u * u + v * v).sqrt();
        let (sx, sy) = (u / BALL, v / BALL);
        let rr = (r / BALL).min(1.0);
        // The glass turns the photograph upside down and squeezes it towards the rim.
        let bend = 0.34 + 0.66 * rr * rr;
        // How much a point of the rim faces the light: the sky above and to the left, and
        // the place opposite, where the ball lets the light out again.
        let facing = (-sx * 0.60 - sy * 0.80) / rr.max(0.001);
        let lit = 0.22 + 0.78 * facing.max(0.0).powi(2) + 0.35 * (-facing).max(0.0).powi(3);
        let line = (rr - 0.978) / 0.010;
        // The sky's reflection at the upper left with a small hard glint in it.
        let window = bell((sx + 0.40).powi(2) / 0.050 + (sy + 0.50).powi(2) / 0.020) * 0.34;
        let glint = bell((sx + 0.47).powi(2) / 0.0040 + (sy + 0.57).powi(2) / 0.0016) * 0.80;
        Glass {
            scene: scenery.sample_blurred(-sx * bend, -sy * bend, smoothstep(0.30, 0.80, rr)),
            // Glass is darkest a little inside its rim, where it is seen at a slant.
            body: 1.0 - 0.42 * smoothstep(0.50, 0.95, rr),
            rr,
            angle: (((sy.atan2(sx) + TAU / 2.0) / TAU * ANGLES as f32) as usize % ANGLES) as u16,
            // A light inside dims towards the rim: there is less glass in front of the eye there.
            depth: 0.30 + 0.70 * (1.0 - rr * rr).sqrt(),
            rim: smoothstep(0.80, 1.0, rr).powi(2) * 0.42 * (0.55 + 0.45 * lit),
            line: bell(line * line) * 0.62 * lit,
            shine: [0.93f32, 0.96, 1.0].map(|sky| (1.0 - sky * window) * (1.0 - glint)),
            gathered: bell(((sx - 0.36).powi(2) + (sy - 0.46).powi(2)) / 0.050),
            edge: ((BALL - r) / px + 0.5).clamp(0.0, 1.0),
        }
    }

    fn glass_at(&self, x: usize, y: usize) -> Option<&Glass> {
        let (column, row) = (x.checked_sub(self.left)?, y.checked_sub(self.top)?);
        (column < self.side && row < self.side).then(|| &self.glass[row * self.side + column])
    }
}

/// One frame's numbers, shared by every pixel of it.
struct Frame {
    l: Look,
    t: f32,
    /// The rings leaving the ball now: radius, width and strength of each.
    rings: [(f32, f32, f32); 3],
    /// The ribbons of light inside, for each direction round the ball: how far from the
    /// middle each runs there and how bright it is. Empty when the look has none. They
    /// depend on the direction and the moment only, so a frame works them out once for
    /// every direction instead of once for every pixel.
    ribbons: Vec<[(f32, f32); 3]>,
    /// The two rings a voice shakes, the same way: how far from the middle each runs.
    voice_rings: Vec<[f32; 2]>,
    breath: f32,
    /// The flash of a state that has just begun, fading from 1.
    flash: f32,
    level: f32,
    pause: f32,
    spin: f32,
    /// One pixel in the picture's units, and the picture's width in pixels.
    px: f32,
    width: usize,
}

impl Frame {
    fn pixel(&self, still: &Still, x: usize, y: usize) -> [u8; 4] {
        let place = still.around[y * still.width + x];
        // Nothing of the orb reaches this far: most of a wide picture is empty.
        if place.r >= REACH {
            return [0; 4];
        }
        let mut layer = Pixel::CLEAR;
        if place.r > BALL * 0.95 {
            layer = self.around(place, x, y);
        }
        if let Some(glass) = still.glass_at(x, y)
            && glass.edge > 0.0
        {
            let colour = self.ball(glass);
            // Most of the ball covers its pixels whole: nothing under them shows.
            if glass.edge >= 1.0 {
                return [(colour[0] * 255.0 + 0.5) as u8, (colour[1] * 255.0 + 0.5) as u8, (colour[2] * 255.0 + 0.5) as u8, 255];
            }
            layer = layer.over(Pixel::new(colour, glass.edge));
        }
        layer.straight()
    }

    /// What surrounds the ball: its shadow on the photograph, its glow, and the rings.
    fn around(&self, place: Around, x: usize, y: usize) -> Pixel {
        let l = &self.l;
        let halo = l.halo * (1.0 + 0.8 * self.flash);
        let mut layer = Pixel::new(SHADOW, place.shadow).over(Pixel::new(l.glow, (place.glow * halo).min(0.70)));
        let bright = mix3(l.glow, [1.0; 3], 0.35);
        if l.rings > 0.01 {
            layer = layer.over(self.rings(place.r, bright));
        }
        if l.timer > 0.01 || l.sweep > 0.01 {
            let (u, v) = ((x as f32 + 0.5 - self.width as f32 * 0.5) * self.px, (y as f32 + 0.5) * self.px - 1.0);
            if l.timer > 0.01 {
                layer = layer.over(self.timer(u, v, place.r, bright));
            }
            if l.sweep > 0.01 {
                layer = layer.over(self.sweep(u, v, place.r, bright));
            }
        }
        layer
    }

    /// The colour of a pixel of the ball: the glass with this frame's light in it.
    fn ball(&self, glass: &Glass) -> Rgb {
        let l = &self.l;
        let (rr, depth) = (glass.rr, glass.depth);
        // A glass ball gathers the scene: what it shows is richer and brighter than what is behind it.
        let mut scene = glass.scene;
        let grey = scene[0] * 0.30 + scene[1] * 0.50 + scene[2] * 0.20;
        scene = mix3([grey; 3], scene, 1.45 * l.colour);
        for channel in &mut scene {
            *channel = (*channel * 1.30).clamp(0.0, 1.0);
        }
        let tinted = mix3(scene, GLASS, 0.10);
        let mut colour = [tinted[0] * glass.body, tinted[1] * glass.body, tinted[2] * glass.body];

        let loud = self.level * l.voice;
        let ember = l.ember * (1.0 + 0.5 * self.flash);
        // A spark with a soft haze round it, not a fog: the scene in the glass stays clear.
        let haze = 0.26 * (0.90 + 0.10 * self.breath) + 0.10 * loud;
        screen(&mut colour, l.light, bell(rr * rr / (haze * haze)) * ember * (0.55 + 0.8 * loud) * depth);
        let spark = 0.075 * (0.85 + 0.15 * self.breath) + 0.03 * loud;
        screen(&mut colour, mix3(l.light, [1.0; 3], 0.6), bell(rr * rr / (spark * spark)) * (ember * 1.5).min(1.0));

        if let Some(ribbons) = self.ribbons.get(glass.angle as usize) {
            for (i, (path, strength)) in ribbons.iter().enumerate() {
                let d = (rr - path) / 0.075;
                screen(&mut colour, mix3(l.light, l.light2, i as f32 * 0.5), bell(d * d) * strength * depth);
            }
        }
        if let Some(rings) = self.voice_rings.get(glass.angle as usize) {
            for (i, (path, weight)) in rings.iter().zip([1.0f32, 0.40]).enumerate() {
                let d = (rr - path) / (0.018 + 0.022 * self.level);
                let ring = bell(d * d) * l.wave * weight * (0.60 + 0.40 * self.level);
                screen(&mut colour, mix3(l.light, l.light2, i as f32), ring * depth);
                // The ring's own glow, wide and faint.
                screen(&mut colour, l.light, bell(d * d / 14.0) * l.wave * weight * 0.16 * depth);
            }
        }

        // The rim catches the light of the state, and a thin bright line closes the ball.
        screen(&mut colour, l.glow, glass.rim);
        screen(&mut colour, mix3(l.glow, [1.0; 3], 0.6), glass.line);
        // The light the ball gathers on its far side, as glass does, and the sky's reflection.
        screen(&mut colour, l.light, glass.gathered * (0.08 + 0.34 * l.ember) * l.colour.max(0.3));
        for (channel, keep) in colour.iter_mut().zip(glass.shine) {
            *channel = (1.0 - (1.0 - *channel) * keep).clamp(0.0, 1.0);
        }
        colour
    }

    /// Rings that leave the ball with a voice or a question.
    fn rings(&self, r: f32, colour: Rgb) -> Pixel {
        let mut alpha = 0.0f32;
        for (radius, width, strength) in self.rings {
            let d = (r - radius) / width;
            alpha += strength * bell(d * d);
        }
        Pixel::new(colour, alpha.min(0.85))
    }

    /// The ring that shows the pause: whole while the person speaks, emptying clockwise from
    /// the top while they are silent. When nothing is left of it, the phrase is over.
    fn timer(&self, u: f32, v: f32, r: f32, colour: Rgb) -> Pixel {
        let d = (r - BALL * TIMER_RADIUS) / (self.px * 1.6).max(0.004);
        let on_ring = bell(d * d);
        if on_ring < 0.004 {
            return Pixel::CLEAR;
        }
        // 0 at the top, growing clockwise to 1.
        let around = (u.atan2(-v) / TAU).rem_euclid(1.0);
        let edge = (self.px * 2.0 / (BALL * TIMER_RADIUS * TAU)).max(0.002);
        let left = smoothstep(self.pause - edge, self.pause + edge, around);
        let lit = if self.pause <= 0.0 { 1.0 } else { left };
        Pixel::new(colour, self.l.timer * on_ring * (0.13 + 0.77 * lit))
    }

    fn sweep(&self, u: f32, v: f32, r: f32, colour: Rgb) -> Pixel {
        let d = (r - BALL * TIMER_RADIUS) / (self.px * 1.6).max(0.004);
        let on_ring = bell(d * d);
        if on_ring < 0.004 {
            return Pixel::CLEAR;
        }
        let behind = (self.spin * 2.2 - v.atan2(u)).rem_euclid(TAU) / TAU;
        Pixel::new(colour, self.l.sweep * (1.0 - behind).powi(4) * on_ring)
    }

    /// Draws one twig over the finished picture: a thin bright line with a soft glow, a bead
    /// where it meets its module, and lights that run along it while the module works.
    fn twig(&self, out: &mut [u8], width: usize, height: usize, twig: &Twig) {
        if twig.grown <= 0.01 {
            return;
        }
        let (tx, ty) = twig.to;
        let reach = (tx * tx + ty * ty).sqrt();
        if reach <= BALL * 1.1 {
            return;
        }
        // From the rim, leaving straight out, bending a little on the way like a growing thing.
        let (dx, dy) = (tx / reach, ty / reach);
        let start = (dx * BALL * 1.02, dy * BALL * 1.02);
        let side = if tx * ty >= 0.0 { 1.0 } else { -1.0 };
        let bow = (reach - BALL) * 0.22 * side;
        let control = ((start.0 + tx) * 0.5 - dy * bow, (start.1 + ty) * 0.5 + dx * bow);
        let grown = twig.grown.clamp(0.0, 1.0);
        let points: Vec<(f32, f32)> = (0..=TWIG_STEPS)
            .map(|i| {
                let s = grown * i as f32 / TWIG_STEPS as f32;
                let (a, b, c) = ((1.0 - s) * (1.0 - s), 2.0 * s * (1.0 - s), s * s);
                (a * start.0 + b * control.0 + c * tx, a * start.1 + b * control.1 + c * ty)
            })
            .collect();
        let tip = points[TWIG_STEPS];
        let colour = [twig.colour[0] as f32 / 255.0, twig.colour[1] as f32 / 255.0, twig.colour[2] as f32 / 255.0];
        let bright = mix3(colour, [1.0; 3], 0.55);
        let line = self.px * 1.1;
        let soft = self.px * 5.0;
        let bead = self.px * 3.6;
        let margin = soft * 2.5;
        let to_px = |u: f32, v: f32| ((u / self.px + width as f32 * 0.5), (v + 1.0) / self.px);
        let (mut left, mut right, mut top, mut bottom) = (f32::MAX, f32::MIN, f32::MAX, f32::MIN);
        for &(x, y) in &points {
            left = left.min(x - margin);
            right = right.max(x + margin);
            top = top.min(y - margin);
            bottom = bottom.max(y + margin);
        }
        let (x0, y0) = to_px(left, top);
        let (x1, y1) = to_px(right, bottom);
        let (x0, y0) = (x0.floor().max(0.0) as usize, y0.floor().max(0.0) as usize);
        let (x1, y1) = ((x1.ceil().max(0.0) as usize).min(width), (y1.ceil().max(0.0) as usize).min(height));
        for py in y0..y1 {
            let v = (py as f32 + 0.5) * self.px - 1.0;
            for px in x0..x1 {
                let u = (px as f32 + 0.5 - width as f32 * 0.5) * self.px;
                let (distance, along) = nearest(&points, u, v);
                let d_line = distance / line;
                let d_soft = distance / soft;
                // Lights leave the ball one after another and fade as they arrive.
                let mut lights = 0.0;
                if twig.flow > 0.01 {
                    for k in 0..2 {
                        let at = (self.t * 0.75 + k as f32 * 0.5).fract();
                        let gap = (along - at) / 0.07;
                        lights += bell(gap * gap) * twig.flow;
                    }
                }
                let core = bell(d_line * d_line) * (0.62 + 0.38 * lights.min(1.0));
                let glow = bell(d_soft * d_soft) * (0.16 + 0.34 * lights.min(1.0));
                let tip_distance = ((u - tip.0).powi(2) + (v - tip.1).powi(2)).sqrt();
                let dot = smoothstep(bead + self.px, bead - self.px, tip_distance) * smoothstep(0.85, 1.0, grown);
                let alpha = (core + glow).max(dot).min(1.0);
                if alpha < 0.004 {
                    continue;
                }
                let tone = mix3(colour, bright, (core + dot + lights * 0.5).min(1.0));
                let at = (py * width + px) * 4;
                let under = Pixel::from_straight(&out[at..at + 4]);
                out[at..at + 4].copy_from_slice(&under.over(Pixel::new(tone, alpha)).straight());
            }
        }
    }
}

/// e^-x for x >= 0, read from a table. A frame takes this some millions of times, thirty
/// times a second; with the library's exponent the orb kept two processor cores busy.
fn bell(x: f32) -> f32 {
    const PER_UNIT: f32 = 64.0;
    const REACH: f32 = 14.0;
    static TABLE: OnceLock<Vec<f32>> = OnceLock::new();
    let table = TABLE.get_or_init(|| (0..=(REACH * PER_UNIT) as usize + 1).map(|i| (-(i as f32) / PER_UNIT).exp()).collect());
    // What is not a number draws nothing either.
    if x.is_nan() || x >= REACH {
        return 0.0;
    }
    let at = x.max(0.0) * PER_UNIT;
    let i = at as usize;
    table[i] + (table[i + 1] - table[i]) * (at - i as f32)
}

/// The sine of x, read from a table for the same reason.
fn wave(x: f32) -> f32 {
    const STEPS: usize = 2048;
    static TABLE: OnceLock<Vec<f32>> = OnceLock::new();
    let table = TABLE.get_or_init(|| (0..=STEPS).map(|i| (i as f32 / STEPS as f32 * TAU).sin()).collect());
    let turns = x * (1.0 / TAU);
    let at = (turns - turns.floor()) * STEPS as f32;
    let i = (at as usize).min(STEPS - 1);
    table[i] + (table[i + 1] - table[i]) * (at - i as f32)
}

/// The distance from (u, v) to a line through `points`, and how far along it (0..=1) the nearest place is.
fn nearest(points: &[(f32, f32)], u: f32, v: f32) -> (f32, f32) {
    let mut best = (f32::MAX, 0.0);
    let last = (points.len() - 1) as f32;
    for (i, pair) in points.windows(2).enumerate() {
        let ((ax, ay), (bx, by)) = (pair[0], pair[1]);
        let (ex, ey) = (bx - ax, by - ay);
        let length = ex * ex + ey * ey;
        let s = if length > 0.0 { (((u - ax) * ex + (v - ay) * ey) / length).clamp(0.0, 1.0) } else { 0.0 };
        let (cx, cy) = (ax + ex * s - u, ay + ey * s - v);
        let distance = cx * cx + cy * cy;
        if distance < best.0 {
            best = (distance, (i as f32 + s) / last);
        }
    }
    (best.0.sqrt(), best.1)
}

/// Adds light the way light adds: brighter, never past white, and without flattening what is under it.
fn screen(colour: &mut Rgb, light: Rgb, amount: f32) {
    let amount = amount.clamp(0.0, 1.0);
    for c in 0..3 {
        colour[c] = 1.0 - (1.0 - colour[c]) * (1.0 - light[c] * amount);
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

    fn from_straight(bytes: &[u8]) -> Self {
        Pixel::new([bytes[0] as f32 / 255.0, bytes[1] as f32 / 255.0, bytes[2] as f32 / 255.0], bytes[3] as f32 / 255.0)
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

/// All states side by side over the scenery, for a visual check
/// (`kvist-voice-screen --orb-preview FILE`). Returns (width, height, RGB).
pub fn contact_sheet(t: f32, side: usize, scenery: &Scenery) -> (u32, u32, Vec<u8>) {
    let columns = 4usize;
    let rows = ALL_STATES.len().div_ceil(columns);
    let (width, height) = (columns * side, rows * side);
    let mut rgb = vec![0u8; width * height * 3];
    for (index, state) in ALL_STATES.iter().enumerate() {
        let mut orb = Orb::new(*state);
        orb.level = 0.6;
        orb.pause = 0.35;
        // Let the clocks run so the picture is not the very first frame.
        for _ in 0..(t * 30.0) as usize {
            orb.step(1.0 / 30.0);
        }
        let twigs: &[Twig] = if *state == OrbState::Working {
            &[
                Twig { to: (0.86, -0.30), colour: [0xFF, 0xC3, 0x7A], grown: 1.0, flow: 1.0 },
                Twig { to: (-0.90, 0.05), colour: [0x9F, 0xD1, 0x8F], grown: 1.0, flow: 0.0 },
            ]
        } else {
            &[]
        };
        let rgba = orb.render(t, side, side, scenery, twigs);
        let (ox, oy) = ((index % columns) * side, (index / columns) * side);
        for y in 0..side {
            for x in 0..side {
                let from = (y * side + x) * 4;
                let alpha = rgba[from + 3] as f32 / 255.0;
                let (u, v) = ((x as f32 + 0.5) / side as f32 * 2.0 - 1.0, (y as f32 + 0.5) / side as f32 * 2.0 - 1.0);
                let ground = scenery.sample(u / SCENERY_REACH, v / SCENERY_REACH);
                let to = ((oy + y) * width + ox + x) * 3;
                for c in 0..3 {
                    rgb[to + c] = (rgba[from + c] as f32 * alpha + ground[c] * 255.0 * (1.0 - alpha)) as u8;
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

    fn grey() -> Scenery {
        Scenery::flat([60, 60, 60])
    }

    fn picture(orb: &Orb, t: f32) -> Vec<u8> {
        orb.render(t, SIDE, SIDE, &grey(), &[])
    }

    fn pixel(picture: &[u8], width: usize, x: usize, y: usize) -> [u8; 4] {
        let at = (y * width + x) * 4;
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

    /// What the light of a state adds to the dead, grey ball.
    fn light_of(state: OrbState) -> [f32; 3] {
        let lit = mean_colour(&picture(&Orb::new(state), 2.0));
        let dead = mean_colour(&picture(&Orb::new(OrbState::Offline), 2.0));
        [lit[0] - dead[0], lit[1] - dead[1], lit[2] - dead[2]]
    }

    #[test]
    fn every_state_renders_a_ball_in_the_middle_and_clear_corners() {
        for state in ALL_STATES {
            let picture = picture(&Orb::new(state), 1.7);
            assert_eq!(picture.len(), SIDE * SIDE * 4);
            assert!(pixel(&picture, SIDE, SIDE / 2, SIDE / 2)[3] > 200, "{state:?}: the middle is solid");
            assert_eq!(pixel(&picture, SIDE, 0, 0)[3], 0, "{state:?}: the corner is clear");
            assert_eq!(pixel(&picture, SIDE, SIDE / 2, 0)[3], 0, "{state:?}: nothing touches the top edge");
        }
    }

    #[test]
    fn the_states_look_different() {
        let sheet: Vec<Vec<u8>> = ALL_STATES.iter().map(|state| picture(&Orb::new(*state), 2.0)).collect();
        for a in 0..sheet.len() {
            for b in a + 1..sheet.len() {
                assert_ne!(sheet[a], sheet[b], "{:?} and {:?}", ALL_STATES[a], ALL_STATES[b]);
            }
        }
    }

    #[test]
    fn the_ball_is_round_in_a_wide_picture() {
        let (width, height) = (SIDE * 2 + 7, SIDE);
        let picture = Orb::new(OrbState::Offline).render(1.0, width, height, &grey(), &[]);
        let across = (0..width).filter(|&x| pixel(&picture, width, x, height / 2)[3] > 250).count();
        let down = (0..height).filter(|&y| pixel(&picture, width, width / 2, y)[3] > 250).count();
        assert!(across.abs_diff(down) <= 1, "{across} across, {down} down");
        assert!(down.abs_diff(height / 2) <= 4, "the ball is half as tall as its picture: {down}");
    }

    #[test]
    fn the_glass_shows_the_photograph_upside_down() {
        // Red above the orb, blue below it.
        let mut rows = Vec::new();
        for y in 0..8 {
            for _ in 0..8 {
                rows.extend_from_slice(if y < 4 { &[200u8, 20, 20] } else { &[20u8, 20, 200] });
            }
        }
        let scenery = Scenery::new(8, 8, &rows);
        let picture = Orb::new(OrbState::Offline).render(1.0, SIDE, SIDE, &Scenery::new(8, 8, &rows), &[]);
        drop(scenery);
        // Offline glass is grey, so compare brightness: red is the brighter grey here.
        let upper = pixel(&picture, SIDE, SIDE / 2, SIDE / 2 - SIDE / 10);
        let lower = pixel(&picture, SIDE, SIDE / 2, SIDE / 2 + SIDE / 10);
        assert!(lower[0] > upper[0] + 8, "what is above shows below: {upper:?} {lower:?}");
    }

    #[test]
    fn a_lit_ball_keeps_the_colours_of_the_photograph() {
        let picture = Orb::new(OrbState::Waiting).render(1.0, SIDE, SIDE, &Scenery::flat([20, 40, 160]), &[]);
        let edge = pixel(&picture, SIDE, SIDE / 2 + SIDE / 6, SIDE / 2 + SIDE / 12);
        assert!(edge[2] > edge[0] && edge[2] > edge[1], "blue behind stays blue in the glass: {edge:?}");
    }

    #[test]
    fn states_keep_the_colour_they_mean() {
        let [r, g, b] = light_of(OrbState::Error);
        // The light is a spark, a haze and the rim, so over the whole ball it shifts the mean by little.
        assert!(r > g + 6.0 && r > b + 6.0, "error is red: {r} {g} {b}");
        let [r, g, b] = light_of(OrbState::Working);
        assert!(r > b + 6.0 && g > b, "working is amber: {r} {g} {b}");
        let [r, g, b] = light_of(OrbState::Listening);
        assert!(b > r + 4.0 && g > r, "listening is cyan: {r} {g} {b}");
        let [r, g, b] = mean_colour(&picture(&Orb::new(OrbState::Offline), 2.0));
        assert!((r - g).abs() < 12.0 && (g - b).abs() < 22.0, "offline is grey: {r} {g} {b}");
    }

    #[test]
    fn the_picture_moves_with_time() {
        let mut orb = Orb::new(OrbState::Thinking);
        let before = picture(&orb, 1.0);
        for _ in 0..12 {
            orb.step(1.0 / 30.0);
        }
        assert_ne!(before, picture(&orb, 1.4));
    }

    #[test]
    fn the_light_keeps_its_look_over_an_hour() {
        let mut orb = Orb::new(OrbState::Thinking);
        let before = mean_colour(&picture(&orb, 1.0));
        for _ in 0..(3600 * 30) {
            orb.step(1.0 / 30.0);
        }
        let after = mean_colour(&picture(&orb, 1.0));
        for c in 0..3 {
            assert!((before[c] - after[c]).abs() < 40.0, "channel {c}: {before:?} -> {after:?}");
        }
    }

    #[test]
    fn a_change_of_state_eases_instead_of_cutting() {
        let mut orb = Orb::new(OrbState::Waiting);
        let before = orb.look.light[2];
        orb.set_state(OrbState::Error);
        orb.step(0.016);
        let target = look(OrbState::Error).light[2];
        assert!((orb.look.light[2] - before).abs() < (target - before).abs() * 0.2);
        for _ in 0..120 {
            orb.step(0.016);
        }
        assert!((orb.look.light[2] - target).abs() < 0.01);
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
    fn a_louder_voice_makes_more_light() {
        let brightness = |level: f32| {
            let mut orb = Orb::new(OrbState::Speaking);
            orb.level = level;
            mean_colour(&picture(&orb, 1.0)).iter().sum::<f32>()
        };
        assert!(brightness(1.0) > brightness(0.0) + 6.0);
    }

    /// Lit pixels on the pause ring.
    fn ring_pixels(pause: f32) -> usize {
        const BIG: usize = 240;
        let mut orb = Orb::new(OrbState::Listening);
        orb.pause = pause;
        let picture = orb.render(1.0, BIG, BIG, &grey(), &[]);
        let core = BALL * BIG as f32 / 2.0;
        let mut lit = 0;
        for y in 0..BIG {
            for x in 0..BIG {
                let (dx, dy) = (x as f32 + 0.5 - BIG as f32 / 2.0, y as f32 + 0.5 - BIG as f32 / 2.0);
                let on_ring = ((dx * dx + dy * dy).sqrt() - core * TIMER_RADIUS).abs() < 1.0;
                if on_ring && pixel(&picture, BIG, x, y)[3] > 150 {
                    lit += 1;
                }
            }
        }
        lit
    }

    #[test]
    fn the_outer_ring_empties_as_the_pause_runs_out() {
        let (whole, half, gone) = (ring_pixels(0.0), ring_pixels(0.5), ring_pixels(1.0));
        assert!(whole > 300, "a whole ring while the person speaks: {whole}");
        assert!(half > whole / 3 && half < whole * 2 / 3, "half of it half-way: {half} of {whole}");
        assert!(gone < whole / 10, "nothing when the phrase is over: {gone}");
    }

    #[test]
    fn a_twig_grows_from_the_ball_to_its_module() {
        const BIG: usize = 240;
        let orb = Orb::new(OrbState::Working);
        let twig = |grown| Twig { to: (0.9, 0.0), colour: [40, 250, 40], grown, flow: 0.0 };
        let end = |picture: &[u8]| pixel(picture, BIG, (BIG as f32 * (0.5 + 0.9 / 2.0)) as usize, BIG / 2);
        let middle = |picture: &[u8]| {
            (0..BIG).map(|y| pixel(picture, BIG, (BIG as f32 * (0.5 + 0.7 / 2.0)) as usize, y)).filter(|p| p[1] > p[0] + 60 && p[3] > 120).count()
        };
        let bare = orb.render(1.0, BIG, BIG, &grey(), &[]);
        let grown = orb.render(1.0, BIG, BIG, &grey(), &[twig(1.0)]);
        let half = orb.render(1.0, BIG, BIG, &grey(), &[twig(0.4)]);
        assert_eq!(middle(&bare), 0);
        assert!(middle(&grown) >= 1, "the line passes between ball and module");
        assert!(end(&grown)[1] > 200 && end(&grown)[3] > 200, "a green bead at its end: {:?}", end(&grown));
        assert_eq!(middle(&half), 0, "a twig that has grown less than half-way has not come this far");
        assert_eq!(orb.render(1.0, BIG, BIG, &grey(), &[twig(0.0)]), bare);
    }

    #[test]
    fn the_nearest_place_on_a_line_is_found_with_how_far_along_it_is() {
        let line = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)];
        let (distance, along) = nearest(&line, 1.5, 0.5);
        assert!((distance - 0.5).abs() < 1e-5 && (along - 0.75).abs() < 1e-5);
        let (distance, along) = nearest(&line, -1.0, 0.0);
        assert!((distance - 1.0).abs() < 1e-5 && along == 0.0);
    }

    #[test]
    fn a_waiting_orb_is_calm_once_it_has_settled_and_nobody_speaks() {
        let mut orb = Orb::new(OrbState::Thinking);
        assert!(!orb.calm(), "ribbons turn while it thinks");
        orb.set_state(OrbState::Waiting);
        orb.step(0.5);
        assert!(!orb.calm(), "the look is still changing");
        orb.step(SETTLE_SECONDS);
        assert!(orb.calm());
        orb.level = 0.3;
        assert!(!orb.calm(), "a voice moves the light");
    }

    #[test]
    fn a_shiver_moves_the_whole_picture_and_a_picture_of_another_size_is_still_right() {
        let scenery = grey();
        let orb = Orb::new(OrbState::Offline);
        // The first size is the one the scenery keeps; another size must not use what was kept.
        let small = orb.render(1.0, SIDE, SIDE, &scenery, &[]);
        let large = orb.render(1.0, SIDE * 2, SIDE * 2, &scenery, &[]);
        assert_eq!(large, orb.render(1.0, SIDE * 2, SIDE * 2, &grey(), &[]));
        assert_eq!(small, orb.render(1.0, SIDE, SIDE, &scenery, &[]));
        let solid = |picture: &[u8]| picture.chunks(4).filter(|p| p[3] == 255).count();
        let shaken = Orb::new(OrbState::Error).render(0.31, SIDE * 2, SIDE * 2, &scenery, &[]);
        assert_eq!(solid(&shaken), solid(&large), "the same ball, somewhere else");
    }

    #[test]
    fn the_tables_agree_with_the_functions_they_stand_for() {
        for i in 0..2000 {
            let x = i as f32 * 0.01;
            assert!((bell(x) - (-x).exp()).abs() < 1e-4, "e^-{x}");
            assert!((wave(x - 10.0) - (x - 10.0).sin()).abs() < 1e-4, "sin {x}");
        }
        assert_eq!(bell(1000.0), 0.0);
        assert_eq!(bell(f32::NAN), 0.0);
        assert_eq!(bell(-1.0), 1.0, "below zero it stays at one");
    }

    #[test]
    fn scenery_is_sampled_between_its_pixels_and_clamped_outside() {
        let scenery = Scenery::new(2, 1, &[0, 0, 0, 255, 255, 255]);
        assert!((scenery.sample(0.0, 0.0)[0] - 0.5).abs() < 0.01);
        assert_eq!(scenery.sample(-5.0, 9.0), [0.0; 3]);
        assert_eq!(scenery.sample(5.0, -9.0), [1.0; 3]);
        assert!((rgb(0xFF8000)[1] - 128.0 / 255.0).abs() < 1e-5);
    }

    #[test]
    fn the_contact_sheet_holds_every_state() {
        let (width, height, rgb) = contact_sheet(1.0, 32, &grey());
        assert_eq!((width, height), (128, 96));
        assert_eq!(rgb.len(), 128 * 96 * 3);
    }
}
