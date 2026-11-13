// Time-based tweens. Every moving thing on screen is a value that eases from
// where it is now to a target, so an animation interrupted half-way turns
// around smoothly instead of jumping.

use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Ease {
    OutCubic,
    /// Overshoots slightly before settling: the "jumps out" motion.
    OutBack,
    /// Slow start, fast middle, slow end, the same in both directions: the motion of a
    /// card unfolding out of the ball and folding back (see `FOLD_ENDS`).
    Fold,
}

/// The shape of `Ease::Fold`, the one place to tune it: each end of the motion takes
/// this share of the time (first number) and covers this share of the distance (second);
/// the fast middle takes the rest of both. With (0.34, 0.18) a motion spends a third of
/// its time creeping off, another third creeping in, and covers two thirds of the way in
/// the middle third. Smaller distance shares make the ends slower and the middle faster.
pub const FOLD_ENDS: (f32, f32) = (0.34, 0.18);

/// Three pieces whose speeds meet at the joins: a power curve that speeds up into a
/// straight line, then a power curve that slows down from it. `start` and `middle` are
/// (share of the time, share of the distance); the end is what is left of both.
fn three_phases(t: f32, start: (f32, f32), middle: (f32, f32)) -> f32 {
    let (t_start, d_start) = start;
    let (t_middle, d_middle) = middle;
    let (t_end, d_end) = (1.0 - t_start - t_middle, 1.0 - d_start - d_middle);
    let speed = d_middle / t_middle;
    if t < t_start {
        let power = speed * t_start / d_start;
        return d_start * (t / t_start).powf(power);
    }
    if t < t_start + t_middle {
        return d_start + speed * (t - t_start);
    }
    let power = speed * t_end / d_end;
    let u = (t - t_start - t_middle) / t_end;
    d_start + d_middle + d_end * (1.0 - (1.0 - u).powf(power))
}

impl Ease {
    pub fn apply(self, t: f32) -> f32 {
        let t = t.clamp(0.0, 1.0);
        match self {
            Ease::OutCubic => 1.0 - (1.0 - t).powi(3),
            Ease::OutBack => {
                let c1 = 1.45;
                let c3 = c1 + 1.0;
                1.0 + c3 * (t - 1.0).powi(3) + c1 * (t - 1.0).powi(2)
            }
            Ease::Fold => {
                let (end_time, end_distance) = FOLD_ENDS;
                three_phases(t, FOLD_ENDS, (1.0 - 2.0 * end_time, 1.0 - 2.0 * end_distance))
            }
        }
    }
}

/// Frames per second when `KVIST_GLASS_FPS` is not set. The terminal shows
/// at most its monitor's refresh rate (kitty: `sync_to_monitor`), and at most
/// 100 with kitty's default `repaint_delay` of 10 ms.
pub const DEFAULT_FPS: u32 = 60;
pub const FPS_RANGE: std::ops::RangeInclusive<u32> = 20..=240;

/// Reads the frame rate from `KVIST_GLASS_FPS`; `Err` carries the bad value.
pub fn frame_rate(value: Option<&str>) -> Result<u32, String> {
    let Some(raw) = value.map(str::trim).filter(|v| !v.is_empty()) else {
        return Ok(DEFAULT_FPS);
    };
    raw.parse::<u32>().ok().filter(|fps| FPS_RANGE.contains(fps)).ok_or_else(|| raw.to_string())
}

/// When the next frame is due: one period after the previous one, so frames
/// keep an even beat whatever drawing took. A loop that fell more than a
/// frame behind starts a new beat now instead of rushing to catch up.
pub fn next_frame(due: Instant, now: Instant, period: Duration) -> Instant {
    let next = due + period;
    if next + period < now { now } else { next }
}

#[derive(Clone, Copy, Debug)]
pub struct Tween {
    from: f32,
    to: f32,
    start: Instant,
    duration: Duration,
    ease: Ease,
}

impl Tween {
    pub fn settled(value: f32) -> Self {
        Tween { from: value, to: value, start: Instant::now(), duration: Duration::ZERO, ease: Ease::OutCubic }
    }

