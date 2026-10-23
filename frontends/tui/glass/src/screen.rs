// The agent's browser as pictures: a thread fetches the core's `frame_url`
// (one JPEG of the virtual screen) about once a second and hands every new
// frame to the interface. The core serves it on loopback only; any other
// address is refused (docs/UI_API.md §8f, "Previews").

use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::Duration;

use crate::backdrop::{self, WebFrame};

const EVERY: Duration = Duration::from_millis(900);
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
            while !stopped.load(Ordering::Relaxed) {
                let message = match fetch(&host, &path) {
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
                if let Some(message) = message
                    && sender.send(message).is_err()
                {
                    return;
                }
                thread::sleep(EVERY);
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

fn fetch(host: &str, path: &str) -> io::Result<Vec<u8>> {
    let address: SocketAddr = host
        .to_socket_addrs()?
        .next()
        .ok_or_else(|| io::Error::other(format!("no address for {host}")))?;
    let mut stream = TcpStream::connect_timeout(&address, TIMEOUT)?;
    stream.set_read_timeout(Some(TIMEOUT))?;
    let request = format!("GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes())?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response)?;
    body(&response)
}

/// The body of an HTTP/1.x response with status 200.
fn body(response: &[u8]) -> io::Result<Vec<u8>> {
    let split = response
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or_else(|| io::Error::other("an incomplete HTTP response"))?;
    let status = response[..split].split(|&b| b == b'\n').next().unwrap_or_default();
    let status = String::from_utf8_lossy(status);
    if status.split_whitespace().nth(1) != Some("200") {
        return Err(io::Error::other(format!("the preview answered {}", status.trim())));
    }
    Ok(response[split + 4..].to_vec())
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
        assert!(body(b"HTTP/1.0 403 Forbidden\r\n\r\nno").is_err());
        assert_eq!(body(b"HTTP/1.0 200 OK\r\nContent-Type: image/jpeg\r\n\r\nJPEG").unwrap(), b"JPEG");
    }

    #[test]
    fn a_frame_is_fetched_from_a_local_server() {
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
            let request = String::from_utf8_lossy(&request).to_string();
            socket.write_all(b"HTTP/1.0 200 OK\r\n\r\nFRAME").unwrap();
            request
        });
        assert_eq!(fetch(&host, "/frame?token=x").unwrap(), b"FRAME");
        let request = server.join().unwrap();
        assert!(request.starts_with("GET /frame?token=x HTTP/1.0\r\n"));
        assert!(request.contains(&format!("Host: {host}\r\n")));
    }
}
