// The keyboard. Android's WebView shrinks the page for it by itself (the viewport asks for it with
// interactive-widget); iOS lays the keyboard over the page. This keeps the dock above it on both: the app is exactly as
// tall as what is visible, and a focused field is scrolled into view.

export function watchViewport() {
  const root = document.documentElement;
  const viewport = window.visualViewport;
  if (!viewport) return;
  // The tallest the page has been is its height without a keyboard; well below it, a keyboard is open.
  let tallest = 0;
  let width = 0;
  const apply = () => {
    // A turned phone is another page: what was the tallest no longer says anything.
    if (viewport.width !== width) [width, tallest] = [viewport.width, 0];
    tallest = Math.max(tallest, viewport.height);
    root.style.setProperty("--app-h", `${Math.round(viewport.height)}px`);
    // Over the keyboard the home indicator's strip is hidden, so the dock does not keep room for it.
    root.classList.toggle("keyboard", viewport.height < tallest - 150);
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
