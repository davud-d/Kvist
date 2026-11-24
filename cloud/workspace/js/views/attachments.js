// Pictures attached to the next message: pasted, dropped or picked. They show
// as thumbnails above the input, never as "[image #N]" text, and travel in the
// `kvist-image-goal` envelope (docs/vision.md) the other interfaces use.

import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { toast } from "./toast.js";

export const MAX_IMAGES = 8;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_TOTAL_BYTES = 20 * 1024 * 1024;

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Cannot read the image"));
    reader.onload = () => resolve({ name: file.name || "pasted-image.png", data_url: String(reader.result), size: file.size });
    reader.readAsDataURL(file);
  });
}

export function imageGoal(text, images) {
  if (!images.length) return text;
  return JSON.stringify({ type: "kvist-image-goal", text, images: images.map(({ name, data_url }) => ({ name, data_url })) });
}

/** The envelope's pictures and text back out of a journaled `user` text. */
export function parseImageGoal(text) {
  if (typeof text !== "string" || !text.startsWith('{"type":"kvist-image-goal"')) return { text, images: [] };
  try {
    const goal = JSON.parse(text);
    return { text: goal.text ?? "", images: (goal.images ?? []).map((i) => ({ name: i.name, data_url: i.data_url })) };
  } catch {
    return { text, images: [] };
  }
}

export function mountAttachments({ composer, field, onChange }) {
  let images = [];
  const tray = h("div", { class: "attach-tray" });
  const picker = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp,image/gif,image/bmp", multiple: true, hidden: true });
  const plus = h("button", { class: "btn attach-btn", type: "button", title: "Attach images" }, icon("plus", 14));
  composer.prepend(tray);
  composer.append(picker);
  field.before(plus);

  function paint() {
    tray.hidden = images.length === 0;
    fill(tray, 
      ...images.map((item) =>
        h(
          "div",
          { class: "thumb", title: item.name },
          h("img", { src: item.data_url, alt: item.name }),
          h("button", { class: "thumb-remove", type: "button", "aria-label": t("cancel"), onclick: () => { images = images.filter((i) => i !== item); paint(); onChange?.(); } }, "×"),
        ),
      ),
    );
    onChange?.();
  }

  async function add(files) {
    const chosen = [...files].filter((f) => f.type.startsWith("image/"));
    if (!chosen.length) return;
    if (images.length + chosen.length > MAX_IMAGES) return toast(`Up to ${MAX_IMAGES} images`, "error");
    if (chosen.some((f) => f.size > MAX_IMAGE_BYTES)) return toast("An image is over 5 MiB", "error");
    const total = [...images, ...chosen].reduce((sum, f) => sum + f.size, 0);
    if (total > MAX_TOTAL_BYTES) return toast("Images are over 20 MiB", "error");
    try {
      images = [...images, ...(await Promise.all(chosen.map(readAsDataUrl)))];
    } catch (error) {
      return toast(error.message, "error");
    }
    paint();
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
  composer.addEventListener("dragover", (event) => {
    if ([...(event.dataTransfer?.types ?? [])].includes("Files")) event.preventDefault();
  });
  composer.addEventListener("drop", (event) => {
    event.preventDefault();
    add(event.dataTransfer.files);
  });
  tray.hidden = true;

  return {
    get images() { return images; },
    clear() { images = []; paint(); },
  };
}
