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
/// The columns kept between the conversation and an open diff.
const DIFF_GAP: u16 = 3;
/// The narrowest conversation and diff that still read well.
const CONVERSATION_MIN: u16 = 40;
const DIFF_MIN: u16 = 40;

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
    /// A file's diff is open on the right; it takes the place of the browser
    /// and helpers cards and narrows the conversation.
    pub diff: bool,
    /// Rows the attached pictures add to the top of the input card.
    pub attach_rows: u16,
}

#[derive(Clone, Debug, Default)]
pub struct Layout {
    pub left: Option<Rect>,
    pub browser_card: Option<Rect>,
    pub thumb: Option<Rect>,
    pub helpers: Option<Rect>,
    pub diff: Option<Rect>,
    pub transcript: Rect,
    pub input: Rect,
    /// The rows at the top of `input` that hold the attached pictures.
    pub attach_rows: u16,
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
    let status_row = rows - 2;
    // The diff takes the right part of the space between the left card and the edge.
    let diff_rect = (shape.diff && side > 0).then(|| {
        let from = MARGIN_X + side + 3;
        let room = cols.saturating_sub(MARGIN_X + from);
        let width = (room * 55 / 100).min(room.saturating_sub(DIFF_GAP + CONVERSATION_MIN)).max(DIFF_MIN);
        Rect::new(cols - MARGIN_X - width, TOP, width, status_row.saturating_sub(TOP + 1))
    });
    let (center_w, center_x) = match diff_rect {
        Some(diff) => {
            let from = MARGIN_X + side + 3;
            let width = diff.x.saturating_sub(DIFF_GAP + from).min(104);
            (width, from)
        }
        None if side > 0 => {
            let width = (cols - 2 * (MARGIN_X + side + 3)).min(104);
            (width, (cols - width) / 2)
        }
        None => {
            let width = cols.saturating_sub(2 * MARGIN_X);
            (width, (cols - width) / 2)
        }
    };

    let input_h = shape.input_lines.clamp(1, 8) + 2 + shape.attach_rows;
    let bottom_y = status_row.saturating_sub(1 + input_h) as f32;
    let middle_y = (rows / 2).saturating_sub(input_h / 2 + 1) as f32;
    let input_y = (middle_y + (bottom_y - middle_y) * shape.input_at_bottom.clamp(0.0, 1.0)).round() as u16;
    let input = Rect::new(center_x, input_y, center_w, input_h);
    let transcript = Rect::new(center_x, TOP, center_w, input_y.saturating_sub(TOP + 1));

    let left = (side > 0 && shape.left_rows > 0)
        .then(|| Rect::new(MARGIN_X, TOP, side, (shape.left_rows + 2).min(status_row.saturating_sub(TOP + 1))));

    let right_x = cols.saturating_sub(MARGIN_X + side);
    let (browser_card, thumb) = if let (true, false, Some(aspect)) = (side > 0, diff_rect.is_some(), shape.browser) {
        let inner_w = side - 4;
        let thumb_h = ((inner_w as f32 * grid.cw as f32 / aspect) / grid.ch as f32).round().clamp(3.0, 16.0) as u16;
        let card = Rect::new(right_x, TOP, side, thumb_h + 3);
        (Some(card), Some(Rect::new(right_x + 2, TOP + 1, inner_w, thumb_h)))
    } else {
        (None, None)
    };
    let helpers = (side > 0 && diff_rect.is_none()).then(|| {
        let y = browser_card.map_or(TOP, |card| card.bottom() + 1);
        Rect::new(right_x, y, side, status_row.saturating_sub(y + 1))
    });

    Layout { left, browser_card, thumb, helpers, diff: diff_rect, transcript, input, attach_rows: shape.attach_rows, hints_row: input.bottom() + 1, status_row }
}

/// Rows the strip of attached pictures adds to the input card once open: the
/// pictures and a gap below them.
pub const STRIP_ROWS: u16 = 6;
const STRIP_GAP: u16 = 2;

