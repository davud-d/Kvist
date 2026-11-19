// Which shape the window has: a phone (one screen at a time), a tablet held upright (sessions beside the
// conversation, the desk as a drawer) or a tablet lying down (sessions, conversation and desk side by side).
// Height counts too: a phone on its side is wide but short and stays a phone.
const WIDE = matchMedia('(min-width: 1000px) and (min-height: 560px)');
const MEDIUM = matchMedia('(min-width: 700px) and (min-height: 560px)');

const read = () => (WIDE.matches ? 'wide' : MEDIUM.matches ? 'medium' : 'narrow');

// onChange(mode) runs when the window changes shape (rotation, Split View, Stage Manager), never at start.
export function createLayout(onChange) {
    const layout = { mode: read() };
    document.documentElement.dataset.layout = layout.mode;
    const changed = () => {
        const next = read();
        if (next === layout.mode) return;
        layout.mode = next;
        document.documentElement.dataset.layout = next;
        onChange(next);
    };
    WIDE.addEventListener('change', changed);
    MEDIUM.addEventListener('change', changed);
    return layout;
}
