// Data the server put into the page for the first screen. Each piece is used once: a later
// screen (after navigating inside the app) asks the server, so it never shows stale data.
let data = null;

export function readBoot() {
  const node = document.getElementById("boot");
  try {
    data = node ? JSON.parse(node.textContent) : null;
  } catch {
    data = null;
  }
  return data;
}

/** The piece if the page still has it, else the result of ``fetcher``. */
export async function fromBoot(name, fetcher) {
  if (data && name in data) {
    const value = data[name];
    delete data[name];
    return value;
  }
  return fetcher();
}
