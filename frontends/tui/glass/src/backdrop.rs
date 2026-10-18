// Everything drawn in pixels: the photograph fitted to the window, its blurred
// versions, and the glass cards. A card is a crop of the blurred photograph
// under it, tinted night blue, with rounded anti-aliased corners and a thin
// rim of light along the edge, which cells alone cannot draw.

use image::imageops::{self, FilterType};
use image::{RgbImage, RgbaImage};

pub const PHOTO: &[u8] = include_bytes!("../assets/backdrop-night-forest.png");

/// How many steps the backdrop takes from sharp to fully blurred.
pub const LEVELS: usize = 4;

pub struct Canvas {
    pub width: u32,
    pub height: u32,
    /// The photograph, untouched apart from fitting the window.
    pub photo: RgbImage,
    /// The photograph darkened towards the top and bottom edges, so text over
    /// it stays readable.
    pub sharp: RgbImage,
    /// What glass shows through: the sharp backdrop, heavily blurred.
    pub frost: RgbImage,
    /// Progressively blurred and darker, for an opened dialog or browser.
    pub levels: Vec<RgbImage>,
    /// The photograph rising from black, for the first seconds.
    pub rise: Vec<RgbImage>,
    /// Average colour of the backdrop's border: the terminal's padding around
    /// the cells is painted with it, so the picture has no frame.
    pub edge: [u8; 3],
}

pub fn decode_photo() -> RgbImage {
    image::load_from_memory(PHOTO).expect("the bundled photograph decodes").to_rgb8()
}

pub fn build(source: &RgbImage, width: u32, height: u32, with_rise: bool) -> Canvas {
    let photo = cover(source, width, height);
    let sharp = vignette(&photo);
    let frost = blur(&sharp, 6, 3);
    let levels = [(3, 2, 0.93), (5, 2, 0.84), (8, 2, 0.75), (12, 3, 0.66)]
        .iter()
        .map(|&(divisor, radius, light)| brightness(&blur(&sharp, divisor, radius), light))
        .collect();
    let rise = if with_rise {
        [0.08, 0.22, 0.40, 0.60, 0.80, 0.93].iter().map(|&light| brightness(&photo, light)).collect()
    } else {
        Vec::new()
    };
    let edge = edge_color(&sharp);
    Canvas { width, height, photo, sharp, frost, levels, rise, edge }
}

fn edge_color(image: &RgbImage) -> [u8; 3] {
    const BAND: u32 = 4;
    let (w, h) = image.dimensions();
    let mut sum = [0u64; 3];
    let mut count = 0u64;
    for (x, y, pixel) in image.enumerate_pixels() {
        if x >= BAND && x + BAND < w && y >= BAND && y + BAND < h {
            continue;
        }
        for c in 0..3 {
            sum[c] += pixel.0[c] as u64;
        }
        count += 1;
    }
    let count = count.max(1);
    [(sum[0] / count) as u8, (sum[1] / count) as u8, (sum[2] / count) as u8]
}

/// Scales the picture to cover the whole area and crops the overflow evenly.
pub fn cover(source: &RgbImage, width: u32, height: u32) -> RgbImage {
    let scale = (width as f32 / source.width() as f32).max(height as f32 / source.height() as f32);
    let scaled_w = ((source.width() as f32 * scale).ceil() as u32).max(width);
    let scaled_h = ((source.height() as f32 * scale).ceil() as u32).max(height);
    let scaled = imageops::resize(source, scaled_w, scaled_h, FilterType::Lanczos3);
    imageops::crop_imm(&scaled, (scaled_w - width) / 2, (scaled_h - height) / 2, width, height).to_image()
}

fn vignette(photo: &RgbImage) -> RgbImage {
    let height = photo.height() as f32;
    let mut out = photo.clone();
    for (_, y, pixel) in out.enumerate_pixels_mut() {
        let v = y as f32 / height;
        let light = 1.0 - 0.42 * smoothstep(0.70, 1.0, v) - 0.22 * smoothstep(0.22, 0.0, v);
        for channel in pixel.0.iter_mut() {
            *channel = (*channel as f32 * light) as u8;
        }
    }
    out
}

