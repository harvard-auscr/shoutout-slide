"""Measure shout-out text the way the slide will render it.

Why a real font file: PowerPoint/Google Slides wrap text using Roboto's actual
glyph advances. If we guessed widths from character counts, a line that fits in
our model could wrap in the renderer, grow the box by a line, and overlap its
neighbour -- the one thing this project must never do. So we measure with the
bundled ``fonts/Roboto-Regular.ttf`` through Pillow, and then apply a small
width slack so the renderer never wraps *earlier* than we predict (wrapping
later only makes the box shorter, which is harmless).

Units: EMU everywhere at the interface (914400 per inch, 12700 per point).
Vertical metrics are calibrated against the corpus of real Google-Slides
autofit boxes (see tests/test_metrics.py::test_line_height_matches_corpus).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import ImageFont

EMU_PER_PT = 12700
EMU_PER_INCH = 914400

FONT_PATH = Path(__file__).resolve().parent.parent / "fonts" / "Roboto-Regular.ttf"
# 12pt is what 395 of the 643 corpus shout-outs use (11pt: 97, 10pt: 39).
DEFAULT_FONT_SIZE_PT = 12.0

# Textbox geometry. The corpus boxes carry Google Slides' default 0.1in insets
# on all four sides; the generator uses zero insets instead because the insets
# are invisible (no fill, no border) yet roughly double a one-line box's height,
# which halves how many shout-outs fit on the slide. The inter-box gap in the
# packer provides the visual breathing room.
CORPUS_INSET_EMU = 91425  # lIns/rIns/tIns/bIns in the scraped decks
BOX_INSET_EMU = 0  # what the generator writes
MAX_BOX_WIDTH_EMU = 3_000_000  # every original shout-out box is exactly this wide

# Google Slides' single-spacing line height for Roboto, measured from the corpus
# autofit boxes: 10pt -> 155850 EMU/line, 11pt -> 169200, 12pt -> 184800, i.e.
# 1.225 * font size at every size. Rounded up to 1.23 so a predicted box is
# never shorter than the rendered one.
LINE_HEIGHT_FACTOR = 1.23

# Widths we predict are multiplied by this before wrapping decisions are made,
# so a renderer with slightly different kerning/hinting still fits every line
# we said would fit. Calibrated on the corpus: at 1.05 Google Slides wrapped 4
# of 531 boxes one line earlier than predicted (an overlap risk); at 1.10 it
# never does, and 88% of boxes still get exactly the rendered line count.
WIDTH_SLACK = 1.10

# Glyphs Roboto lacks (emoji, CJK, symbols) are drawn from a fallback font by
# the renderer; colour emoji are roughly square at ~1.3em in both PowerPoint
# (Segoe UI Emoji) and Google Slides (Noto Color Emoji).
FALLBACK_GLYPH_EM = 1.3

# Code points that render with zero advance inside an emoji sequence.
_ZERO_WIDTH = {0x200D, 0xFE0E, 0xFE0F, 0x20E3} | set(range(0x1F3FB, 0x1F400))
# Pillow measures at this multiple of the point size for sub-point accuracy.
_SCALE = 16


@dataclass(frozen=True)
class Measured:
    """Result of laying one shout-out into a box: wrapped lines and box size (EMU)."""

    lines: tuple[str, ...]
    width_emu: int
    height_emu: int


class TextMeasurer:
    """Wraps and sizes text for one font size. Instances are cheap; cache one per size."""

    def __init__(
        self,
        font_size_pt: float = DEFAULT_FONT_SIZE_PT,
        inset_emu: int = BOX_INSET_EMU,
        font_path: Path = FONT_PATH,
    ):
        self.font_size_pt = font_size_pt
        self.inset_emu = inset_emu
        self._font = ImageFont.truetype(str(font_path), int(round(font_size_pt * _SCALE)))
        self._cmap = _cmap_for(font_path)
        self.line_height_emu = int(round(font_size_pt * LINE_HEIGHT_FACTOR * EMU_PER_PT))

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #
    def text_width_emu(self, text: str) -> int:
        """Rendered advance width of a single line, including slack."""
        return int(round(self._raw_width_pt(text) * WIDTH_SLACK * EMU_PER_PT))

    def wrap(self, text: str, max_width_emu: int) -> list[str]:
        """Word-wrap ``text`` into lines no wider than ``max_width_emu``.

        Mirrors PowerPoint: explicit newlines always break; words break at
        spaces; a single word wider than the box is split character-wise.
        """
        lines: list[str] = []
        for paragraph in text.split("\n"):
            lines.extend(self._wrap_paragraph(paragraph, max_width_emu))
        return lines

    def measure(self, text: str, max_box_width_emu: int = MAX_BOX_WIDTH_EMU) -> Measured:
        """Size the box for ``text``: as narrow as its longest line, at most ``max_box_width_emu``."""
        available = max_box_width_emu - 2 * self.inset_emu
        lines = self.wrap(text, available)
        widest = max((self.text_width_emu(line) for line in lines), default=0)
        width = min(max_box_width_emu, widest + 2 * self.inset_emu)
        height = len(lines) * self.line_height_emu + 2 * self.inset_emu
        return Measured(tuple(lines), width, height)

    # ------------------------------------------------------------------ #
    # Private mechanics
    # ------------------------------------------------------------------ #
    def _wrap_paragraph(self, paragraph: str, max_width_emu: int) -> list[str]:
        words = paragraph.split(" ")
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if self.text_width_emu(candidate) <= max_width_emu:
                current = candidate
                continue
            if current:
                lines.append(current)
            # Word alone is too wide: split it by characters like the renderer does.
            current = ""
            for ch in word:
                if self.text_width_emu(current + ch) <= max_width_emu or not current:
                    current += ch
                else:
                    lines.append(current)
                    current = ch
        lines.append(current)
        return lines

    def _raw_width_pt(self, text: str) -> float:
        """Advance width in points: Pillow for glyphs Roboto has, em-fraction for the rest."""
        width_px = 0.0
        run = ""
        for ch in text:
            if ord(ch) in self._cmap:
                run += ch
                continue
            if run:
                width_px += self._font.getlength(run)
                run = ""
            if ord(ch) not in _ZERO_WIDTH:
                width_px += FALLBACK_GLYPH_EM * self.font_size_pt * _SCALE
        if run:
            width_px += self._font.getlength(run)
        return width_px / _SCALE


@lru_cache(maxsize=None)
def _cmap_for(font_path: Path) -> frozenset[int]:
    """Code points the font can draw itself; everything else falls back."""
    return frozenset(TTFont(str(font_path)).getBestCmap().keys())
