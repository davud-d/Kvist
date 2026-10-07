// Motion in one place. Durations and the easing are the tokens of :root (--dur-fast, --dur-base, --ease), read
// when an animation starts. Only opacity and transform are animated from here. Nothing starts while the tab is
// hidden or the person asked for reduced motion; the caller then gets null and shows or hides at once.
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const token = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export const canAnimate = () => !reduced.matches && !document.hidden;

const timing = (duration, extra) => ({ duration: parseFloat(token(duration)), easing: token('--ease'), ...extra });

// Opacity from 0, with a rise of `rise` px when given. `translate` is for a node that already carries a
// transform of its own (a centred button): its offset is part of both keyframes.
export function appear(node, { rise = 0, duration = '--dur-base', translate = '' } = {}) {
    if (!canAnimate()) return null;
    const from = { opacity: 0 }, to = { opacity: 1 };
    if (rise) {
        from.transform = `${translate} translateY(${rise}px)`.trim();
        to.transform = `${translate} translateY(0)`.trim();
    }
    return node.animate([from, to], timing(duration));
}

// Opacity to 0, held at the end until the caller hides the node. Null when motion is off.
export function vanish(node, duration = '--dur-fast') {
    if (!canAnimate()) return null;
    return node.animate([{ opacity: 1 }, { opacity: 0 }], timing(duration, { fill: 'forwards' }));
}

// Slides a node in from `from` (a CSS translate such as 'translateX(-100%)') or out to it.
export function slide(node, from, { out = false, duration = out ? '--dur-fast' : '--dur-base' } = {}) {
    if (!canAnimate()) return null;
    const frames = [{ transform: from }, { transform: 'translateX(0)' }];
    return node.animate(out ? frames.reverse() : frames, timing(duration, out ? { fill: 'forwards' } : {}));
}
