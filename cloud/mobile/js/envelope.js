// The message that carries photos (docs/vision.md): a JSON object the core reads goals by. Pure (no DOM), so the feed and the
// unit tests can use it. The server rewrites every photo (cloud/backend/kvist_cloud/images.py) and keeps only their names in
// the history; what this page sent is remembered here for the length of the visit, so the person still sees their own photos.

const TYPE = "kvist-image-goal";
const sent = new Map();

export function imageGoal(text, images) {
  if (!images.length) return text;
  for (const { name, data_url: url } of images) sent.set(name, url);
  return JSON.stringify({ type: TYPE, text, images: images.map(({ name, data_url: url }) => ({ name, data_url: url })) });
}

/** The words and the photos out of a stored `user` text; the photos this page sent come back with their pictures. */
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
