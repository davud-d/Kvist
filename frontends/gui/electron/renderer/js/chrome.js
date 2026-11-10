"use strict"
// Fills the static markup: icons (data-icon) and bilingual text (data-i18n).

;(function fillChrome() {
  const K = window.K

  for (const node of document.querySelectorAll("[data-icon]")) {
    node.prepend(K.icon(node.dataset.icon, node.classList.contains("nav-icon") ? 16 : 15))
  }
  for (const node of document.querySelectorAll("[data-i18n]")) {
    const [en, ru] = node.dataset.i18n.split("|")
    node.textContent = K.t(en, ru)
  }
  for (const node of document.querySelectorAll("[data-i18n-title]")) {
    const [en, ru] = node.dataset.i18nTitle.split("|")
    node.title = K.t(en, ru)
  }
  document.getElementById("prompt").placeholder = K.t(
    "Ask Kvist to do something. @ mentions a file, / opens commands",
    "Попроси Kvist что-нибудь сделать. @ — файл, / — команды",
  )
})()
