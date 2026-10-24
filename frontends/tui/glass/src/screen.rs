// The agent's browser as pictures: a thread reads the core's `stream_url` (an
// MJPEG stream of the virtual screen, 30 frames a second by default) and hands
// every new frame to the interface. The core serves it on loopback only; any
// other address is refused (docs/UI_API.md §8f, "Previews").

use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::Duration;

use crate::backdrop::{self, WebFrame};

const TIMEOUT: Duration = Duration::from_secs(3);

pub struct Feed {
    pub url: String,
    stop: Arc<AtomicBool>,
    pub frames: Receiver<Result<Arc<WebFrame>, String>>,
}

impl Feed {
    pub fn start(url: &str) -> Result<Feed, String> {
        let (host, path) = loopback(url)?;
        let stop = Arc::new(AtomicBool::new(false));
        let (sender, frames) = mpsc::channel();
        let stopped = Arc::clone(&stop);
        thread::spawn(move || {
            let mut last: Vec<u8> = Vec::new();
            let mut version = 0;
            let mut stream = match open_stream(&host, &path) {
                Ok(stream) => stream,
                Err(error) => {
                    sender.send(Err(error.to_string())).ok();
                    return;
                }
            };
            while !stopped.load(Ordering::Relaxed) {
                let message = match next_frame(&mut stream) {
                    // A still screen is sent over and over; decoding it again shows nothing new.
                    Ok(jpeg) if jpeg == last => None,
                    Ok(jpeg) => match image::load_from_memory(&jpeg) {
                        Ok(picture) => {
                            version += 1;
                            last = jpeg;
                            Some(Ok(Arc::new(backdrop::web_frame(picture.to_rgb8(), version))))
                        }
                        Err(error) => Some(Err(format!("the frame is not an image ({error})"))),
                    },
                    Err(error) => Some(Err(error.to_string())),
                };
                let failed = matches!(message, Some(Err(_)));
                if let Some(message) = message
                    && sender.send(message).is_err()
                {
                    return;
                }
                if failed {
                    return;
                }
            }
        });
        Ok(Feed { url: url.to_string(), stop, frames })
    }
}

impl Drop for Feed {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
    }
}

/// Decodes a picture the agent saved, off the interface's thread (a blur of
/// a large picture takes a moment), and sends it as a frame of the card.
pub fn load_image(path: String, version: u64) -> Receiver<Result<Arc<WebFrame>, String>> {
    let (sender, frames) = mpsc::channel();
    thread::spawn(move || {
        let frame = image::open(&path)
            .map(|picture| Arc::new(backdrop::web_frame(picture.to_rgb8(), version)))
            .map_err(|error| format!("cannot show {path}: {error}"));
        let _ = sender.send(frame);
    });
    frames
}

/// Splits a loopback `http://` URL into "host:port" and the request path.
fn loopback(url: &str) -> Result<(String, String), String> {
    let rest = url.strip_prefix("http://").ok_or_else(|| format!("not an http address: {url}"))?;
    let (host, path) = rest.split_once('/').map_or((rest, "/".to_string()), |(h, p)| (h, format!("/{p}")));
    let name = host.rsplit_once(':').map_or(host, |(name, _)| name);
    if !matches!(name, "127.0.0.1" | "localhost" | "[::1]") {
        return Err(format!("not a loopback address: {url}"));
    }
    Ok((host.to_string(), path))
}

/// Connects and reads the response head; what follows are the stream's parts.
fn open_stream(host: &str, path: &str) -> io::Result<BufReader<TcpStream>> {
    let address: SocketAddr = host
        .to_socket_addrs()?
        .next()
        .ok_or_else(|| io::Error::other(format!("no address for {host}")))?;
    let mut stream = TcpStream::connect_timeout(&address, TIMEOUT)?;
    stream.set_read_timeout(Some(TIMEOUT))?;
    let request = format!("GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes())?;
    let mut reader = BufReader::new(stream);
    let mut status = String::new();
    reader.read_line(&mut status)?;
    if status.split_whitespace().nth(1) != Some("200") {
        return Err(io::Error::other(format!("the preview answered {}", status.trim())));
    }
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            return Err(io::Error::other("an incomplete HTTP response"));
        }
        if line.trim().is_empty() {
            return Ok(reader);
        }
    }
}

/// The JPEG of the next part: lines up to a blank one, then `Content-Length` bytes.
fn next_frame(reader: &mut BufReader<TcpStream>) -> io::Result<Vec<u8>> {
    let mut length = None;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            return Err(io::Error::other("the preview stream ended"));
        }
        let line = line.trim();
        if line.is_empty() {
            if length.is_some() {
                break;
            }
            continue;
        }
        if let Some((name, value)) = line.split_once(':')
            && name.eq_ignore_ascii_case("content-length")
        {
            length = value.trim().parse::<usize>().ok();
        }
    }
    let mut jpeg = vec![0u8; length.unwrap_or_default()];
    reader.read_exact(&mut jpeg)?;
    Ok(jpeg)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn only_loopback_addresses_are_fetched() {
        assert_eq!(loopback("http://127.0.0.1:4100/frame?t=1"), Ok(("127.0.0.1:4100".into(), "/frame?t=1".into())));
        assert!(loopback("http://localhost:9/").is_ok());
        assert!(loopback("http://example.com:80/frame").is_err());
        assert!(loopback("https://127.0.0.1:1/").is_err());
    }

    #[test]
    fn a_non_200_answer_is_an_error() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let host = listener.local_addr().unwrap().to_string();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut chunk = [0u8; 256];
            socket.read(&mut chunk).unwrap();
            socket.write_all(b"HTTP/1.0 403 Forbidden\r\n\r\nno").unwrap();
        });
        assert!(open_stream(&host, "/stream").is_err());
        server.join().unwrap();
    }

    #[test]
    fn frames_are_read_one_part_at_a_time() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let host = listener.local_addr().unwrap().to_string();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut chunk = [0u8; 256];
            while !request.ends_with(b"\r\n\r\n") {
                let read = socket.read(&mut chunk).unwrap();
                request.extend_from_slice(&chunk[..read]);
            }
            socket
                .write_all(
                    b"HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=kvistframe\r\n\r\n\
                      --kvistframe\r\nContent-Type: image/jpeg\r\nContent-Length: 5\r\n\r\nFRAME\r\n\
                      --kvistframe\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\nTWO\r\n",
                )
                .unwrap();
            String::from_utf8_lossy(&request).to_string()
        });
        let mut reader = open_stream(&host, "/stream?token=x").unwrap();
        assert_eq!(next_frame(&mut reader).unwrap(), b"FRAME");
        assert_eq!(next_frame(&mut reader).unwrap(), b"TWO");
        assert!(next_frame(&mut reader).is_err());
        let request = server.join().unwrap();
        assert!(request.starts_with("GET /stream?token=x HTTP/1.0\r\n"));
        assert!(request.contains(&format!("Host: {host}\r\n")));
    }
}
