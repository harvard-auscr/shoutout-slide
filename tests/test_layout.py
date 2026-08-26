"""Packer guarantees: no overlap (with gap), inside the margins, order preserved."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shoutout_gen.layout import (  # noqa: E402
    DEFAULT_GAP_EMU,
    DEFAULT_MARGIN_EMU,
    MAX_SPREAD,
    MODES,
    SLIDE_HEIGHT_EMU,
    SLIDE_WIDTH_EMU,
    Box,
    BoxTooLargeError,
    overlapping_pairs,
    pack,
    pack_fewest_slides,
)


def _random_boxes(n: int, seed: int) -> list[Box]:
    rng = random.Random(seed)
    return [Box(i, rng.randint(300_000, 3_000_000), rng.choice([187_000, 374_000, 561_000])) for i in range(n)]


def _check_invariants(boxes, placements):
    assert [p.key for p in placements] == [b.key for b in boxes]
    by_slide: dict[int, list] = {}
    for b, p in zip(boxes, placements):
        assert p.x_emu >= DEFAULT_MARGIN_EMU and p.y_emu >= DEFAULT_MARGIN_EMU
        assert p.x_emu + b.width_emu <= SLIDE_WIDTH_EMU - DEFAULT_MARGIN_EMU
        assert p.y_emu + b.height_emu <= SLIDE_HEIGHT_EMU - DEFAULT_MARGIN_EMU
        # Inflate by the gap on every side: even inflated boxes must not touch.
        g = DEFAULT_GAP_EMU // 2
        by_slide.setdefault(p.slide, []).append((p.x_emu - g, p.y_emu - g, b.width_emu + 2 * g, b.height_emu + 2 * g))
    for rects in by_slide.values():
        assert overlapping_pairs(rects) == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("seed", range(5))
def test_random_boxes_never_overlap_or_leave_slide(mode, seed):
    boxes = _random_boxes(40, seed)
    _check_invariants(boxes, pack(boxes, mode=mode, seed=seed))


@pytest.mark.parametrize("mode", MODES)
def test_half_full_slide_is_spread_to_both_far_edges(mode):
    # 20 boxes that pack into ~7 rows of 3: needs < MAX_SPREAD stretch on both axes,
    # so after spreading the farthest edges must sit on the margin (within int rounding).
    boxes = [Box(i, 2_500_000, 561_000) for i in range(20)]
    placements = pack(boxes, mode=mode)
    _check_invariants(boxes, placements)
    right = max(p.x_emu + b.width_emu for p, b in zip(placements, boxes))
    bottom = max(p.y_emu + b.height_emu for p, b in zip(placements, boxes))
    assert right >= SLIDE_WIDTH_EMU - DEFAULT_MARGIN_EMU - 10
    assert bottom >= SLIDE_HEIGHT_EMU - DEFAULT_MARGIN_EMU - 10


def test_spread_is_capped_for_tiny_inputs():
    # Dense packs these two adjacently at top-left; spreading may stretch that
    # by at most MAX_SPREAD so they aren't flung to opposite corners.
    boxes = [Box(0, 500_000, 187_000), Box(1, 500_000, 187_000)]
    placements = pack(boxes, mode="dense")
    _check_invariants(boxes, placements)
    gap_between = placements[1].x_emu - (placements[0].x_emu + 500_000)
    assert 0 < gap_between <= MAX_SPREAD * (500_000 + 2 * DEFAULT_GAP_EMU)


def test_single_row_cannot_spread_vertically():
    boxes = [Box(0, 1_000_000, 187_000), Box(1, 1_000_000, 187_000)]
    placements = pack(boxes, mode="dense")
    assert placements[0].y_emu == placements[1].y_emu == DEFAULT_MARGIN_EMU


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        pack(_random_boxes(3, 0), mode="diagonal")


def test_deterministic_for_same_seed():
    boxes = _random_boxes(25, 7)
    assert pack(boxes, seed=3) == pack(boxes, seed=3)
    assert pack(boxes, seed=3) != pack(boxes, seed=4)


def test_overflows_to_more_slides_when_full():
    boxes = [Box(i, 2_900_000, 561_000) for i in range(60)]
    placements = pack(boxes, mode="dense")
    _check_invariants(boxes, placements)
    assert max(p.slide for p in placements) + 1 >= 3  # 60 boxes of that size need >= 3 slides


def test_dense_never_needs_more_slides_than_compact():
    for seed in range(3):
        boxes = _random_boxes(45, seed)
        compact = max(p.slide for p in pack(boxes, mode="compact", seed=seed))
        dense = max(p.slide for p in pack(boxes, mode="dense", seed=seed))
        assert dense <= compact


def test_pack_fewest_slides_prefers_compact_when_it_fits():
    boxes = _random_boxes(6, 1)
    assert pack_fewest_slides(boxes) == pack(boxes, mode="compact")


def test_pack_fewest_slides_never_worse_than_any_single_mode():
    boxes = _random_boxes(70, 2)  # too many for one slide in any mode
    best = max(p.slide for p in pack_fewest_slides(boxes))
    assert best <= min(max(p.slide for p in pack(boxes, mode=m)) for m in MODES)


def test_box_too_large_raises():
    with pytest.raises(BoxTooLargeError):
        pack([Box(0, SLIDE_WIDTH_EMU, 100_000)])
    with pytest.raises(BoxTooLargeError):
        pack([Box(0, 100_000, SLIDE_HEIGHT_EMU)])


def test_empty_input():
    assert pack([]) == []


def test_overlapping_pairs_detects_touching_edges_as_clear():
    assert overlapping_pairs([(0, 0, 10, 10), (10, 0, 10, 10)]) == []
    assert overlapping_pairs([(0, 0, 10, 10), (9, 9, 10, 10)]) == [(0, 1)]
