// Pictures for the next message: taken with the camera, picked from the library, pasted, or dropped on the field (an iPad
// or a tablet takes them from another app). They show as thumbnails inside the field, with a cross, never as text.
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
let counter = 0;

/**
 * Every photo is drawn again in the browser as a JPEG of at most 2048 px on its long side: the camera's turn is applied to
 * the pixels (so no EXIF is needed), HEIC and the rest become JPEG, transparency becomes white, and no metadata survives.
 * The server does not trust this (it rewrites whatever arrives); this makes the upload small and the picture right-side up.
 */
async function shrink(file) {
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    bitmap = await new Promise((resolve, reject) => {
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
  const width = bitmap.naturalWidth ?? bitmap.width;
  const height = bitmap.naturalHeight ?? bitmap.height;
  const scale = Math.min(1, SIDE / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const context = canvas.getContext("2d");
  context.fillStyle = "#fff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close?.();
  const blob = await new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("encode"))), "image/jpeg", QUALITY));
  const data_url = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("read"));
    reader.onload = () => resolve(String(reader.result));
    reader.readAsDataURL(blob);
  });
  // A name of our own: no two photos of a visit share one, so what was sent can be matched to what comes back.
  return { name: `photo-${Date.now().toString(36)}-${++counter}.jpg`, data_url, size: blob.size };
}

/** Mounts the tray and the hidden pickers into `box`; `field` takes pastes, `box` takes drops. */
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
  box.addEventListener("dragover", (event) => {
    if ([...(event.dataTransfer?.types ?? [])].includes("Files")) {
      event.preventDefault();
      box.classList.add("drop");
    }
  });
  for (const type of ["dragleave", "drop"]) box.addEventListener(type, () => box.classList.remove("drop"));
  box.addEventListener("drop", (event) => {
    event.preventDefault();
    add(event.dataTransfer.files);
  });

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
