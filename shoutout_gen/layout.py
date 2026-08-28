"""Pack measured shout-out boxes onto slides with zero overlap.

Pure geometry: input is a list of box sizes (EMU), output is a slide index and
top-left corner for each. No PPTX knowledge lives here, so the packer can be
property-tested exhaustively (see tests/test_layout.py).

Algorithm -- greedy fill on a raster, then spread:
  * The slide is a grid of small cells (0.05in). Placed boxes, inflated by the
    inter-box gap, mark cells occupied; the outer margin is pre-occupied.
  * Placement order is independent of click order. ``order="size"`` places
    largest-first (tall multi-line boxes are the hardest to fit, so this is
    the maximum-capacity order) -- but that order is readable straight off
    the finished slide, every long shout-out in the top rows.
    ``order="rows"`` cuts that same size-ordered sequence into row-sized
    chunks (by width) and deals the chunks in a seeded random order: rows stay
    height-homogeneous, which is what lets near-full weeks fit, but which rows
    land on top is random. pack_fewest_slides packs several such deals plus
    size order and keeps the least height-graded one that fits. Rejected on
    the way here: a fully randomised placement order (it does not remove the
    gradient, it inverts it -- short boxes backfill the upper gaps while tall
    latecomers sink); re-dealing the finished rows afterwards (once box widths
    vary, small boxes nest into gaps inside taller rows and there are no rows
    left to re-deal); and dealing whole height classes (too coarse -- the few
    tall rows of a light week can only go top, middle or bottom as a block).
  * For each box, every grid position where it fits is found in one shot with a
    2-D prefix sum over the occupancy grid. "compact" mode picks randomly among
    the fitting spots in the topmost row band (the corpus' rows-with-jitter
    look); "dense" mode takes the strict top-left spot (highest capacity).
  * When nothing fits, the box starts a new slide. The caller decides whether
    overflow is acceptable.
  * Finally each slide's layout is stretched uniformly so it spans the usable
    area instead of huddling top-left. Scaling positions by k >= 1 keeps every
    pairwise separation at least as large, so it cannot create an overlap.

Correctness does not depend on the choice heuristic: a box is only ever put on
cells the prefix sum proved were free, so the no-overlap guarantee holds no
matter how ugly the chosen spot is.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Google Slides 16:9 page, the size of every deck in the corpus.
SLIDE_WIDTH_EMU = 9_144_000
SLIDE_HEIGHT_EMU = 5_143_500

CELL_EMU = 45_720  # 0.05in raster; finer = prettier, slower
DEFAULT_GAP_EMU = 45_720  # 0.05in minimum clear space between any two boxes
DEFAULT_MARGIN_EMU = 91_440  # 0.10in keep-out at the slide edge
MAX_SPREAD = 3.0  # a two-shout-out week is spread, but not flung to opposite corners

MODES = ("compact", "dense")  # prettiest -> highest capacity
ORDERS = ("size", "rows")  # largest-first; row-sized chunks of it dealt in a random order
ROW_DRAWS = 32  # row deals tried besides size order; the least height-graded fit wins. Busy weeks fit
# few deals: 8 or 16 draws left GM 4 at -0.34, 32 puts every corpus week within +-0.05.


@dataclass(frozen=True)
class Box:
    """A box to place; ``key`` is whatever the caller uses to identify it."""

    key: int
    width_emu: int
    height_emu: int


@dataclass(frozen=True)
class Placement:
    """Where a box landed: 0-based slide index and top-left corner in EMU."""

    key: int
    slide: int
    x_emu: int
    y_emu: int


class BoxTooLargeError(ValueError):
    """A single box cannot fit on an empty slide even by itself."""


# --------------------------------------------------------------------------- #
# Public interface
# --------------------------------------------------------------------------- #
def pack_fewest_slides(boxes: list[Box], seed: int = 0, **kwargs) -> list[Placement]:
    """Prettiest layout that fits on one slide; failing that, the one needing fewest slides.

    The jittered look is what the club's slides have always had, but it wastes
    some space; when a busy week would spill onto an extra slide, the dense
    layout that keeps everything on one slide beats a pretty one that doesn't.

    Prettiest also means no visible size gradient: per mode, size order and
    ROW_DRAWS random row deals are all packed, and the least height-graded of
    those that fit on one slide wins. Size order is always among the
    candidates, so capacity is never worse than largest-first.
    """
    sizes = {b.key: b for b in boxes}
    best: list[Placement] | None = None
    for mode in MODES:
        candidates = [pack(boxes, seed=seed, mode=mode, order="size", **kwargs)]
        candidates += [
            pack(boxes, seed=seed + draw, mode=mode, order="rows", **kwargs) for draw in range(1, ROW_DRAWS + 1)
        ]
        fitting = [c for c in candidates if _slide_count(c) <= 1]
        if fitting:
            return min(fitting, key=lambda c: abs(height_gradient([(p.y_emu, sizes[p.key].height_emu) for p in c])))
        for candidate in candidates:
            if best is None or _slide_count(candidate) < _slide_count(best):
                best = candidate
    assert best is not None
    return best


def pack(
    boxes: list[Box],
    slide_width_emu: int = SLIDE_WIDTH_EMU,
    slide_height_emu: int = SLIDE_HEIGHT_EMU,
    gap_emu: int = DEFAULT_GAP_EMU,
    margin_emu: int = DEFAULT_MARGIN_EMU,
    seed: int = 0,
    mode: str = "compact",
    order: str = "size",
) -> list[Placement]:
    """Place every box; returns placements in the same order as ``boxes``.

    Raises BoxTooLargeError if a box is wider/taller than the usable slide area.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    if order not in ORDERS:
        raise ValueError(f"unknown order {order!r}; expected one of {ORDERS}")
    grid_w = slide_width_emu // CELL_EMU
    grid_h = slide_height_emu // CELL_EMU
    gap_cells = _ceil_div(gap_emu, CELL_EMU)
    margin_cells = _ceil_div(margin_emu, CELL_EMU)
    rng = np.random.default_rng(seed)

    placement_order = sorted(boxes, key=lambda b: (b.height_emu, b.width_emu), reverse=True)
    if order == "rows":
        placement_order = _row_deal(placement_order, rng, slide_width_emu - 2 * margin_emu, gap_emu)
    slides: list[_SlideGrid] = []
    placed: dict[int, Placement] = {}
    for box in placement_order:
        w = _ceil_div(box.width_emu, CELL_EMU)
        h = _ceil_div(box.height_emu, CELL_EMU)
        if w > grid_w - 2 * margin_cells or h > grid_h - 2 * margin_cells:
            raise BoxTooLargeError(f"box {box.key} ({box.width_emu}x{box.height_emu} EMU) exceeds the slide")
        for slide_idx, grid in enumerate(slides):
            spot = grid.find_spot(w, h, rng, mode)
            if spot is not None:
                break
        else:
            slides.append(_SlideGrid(grid_w, grid_h, margin_cells))
            slide_idx = len(slides) - 1
            spot = slides[-1].find_spot(w, h, rng, mode)
            assert spot is not None  # guaranteed by the size check above
        row, col = spot
        slides[slide_idx].occupy(row, col, w, h, gap_cells)
        placed[box.key] = Placement(box.key, slide_idx, col * CELL_EMU, row * CELL_EMU)

    sizes = {b.key: b for b in boxes}
    spread = _spread(list(placed.values()), sizes, slide_width_emu, slide_height_emu, margin_cells * CELL_EMU)
    return [spread[b.key] for b in boxes]