/// A soft blur: shrink, box-blur three times (close to a gaussian), enlarge.
pub fn blur(image: &RgbImage, divisor: u32, radius: u32) -> RgbImage {
    let small_w = (image.width() / divisor).max(1);
    let small_h = (image.height() / divisor).max(1);
    let mut small = imageops::resize(image, small_w, small_h, FilterType::Triangle);
    for _ in 0..3 {
        box_blur(&mut small, radius);
    }
    imageops::resize(&small, image.width(), image.height(), FilterType::Triangle)
}

fn box_blur(image: &mut RgbImage, radius: u32) {
    let (width, height) = image.dimensions();
    let r = radius as i64;
    let window = (2 * r + 1) as u32;
    let mut line = vec![[0u8; 3]; width.max(height) as usize];
    for y in 0..height {
        for x in 0..width {
            line[x as usize] = image.get_pixel(x, y).0;
        }
        running_average(&line[..width as usize], r, window, |x, value| image.put_pixel(x, y, image::Rgb(value)));
    }
    for x in 0..width {
        for y in 0..height {
            line[y as usize] = image.get_pixel(x, y).0;
        }
        running_average(&line[..height as usize], r, window, |y, value| image.put_pixel(x, y, image::Rgb(value)));
    }
}

fn running_average(line: &[[u8; 3]], r: i64, window: u32, mut put: impl FnMut(u32, [u8; 3])) {
    let last = line.len() as i64 - 1;
    let at = |i: i64| line[i.clamp(0, last) as usize];
    let mut sum = [0u32; 3];
    for i in -r..=r {
        let p = at(i);
        for c in 0..3 {
            sum[c] += p[c] as u32;
        }
    }
    for i in 0..line.len() as i64 {
        put(i as u32, [(sum[0] / window) as u8, (sum[1] / window) as u8, (sum[2] / window) as u8]);
        let (add, drop) = (at(i + r + 1), at(i - r));
        for c in 0..3 {
            sum[c] = sum[c] + add[c] as u32 - drop[c] as u32;
        }
    }
}

pub fn brightness(image: &RgbImage, light: f32) -> RgbImage {
    let mut out = image.clone();
    for channel in out.iter_mut() {
        *channel = (*channel as f32 * light) as u8;
    }
    out
}

fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    let t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

#[derive(Clone, Copy, Debug)]
pub struct Glass {
    pub tint: [u8; 3],
    /// How much of the tint covers the frosted photograph.
    pub alpha: f32,
    pub radius: f32,
    /// Strength of the light along the top edge.
    pub rim: f32,
}

/// A glass card of `width`×`height` pixels whose top-left corner sits at
/// (`x`, `y`) on `source`. Returns RGBA; the corners are transparent.
pub fn card(source: &RgbImage, x: i64, y: i64, width: u32, height: u32, glass: &Glass) -> RgbaImage {
    let mut out = RgbaImage::new(width, height);
    let (sw, sh) = (source.width() as i64, source.height() as i64);
    let half_w = width as f32 / 2.0;
    let half_h = height as f32 / 2.0;
    let radius = glass.radius.min(half_w).min(half_h);
    for (px, py, pixel) in out.enumerate_pixels_mut() {
        let d = rounded_rect_distance(px as f32 + 0.5 - half_w, py as f32 + 0.5 - half_h, half_w, half_h, radius);
        let coverage = (0.5 - d).clamp(0.0, 1.0);
        if coverage <= 0.0 {
            continue;
        }
        let under = source.get_pixel((x + px as i64).clamp(0, sw - 1) as u32, (y + py as i64).clamp(0, sh - 1) as u32).0;
        let ring = (1.0 - (d + 1.0).abs()).clamp(0.0, 1.0);
        let fall = 1.0 - 0.8 * (py as f32 / height as f32);
        let light = ring * glass.rim * fall * 255.0;
        let grain = grain(px, py);
        let mut rgba = [0u8; 4];
        for c in 0..3 {
            let mixed = under[c] as f32 + (glass.tint[c] as f32 - under[c] as f32) * glass.alpha;
            rgba[c] = (mixed + light + grain).clamp(0.0, 255.0) as u8;
        }
        rgba[3] = (coverage * 255.0) as u8;
        pixel.0 = rgba;
    }
    out
}

