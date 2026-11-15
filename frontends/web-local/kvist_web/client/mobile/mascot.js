// The squirrel: a 32x32 sprite in three frames. At rest it blinks now and then; at work its tail wags;
// when it waits for the person it hops. With reduced motion it keeps the first frame.
const BASE = '/assets/mobile/sprites/';
const WAG = [1, 2, 3, 2];
const WAG_MS = 170;
const BLINK_EVERY_MS = 3600;
const BLINK_MS = 160;

export function createMascot(image, { winter }) {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const prefix = winter ? 'squirrel-winter-' : 'squirrel-';
    let mood = 'idle', tick = 0, timer = 0;

    const show = frame => { image.src = `${BASE}${prefix}${frame}.png`; };
    const stop = () => { clearInterval(timer); timer = 0; };
    const start = (every, step) => { stop(); timer = setInterval(step, every); };

    function apply(next) {
        mood = next;
        image.dataset.mood = next;
        stop();
        show(1);
        if (reduced) return;
        if (next === 'work') start(WAG_MS, () => show(WAG[tick++ % WAG.length]));
        else start(BLINK_EVERY_MS, () => { show(3); setTimeout(() => show(1), BLINK_MS); });
    }

    apply('idle');
    return {
        setMood(next) {
            if (next !== mood) apply(next);
        },
    };
}
