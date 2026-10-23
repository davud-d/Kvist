// Turns a description of what should be on screen (a `Scene`) into kitty
// uploads and placements. Heavy work (fitting the photograph, blurring it)
// runs on a worker thread; cards and thumbnails are small and are rendered
// here on demand, then cached by size and position.

use std::collections::HashMap;
use std::io;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::{Duration, Instant};

use ratatui::layout::Rect;

use crate::backdrop::{self, Canvas, Glass, LEVELS, WebFrame};
use crate::kitty::{Gpu, Placement, Z_BACKDROP, Z_CARD, Z_DIALOG, Z_THUMB, Z_TOP, Z_WATER};
use crate::layout::Grid;
use crate::math;
use crate::view::FormulaSpot;
use crate::theme::GLASS_TINT;
use crate::video::{self, Video};
use crate::water;

pub const SLOT_BACKDROP: u32 = 1;
pub const SLOT_LEFT: u32 = 2;
pub const SLOT_BROWSER_CARD: u32 = 3;
pub const SLOT_HELPERS: u32 = 4;
pub const SLOT_READING: u32 = 5;
pub const SLOT_INPUT: u32 = 6;
pub const SLOT_DIALOG: u32 = 7;
pub const SLOT_MENU: u32 = 10;
pub const SLOT_DIFF: u32 = 11;
const SLOT_WATER: u32 = 12;
const SLOT_THUMB: u32 = 8;
/// The first of the slots of the pictures attached to a request.
pub const SLOT_ATTACH: u32 = 20;
const SLOT_POPUP: u32 = 9;
/// The first of the slots of the formulas on screen.
const SLOT_FORMULA: u32 = 100;
/// Most formulas drawn at once; the rest of a very long answer shows as blank rows until scrolled.
const MAX_FORMULAS: usize = 24;
/// Above every card and below the dialog, so a menu or dialog covers a formula as it does the text.
const Z_FORMULA: i32 = Z_CARD + 1_000_000;
const FORMULA_INK: [u8; 3] = [0xDD, 0xE4, 0xEE];
const FORMULA_INK_DIM: [u8; 3] = [0x6E, 0x7A, 0x92];
const CARD_SLOTS: [u32; 8] =
    [SLOT_LEFT, SLOT_BROWSER_CARD, SLOT_HELPERS, SLOT_READING, SLOT_INPUT, SLOT_DIALOG, SLOT_MENU, SLOT_DIFF];
/// A cached picture unused for this many frames is freed.
const KEEP_FRAMES: u64 = 180;

pub struct Worker {
    requests: Sender<(u32, u32)>,
    pub results: Receiver<Arc<Canvas>>,
    pub errors: Receiver<String>,
    stop: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
}

impl Worker {
    pub fn spawn() -> Self {
        let (requests, inbox) = mpsc::channel::<(u32, u32)>();
        let (outbox, results) = mpsc::sync_channel(1);
        let (errors_out, errors) = mpsc::channel();
        let stop = Arc::new(AtomicBool::new(false));
        let stopped = stop.clone();
        let thread = thread::spawn(move || {
            let mut video = match Video::start() {
                Ok(video) => Some(video),
                Err(error) => {
                    let _ = errors_out.send(error.to_string());
                    None
                }
            };
            let mut photo = backdrop::decode_photo();
            let mut first = true;
            let mut size = (0, 0);
            let mut dirty = false;
            let mut rise_until = Instant::now();
            let mut due = Instant::now();
            while !stopped.load(Ordering::Relaxed) {
                match inbox.recv_timeout(due.saturating_duration_since(Instant::now())) {
                    Ok(newer) => { size = newer; dirty = true; }
                    Err(mpsc::RecvTimeoutError::Timeout) => {}
                    Err(mpsc::RecvTimeoutError::Disconnected) => break,
                }
                due = Instant::now() + video::FRAME_TIME;
                while let Ok(newer) = inbox.try_recv() {
                    size = newer;
                    dirty = true;
                }
                if Instant::now() < rise_until { continue; }
                if let Some(frame) = video.as_ref().and_then(Video::take) {
                    match frame {
                        Ok(frame) => { photo = frame; dirty = true; }
                        Err(error) => {
                            let _ = errors_out.send(error);
                            video = None;
                        }
                    }
                }
                if !dirty || size.0 == 0 || size.1 == 0 { continue; }
                let canvas = backdrop::build(&photo, size.0, size.1, first);
                match outbox.try_send(Arc::new(canvas)) {
                    Ok(()) => {
                        if first { rise_until = Instant::now() + Duration::from_millis(800); }
                        first = false;
                        dirty = false;
                    }
                    Err(mpsc::TrySendError::Full(_)) => {}
                    Err(mpsc::TrySendError::Disconnected(_)) => break,
                }
            }
        });
        Worker { requests, results, errors, stop, thread: Some(thread) }
    }

