// The kitty graphics protocol: upload a picture once, then place, move and
// remove it by id. Animations only change placements, which costs a few bytes;
// uploads happen when content changes.
//
// Layers by z-index. Below -1_073_741_824 a picture is drawn under cells that
// have a background colour, so a highlighted row covers it while ordinary
// cells (default background) let it show through.

use std::collections::HashMap;
use std::fs;
use std::io::{self, Write};
use std::path::PathBuf;

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use flate2::Compression;
use flate2::write::ZlibEncoder;

use crate::probe::TEMP_MARKER;

pub const Z_BACKDROP: i32 = -1_100_000_000;
pub const Z_CARD: i32 = -1_090_000_000;
pub const Z_DIALOG: i32 = -1_085_000_000;
pub const Z_THUMB: i32 = -1_080_000_000;
/// Above text: the opened browser hides whatever cells are under it.
pub const Z_TOP: i32 = 100;

const CHUNK: usize = 4096;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Placement {
    pub image: u32,
    pub col: u16,
    pub row: u16,
    /// Pixel offset inside the first cell, for motion finer than a cell.
    pub x_off: u32,
    pub y_off: u32,
    /// Scale the picture to this many cells; `None` shows it pixel for pixel.
    pub cells: Option<(u16, u16)>,
    /// Show only this part of the picture (x, y, width, height in pixels).
    pub crop: Option<(u32, u32, u32, u32)>,
    pub z: i32,
}

impl Placement {
    /// A placement whose top-left corner is at a pixel position.
    pub fn at_px(image: u32, x: f32, y: f32, cw: u32, ch: u32, cells: Option<(u16, u16)>, z: i32) -> Self {
        let x = x.max(0.0).round() as u32;
        let y = y.max(0.0).round() as u32;
        Placement {
            image,
            col: (x / cw) as u16,
            row: (y / ch) as u16,
            x_off: x % cw,
            y_off: y % ch,
            cells,
            crop: None,
            z,
        }
    }
}

pub struct Gpu {
    temp_dir: Option<PathBuf>,
    next_id: u32,
    uploads: u64,
    out: Vec<u8>,
    /// Placement slot -> what is shown there now.
    slots: HashMap<u32, Placement>,
}

impl Gpu {
    pub fn new(temp_dir: Option<PathBuf>) -> Self {
        Gpu { temp_dir, next_id: 7001, uploads: 0, out: Vec::new(), slots: HashMap::new() }
    }

    pub fn upload_rgb(&mut self, width: u32, height: u32, rgb: &[u8]) -> io::Result<u32> {
        self.upload(width, height, 24, rgb)
    }

    pub fn upload_rgba(&mut self, width: u32, height: u32, rgba: &[u8]) -> io::Result<u32> {
        self.upload(width, height, 32, rgba)
    }

    fn upload(&mut self, width: u32, height: u32, format: u8, data: &[u8]) -> io::Result<u32> {
        let id = self.next_id;
        self.next_id += 1;
        self.uploads += 1;
        match self.temp_dir.clone() {
            Some(dir) => {
                let path = dir.join(format!("kvist-glass-{TEMP_MARKER}-{}-{}", std::process::id(), self.uploads));
                fs::write(&path, data)?;
                let encoded = STANDARD.encode(path.as_os_str().as_encoded_bytes());
                self.out.extend_from_slice(
                    format!("\x1b_Ga=t,t=t,f={format},s={width},v={height},i={id},q=2;{encoded}\x1b\\").as_bytes(),
                );
            }
            None => self.transmit_inline(id, width, height, format, data)?,
        }
        Ok(id)
    }