def overlapping_pairs(rects: list[tuple[int, int, int, int]]) -> list[tuple[int, int]]:
    """Indices of rectangle pairs (x, y, w, h) that intersect. Used by tests and the CLI's self-check."""
    hits = []
    for i in range(len(rects)):
        ax, ay, aw, ah = rects[i]
        for j in range(i + 1, len(rects)):
            bx, by, bw, bh = rects[j]
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                hits.append((i, j))
    return hits


def height_gradient(boxes: list[tuple[int, int]]) -> float:
    """Pearson correlation between a box's top edge and its height, over (y, height) pairs.

    This is the number behind "the long ones are all at the top": size-ordered
    layouts of busy weeks score about -0.8 (taller boxes have smaller y);
    0 means height says nothing about vertical position. Returns 0.0 when
    either value is constant (one box, a single row, uniform heights).
    """
    if len(boxes) < 2:
        return 0.0
    ys, hs = np.array(boxes, dtype=float).T
    if ys.std() == 0 or hs.std() == 0:
        return 0.0
    return float(np.corrcoef(ys, hs)[0, 1])


# --------------------------------------------------------------------------- #
# Private mechanics
# --------------------------------------------------------------------------- #
class _SlideGrid:
    """Occupancy raster for one slide plus the prefix-sum feasibility query."""

    def __init__(self, width: int, height: int, margin: int):
        self.w, self.h = width, height
        self.occ = np.zeros((height, width), dtype=np.int32)
        # Keep-out border: nothing may touch the slide edge.
        self.occ[:margin, :] = 1
        self.occ[-margin:, :] = 1
        self.occ[:, :margin] = 1
        self.occ[:, -margin:] = 1

    def find_spot(self, w: int, h: int, rng: np.random.Generator, mode: str) -> tuple[int, int] | None:
        """Top-left cell for a w x h box, or None if it fits nowhere."""
        blocked = _window_sums(_integral(self.occ), h, w)  # shape (H-h+1, W-w+1)
        feasible = blocked == 0
        if not feasible.any():
            return None
        rows_idx, cols_idx = np.indices(feasible.shape)
        if mode == "compact":
            # Random spot within one box-height of the topmost feasible row.
            top_row = int(rows_idx[feasible].min())
            score = rng.random(feasible.shape)
            score[rows_idx > top_row + h] = -np.inf
        else:
            # Dense: strict top-left fill (bottom-left-fill heuristic).
            score = -(rows_idx * feasible.shape[1] + cols_idx).astype(np.float64)
        score[~feasible] = -np.inf
        row, col = np.unravel_index(int(np.argmax(score)), score.shape)
        return int(row), int(col)

    def occupy(self, row: int, col: int, w: int, h: int, gap: int) -> None:
        """Mark the box plus its gap halo as taken (clipped to the grid)."""
        r0, r1 = max(0, row - gap), min(self.h, row + h + gap)
        c0, c1 = max(0, col - gap), min(self.w, col + w + gap)
        self.occ[r0:r1, c0:c1] = 1


