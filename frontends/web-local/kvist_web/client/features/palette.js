import { inboxItems, paletteItems } from "/assets/features/navigation.js"

const overlay = document.querySelector("#palette"), input = document.querySelector("#palette-input"), results = document.querySelector("#palette-results")
let items = [], index = 0, callback = () => {}
let previousFocus = null
const commands = ["New session","Show inbox","Connect server","Add workspace","Stop agent","Open activity","Open diff","Open terminal","Settings"]

export function openPalette(mode, onChoose) {
  previousFocus = document.activeElement; callback = onChoose; overlay.hidden = false; input.value = ""
  input.placeholder = mode === "search" ? "Search sessions and workspaces…" : mode === "inbox" ? "Search unread and attention-needed sessions…" : "Type a command or search sessions…"
  items = mode === "search" ? paletteItems() : mode === "inbox" ? inboxItems() : [...commands.map(label => ({ type:"command", label, detail:"Command" })), ...paletteItems()]
  index = 0; render(); queueMicrotask(() => input.focus())
}
export function closePalette() { if (overlay.hidden) return; overlay.hidden = true; if (previousFocus instanceof HTMLElement) previousFocus.focus(); previousFocus = null }
function render() {
  const query = input.value.trim().toLowerCase(), filtered = items.filter(item => `${item.label} ${item.detail}`.toLowerCase().includes(query)).slice(0,40)
  results.replaceChildren(); index = Math.min(index, Math.max(0, filtered.length - 1))
  filtered.forEach((item, i) => { const button=document.createElement("button");button.className=`palette-item ${i===index?"active":""}`;const label=document.createElement("span"),detail=document.createElement("small");label.textContent=item.label;detail.textContent=item.detail;button.append(label,detail);button.onclick=()=>choose(item);results.append(button) })
  results._filtered = filtered
}
function choose(item) { closePalette(); callback(item) }
input.addEventListener("input", () => { index=0; render() })
input.addEventListener("keydown", event => { const filtered=results._filtered||[]; if(event.key==="ArrowDown"){event.preventDefault();index=Math.min(index+1,filtered.length-1);render()}else if(event.key==="ArrowUp"){event.preventDefault();index=Math.max(0,index-1);render()}else if(event.key==="Enter"&&filtered[index])choose(filtered[index]);else if(event.key==="Escape")closePalette() })
overlay.addEventListener("mousedown", event => { if(event.target===overlay)closePalette() })