    fn transmit_inline(&mut self, id: u32, width: u32, height: u32, format: u8, data: &[u8]) -> io::Result<()> {
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::fast());
        encoder.write_all(data)?;
        let encoded = STANDARD.encode(encoder.finish()?);
        let chunks: Vec<&[u8]> = encoded.as_bytes().chunks(CHUNK).collect();
        for (index, chunk) in chunks.iter().enumerate() {
            let more = u8::from(index + 1 < chunks.len());
            if index == 0 {
                self.out.extend_from_slice(
                    format!("\x1b_Ga=t,t=d,o=z,f={format},s={width},v={height},i={id},q=2,m={more};").as_bytes(),
                );
            } else {
                self.out.extend_from_slice(format!("\x1b_Gm={more};").as_bytes());
            }
            self.out.extend_from_slice(chunk);
            self.out.extend_from_slice(b"\x1b\\");
        }
        Ok(())
    }

    /// Frees a picture and every placement of it.
    pub fn free(&mut self, image: u32) {
        self.slots.retain(|_, placed| placed.image != image);
        self.out.extend_from_slice(format!("\x1b_Ga=d,d=I,i={image},q=2\x1b\\").as_bytes());
    }

    /// Shows `placement` in `slot`, or clears the slot. A slot that moves to a
    /// new picture places the new one before removing the old, so nothing
    /// blinks in between.
    pub fn set(&mut self, slot: u32, placement: Option<Placement>) {
        let previous = self.slots.get(&slot).copied();
        if previous == placement {
            return;
        }
        if let Some(p) = placement {
            self.place(slot, &p);
            self.slots.insert(slot, p);
        } else {
            self.slots.remove(&slot);
        }
        if let Some(old) = previous
            && placement.is_none_or(|p| p.image != old.image)
        {
            self.out.extend_from_slice(format!("\x1b_Ga=d,d=i,i={},p={slot},q=2\x1b\\", old.image).as_bytes());
        }
    }

    fn place(&mut self, slot: u32, p: &Placement) {
        let mut keys = format!("a=p,i={},p={slot},X={},Y={},z={},C=1,q=2", p.image, p.x_off, p.y_off, p.z);
        if let Some((cols, rows)) = p.cells {
            keys.push_str(&format!(",c={},r={}", cols.max(1), rows.max(1)));
        }
        if let Some((x, y, w, h)) = p.crop {
            keys.push_str(&format!(",x={x},y={y},w={w},h={h}"));
        }
        // Save and restore the cursor around the move the placement needs.
        self.out.extend_from_slice(format!("\x1b7\x1b[{};{}H\x1b_G{keys}\x1b\\\x1b8", p.row + 1, p.col + 1).as_bytes());
    }

    /// Forgets every slot after the terminal dropped all pictures (resize).
    pub fn reset(&mut self) {
        self.slots.clear();
        self.out.extend_from_slice(b"\x1b_Ga=d,d=A,q=2\x1b\\");
    }

    pub fn flush(&mut self, sink: &mut impl Write) -> io::Result<()> {
        if self.out.is_empty() {
            return Ok(());
        }
        sink.write_all(&self.out)?;
        sink.flush()?;
        self.out.clear();
        Ok(())
    }

    #[cfg(test)]
    fn take(&mut self) -> String {
        String::from_utf8(std::mem::take(&mut self.out)).unwrap()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn placement(image: u32) -> Placement {
        Placement { image, col: 2, row: 3, x_off: 0, y_off: 0, cells: None, crop: None, z: Z_CARD }
    }

    #[test]
    fn an_unchanged_placement_sends_nothing() {
        let mut gpu = Gpu::new(None);
        gpu.set(1, Some(placement(9)));
        assert!(gpu.take().contains("a=p,i=9,p=1"));
        gpu.set(1, Some(placement(9)));
        assert!(gpu.take().is_empty());
    }

    #[test]
    fn switching_pictures_places_the_new_one_first() {
        let mut gpu = Gpu::new(None);
        gpu.set(1, Some(placement(9)));
        gpu.take();
        gpu.set(1, Some(placement(10)));
        let sent = gpu.take();
        let placed = sent.find("a=p,i=10").unwrap();
        let removed = sent.find("a=d,d=i,i=9").unwrap();
        assert!(placed < removed);
    }

    #[test]
    fn inline_uploads_are_chunked() {
        let mut gpu = Gpu::new(None);
        let noise: Vec<u8> = (0..60_000u32).map(|i| (i.wrapping_mul(2_654_435_761) >> 13) as u8).collect();
        gpu.upload_rgb(100, 200, &noise).unwrap();
        let sent = gpu.take();
        assert!(sent.starts_with("\x1b_Ga=t,t=d,o=z,f=24,s=100,v=200"));
        assert!(sent.contains("\x1b_Gm=0;"));
    }

    #[test]
    fn pixel_positions_split_into_cell_and_offset() {
        let p = Placement::at_px(1, 25.0, 41.0, 10, 20, None, 0);
        assert_eq!((p.col, p.row, p.x_off, p.y_off), (2, 2, 5, 1));
    }
}
