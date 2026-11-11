"use strict"
// Fills the static markup's icons (data-icon). Runs once, before the app starts.

;(function fillChrome() {
  const K = window.K
  for (const node of document.querySelectorAll("[data-icon]")) {
    const size = node.classList.contains("rail-btn") ? 20 : node.classList.contains("jump") ? 16 : 17
    node.prepend(K.icon(node.dataset.icon, size))
  }
})()
