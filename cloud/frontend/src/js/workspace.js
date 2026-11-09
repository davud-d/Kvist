// The link into the workspace (a page of its own). Without a saved model key the task would only bounce
// there, so the person is sent to the key form first and told why.
import { api } from "./api.js";
import { h } from "./dom.js";

export const NEED_KEY_PATH = "/settings?need=key";

export function workspaceLink(className, text, navigate) {
  const link = h(`a.${className}`, { href: "/pixel/", text });
  link.addEventListener("click", async (event) => {
    // A modified click keeps its usual meaning (new tab).
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    const key = await api("/api/model-key").catch(() => null);
    // Unknown (the request failed) is let through: the workspace says the same thing itself.
    if (key && !key.set) navigate(NEED_KEY_PATH);
    else location.assign("/pixel/");
  });
  return link;
}