    pub fn value(&self, now: Instant) -> f32 {
        if self.duration.is_zero() {
            return self.to;
        }
        let t = now.saturating_duration_since(self.start).as_secs_f32() / self.duration.as_secs_f32();
        if t >= 1.0 {
            return self.to;
        }
        self.from + (self.to - self.from) * self.ease.apply(t)
    }

    /// Linear progress of the running animation, 0..1 (1 when settled).
    pub fn progress(&self, now: Instant) -> f32 {
        if self.duration.is_zero() {
            return 1.0;
        }
        (now.saturating_duration_since(self.start).as_secs_f32() / self.duration.as_secs_f32()).min(1.0)
    }

    pub fn target(&self) -> f32 {
        self.to
    }

    pub fn running(&self, now: Instant) -> bool {
        self.progress(now) < 1.0
    }

    pub fn go(&mut self, to: f32, now: Instant, ms: u64, ease: Ease) {
        if (self.to - to).abs() < f32::EPSILON {
            return;
        }
        self.from = self.value(now);
        self.to = to;
        self.start = now;
        self.duration = Duration::from_millis(ms);
        self.ease = ease;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_frame_rate_defaults_and_rejects_nonsense() {
        assert_eq!(frame_rate(None), Ok(DEFAULT_FPS));
        assert_eq!(frame_rate(Some(" 100 ")), Ok(100));
        assert_eq!(frame_rate(Some("1000")), Err("1000".to_string()));
        assert_eq!(frame_rate(Some("fast")), Err("fast".to_string()));
    }

    #[test]
    fn frames_keep_their_beat_and_a_late_loop_restarts_it() {
        let start = Instant::now();
        let period = Duration::from_millis(10);
        assert_eq!(next_frame(start, start + Duration::from_millis(4), period), start + period);
        let late = start + Duration::from_millis(35);
        assert_eq!(next_frame(start, late, period), late);
    }

    #[test]
    fn fold_creeps_at_both_ends_and_runs_in_the_middle() {
        let at = |share: f32| Ease::Fold.apply(share);
        assert!(at(0.10) < 0.01, "the start is slow");
        assert!(at(0.90) > 0.99, "the end is slow");
        assert!((at(0.5) - 0.5).abs() < 1e-5, "it is half way half way through");
        // Speed in the middle is the distance the middle covers over the time it takes.
        let speed = (at(0.51) - at(0.49)) / 0.02;
        assert!((speed - (1.0 - 2.0 * FOLD_ENDS.1) / (1.0 - 2.0 * FOLD_ENDS.0)).abs() < 1e-3, "{speed}");
    }

    #[test]
    fn fold_is_the_same_curve_forwards_and_back() {
        for step in 0..=100 {
            let t = step as f32 / 100.0;
            assert!((Ease::Fold.apply(t) + Ease::Fold.apply(1.0 - t) - 1.0).abs() < 1e-5, "t {t}");
        }
    }

    #[test]
    fn fold_never_moves_backwards_or_jumps() {
        let mut previous = 0.0;
        for step in 1..=600 {
            let value = Ease::Fold.apply(step as f32 / 600.0);
            assert!(value >= previous);
            assert!(value - previous < 0.01, "jump at step {step}");
            previous = value;
        }
    }

    #[test]
    fn easings_start_at_zero_and_end_at_one() {
        for ease in [Ease::OutCubic, Ease::OutBack, Ease::Fold] {
            assert!(ease.apply(0.0).abs() < 1e-6, "{ease:?}");
            assert!((ease.apply(1.0) - 1.0).abs() < 1e-6, "{ease:?}");
        }
    }

    #[test]
    fn out_back_overshoots() {
        assert!(Ease::OutBack.apply(0.7) > 1.0);
    }

    #[test]
    fn a_retargeted_tween_continues_from_where_it_was() {
        let start = Instant::now();
        let mut tween = Tween::settled(0.0);
        tween.go(1.0, start, 100, Ease::OutCubic);
        let midway = start + Duration::from_millis(50);
        let before = tween.value(midway);
        tween.go(0.0, midway, 100, Ease::OutCubic);
        assert!((tween.value(midway) - before).abs() < 1e-6);
        assert_eq!(tween.value(midway + Duration::from_millis(200)), 0.0);
    }
}
