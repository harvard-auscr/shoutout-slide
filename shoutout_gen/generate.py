"""Orchestrate spreadsheet -> measured boxes -> packed slides -> PPTX.

This is the one place the pipeline's pieces meet; the CLI in
``generate_shoutouts.py`` is a thin wrapper so tests can call ``generate``
directly with a list of strings.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .deck import TEMPLATE_PATH, PlacedShoutout, slide_size, write_deck
from .layout import Box, Placement, overlapping_pairs, pack, pack_fewest_slides
from .metrics import TextMeasurer

LAYOUT_MODES = ("auto", "compact", "dense")

# Sizes tried, in order, when the font size is left on "auto": the three sizes
# the club's own slides have used. Stops at the first that fits one slide.
AUTO_FONT_SIZES_PT = (12.0, 11.0, 10.0)

# Wrap width drawn per shout-out, uniformly from this range (EMU), seeded. The
# club's original boxes were all exactly 3.0M EMU wide and the packer fills a
# row with three of them, so equal widths read as strict columns once the rows
# are re-dealt (wet test C2). The range was chosen on the corpus: the same
# font ladder as a fixed width on every week, height gradient within +-0.16,
# and no week pushed into the dense layout (whose interlocked rows cannot be
# re-dealt).
BOX_WIDTH_RANGE_EMU = (2_200_000, 3_800_000)


@dataclass(frozen=True)
class GenerateResult:
    """What was produced, for the CLI summary and for tests."""

    output: Path
    placed: tuple[PlacedShoutout, ...]  # click order
    slide_count: int
    per_slide_counts: tuple[int, ...]
    font_size_pt: float


def output_name_for(gm_number: int, date: str) -> str:
    """Same convention as the scraped decks: "GM #25 9.10---shoutouts.pptx"."""
    return f"GM #{gm_number} {date}---shoutouts.pptx"


def generate(
    texts: list[str],
    out_path: Path,
    font_size_pt: float | None = None,
    layout_mode: str = "auto",
    seed: int = 0,
    template_path: Path = TEMPLATE_PATH,
) -> GenerateResult:
    """Lay out ``texts`` (already in click order) and write the deck.

    ``font_size_pt``: a fixed size, or None to walk AUTO_FONT_SIZES_PT until
    everything fits on one slide (the smallest size is used if nothing does).
    ``layout_mode``: "auto" = prettiest heuristic that fits on one slide,
    else the one needing fewest slides; "compact"/"dense" force one.
    """
    if layout_mode not in LAYOUT_MODES:
        raise ValueError(f"layout_mode must be one of {LAYOUT_MODES}, got {layout_mode!r}")
    if not texts:
        raise ValueError("no shout-outs to place")

    width, height = slide_size(template_path)
    sizes = (font_size_pt,) if font_size_pt is not None else AUTO_FONT_SIZES_PT
    box_widths = _box_widths(len(texts), seed)
    for size in sizes:
        placed = _layout(texts, box_widths, size, layout_mode, seed, width, height)
        if max(p.slide for p in placed) == 0:
            break  # fits on one slide -- done; otherwise try the next size, keeping the last attempt
    _assert_no_overlap(placed)  # belt and braces: the packer's core guarantee, re-checked on the output
    write_deck(list(placed), out_path, size, template_path)

    slide_count = max(p.slide for p in placed) + 1
    counts = tuple(sum(1 for p in placed if p.slide == s) for s in range(slide_count))
    return GenerateResult(out_path, placed, slide_count, counts, size)


def _box_widths(count: int, seed: int) -> list[int]:
    """One wrap width per shout-out, drawn once so every font size in the ladder
    is measured against the same box shape (and the same seed gives the same deck)."""
    lo, hi = BOX_WIDTH_RANGE_EMU
    return [int(w) for w in np.random.default_rng(seed).integers(lo, hi + 1, size=count)]


def _layout(
    texts: list[str], box_widths: list[int], font_size_pt: float, layout_mode: str, seed: int, width: int, height: int
) -> tuple[PlacedShoutout, ...]:
    """Measure and pack at one font size; returns placed shout-outs in click order."""
    measurer = TextMeasurer(font_size_pt)
    measured = [measurer.measure(text, box_width) for text, box_width in zip(texts, box_widths)]
    boxes = [Box(i, m.width_emu, m.height_emu) for i, m in enumerate(measured)]
    kwargs = dict(slide_width_emu=width, slide_height_emu=height, seed=seed)
    placements: list[Placement] = (
        pack_fewest_slides(boxes, **kwargs) if layout_mode == "auto" else pack(boxes, mode=layout_mode, **kwargs)
    )
    return tuple(
        PlacedShoutout(text, p.slide, p.x_emu, p.y_emu, m.width_emu, m.height_emu)
        for text, p, m in zip(texts, placements, measured)
    )


def _assert_no_overlap(placed: tuple[PlacedShoutout, ...]) -> None:
    for slide in {p.slide for p in placed}:
        rects = [(p.x_emu, p.y_emu, p.width_emu, p.height_emu) for p in placed if p.slide == slide]
        hits = overlapping_pairs(rects)
        if hits:
            raise AssertionError(f"packer produced overlapping boxes on slide {slide}: {hits}")
