"""Open a generated deck in real PowerPoint and check what it actually renders.

Why: python-pptx can only prove the *boxes* we wrote don't overlap. Whether the
*text* overlaps depends on how the renderer wraps it, so this asks PowerPoint
(via COM) for every text run's rendered bounds and checks those pairwise, plus
that each shape's animation exists and that nothing leaves the slide. It also
exports a PNG per slide for eyeballing.

Caveat: this machine has no Roboto installed, so PowerPoint substitutes a
different font here; Google Slides (where the deck is presented) has Roboto.
A pass here is therefore a pass under a *different* font than the one the
layout was measured for -- a useful robustness check, not the exact target.

Usage:
    python verify_render.py "output/GM #25 9.10---shoutouts.pptx" [--png-dir folder]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shoutout_gen.layout import overlapping_pairs


def verify(pptx_path: Path, png_dir: Path | None = None) -> list[str]:
    """Return a list of problems (empty == pass). Prints a per-slide summary."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = win32com.client.Dispatch("PowerPoint.Application")
    problems: list[str] = []
    pres = app.Presentations.Open(str(pptx_path.resolve()), ReadOnly=True, Untitled=False, WithWindow=False)
    try:
        page_w, page_h = pres.PageSetup.SlideWidth, pres.PageSetup.SlideHeight
        for s in range(1, pres.Slides.Count + 1):
            slide = pres.Slides(s)
            text_rects, box_rects = [], []
            for shape in slide.Shapes:
                if not shape.HasTextFrame:
                    continue
                tr = shape.TextFrame.TextRange
                text_rects.append((tr.BoundLeft, tr.BoundTop, tr.BoundWidth, tr.BoundHeight))
                box_rects.append((shape.Left, shape.Top, shape.Width, shape.Height))
                if shape.Left < 0 or shape.Top < 0 or shape.Left + shape.Width > page_w or shape.Top + shape.Height > page_h:
                    problems.append(f"slide {s}: shape {shape.Name!r} leaves the page")
                # If the renderer wrapped a line we didn't expect, the text gets
                # taller than the box we packed -- that is the failure that
                # produces overlaps. (BoundWidth is not compared: PowerPoint
                # pads it by a constant ~3pt of glyph overhang, and an over-wide
                # line would show up as extra height anyway.)
                if tr.BoundHeight > shape.Height + 0.5:
                    problems.append(
                        f"slide {s}: text of {tr.Text[:30]!r} renders {tr.BoundHeight:.1f}pt tall, "
                        f"taller than its {shape.Height:.1f}pt box (unexpected wrap)"
                    )
            text_hits = overlapping_pairs(text_rects)
            box_hits = overlapping_pairs(box_rects)
            for i, j in text_hits:
                problems.append(f"slide {s}: rendered text overlaps between shapes {i} and {j}")
            anims = slide.TimeLine.MainSequence.Count
            if anims != len(text_rects):
                problems.append(f"slide {s}: {anims} animations for {len(text_rects)} text shapes")
            print(
                f"slide {s}: {len(text_rects)} shapes, {anims} click animations, "
                f"text overlaps={len(text_hits)}, box overlaps={len(box_hits)}"
            )
            if png_dir is not None:
                png_dir.mkdir(parents=True, exist_ok=True)
                slide.Export(str(png_dir / f"{pptx_path.stem}_s{s}.png"), "PNG", 1920, 1080)
    finally:
        pres.Close()
        app.Quit()
    return problems


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--png-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    problems = verify(args.pptx, args.png_dir)
    for p in problems:
        print("PROBLEM:", p)
    print("PASS" if not problems else f"FAIL ({len(problems)} problems)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(_main())
