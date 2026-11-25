// Pictures attached to the next message: picked with "+", pasted or dropped on the field. They show as thumbnails inside
// the field, with a cross, never as "[Image #N]" text, and travel in the `kvist-image-goal` envelope (docs/vision.md) the
// other interfaces use. The phone page does the same (cloud/mobile/js/attachments.js).

import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { toast } from "./toast.js";

export const MAX_IMAGES = 8;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const SIDE = 2048;
const QUALITY = 0.85;
// What the feed shows of a sent photo after a reload: the server keeps only names, so a small copy stays in this browser.
const THUMB_SIDE = 480;
const MEMORY_KEY = "kvist.sent-photos";
const MEMORY_SIZE = 60;
let counter = 0;

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

function remember(name, thumb) {
  sent.set(name, thumb);
  const recent = [...sent].slice(-MEMORY_SIZE);
  try {
    globalThis.localStorage?.setItem(MEMORY_KEY, JSON.stringify(recent));
  } catch (error) {
    // A full or forbidden storage only costs the thumbnails after a reload.
    if (!(error instanceof DOMException)) throw error;
  }
}

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
 * Every picture is drawn again as a JPEG of at most 2048 px on its long side: the camera's turn is applied to the pixels,
 * any format the browser can show becomes JPEG, transparency becomes white, and no metadata survives. The server does not
 * trust this (cloud/backend/kvist_cloud/images.py rewrites whatever arrives); it keeps the upload small and the picture upright.
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
  // A name of our own: no two photos share one, so what was sent can be matched to what the history gives back.
  return { name: `photo-${Date.now().toString(36)}-${++counter}.jpg`, data_url, thumb, size: blob.size };
}

export function imageGoal(text, images) {
  if (!images.length) return text;
  for (const image of images) remember(image.name, image.thumb);
  return JSON.stringify({ type: "kvist-image-goal", text, images: images.map(({ name, data_url }) => ({ name, data_url })) });
}

/** The envelope's words and pictures back out of a stored `user` text; photos sent from this browser get their picture back. */
export function parseImageGoal(text) {
  if (typeof text !== "string" || !text.startsWith('{"type":"kvist-image-goal"')) return { text, images: [] };
  try {
    const goal = JSON.parse(text);
    return { text: goal.text ?? "", images: (goal.images ?? []).map((i) => ({ name: String(i.name ?? ""), data_url: i.data_url || sent.get(i.name) || "" })) };
  } catch {
    return { text, images: [] };
  }
}

const hasFiles = (event) => [...(event.dataTransfer?.types ?? [])].includes("Files");
// The home page and a task each have a field; a photo let go anywhere goes to the one on screen.
const fields = new Set();
let depth = 0;

function showDrop(on) {
  document.body.dataset.drop = t("drop photos");
  document.body.classList.toggle("dropping", on);
}

/** A photo dragged from another window may be let go anywhere on the page, as in the chat apps people know. */
function catchDrops(composer, add) {
  fields.add({ composer, add });
  if (fields.size > 1) return;
  document.addEventListener("dragenter", (event) => {
    if (!hasFiles(event)) return;
    depth += 1;
    showDrop(true);
  });
  document.addEventListener("dragleave", (event) => {
    if (!hasFiles(event)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) showDrop(false);
  });
  document.addEventListener("dragover", (event) => {
    if (hasFiles(event)) event.preventDefault();
  });
  document.addEventListener("drop", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth = 0;
    showDrop(false);
    const shown = [...fields].find((target) => target.composer.isConnected && target.composer.offsetParent !== null);
    shown?.add(event.dataTransfer.files);
  });
}

/** The tray goes inside `composer` above the field, "+" at the start of `foot`; `field` takes pastes, the whole page drops. */
export function mountAttachments({ composer, field, foot, onChange }) {
  let images = [];
  const tray = h("div", { class: "attach-tray", hidden: true });
  const picker = h("input", { type: "file", accept: "image/*", multiple: true, hidden: true });
  const plus = h("button", { class: "btn flat attach-btn", type: "button", title: t("attach photos"), "aria-label": t("attach photos") }, icon("plus", 18));
  composer.prepend(tray);
  composer.append(picker);
  foot.prepend(plus);

  function set(next) {
    images = next;
    tray.hidden = images.length === 0;
    fill(tray, ...images.map((item) => h("div", { class: "thumb" },
      h("img", { src: item.data_url, alt: "" }),
      h("button", { class: "thumb-remove", type: "button", title: t("remove photo"), "aria-label": t("remove photo"), onclick: () => set(images.filter((i) => i !== item)) }, icon("close", 12)))));
    onChange();
  }

  async function add(files) {
    const chosen = [...files].filter((f) => f.type.startsWith("image/"));
    if (!chosen.length) return;
    if (images.length + chosen.length > MAX_IMAGES) return toast(t("images max").replace("{n}", MAX_IMAGES), "error");
    let shrunk;
    try {
      shrunk = await Promise.all(chosen.map(shrink));
    } catch {
      return toast(t("image unreadable"), "error");
    }
    if (shrunk.some((f) => f.size > MAX_IMAGE_BYTES)) return toast(t("image big"), "error");
    if ([...images, ...shrunk].reduce((sum, f) => sum + f.size, 0) > MAX_TOTAL_BYTES) return toast(t("images big"), "error");
    set([...images, ...shrunk]);
    field.focus();
    return undefined;
  }

  plus.addEventListener("click", () => picker.click());
  picker.addEventListener("change", () => {
    add(picker.files);
    picker.value = "";
  });
  field.addEventListener("paste", (event) => {
    const files = [...(event.clipboardData?.items ?? [])].filter((i) => i.kind === "file" && i.type.startsWith("image/")).map((i) => i.getAsFile());
    if (!files.length) return;
    event.preventDefault();
    add(files);
  });
  catchDrops(composer, add);

  return {
    get images() {
      return images;
    },
    set,
  };
}