/// Signed distance from a point (relative to the centre) to a rounded
/// rectangle: negative inside, positive outside.
fn rounded_rect_distance(x: f32, y: f32, half_w: f32, half_h: f32, radius: f32) -> f32 {
    let qx = x.abs() - (half_w - radius);
    let qy = y.abs() - (half_h - radius);
    let outside = (qx.max(0.0).powi(2) + qy.max(0.0).powi(2)).sqrt();
    outside + qx.max(qy).min(0.0) - radius
}

/// A faint fixed noise: frosted glass is never perfectly flat.
fn grain(x: u32, y: u32) -> f32 {
    let mut h = x.wrapping_mul(374_761_393) ^ y.wrapping_mul(668_265_263);
    h = (h ^ (h >> 13)).wrapping_mul(1_274_126_177);
    ((h >> 24) as f32 / 255.0 - 0.5) * 3.0
}

/// Gives a picture rounded transparent corners.
pub fn rounded(image: &RgbImage, radius: f32) -> RgbaImage {
    let (width, height) = image.dimensions();
    let half_w = width as f32 / 2.0;
    let half_h = height as f32 / 2.0;
    RgbaImage::from_fn(width, height, |x, y| {
        let d = rounded_rect_distance(x as f32 + 0.5 - half_w, y as f32 + 0.5 - half_h, half_w, half_h, radius);
        let p = image.get_pixel(x, y).0;
        image::Rgba([p[0], p[1], p[2], ((0.5 - d).clamp(0.0, 1.0) * 255.0) as u8])
    })
}

/// One frame of the agent's browser: sharp, and two blurred steps it
/// sharpens through while it opens. `version` grows with every new frame.
pub struct WebFrame {
    pub raw: RgbImage,
    pub sharp: RgbaImage,
    pub soft: RgbaImage,
    pub softer: RgbaImage,
    pub version: u64,
}

pub fn web_frame(raw: RgbImage, version: u64) -> WebFrame {
    let radius = raw.width() as f32 / 90.0;
    WebFrame {
        sharp: rounded(&raw, radius),
        soft: rounded(&blur(&raw, 3, 2), radius),
        softer: rounded(&blur(&raw, 6, 3), radius),
        raw,
        version,
    }
}

/// A screenshot reduced to thumbnail size with a high-quality filter (the
/// terminal's own scaling would alias the page's text).
pub fn thumbnail(frame: &RgbImage, width: u32, height: u32, light: f32) -> RgbaImage {
    rounded(&brightness(&cover(frame, width, height), light), 7.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cover_fills_the_area_exactly() {
        let source = RgbImage::from_pixel(160, 90, image::Rgb([10, 20, 30]));
        for (w, h) in [(300, 100), (100, 300), (160, 90)] {
            assert_eq!(cover(&source, w, h).dimensions(), (w, h));
        }
    }

    #[test]
    fn card_corners_are_transparent_and_middle_is_opaque() {
        let source = RgbImage::from_pixel(200, 100, image::Rgb([200, 200, 200]));
        let glass = Glass { tint: [0, 0, 0], alpha: 0.5, radius: 12.0, rim: 0.0 };
        let card = card(&source, 10, 10, 120, 60, &glass);
        assert_eq!(card.get_pixel(0, 0).0[3], 0);
        let middle = card.get_pixel(60, 30).0;
        assert_eq!(middle[3], 255);
        assert!((middle[0] as i32 - 100).abs() <= 2);
    }

    #[test]
    fn the_edge_colour_ignores_the_middle() {
        let mut image = RgbImage::from_pixel(40, 40, image::Rgb([10, 20, 30]));
        for x in 10..30 {
            for y in 10..30 {
                image.put_pixel(x, y, image::Rgb([255, 255, 255]));
            }
        }
        assert_eq!(edge_color(&image), [10, 20, 30]);
    }

    #[test]
    fn blur_keeps_a_flat_colour_flat() {
        let flat = RgbImage::from_pixel(64, 48, image::Rgb([90, 120, 150]));
        let blurred = blur(&flat, 4, 2);
        assert_eq!(blurred.dimensions(), (64, 48));
        assert!(blurred.pixels().all(|p| p.0.iter().zip([90, 120, 150]).all(|(&a, b)| (a as i32 - b).abs() <= 1)));
    }
}