def _row_deal(size_ordered: list[Box], rng: np.random.Generator, usable_width_emu: int, gap_emu: int) -> list[Box]:
    """Cut a size-ordered sequence into row-sized chunks and deal the chunks in a random order.

    A chunk is the run of consecutive boxes whose widths (plus gaps) fill one
    row, so it is height-homogeneous like the rows the packer would build
    from size order anyway. The compact packer fills the slide top-down, so
    the chunk order is the vertical order of the rows: randomising it is what
    stops "tallest rows on top" at the granularity of a single row, without
    giving up the rows near-full weeks need in order to fit.
    """
    chunks: list[list[Box]] = []
    current: list[Box] = []
    used = 0
    for box in size_ordered:
        if current and used + gap_emu + box.width_emu > usable_width_emu:
            chunks.append(current)
            current, used = [], 0
        used += box.width_emu + (gap_emu if current else 0)
        current.append(box)
    if current:
        chunks.append(current)
    return [box for idx in rng.permutation(len(chunks)) for box in chunks[idx]]


def _spread(
    placements: list[Placement], sizes: dict[int, Box], slide_w: int, slide_h: int, margin: int
) -> dict[int, Placement]:
    """Stretch each slide's layout to span the usable area (scale factors >= 1, capped).

    Positions are scaled, sizes are not, so the factor is the largest k for
    which every box's far edge, ``(x - min_x) * k + w``, still fits: the
    minimum over boxes of ``(usable - w) / (x - min_x)``. That is >= 1 because
    the packed layout already fit, and it puts the farthest edge exactly on the
    margin unless MAX_SPREAD caps it first.
    """
    out: dict[int, Placement] = {}
    for slide in {p.slide for p in placements}:
        on_slide = [p for p in placements if p.slide == slide]
        min_x = min(p.x_emu for p in on_slide)
        min_y = min(p.y_emu for p in on_slide)
        kx = _spread_factor([(p.x_emu - min_x, sizes[p.key].width_emu) for p in on_slide], slide_w - 2 * margin)
        ky = _spread_factor([(p.y_emu - min_y, sizes[p.key].height_emu) for p in on_slide], slide_h - 2 * margin)
        for p in on_slide:
            out[p.key] = Placement(
                p.key, slide, margin + int((p.x_emu - min_x) * kx), margin + int((p.y_emu - min_y) * ky)
            )
    return out


def _spread_factor(offsets_and_sizes: list[tuple[int, int]], usable: int) -> float:
    """Largest k (capped) such that offset * k + size <= usable for every box."""
    limits = [(usable - size) / offset for offset, size in offsets_and_sizes if offset > 0]
    return max(1.0, min([MAX_SPREAD, *limits]))


def _slide_count(placements: list[Placement]) -> int:
    return max((p.slide for p in placements), default=-1) + 1


def _integral(a: np.ndarray) -> np.ndarray:
    """Summed-area table with a zero row/col on top/left so window sums are one expression."""
    out = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.int64)
    out[1:, 1:] = a.cumsum(0).cumsum(1)
    return out


def _window_sums(integral: np.ndarray, h: int, w: int) -> np.ndarray:
    """Sum of every h x w window; result[r, c] is the window whose top-left is (r, c)."""
    return integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)
