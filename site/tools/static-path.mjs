/**
 * static-path.mjs — the one containment rule the three dev servers share.
 *
 * build.mjs, audit.mjs and perf.mjs all serve dist/ over HTTP. Each used to
 * carry its own copy of "turn a request into a file path", and the copies did
 * not agree: one compared with a bare string prefix, two compared with nothing
 * at all. The rule lives here now so it cannot drift again.
 *
 * Two things make this less obvious than it looks:
 *
 *   - new URL() normalises dot segments, including the percent-encoded spellings
 *     the URL standard recognises (".%2e", "%2e%2e"). That alone looks like it
 *     handles traversal — it does not. "..%2f" is a single segment to the
 *     parser, survives normalisation intact, and only becomes "../" when
 *     decodeURIComponent runs afterwards. Decoding after normalising is the
 *     whole bug.
 *   - startsWith(root) is not containment: with root "/srv/dist" it also
 *     accepts "/srv/dist2/secret". The separator has to be part of the compare.
 */
import { realpathSync } from "node:fs";
import { resolve, sep } from "node:path";

/**
 * The absolute file a request asks for, or null if it asks for anything outside
 * `root`. `root` must already be absolute.
 */
export function resolveRequest(root, requestUrl) {
  let path;
  try {
    path = decodeURIComponent(new URL(requestUrl, "http://localhost").pathname);
  } catch {
    return null; // a protocol-relative "//" or a bad escape; not a file either way
  }
  if (path.includes("\0")) return null;
  if (path.endsWith("/")) path += "index.html";

  /* A URL pathname always opens with "/", so "." + path is a relative path:
     the request cannot address the filesystem root, and the dot segments
     decodeURIComponent just revealed collapse before the compare, not after. */
  const file = resolve(root, `.${path}`);
  if (file !== root && !file.startsWith(root + sep)) return null;

  /* Lexical containment is not enough: readFileSync follows symlinks, so a
     link planted inside root can point anywhere. Resolve both the file and the
     root to their real paths and re-check, so the byte actually read stays
     inside root. A file that is not on disk yet has no real path; readFileSync
     will 404 it the same either way. */
  try {
    const real = realpathSync(file);
    const realRoot = realpathSync(root);
    if (real !== realRoot && !real.startsWith(realRoot + sep)) return null;
  } catch {
    // file or root not on disk; the read below 404s a missing file
  }
  return file;
}
