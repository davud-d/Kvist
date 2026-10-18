// Where everything sits, in cells. Side columns hold the cards (files and
// helpers on the left, the browser and the helpers panel on the right); the
// middle column holds the conversation and the input. Below 124 columns the
// side columns disappear and the middle takes the width.

use ratatui::layout::Rect;

pub const MARGIN_X: u16 = 2;
pub const TOP: u16 = 1;
/// The browser's proportions until its first frame arrives (the browser
/// skill's default viewport is 16:10).
pub const BROWSER_ASPECT: f32 = 1280.0 / 800.0;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Grid {
    pub cols: u16,
    pub rows: u16,
    /// Cell size in pixels.
    pub cw: u32,
    pub ch: u32,
}

impl Grid {
    pub fn px(&self, rect: Rect) -> (f32, f32, f32, f32) {
        (
            rect.x as f32 * self.cw as f32,
            rect.y as f32 * self.ch as f32,
            rect.width as f32 * self.cw as f32,
            rect.height as f32 * self.ch as f32,
        )
    }

    pub fn full(&self) -> Rect {
        Rect::new(0, 0, self.cols, self.rows)
    }
}

pub struct Shape {
    /// Content rows of the left card; 0 hides it.
    pub left_rows: u16,
    /// Width / height of the browser frame; `None` when there is no browser.
    pub browser: Option<f32>,
    pub input_lines: u16,
    /// 0 = input in the middle of the screen (empty session), 1 = at the bottom.
    pub input_at_bottom: f32,
}

#[derive(Clone, Debug, Default)]
pub struct Layout {
    pub left: Option<Rect>,
    pub browser_card: Option<Rect>,
    pub thumb: Option<Rect>,
    pub helpers: Option<Rect>,
    pub transcript: Rect,
    pub input: Rect,
    pub hints_row: u16,
    pub status_row: u16,
}

pub fn compute(grid: &Grid, shape: &Shape) -> Layout {
    let (cols, rows) = (grid.cols, grid.rows.max(8));
    // The middle column must keep at least ~50 columns for the conversation.
    let side = match cols {
        150.. => 40,
        124..=149 => 32,
        _ => 0,
    };
    let center_w = if side > 0 { (cols - 2 * (MARGIN_X + side + 3)).min(104) } else { cols.saturating_sub(2 * MARGIN_X) };
    let center_x = (cols - center_w) / 2;
    let status_row = rows - 2;

    let input_h = shape.input_lines.clamp(1, 8) + 2;
    let bottom_y = status_row.saturating_sub(1 + input_h) as f32;
    let middle_y = (rows / 2).saturating_sub(input_h / 2 + 1) as f32;
    let input_y = (middle_y + (bottom_y - middle_y) * shape.input_at_bottom.clamp(0.0, 1.0)).round() as u16;
    let input = Rect::new(center_x, input_y, center_w, input_h);
    let transcript = Rect::new(center_x, TOP, center_w, input_y.saturating_sub(TOP + 1));

    let left = (side > 0 && shape.left_rows > 0)
        .then(|| Rect::new(MARGIN_X, TOP, side, (shape.left_rows + 2).min(status_row.saturating_sub(TOP + 1))));

    let right_x = cols.saturating_sub(MARGIN_X + side);
    let (browser_card, thumb) = if let (true, Some(aspect)) = (side > 0, shape.browser) {
        let inner_w = side - 4;
        let thumb_h = ((inner_w as f32 * grid.cw as f32 / aspect) / grid.ch as f32).round().clamp(3.0, 16.0) as u16;
        let card = Rect::new(right_x, TOP, side, thumb_h + 3);
        (Some(card), Some(Rect::new(right_x + 2, TOP + 1, inner_w, thumb_h)))
    } else {
        (None, None)
    };
    let helpers = (side > 0).then(|| {
        let y = browser_card.map_or(TOP, |card| card.bottom() + 1);
        Rect::new(right_x, y, side, status_row.saturating_sub(y + 1))
    });

    Layout { left, browser_card, thumb, helpers, transcript, input, hints_row: input.bottom() + 1, status_row }
}

/// The opened browser: as large as fits in 82% × 78% of the window, with the
/// screenshot's own proportions, plus one row above it for its title.
pub fn browser_full(grid: &Grid, aspect: f32) -> Rect {
    let avail_w = grid.cols as f32 * 0.82 * grid.cw as f32;
    let avail_h = (grid.rows as f32 * 0.78 - 1.0) * grid.ch as f32;
    let width = avail_w.min(avail_h * aspect);
    let cols = (width / grid.cw as f32).round() as u16;
    let rows = (width / aspect / grid.ch as f32).round() as u16;
    let x = (grid.cols.saturating_sub(cols)) / 2;
    let y = (grid.rows.saturating_sub(rows)) / 2 + 1;
    Rect::new(x, y, cols, rows)
}

/// A dialog `height` rows tall (at most the window allows), centred.
pub fn dialog(grid: &Grid, height: u16) -> Rect {
    let w = 66.min(grid.cols.saturating_sub(8));
    let h = height.min(grid.rows.saturating_sub(4));
    Rect::new((grid.cols - w) / 2, (grid.rows - h) / 2, w, h)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn grid(cols: u16, rows: u16) -> Grid {
        Grid { cols, rows, cw: 10, ch: 21 }
    }

    fn shape(at_bottom: f32) -> Shape {
        Shape { left_rows: 5, browser: Some(BROWSER_ASPECT), input_lines: 1, input_at_bottom: at_bottom }
    }

    #[test]
    fn wide_windows_get_side_columns_that_do_not_overlap_the_middle() {
        let layout = compute(&grid(180, 50), &shape(1.0));
        let left = layout.left.unwrap();
        let card = layout.browser_card.unwrap();
        assert!(left.right() < layout.input.x);
        assert!(layout.input.right() < card.x);
        assert!(layout.helpers.unwrap().y > card.bottom());
    }

    #[test]
    fn narrow_windows_drop_the_side_columns() {
        let layout = compute(&grid(90, 30), &shape(1.0));
        assert!(layout.left.is_none() && layout.browser_card.is_none() && layout.helpers.is_none());
        assert_eq!(layout.input.width, 90 - 2 * MARGIN_X);
    }

    #[test]
    fn the_input_moves_from_the_middle_to_above_the_status_row() {
        let middle = compute(&grid(180, 50), &shape(0.0)).input;
        let bottom = compute(&grid(180, 50), &shape(1.0));
        assert!(middle.y < bottom.input.y);
        assert_eq!(bottom.input.bottom() + 1, bottom.status_row);
    }

    #[test]
    fn the_opened_browser_keeps_its_proportions_and_fits() {
        let g = grid(200, 55);
        for wanted in [BROWSER_ASPECT, 4.0 / 3.0, 16.0 / 9.0] {
            let r = browser_full(&g, wanted);
            let aspect = (r.width as f32 * 10.0) / (r.height as f32 * 21.0);
            assert!((aspect - wanted).abs() < 0.08, "{aspect} for {wanted}");
            assert!(r.right() <= g.cols && r.bottom() <= g.rows && r.y >= 1);
        }
    }

    #[test]
    fn a_dialog_is_as_tall_as_asked_but_fits_the_window() {
        assert_eq!(dialog(&grid(180, 50), 12).height, 12);
        assert_eq!(dialog(&grid(180, 20), 30).height, 16);
    }
}
