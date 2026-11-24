// The keyboard. Android's WebView shrinks the page for it by itself (the viewport asks for it with
// interactive-widget); iOS lays the keyboard over the page. This keeps the dock above it on both: the app is exactly as
// tall as what is visible, and a focused field is scrolled into view.

export function watchViewport() {
  const root = document.documentElement;
  const viewport = window.visualViewport;
  if (!viewport) return;
  const apply = () => {
    root.style.setProperty("--app-h", `${Math.round(viewport.height)}px`);
    // iOS scrolls the whole page up when the keyboard opens; the layout already follows the viewport.
    if (viewport.offsetTop) window.scrollTo(0, 0);
  };
  viewport.addEventListener("resize", apply);
  viewport.addEventListener("scroll", apply);
  apply();
  document.addEventListener("focusin", (event) => {
    if (event.target.matches("input, textarea")) setTimeout(() => event.target.scrollIntoView({ block: "nearest" }), 250);
  });
}