/// Where the attached pictures sit over the input's text: left to right, each
/// as wide as its proportions ask, as many as fit. Also how many did not fit.
pub fn strip(input: Rect, grid: &Grid, aspects: &[f32]) -> (Vec<Rect>, usize) {
    let height = STRIP_ROWS - 1;
    let right = input.right().saturating_sub(3);
    let mut x = input.x + 3;
    let mut rects = Vec::new();
    for aspect in aspects {
        let width = ((height as f32 * grid.ch as f32 * aspect / grid.cw as f32).round() as u16).clamp(6, 24);
        if x + width > right {
            break;
        }
        rects.push(Rect::new(x, input.y + 1, width, height));
        x += width + STRIP_GAP;
    }
    let hidden = aspects.len() - rects.len();
    (rects, hidden)
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

/// The most suggestions the slash menu lists at once; the rest scroll.
pub const MENU_ROWS: usize = 8;

/// The slash menu above the input: one row per suggestion (at most
/// `MENU_ROWS`, fewer when the room above runs out) between two padding rows.
/// Zero height when there is no room for even one.
pub fn menu(input: Rect, count: usize) -> Rect {
    let room = input.y.saturating_sub(TOP + 1 + 2) as usize;
    let rows = count.min(MENU_ROWS).min(room);
    if rows == 0 {
        return Rect::new(input.x, input.y, input.width, 0);
    }
    let height = rows as u16 + 2;
    Rect::new(input.x, input.y - 1 - height, input.width, height)
}

/// The width of a dialog that has no diff to show.
pub const DIALOG_WIDTH: u16 = 66;
/// The width of an approval that shows a diff: room for two files side by side.
pub const DIFF_DIALOG_WIDTH: u16 = 124;

/// A dialog `width` columns wide and `height` rows tall (at most the window
/// allows), centred.
pub fn dialog(grid: &Grid, width: u16, height: u16) -> Rect {
    let w = width.min(grid.cols.saturating_sub(8));
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
        Shape { left_rows: 5, browser: Some(BROWSER_ASPECT), input_lines: 1, input_at_bottom: at_bottom, diff: false, attach_rows: 0 }
    }

    #[test]
    fn an_open_diff_takes_the_right_side_and_the_conversation_moves_aside() {
        for cols in [124, 160, 180, 240] {
            let mut open = shape(1.0);
            open.diff = true;
            let layout = compute(&grid(cols, 50), &open);
            let diff = layout.diff.unwrap();
            assert!(layout.left.unwrap().right() < layout.input.x, "{cols}");
            assert!(layout.input.right() < diff.x, "{cols}");
            assert!(diff.width >= DIFF_MIN && diff.right() == cols - MARGIN_X, "{cols}");
            assert!(layout.input.width >= CONVERSATION_MIN, "{cols}");
            assert!(layout.browser_card.is_none() && layout.helpers.is_none(), "{cols}");
        }
    }

    #[test]
    fn a_window_without_side_columns_has_no_diff_panel() {
        let mut open = shape(1.0);
        open.diff = true;
        assert!(compute(&grid(90, 30), &open).diff.is_none());
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
    fn attached_pictures_raise_the_card_and_keep_its_bottom_edge() {
        let plain = compute(&grid(180, 50), &shape(1.0)).input;
        let mut with = shape(1.0);
        with.attach_rows = STRIP_ROWS;
        let raised = compute(&grid(180, 50), &with);
        assert_eq!(raised.input.bottom(), plain.bottom());
        assert_eq!(raised.input.height, plain.height + STRIP_ROWS);
        assert_eq!(raised.attach_rows, STRIP_ROWS);
    }

    #[test]
    fn the_strip_lists_what_fits_and_counts_the_rest() {
        let input = Rect::new(20, 30, 60, 9);
        let (rects, hidden) = strip(input, &grid(180, 50), &[1.6; 5]);
        assert!(!rects.is_empty() && rects.len() + hidden == 5);
        assert!(rects.iter().all(|r| r.x >= input.x + 3 && r.right() <= input.right() - 3));
        assert!(rects.windows(2).all(|w| w[0].right() < w[1].x));
    }

    #[test]
    fn the_menu_sits_above_the_input_and_is_capped_by_rows_and_room() {
        let input = Rect::new(20, 30, 80, 3);
        let menu_rect = menu(input, 19);
        assert_eq!(menu_rect.height as usize, MENU_ROWS + 2);
        assert_eq!(menu_rect.bottom() + 1, input.y);
        assert_eq!((menu_rect.x, menu_rect.width), (input.x, input.width));
        assert_eq!(menu(input, 3).height, 5);
        assert!(menu(Rect::new(20, 5, 80, 3), 19).y >= TOP);
        assert_eq!(menu(Rect::new(20, 4, 80, 3), 19).height, 0);
    }

    #[test]
    fn a_dialog_is_as_tall_as_asked_but_fits_the_window() {
        assert_eq!(dialog(&grid(180, 50), DIALOG_WIDTH, 12).height, 12);
        assert_eq!(dialog(&grid(180, 20), DIALOG_WIDTH, 30).height, 16);
        assert_eq!(dialog(&grid(180, 50), 120, 12).width, 120);
        assert_eq!(dialog(&grid(90, 50), 120, 12).width, 82);
    }
}
