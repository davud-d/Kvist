/**
 * player.js — replays a compiled cast into a <pre>.
 *
 * The compiler already did the work: frames carry only the rows that changed,
 * and each row is an id into a dictionary of distinct row states. Playing is
 * therefore a matter of walking a list and rewriting a handful of rows, which
 * is why this stays small enough to ship uncompressed on a slow connection.
 *
 * Colour indices point at --ansi-0..15 in tokens.css, so the replay repaints
 * with the rest of the page and follows the theme without re-fetching.
 *
 * A page can hold several of these — the guides page holds six — so nothing is
 * fetched until a replay is near the viewport, and only the front page's
 * replay starts on its own. The rest keep the frame the server rendered until
 * somebody presses Play; that frame is chosen per guide and is the point of
 * the recording, so replacing it on load would be a downgrade.
 */

const RATES = [1, 2, 4];

export function mountPlayer(root, { reducedMotion, strings }) {
  const screen = root.querySelector("[data-screen]");
  const source = root.dataset.cast;
  if (!screen || !source) return;
  const autoplay = root.dataset.autoplay !== "false";

  const play = root.querySelector("[data-play]");
  const restart = root.querySelector("[data-restart]");
  const speed = root.querySelector("[data-speed]");
  const scrub = root.querySelector("[data-scrub]");
  const time = root.querySelector("[data-time]");

  /** Row elements, created on first playback; until then the server's stand. */
  let lines = [];
  let cast = null;
  let loading = false;
  let cursor = 0;
  let elapsed = 0;
  let last = 0;
  let raf = 0;
  let playing = false;
  let rate = 1;

  /* Six recordings on one page are six requests nobody asked for, so each one
     waits until it is close enough to be looked at. */
  if ("IntersectionObserver" in window) {
    const watcher = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          watcher.disconnect();
          load();
        }
      },
      { rootMargin: "300px 0px" },
    );
    watcher.observe(root);
  } else {
    load();
  }

  function load() {
    if (loading || cast) return;
    loading = true;
    fetch(source)
      .then((response) => response.json())
      .then((data) => {
        cast = data;
        if (scrub) {
          scrub.max = String(cast.duration);
          scrub.disabled = false;
        }
        root.dataset.ready = "true";
        sync();
        if (autoplay && !reducedMotion()) start();
      })
      .catch(() => {
        // The server-rendered frame is already in the <pre>; leave it there.
        root.dataset.ready = "static";
      });
  }

  /** Take the screen over from the server's markup, once. */
  function adopt() {
    if (lines.length) return;
    screen.textContent = "";
    for (let i = 0; i < cast.h; i += 1) {
      const line = document.createElement("span");
      screen.append(line);
      lines.push(line);
    }
    cursor = 0;
    for (let i = 0; i < cast.h; i += 1) paint(i, 0);
  }

  function paint(index, id) {
    const line = lines[index];
    if (!line) return;
    line.textContent = "";
    for (const [text, fg, bg, bold] of cast.rows[id]) {
      const node = document.createElement(bold ? "b" : "span");
      node.textContent = text;
      if (fg !== -1) node.style.color = `var(--ansi-${fg})`;
      if (bg !== -2) node.style.background = `var(--ansi-${bg})`;
      line.append(node);
    }
  }

  /** Apply every frame up to `ms`, rebuilding from the start when rewinding. */
  function seek(ms) {
    if (!cast) return;
    adopt();
    if (ms < elapsed || cursor === 0) {
      cursor = 0;
      for (let i = 0; i < cast.h; i += 1) paint(i, 0);
    }
    elapsed = ms;
    while (cursor < cast.frames.length && cast.frames[cursor][0] <= ms) {
      const frame = cast.frames[cursor];
      for (let i = 1; i < frame.length; i += 2) paint(frame[i], frame[i + 1]);
      cursor += 1;
    }
    sync();
  }

  function showFinal() {
    if (!cast) return;
    adopt();
    for (let i = 0; i < cast.h; i += 1) paint(i, cast.final[i]);
    elapsed = cast.duration;
    cursor = cast.frames.length;
    sync();
  }

  function sync() {
    if (!cast) return;
    if (scrub && document.activeElement !== scrub) {
      scrub.value = String(Math.round(elapsed));
    }
    if (time) time.textContent = `${clock(elapsed)} / ${clock(cast.duration)}`;
    if (play) {
      play.textContent = playing ? strings.pause : strings.play;
      play.setAttribute("aria-pressed", playing ? "true" : "false");
    }
  }

  function tick(now) {
    if (!playing) return;
    if (last === 0) last = now;
    elapsed += Math.min(now - last, 250) * rate;
    last = now;
    if (elapsed >= cast.duration) {
      showFinal();
      stop();
      return;
    }
    seek(elapsed);
    raf = requestAnimationFrame(tick);
  }

  function start() {
    if (playing || !cast || reducedMotion()) return;
    adopt();
    if (elapsed >= cast.duration) seek(0);
    playing = true;
    last = 0;
    raf = requestAnimationFrame(tick);
    sync();
  }

  function stop() {
    playing = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    last = 0;
    sync();
  }

  play?.addEventListener("click", () => {
    if (!cast) {
      load();
      return;
    }
    if (playing) stop();
    else start();
  });
  restart?.addEventListener("click", () => {
    stop();
    seek(0);
    start();
  });
  speed?.addEventListener("click", () => {
    rate = RATES[(RATES.indexOf(rate) + 1) % RATES.length];
    speed.textContent = `${rate}×`;
  });
  scrub?.addEventListener("input", () => {
    // Read first: stop() syncs the controls, which would write the current
    // position back over the one just requested.
    const target = Number(scrub.value);
    stop();
    seek(target);
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
  });

  window.addEventListener("kvist:motion", () => {
    if (reducedMotion()) {
      stop();
      showFinal();
    }
  });
}

function clock(ms) {
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
