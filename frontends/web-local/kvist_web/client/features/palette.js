import { inboxItems, paletteItems } from "/assets/features/navigation.js";
import { hideOverlay, showOverlay } from "/assets/utils/ui.js";
const overlay = document.querySelector("#palette"), input = document.querySelector("#palette-input"), results = document.querySelector("#palette-results");
let items = [], index = 0, callback = () => { };
const commands = ["Change model", "Change reasoning", "Change role", "Providers and keys", "New session", "Switch session", "Switch workspace", "Switch server", "Connect server", "Add workspace", "Open files", "Open diff", "Open terminal", "Open activity", "Open context", "Open usage", "Show unread", "Stop agent", "Settings"];
export function openPalette(mode, onChoose) {
    callback = onChoose;
    input.value = "";
    input.placeholder = mode === "search" ? "Search sessions and workspaces…" : mode === "inbox" ? "Search unread and attention-needed sessions…" : "Type a command or search sessions…";
    items = mode === "search" ? paletteItems() : mode === "inbox" ? inboxItems() : [...commands.map(label => ({ type: "command", label, detail: "Command" })), ...paletteItems()];
    index = 0;
    render();
    showOverlay(overlay);
}
export function closePalette() { hideOverlay(overlay); }
function render() {
    const query = input.value.trim().toLowerCase(), filtered = items.filter(item => `${item.label} ${item.detail}`.toLowerCase().includes(query)).slice(0, 40);
    results.replaceChildren();
    index = Math.min(index, Math.max(0, filtered.length - 1));
    filtered.forEach((item, i) => { const button = document.createElement("button"); button.className = `palette-item ${i === index ? "active" : ""}`; const label = document.createElement("span"), detail = document.createElement("small"); label.textContent = item.label; detail.textContent = item.detail; button.append(label, detail); button.onclick = () => choose(item); results.append(button); });
    if (!filtered.length) {
        const empty = document.createElement("p");
        empty.className = "view-empty";
        empty.textContent = "No matching sessions or commands.";
        results.append(empty);
    }
    results._filtered = filtered;
    results.children[index]?.scrollIntoView({ block: "nearest" });
}
function choose(item) { closePalette(); callback(item); }
input.addEventListener("input", () => { index = 0; render(); });
input.addEventListener("keydown", event => { const filtered = results._filtered || []; if (event.key === "ArrowDown") {
    event.preventDefault();
    index = Math.min(index + 1, filtered.length - 1);
    render();
}
else if (event.key === "ArrowUp") {
    event.preventDefault();
    index = Math.max(0, index - 1);
    render();
}
else if (event.key === "Enter" && filtered[index]) {
    event.preventDefault();
    choose(filtered[index]);
}
else if (event.key === "Escape")
    closePalette(); });
overlay.addEventListener("mousedown", event => { if (event.target === overlay)
    closePalette(); });
document.querySelector("#close-palette").onclick = closePalette;
