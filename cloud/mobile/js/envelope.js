// The message that carries photos (docs/vision.md): a JSON object the core reads goals by. No DOM, so the feed and the
// unit tests can use it. The server rewrites every photo (cloud/backend/kvist_cloud/images.py) and keeps only their names in
// the history; what this page sent is remembered here, a small copy of each photo in the browser's storage, so the person
// still sees their own photos after a reload.

const TYPE = "kvist-image-goal";
const MEMORY_KEY = "kvist.sent-photos";
const MEMORY_SIZE = 60;
const sent = new Map(readMemory());

function readMemory() {
  try {
    const list = JSON.parse(globalThis.localStorage?.getItem(MEMORY_KEY) ?? "[]");
    return Array.isArray(list) ? list.filter((pair) => Array.isArray(pair) && pair.length === 2) : [];
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof DOMException) return [];
    throw error;
  }
}

function remember(photos) {
  for (const { name, data_url: url, thumb } of photos) sent.set(name, thumb || url);
  // Only the small copies are kept past this visit: a full photo would fill the storage in a few messages.
  const kept = [...sent].filter(([, url]) => url.length < 200_000).slice(-MEMORY_SIZE);
  try {
    globalThis.localStorage?.setItem(MEMORY_KEY, JSON.stringify(kept));
  } catch (error) {
    // A full or forbidden storage only costs the pictures after a reload.
    if (!(error instanceof DOMException)) throw error;
  }
}

export function imageGoal(text, images) {
  if (!images.length) return text;
  remember(images);
  return JSON.stringify({ type: TYPE, text, images: images.map(({ name, data_url: url }) => ({ name, data_url: url })) });
}

/** The words and the photos out of a stored `user` text; the photos this browser sent come back with their pictures. */
export function parseImageGoal(text) {
  if (typeof text !== "string" || !text.trimStart().startsWith("{")) return { text, images: [] };
  try {
    const goal = JSON.parse(text);
    if (goal?.type !== TYPE) return { text, images: [] };
    return { text: goal.text ?? "", images: (goal.images ?? []).map((i) => ({ name: String(i.name ?? ""), data_url: i.data_url || sent.get(i.name) || "" })) };
  } catch {
    return { text, images: [] };
  }
}
