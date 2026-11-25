// Vendored from cloud/workspace/js/folds.js (the web workspace). Keep in step by hand.
// When a group of calls folds, ported from Kvist Glass (frontends/tui/glass/src/calls.rs) with its timings and
// curve: a group stays open while it runs; once it is finished and something follows it (or the turn ends), it
// holds for two seconds and then folds over 320 ms, easing out. A group that gets another call after that opens
// again from wherever it stood, with no jump; what the person opens or folds by hand wins over all of it.
// Pure: the time is passed in, so the tests can drive it.

export const HOLD_MS = 2000;
export const FOLD_MS = 320;

/** Glass's Ease::OutCubic, and the same curve for CSS. */
export const outCubic = (t) => 1 - (1 - t) ** 3;
export const OUT_CUBIC_CSS = "cubic-bezier(0.33, 1, 0.68, 1)";

// A tween between two openness values; interrupted half-way, the next one starts from where this one stood.
const flip = (from, to, at) => ({ from, to, at });
const flipValue = (f, now) => f.from + (f.to - f.from) * outCubic(Math.min(1, (now - f.at) / FOLD_MS));
const flipRunning = (f, now) => now - f.at < FOLD_MS;

const callsOf = (block) => block.items.filter((item) => item.kind === "call");

export class Folds {
  constructor() {
    /** When each group was passed by something later (its hold began), and how many calls it had then. */
    this.settled = new Map();
    /** Groups that were folding or folded when another call joined them: they open again, not with a jump. */
    this.reopened = new Map();
    /** What the person opened or folded by hand. */
    this.flips = new Map();
  }

  /**
   * Starts the hold of every group that is finished and followed by something (or by the end of the turn).
   * `quiet`: the hold is taken as long over, for groups that were already done when the page opened the task,
   * so an old conversation does not open everything and fold it again.
   */
  settle(blocks, busy, now, { quiet = false } = {}) {
    blocks.forEach((block, index) => {
      if (block.kind !== "work") return;
      const count = callsOf(block).length;
      const had = this.settled.get(block.id);
      if (had) {
        if (had.count === count) return;
        const from = this.openness(block.id, now);
        this.settled.delete(block.id);
        this.reopened.set(block.id, flip(from, 1, now));
      }
      const done = callsOf(block).every((call) => call.phase !== "run");
      if (done && (index < blocks.length - 1 || !busy)) {
        this.reopened.delete(block.id);
        this.settled.set(block.id, { at: quiet ? now - HOLD_MS - FOLD_MS : now, count });
      }
    });
  }

  /** 1 when the group's calls are fully shown, 0 when it is folded. */
  openness(id, now) {
    const manual = this.flips.get(id);
    if (manual) return flipValue(manual, now);
    const settled = this.settled.get(id);
    if (!settled) {
      const reopened = this.reopened.get(id);
      return reopened ? flipValue(reopened, now) : 1;
    }
    const t = (now - settled.at - HOLD_MS) / FOLD_MS;
    return 1 - outCubic(Math.min(1, Math.max(0, t)));
  }

  toggle(id, now) {
    const from = this.openness(id, now);
    this.flips.set(id, flip(from, from > 0.5 ? 0 : 1, now));
  }

  /** True while a hold counts down or something folds, so the page keeps redrawing. */
  moving(now) {
    for (const { at } of this.settled.values()) if (now - at < HOLD_MS + FOLD_MS) return true;
    for (const f of [...this.flips.values(), ...this.reopened.values()]) if (flipRunning(f, now)) return true;
    return false;
  }
}
