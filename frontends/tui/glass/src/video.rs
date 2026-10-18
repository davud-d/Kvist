use std::fs;
use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use image::RgbImage;

pub const FRAME_TIME: Duration = Duration::from_nanos(1_000_000_000 / 24);
const VIDEO: &[u8] = include_bytes!("../assets/backdrop-night-forest.mp4");
const WIDTH: u32 = 960;
const HEIGHT: u32 = 540;
type Frame = Result<RgbImage, String>;

pub struct Video {
    child: Option<Child>,
    reader: Option<JoinHandle<()>>,
    latest: Arc<Mutex<Option<Frame>>>,
    directory: PathBuf,
}

impl Video {
    pub fn start() -> io::Result<Self> {
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).map_err(io::Error::other)?.as_nanos();
        let directory = std::env::temp_dir().join(format!("kvist-forest-{}-{nonce}", std::process::id()));
        fs::create_dir(&directory)?;
        let mut video = Self { child: None, reader: None, latest: Arc::new(Mutex::new(None)), directory };
        let path = video.directory.join("backdrop-night-forest.mp4");
        fs::File::create_new(&path)?.write_all(VIDEO)?;
        let mut child = Command::new(ffmpeg())
            .args(["-v", "error", "-nostdin", "-threads", "1", "-re", "-stream_loop", "-1", "-i"])
            .arg(&path)
            .args(["-an", "-vf", "fps=24,scale=960:540", "-threads", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
            .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()?;
        let mut stdout = child.stdout.take().expect("piped video output");
        video.child = Some(child);
        let latest = video.latest.clone();
        video.reader = Some(thread::spawn(move || {
            loop {
                let mut bytes = vec![0; (WIDTH * HEIGHT * 3) as usize];
                let frame = stdout.read_exact(&mut bytes)
                    .map(|()| RgbImage::from_raw(WIDTH, HEIGHT, bytes).expect("complete RGB frame"))
                    .map_err(|error| format!("ffmpeg: {error}"));
                let failed = frame.is_err();
                // Keep only the newest frame when resizing or uploading is slower than decoding.
                *latest.lock().expect("video frame lock") = Some(frame);
                if failed { break; }
            }
        }));
        Ok(video)
    }

    pub fn take(&self) -> Option<Frame> {
        self.latest.lock().expect("video frame lock").take()
    }
}

impl Drop for Video {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        if let Some(reader) = self.reader.take() { let _ = reader.join(); }
        let _ = fs::remove_file(self.directory.join("backdrop-night-forest.mp4"));
        let _ = fs::remove_dir(&self.directory);
    }
}

fn ffmpeg() -> PathBuf {
    if let Some(path) = std::env::var_os("KVIST_FFMPEG") { return path.into(); }
    if let Some(paths) = std::env::var_os("PATH") {
        for directory in std::env::split_paths(&paths) {
            let path = directory.join("ffmpeg");
            if path.is_file() { return path; }
        }
    }
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default()
        .join(".local/share/kvist/ffmpeg/ffmpeg")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    #[test]
    #[ignore = "requires ffmpeg; plays beyond the bundled clip's duration"]
    fn video_loops_and_cleans_up_on_drop() {
        let video = Video::start().expect("ffmpeg starts");
        let directory = video.directory.clone();
        let started = Instant::now();
        let mut first = None;
        let mut changed = false;
        let mut looped = false;
        while started.elapsed() < Duration::from_secs(13) {
            if let Some(frame) = video.take() {
                let frame = frame.expect("video keeps decoding across the loop");
                assert_eq!(frame.dimensions(), (WIDTH, HEIGHT));
                if let Some(first) = &first { changed |= first != &frame; }
                else { first = Some(frame); }
                if started.elapsed() > Duration::from_millis(11_000) { looped = true; break; }
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(changed, "the backdrop must move");
        assert!(looped, "frames must continue after the end of the clip");
        let stopped = Instant::now();
        drop(video);
        assert!(stopped.elapsed() < Duration::from_secs(2));
        assert!(!directory.exists(), "embedded video temp files must be removed");
    }
}