    pub fn request(&self, width: u32, height: u32) -> io::Result<()> {
        self.requests
            .send((width, height))
            .map_err(|_| io::Error::other("the picture worker stopped"))
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() { let _ = thread.join(); }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Backdrop {
    Black,
    Rise(usize),
    Photo,
    Sharp,
    /// 1..=LEVELS, more blurred and darker as it grows.
    Level(usize),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum CardStyle {
    Side,
    Input,
    Reading,
    Dialog,
    /// The slash menu; 0 is clear glass and 2 fully frosted, so it blurs in as it opens.
    Menu(u8),
    /// Any card while a dialog or the browser is open over it.
    Dimmed,
}

pub struct CardSpec {
    pub slot: u32,
    pub style: CardStyle,
    pub rect: Rect,
    /// Pixel offset from `rect` while the card slides.
    pub dx: f32,
    pub dy: f32,
}

/// A picture develops over this many steps, each `DEVELOP_STEP_MS` long.
pub const DEVELOP_STEPS: u8 = 8;
pub const DEVELOP_STEP_MS: u128 = 260;

pub struct ThumbSpec {
    pub rect: Rect,
    pub dx: f32,
    pub dy: f32,
    /// The picture to show; `None` is the agent's browser.
    pub frame: Option<Arc<WebFrame>>,
    /// Darkened, while a dialog is open over it.
    pub dim: bool,
    /// How far the picture has developed, `0..=DEVELOP_STEPS`.
    pub develop: u8,
}

pub struct PopupSpec {
    /// Pixel rectangle (may be fractional mid-animation).
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
    /// 0 = most blurred, 2 = sharp.
    pub sharpness: u8,
    /// The picture to show; `None` is the agent's browser.
    pub frame: Option<Arc<WebFrame>>,
}

/// The pool poured by a selection, and the clock its caustics drift by.
pub struct WaterSpec {
    pub shape: water::Shape,
    pub clock: f32,
}

pub struct Scene {
    pub backdrop: Backdrop,
    pub cards: Vec<CardSpec>,
    pub thumb: Option<ThumbSpec>,
    /// The attached pictures, each in its own slot from `SLOT_ATTACH`.
    pub attachments: Vec<(u32, ThumbSpec)>,
    pub popup: Option<PopupSpec>,
    pub water: Option<WaterSpec>,
    /// Display formulas to draw over the conversation; `dim` while a dialog is open over it.
    pub formulas: Vec<FormulaSpot>,
    pub formulas_dim: bool,
}

type BackdropIds = HashMap<Backdrop, u32>;

/// The current browser frame's three uploads.
struct WebIds {
    version: u64,
    sharp: u32,
    soft: u32,
    softer: u32,
}

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct CardKey {
    style: CardStyle,
    rect: (u16, u16, u16, u16),
}

pub struct Pictures {
    pub gpu: Gpu,
    canvas: Option<Arc<Canvas>>,
    web: Option<Arc<WebFrame>>,
    backdrop: Option<BackdropIds>,
    web_ids: Option<WebIds>,
    /// Uploads of an older browser frame, freed once nothing shows them.
    stale_web: Vec<u32>,
    stale_canvas: Vec<u32>,
    cards: HashMap<CardKey, (u32, u64)>,
    /// The picture of the pool now shown, by what it was drawn from.
    water: Option<(u64, u32)>,
    /// (frame version, width, height, dimmed) -> picture.
    thumbs: HashMap<(u64, u32, u32, bool, u8), (u32, u64)>,
    /// Formula pictures by (tex, cols, rows, em, dimmed); `None` is a formula that would not draw.
    formulas: HashMap<(String, u16, u16, u32, bool), (Option<u32>, u64)>,
    formula_slots: usize,
    frame: u64,
}

impl Pictures {
    pub fn new(gpu: Gpu) -> Self {
        Pictures {
            gpu,
            canvas: None,
            web: None,
            backdrop: None,
            web_ids: None,
            stale_web: Vec::new(),
            stale_canvas: Vec::new(),
            cards: HashMap::new(),
            water: None,
            thumbs: HashMap::new(),
            formulas: HashMap::new(),
            formula_slots: 0,
            frame: 0,
        }
    }

    pub fn accept(&mut self, canvas: Arc<Canvas>) {
        self.forget_canvas_pictures();
        self.canvas = Some(canvas);
    }

    /// A new frame of the agent's browser, or `None` once it closed.
    pub fn set_web(&mut self, frame: Option<Arc<WebFrame>>) {
        if frame.is_none()
            && let Some(old) = self.web_ids.take()
        {
            self.stale_web.extend([old.sharp, old.soft, old.softer]);
        }
        self.web = frame;
    }

    /// Width / height of the browser frame, when there is one.
    pub fn web_aspect(&self) -> Option<f32> {
        let raw = &self.web.as_ref()?.raw;
        Some(raw.width() as f32 / raw.height().max(1) as f32)
    }

    pub fn has_canvas_for(&self, grid: &Grid) -> bool {
        self.canvas
            .as_ref()
            .is_some_and(|c| c.width == grid.cols as u32 * grid.cw && c.height == grid.rows as u32 * grid.ch)
    }

    /// OSC 11 for the current backdrop's border colour.
    pub fn padding_colour(&self) -> Option<String> {
        let [r, g, b] = self.canvas.as_ref()?.edge;
        Some(format!("\x1b]11;rgb:{r:02x}/{g:02x}/{b:02x}\x1b\\"))
    }

    pub fn rise_frames(&self) -> usize {
        self.canvas.as_ref().map_or(0, |c| c.rise.len())
    }

    /// The terminal drops every picture on resize, so all ids become stale.
    pub fn reset(&mut self) {
        self.gpu.reset();
        self.canvas = None;
        self.backdrop = None;
        self.web_ids = None;
        self.stale_web.clear();
        self.stale_canvas.clear();
        self.cards.clear();
        self.water = None;
        self.thumbs.clear();
        self.formulas.clear();
        self.formula_slots = 0;
    }

    fn forget_canvas_pictures(&mut self) {
        if let Some(ids) = self.backdrop.take() {
            self.stale_canvas.extend(ids.into_values());
        }
        for (_, (id, _)) in self.cards.drain() {
            self.stale_canvas.push(id);
        }
    }

    /// The rising-from-black frames are only needed once.
    pub fn drop_rise(&mut self) {
        let Some(ids) = self.backdrop.as_mut() else { return };
        ids.retain(|kind, id| {
            if matches!(kind, Backdrop::Rise(_)) { self.stale_canvas.push(*id); false } else { true }
        });
    }

    pub fn show(&mut self, grid: &Grid, scene: &Scene) -> io::Result<()> {
        self.frame += 1;
        if !self.has_canvas_for(grid) {
            return Ok(());
        }
        let Some(canvas) = self.canvas.clone() else { return Ok(()) };
        let backdrop = self.backdrop_id(&canvas, scene.backdrop)?;
        self.gpu.set(SLOT_BACKDROP, backdrop.map(|image| Placement::at_px(image, 0.0, 0.0, grid.cw, grid.ch, Some((grid.cols, grid.rows)), Z_BACKDROP)));

        for slot in CARD_SLOTS {
            let spec = scene.cards.iter().find(|c| c.slot == slot);
            let placement = match spec {
                Some(spec) => self.card_placement(&canvas, grid, spec)?,
                None => None,
            };
            self.gpu.set(slot, placement);
        }
        let thumb = match &scene.thumb {
            Some(spec) => self.thumb_placement(grid, spec)?,
            None => None,
        };
        self.gpu.set(SLOT_THUMB, thumb);
        for slot in SLOT_ATTACH..SLOT_ATTACH + crate::attach::MAX_IMAGES as u32 {
            let placement = match scene.attachments.iter().find(|(s, _)| *s == slot) {
                Some((_, spec)) => self.thumb_placement(grid, spec)?,
                None => None,
            };
            self.gpu.set(slot, placement);
        }
        self.show_formulas(grid, scene)?;
        let popup = match &scene.popup {
            Some(spec) => self.popup_placement(grid, spec)?,
            None => None,
        };
        self.gpu.set(SLOT_POPUP, popup);
        let water = match &scene.water {
            Some(spec) => self.water_placement(grid, spec)?,
            None => {
                self.stale_canvas.extend(self.water.take().map(|(_, id)| id));
                None
            }
        };
        self.gpu.set(SLOT_WATER, water);
        for id in std::mem::take(&mut self.stale_web) {
            self.gpu.free(id);
        }
        // Replace placements before freeing previous frames to avoid flashing between uploads.
        for id in std::mem::take(&mut self.stale_canvas) {
            self.gpu.free(id);
        }
        self.sweep();
        Ok(())
    }

    fn backdrop_id(&mut self, canvas: &Canvas, backdrop: Backdrop) -> io::Result<Option<u32>> {
        let image = match backdrop {
            Backdrop::Black => return Ok(None),
            Backdrop::Rise(i) => canvas.rise.get(i).unwrap_or(&canvas.photo),
            Backdrop::Photo => &canvas.photo,
            Backdrop::Sharp | Backdrop::Level(0) => &canvas.sharp,
            Backdrop::Level(k) => &canvas.levels[k.min(LEVELS) - 1],
        };
        let ids = self.backdrop.get_or_insert_with(HashMap::new);
        if let Some(id) = ids.get(&backdrop) { return Ok(Some(*id)); }
        let id = self.gpu.upload_rgb(image.width(), image.height(), image.as_raw())?;
        ids.insert(backdrop, id);
        Ok(Some(id))
    }

    fn card_placement(&mut self, canvas: &Canvas, grid: &Grid, spec: &CardSpec) -> io::Result<Option<Placement>> {
        if spec.rect.width == 0 || spec.rect.height == 0 {
            return Ok(None);
        }
        let key = CardKey { style: spec.style, rect: (spec.rect.x, spec.rect.y, spec.rect.width, spec.rect.height) };
        let (x, y, w, h) = grid.px(spec.rect);
        let image = match self.cards.get_mut(&key) {
            Some(entry) => {
                entry.1 = self.frame;
                entry.0
            }
            None => {
                let source = match spec.style {
                    CardStyle::Dialog | CardStyle::Dimmed => &canvas.levels[LEVELS - 1],
                    CardStyle::Menu(0) => &canvas.sharp,
                    CardStyle::Menu(1) => &canvas.levels[0],
                    _ => &canvas.frost,
                };
                let scale = (source.width() as f32 / canvas.width as f32, source.height() as f32 / canvas.height as f32);
                let card = backdrop::card(source, x as i64, y as i64, w as u32, h as u32, &glass(spec.style, grid.ch), scale);
                let id = self.gpu.upload_rgba(w as u32, h as u32, card.as_raw())?;
                self.cards.insert(key, (id, self.frame));
                id
            }
        };
        let z = if matches!(spec.style, CardStyle::Dialog | CardStyle::Menu(_)) { Z_DIALOG } else { Z_CARD };
        Ok(clipped(image, x + spec.dx, y + spec.dy, w, h, grid, z))
    }

    /// The pool over the cells it covers; drawn again only when it changed.
    fn water_placement(&mut self, grid: &Grid, spec: &WaterSpec) -> io::Result<Option<Placement>> {
        let Some(area) = water::bounds(&spec.shape, grid.cols, grid.rows) else { return Ok(None) };
        let key = water::signature(&spec.shape, spec.clock);
        let image = match self.water {
            Some((shown, id)) if shown == key => id,
            _ => {
                let picture = water::render(&spec.shape, area, grid.cw, grid.ch, spec.clock);
                let id = self.gpu.upload_rgba(picture.width(), picture.height(), picture.as_raw())?;
                self.stale_canvas.extend(self.water.replace((key, id)).map(|(_, old)| old));
                id
            }
        };
        let (x, y, _, _) = grid.px(area);
        Ok(Some(Placement::at_px(image, x, y, grid.cw, grid.ch, Some((area.width, area.height)), Z_WATER)))
    }

    fn thumb_placement(&mut self, grid: &Grid, spec: &ThumbSpec) -> io::Result<Option<Placement>> {
        let Some(web) = spec.frame.clone().or_else(|| self.web.clone()) else { return Ok(None) };
        let (x, y, w, h) = grid.px(spec.rect);
        let key = (web.version, w as u32, h as u32, spec.dim, spec.develop);
        let image = match self.thumbs.get_mut(&key) {
            Some(entry) => {
                entry.1 = self.frame;
                entry.0
            }
            None => {
                let light = if spec.dim { 0.3 } else { 1.0 };
                let thumb = if spec.develop >= DEVELOP_STEPS {
                    backdrop::thumbnail(&web.raw, w as u32, h as u32, light)
                } else {
                    backdrop::developing_thumbnail(&web.raw, w as u32, h as u32, light, spec.develop, DEVELOP_STEPS)
                };
                let id = self.gpu.upload_rgba(w as u32, h as u32, thumb.as_raw())?;
                self.thumbs.insert(key, (id, self.frame));
                id
            }
        };
        Ok(clipped(image, x + spec.dx, y + spec.dy, w, h, grid, Z_THUMB))
    }

    fn show_formulas(&mut self, grid: &Grid, scene: &Scene) -> io::Result<()> {
        let shown = scene.formulas.len().min(MAX_FORMULAS);
        for (i, spot) in scene.formulas.iter().take(shown).enumerate() {
            let placement = self.formula_placement(grid, spot, scene.formulas_dim)?;
            self.gpu.set(SLOT_FORMULA + i as u32, placement);
        }
        for slot in shown..self.formula_slots {
            self.gpu.set(SLOT_FORMULA + slot as u32, None);
        }
        self.formula_slots = shown;
        Ok(())
    }

    fn formula_placement(&mut self, grid: &Grid, spot: &FormulaSpot, dim: bool) -> io::Result<Option<Placement>> {
        let key = (spot.tex.clone(), spot.fit.cols, spot.fit.rows, spot.fit.em.to_bits(), dim);
        let image = match self.formulas.get_mut(&key) {
            Some(entry) => {
                entry.1 = self.frame;
                entry.0
            }
            None => {
                let ink = if dim { FORMULA_INK_DIM } else { FORMULA_INK };
                let id = match math::paint(&spot.tex, spot.fit, (grid.cw, grid.ch), ink) {
                    Some(picture) => Some(self.gpu.upload_rgba(picture.width(), picture.height(), picture.as_raw())?),
                    None => None,
                };
                self.formulas.insert(key, (id, self.frame));
                id
            }
        };
        let Some(image) = image else { return Ok(None) };
        let (cw, ch) = (grid.cw, grid.ch);
        let mut placement = Placement::at_px(image, (spot.col as u32 * cw) as f32, (spot.row as u32 * ch) as f32, cw, ch, None, Z_FORMULA);
        if spot.hidden > 0 || spot.shown < spot.fit.rows {
            placement.crop = Some((0, spot.hidden as u32 * ch, spot.fit.cols as u32 * cw, spot.shown as u32 * ch));
        }
        Ok(Some(placement))
    }

    fn popup_placement(&mut self, grid: &Grid, spec: &PopupSpec) -> io::Result<Option<Placement>> {
        let Some(web) = spec.frame.clone().or_else(|| self.web.clone()) else { return Ok(None) };
        if self.web_ids.as_ref().is_none_or(|ids| ids.version != web.version) {
            let mut upload = |image: &image::RgbaImage| self.gpu.upload_rgba(image.width(), image.height(), image.as_raw());
            let fresh = WebIds { version: web.version, sharp: upload(&web.sharp)?, soft: upload(&web.soft)?, softer: upload(&web.softer)? };
            // The slot moves to the new picture before the old one is freed.
            if let Some(old) = self.web_ids.replace(fresh) {
                self.stale_web.extend([old.sharp, old.soft, old.softer]);
            }
        }
        let Some(ids) = self.web_ids.as_ref() else { return Ok(None) };
        let image = match spec.sharpness {
            0 => ids.softer,
            1 => ids.soft,
            _ => ids.sharp,
        };
        let cols = (spec.w / grid.cw as f32).round().max(1.0) as u16;
        let rows = (spec.h / grid.ch as f32).round().max(1.0) as u16;
        Ok(Some(Placement::at_px(image, spec.x, spec.y, grid.cw, grid.ch, Some((cols, rows)), Z_TOP)))
    }

    fn sweep(&mut self) {
        let frame = self.frame;
        let mut stale = Vec::new();
        self.cards.retain(|_, (id, used)| {
            let keep = frame - *used < KEEP_FRAMES;
            if !keep {
                stale.push(*id);
            }
            keep
        });
        self.thumbs.retain(|_, (id, used)| {
            let keep = frame - *used < KEEP_FRAMES;
            if !keep {
                stale.push(*id);
            }
            keep
        });
        self.formulas.retain(|_, (id, used)| {
            let keep = frame - *used < KEEP_FRAMES;
            if !keep {
                stale.extend(*id);
            }
            keep
        });
        for id in stale {
            self.gpu.free(id);
        }
    }
}

/// Places a picture at a pixel rectangle, cropping whatever falls off the
/// screen (kitty would otherwise squeeze or scroll it).
fn clipped(image: u32, x: f32, y: f32, w: f32, h: f32, grid: &Grid, z: i32) -> Option<Placement> {
    let screen_w = grid.cols as f32 * grid.cw as f32;
    let screen_h = grid.rows as f32 * grid.ch as f32;
    let left = x.max(0.0);
    let top = y.max(0.0);
    let right = (x + w).min(screen_w);
    let bottom = (y + h).min(screen_h);
    if right - left < 1.0 || bottom - top < 1.0 {
        return None;
    }
    let mut placement = Placement::at_px(image, left, top, grid.cw, grid.ch, None, z);
    let crop = ((left - x).round() as u32, (top - y).round() as u32, (right - left).round() as u32, (bottom - top).round() as u32);
    if crop != (0, 0, w.round() as u32, h.round() as u32) {
        placement.crop = Some(crop);
    }
    Some(placement)
}

fn glass(style: CardStyle, ch: u32) -> Glass {
    let radius = ch as f32 * 0.6;
    match style {
        CardStyle::Side => Glass { tint: GLASS_TINT, alpha: 0.55, radius, rim: 0.10 },
        CardStyle::Input => Glass { tint: GLASS_TINT, alpha: 0.62, radius, rim: 0.16 },
        CardStyle::Reading => Glass { tint: GLASS_TINT, alpha: 0.32, radius, rim: 0.05 },
        // Lighter than the blurred night around it, so the dialog reads as a pane.
        CardStyle::Dialog => Glass { tint: [0x26, 0x31, 0x4A], alpha: 0.80, radius, rim: 0.24 },
        CardStyle::Menu(_) => Glass { tint: [0x14, 0x1E, 0x34], alpha: 0.74, radius, rim: 0.18 },
        CardStyle::Dimmed => Glass { tint: GLASS_TINT, alpha: 0.62, radius, rim: 0.03 },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "requires ffmpeg; exercises video resize and worker shutdown"]
    fn video_worker_resizes_and_stops() {
        let worker = Worker::spawn();
        worker.request(100, 80).unwrap();
        assert_eq!(worker.results.recv_timeout(Duration::from_secs(3)).unwrap().width, 100);
        worker.request(1920, 1080).unwrap();
        let started = Instant::now();
        let mut frames = 0;
        while started.elapsed() < Duration::from_secs(3) {
            let canvas = worker.results.recv_timeout(Duration::from_secs(3)).unwrap();
            if canvas.width == 1920 {
                assert_eq!(canvas.height, 1080);
                frames += 1;
            }
        }
        eprintln!("Full HD canvases in three seconds: {frames}");
        assert!(frames > 1, "video frames must keep replacing the canvas after resize");
        let stopped = Instant::now();
        drop(worker);
        assert!(stopped.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn a_new_video_frame_replaces_backdrop_and_cards_before_freeing_old_images() {
        let grid = Grid { cols: 10, rows: 4, cw: 10, ch: 20 };
        let source = image::RgbImage::from_pixel(100, 80, image::Rgb([20, 40, 60]));
        let mut pictures = Pictures::new(Gpu::new(None));
        let scene = Scene {
            backdrop: Backdrop::Sharp,
            cards: vec![CardSpec {
                slot: SLOT_INPUT, style: CardStyle::Input,
                rect: Rect::new(1, 1, 8, 2), dx: 0.0, dy: 0.0,
            }],
            thumb: None, attachments: Vec::new(), popup: None, water: None, formulas: Vec::new(), formulas_dim: false,
        };
        pictures.accept(Arc::new(backdrop::build(&source, 100, 80, false)));
        pictures.show(&grid, &scene).unwrap();
        assert_eq!(pictures.backdrop.as_ref().unwrap().len(), 1, "upload only the visible blur level");
        let old_backdrop = pictures.backdrop.as_ref().unwrap()[&Backdrop::Sharp];
        let old_card = pictures.cards.values().next().unwrap().0;
        pictures.gpu.flush(&mut Vec::new()).unwrap();
        pictures.accept(Arc::new(backdrop::build(&source, 100, 80, false)));
        pictures.show(&grid, &scene).unwrap();
        let mut output = Vec::new();
        pictures.gpu.flush(&mut output).unwrap();
        let output = String::from_utf8(output).unwrap();
        let placed = output.rfind("a=p,").unwrap();
        for id in [old_backdrop, old_card] {
            let freed = output.find(&format!("a=d,d=I,i={id},")).unwrap();
            assert!(freed > placed, "the old frame stays visible until all new placements exist");
        }
        assert!(pictures.stale_canvas.is_empty());
    }

    fn grid() -> Grid {
        Grid { cols: 100, rows: 40, cw: 10, ch: 20 }
    }

    #[test]
    fn a_card_partly_off_the_left_edge_is_cropped() {
        let p = clipped(1, -50.0, 40.0, 200.0, 100.0, &grid(), 0).unwrap();
        assert_eq!((p.col, p.row), (0, 2));
        assert_eq!(p.crop, Some((50, 0, 150, 100)));
    }

    #[test]
    fn a_card_fully_on_screen_is_not_cropped() {
        assert_eq!(clipped(1, 20.0, 40.0, 200.0, 100.0, &grid(), 0).unwrap().crop, None);
    }

    #[test]
    fn a_card_entirely_off_screen_is_not_placed() {
        assert!(clipped(1, 1000.0, 40.0, 200.0, 100.0, &grid(), 0).is_none());
    }
}
