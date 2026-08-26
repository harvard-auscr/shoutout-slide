"""Pack measured shout-out boxes onto slides with zero overlap.

Pure geometry: input is a list of box sizes (EMU), output is a slide index and
top-left corner for each. No PPTX knowledge lives here, so the packer can be
property-tested exhaustively (see tests/test_layout.py).

Algorithm -- greedy fill on a raster, then spread:
  * The slide is a grid of small cells (0.05in). Placed boxes, inflated by the
    inter-box gap, mark cells occupied; the outer margin is pre-occupied.
  * Placement order is independent of click order. ``order="size"`` places
    largest-first (tall multi-line boxes are the hardest to fit, so this is
    the maximum-capacity order); ``order="shuffle"`` places in a seeded random
    order, because size order is readable straight off the finished slide --
    every long shout-out clustered in the top rows. ``order="bands"`` packs in
    size order but then permutes the resulting row bands vertically: near-full
    weeks need size order's height-homogeneous rows to fit at all (a shuffled
    order mixes tall and short boxes into the same band and wastes the space
    above the short ones), and reordering whole bands keeps that capacity
    while destroying the tall-rows-first gradient.
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
ORDERS = ("size", "shuffle", "bands")  # highest capacity -> most mixed -> both
SHUFFLE_ATTEMPTS = 8  # seeded reorderings tried before conceding to size order


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

    Prettiest also means no visible size gradient: size-ordered compact
    packing reads as "long ones at the top" on the finished slide. Several
    fully shuffled attempts are tried first (the most organic mix); weeks too
    full for any shuffle fall back to size-ordered packing with its row bands
    permuted ("bands"), which fits exactly what v1.0.0 fit while still hiding
    the gradient.
    """
    for attempt in range(SHUFFLE_ATTEMPTS):
        candidate = pack(boxes, seed=seed + attempt, mode="compact", order="shuffle", **kwargs)
        if _slide_count(candidate) <= 1:
            return candidate
    best: list[Placement] | None = None
    for mode in MODES:
        candidate = pack(boxes, seed=seed, mode=mode, order="bands", **kwargs)
        if _slide_count(candidate) <= 1:
            return candidate
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

    if order == "shuffle":
        placement_order = [boxes[i] for i in rng.permutation(len(boxes))]
    else:  # "size" and "bands" both pack largest-first; "bands" reorders rows afterwards
        placement_order = sorted(boxes, key=lambda b: (b.height_emu, b.width_emu), reverse=True)
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
    raw = list(placed.values())
    if order == "bands":
        raw = _shuffle_bands(raw, sizes, rng, gap_emu, slide_height_emu - margin_cells * CELL_EMU)
    spread = _spread(raw, sizes, slide_width_emu, slide_height_emu, margin_cells * CELL_EMU)
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


def _shuffle_bands(
    placements: list[Placement],
    sizes: dict[int, Box],
    rng: np.random.Generator,
    gap_emu: int,
    max_bottom_emu: int,
) -> list[Placement]:
    """Permute each slide's row bands vertically; x positions never move.

    A band is a maximal group of boxes whose y-intervals overlap transitively,
    so distinct bands are disjoint horizontal strips. Restacked bands are
    separated by exactly ``gap_emu``, which preserves the packer's clearance
    guarantee: any box in a lower band starts at least ``gap_emu`` below every
    box in the band above. If the restack would poke past ``max_bottom_emu``
    (possible only when the original layout interleaved x-disjoint bands more
    tightly than the gap), that slide keeps its original order -- correctness
    over looks.
    """
    out: list[Placement] = []
    for slide in sorted({p.slide for p in placements}):
        group = sorted((p for p in placements if p.slide == slide), key=lambda p: p.y_emu)
        # Merge overlapping y-intervals into bands: (top, bottom, members).
        bands: list[tuple[int, int, list[Placement]]] = []
        for p in group:
            bottom = p.y_emu + sizes[p.key].height_emu
            if bands and p.y_emu < bands[-1][1]:
                bands[-1] = (bands[-1][0], max(bands[-1][1], bottom), bands[-1][2] + [p])
            else:
                bands.append((p.y_emu, bottom, [p]))
        restacked_bottom = bands[0][0] + sum(b[1] - b[0] for b in bands) + gap_emu * (len(bands) - 1)
        if restacked_bottom > max_bottom_emu:
            out.extend(group)
            continue
        y = bands[0][0]  # keep the original top offset; _spread stretches the rest
        for idx in rng.permutation(len(bands)):
            top, bottom, members = bands[idx]
            out.extend(Placement(p.key, slide, p.x_emu, y + (p.y_emu - top)) for p in members)
            y += (bottom - top) + gap_emu
    return out


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
