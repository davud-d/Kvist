// The council: four cards, one per angle, filled in as the members answer. Under a card are the risks the other members
// see in it; when the verdict arrives the chosen card is lit with the reason, and those who disagree say why.

import { isChosen } from "../bestof.js";
import { angleBlurb, angleName, w } from "../bestof-words.js";
import { fill, h, icon } from "../dom.js";

function riskLine(council, risk) {
  const target = council.cards.find((card) => card.id === risk.target);
  return h("li", {}, h("span", { class: "bo-risk-to" }, `→ ${target ? angleName(target.angle) : risk.target}: `), risk.risk);
}

function card(council, item) {
  const chosen = isChosen(council, item);
  return h(
    "div",
    { class: `co-card px${chosen ? " chosen" : ""}${item.decision === null ? " waiting" : ""}` },
    h("div", { class: "co-angle" }, h("b", {}, angleName(item.angle)), chosen ? h("span", { class: "chip" }, w("chosen")) : null),
    h("div", { class: "co-blurb dim" }, angleBlurb(item.angle)),
    item.decision === null ? h("div", { class: "co-wait dim" }, w("thinking")) : h("div", { class: "co-decision" }, item.decision),
    item.argument ? h("div", { class: "co-argument" }, item.argument) : null,
    item.risks.length ? h("ul", { class: "co-risks" }, item.risks.map((risk) => riskLine(council, risk))) : null,
  );
}

function verdictBlock(council) {
  const verdict = council.verdict;
  if (!verdict) return null;
  return h(
    "div",
    { class: "co-verdict px" },
    verdict.decision ? h("div", { class: "co-decision" }, verdict.decision) : null,
    verdict.reason ? h("div", {}, h("b", {}, `${w("why")}: `), verdict.reason) : null,
    verdict.dissent.length
      ? h("div", {}, h("b", {}, `${w("dissent")}:`), h("ul", { class: "co-risks" }, verdict.dissent.map((d) => h("li", {}, h("span", { class: "bo-risk-to" }, `${angleName(d.who)}: `), d.why))))
      : null,
  );
}

/** Mounts the council into `el`; `paint(council)` redraws it. */
export function mountCouncil(el, { onClose }) {
  const title = h("div", { class: "bo-title" });
  const blurb = h("div", { class: "bo-blurb" });
  const close = h("button", { class: "btn flat", type: "button", onclick: onClose }, icon("close", 12));
  const question = h("div", { class: "bo-task" });
  const cards = h("div", { class: "co-cards" });
  const verdict = h("div", { class: "co-foot" });
  el.append(h("div", { class: "bo-head" }, h("div", { class: "bo-heading" }, title, blurb), close), question, cards, verdict);

  return (council) => {
    title.textContent = w("councilTitle");
    blurb.textContent = w("councilBlurb");
    close.title = w("close");
    fill(question, h("span", { class: "dim" }, `${w("question")}: `), h("span", {}, council.question));
    fill(cards, council.cards.map((item) => card(council, item)));
    fill(verdict, verdictBlock(council));
  };
}
