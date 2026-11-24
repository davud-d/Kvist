// A phone app stays open for days: coming back to the front, ask the server which release it serves, and load it when
// it is not the one on the screen. A half-typed message is kept by the box, so the reload waits for an empty one.

export async function reloadIfOutdated(isTyping = () => false) {
  const mine = document.querySelector('meta[name="kvist-build"]')?.content;
  if (!mine || mine.startsWith("__")) return;
  try {
    const response = await fetch("/m/build", { cache: "no-store" });
    if (!response.ok) return;
    const live = (await response.text()).trim();
    if (live && live !== mine && !isTyping()) location.reload();
  } catch {
    // Offline: the page that is open keeps working.
  }
}
