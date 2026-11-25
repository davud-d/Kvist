// Pictures for the next message: taken with the camera, picked from the library, pasted, or dropped anywhere on the page
// (a tablet in split screen, a desktop browser, another app). They show as thumbnails inside the field, with a cross, never as text.
// Ported from the web workspace (cloud/workspace/js/views/attachments.js): the same limits and the same envelope.

import { fill, h, icon } from "./dom.js";
import { t } from "./i18n.js";
import { toast } from "./sheet.js";

export { imageGoal, parseImageGoal } from "./envelope.js";

export const MAX_IMAGES = 8;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const KINDS = /^image\/(png|jpe?g|webp|gif|heic|heif|bmp)$/;

const SIDE = 2048;
const QUALITY = 0.85;
const THUMB_SIDE = 480;
let counter = 0;

async function decode(file) {
  try {
    return await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        resolve(img);
      };
      img.onerror = () => reject(new Error("decode"));
      img.src = url;
    });
  }
}

function draw(bitmap, side) {
  const width = bitmap.naturalWidth ?? bitmap.width;
  const height = bitmap.naturalHeight ?? bitmap.height;
  const scale = Math.min(1, side / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const context = canvas.getContext("2d");
  context.fillStyle = "#fff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return canvas;
}

/**
 * Every photo is drawn again in the browser as a JPEG of at most 2048 px on its long side: the camera's turn is applied to
 * the pixels (so no EXIF is needed), HEIC and the rest become JPEG, transparency becomes white, and no metadata survives.
 * The server does not trust this (it rewrites whatever arrives); this makes the upload small and the picture right-side up.
 * A small copy is made at the same time: the feed shows it again after a reload (envelope.js).
 */
async function shrink(file) {
  const bitmap = await decode(file);
  const full = draw(bitmap, SIDE);
  const thumb = draw(bitmap, THUMB_SIDE).toDataURL("image/jpeg", 0.7);
  bitmap.close?.();
  const blob = await new Promise((resolve, reject) => full.toBlob((b) => (b ? resolve(b) : reject(new Error("encode"))), "image/jpeg", QUALITY));
  const data_url = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("read"));
    reader.onload = () => resolve(String(reader.result));
    reader.readAsDataURL(blob);
  });
  // A name of our own: no two photos of a visit share one, so what was sent can be matched to what comes back.
  return { name: `photo-${Date.now().toString(36)}-${++counter}.jpg`, data_url, thumb, size: blob.size };
}

const hasFiles = (event) => [...(event.dataTransfer?.types ?? [])].includes("Files");

/**
 * A photo dragged from another app (split screen on a tablet, a desktop browser) may be let go anywhere on the page, as
 * in the chat apps people know: while it is over the page, the whole page says it will take it. The Android app gets a
 * drop itself (a WebView does not pass another app's files to the page) and hands it over through `window.kvistDrop`.
 */
function catchDrops(box, add) {
  let depth = 0;
  const show = (on) => {
    document.body.dataset.drop = t("drop photos");
    document.body.classList.toggle("dropping", on);
  };
  document.addEventListener("dragenter", (event) => {
    if (!hasFiles(event)) return;
    depth += 1;
    show(true);
  });
  document.addEventListener("dragleave", (event) => {
    if (!hasFiles(event)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) show(false);
  });
  document.addEventListener("dragover", (event) => {
    if (hasFiles(event)) event.preventDefault();
  });
  document.addEventListener("drop", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth = 0;
    show(false);
    // The field is not there while a question or a closed session takes its place: a photo then has nowhere to go.
    if (box.isConnected && box.offsetParent !== null) add(event.dataTransfer.files);
  });
  window.kvistDragging = (on) => show(Boolean(on));
  window.kvistDrop = (items) => {
    show(false);
    add(items.map(({ name, type, data }) => new File([Uint8Array.from(atob(data), (c) => c.charCodeAt(0))], name, { type })));
  };
}

/** Mounts the tray and the hidden pickers into `box`; `field` takes pastes, the whole page takes drops. */
export function mountAttachments({ box, field, onChange }) {
  let images = [];
  const tray = h("div", { class: "tray", hidden: true });
  // Two inputs: the library (any number) and the camera (capture opens the camera app directly on a phone).
  const library = h("input", { type: "file", accept: "image/*", multiple: true, hidden: true });
  const camera = h("input", { type: "file", accept: "image/*", capture: "environment", hidden: true });
  box.prepend(tray);
  box.append(library, camera);

  function paint() {
    tray.hidden = images.length === 0;
    fill(tray, images.map((item) => h("div", { class: "thumb", title: item.name },
      h("img", { src: item.data_url, alt: "" }),
      h("button", { class: "thumb-x", type: "button", aria: { label: t("cancel") }, onclick: () => {
        images = images.filter((i) => i !== item);
        paint();
      } }, icon("close", 12)))));
    onChange();
  }

  async function add(files) {
    const chosen = [...files].filter((f) => f.type.startsWith("image/") || KINDS.test(f.type));
    if (!chosen.length) return;
    if (images.length + chosen.length > MAX_IMAGES) return toast(t("images max", { n: MAX_IMAGES }), "error");
    let shrunk;
    try {
      shrunk = await Promise.all(chosen.map(shrink));
    } catch {
      return toast(t("image unreadable"), "error");
    }
    if (shrunk.some((f) => f.size > MAX_IMAGE_BYTES)) return toast(t("image big"), "error");
    if ([...images, ...shrunk].reduce((sum, f) => sum + f.size, 0) > MAX_TOTAL_BYTES) return toast(t("images big"), "error");
    images = [...images, ...shrunk];
    paint();
    return undefined;
  }

  for (const input of [library, camera]) {
    input.addEventListener("change", () => {
      add(input.files);
      input.value = "";
    });
  }
  field.addEventListener("paste", (event) => {
    const files = [...(event.clipboardData?.items ?? [])].filter((i) => i.kind === "file" && i.type.startsWith("image/")).map((i) => i.getAsFile());
    if (!files.length) return;
    event.preventDefault();
    add(files);
  });
  catchDrops(box, add);

  return {
    get images() {
      return images;
    },
    pickPhoto: () => library.click(),
    takePhoto: () => camera.click(),
    clear() {
      images = [];
      paint();
    },
  };
}
