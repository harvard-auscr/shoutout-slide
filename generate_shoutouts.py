"""Generate the weekly shout-out slide from the sign-up spreadsheet.

    python generate_shoutouts.py shoutouts.csv --gm 25 --date 9.10
        -> output/GM #25 9.10---shoutouts.pptx

Every shout-out becomes its own textbox (Roboto), packed so no two boxes
touch, and each one appears on its own click in timestamp order. The font size
starts at 12pt and steps down to 11 then 10 until everything fits on one slide;
pass ``--font-size N`` to pin it (a very busy week then spills to a second
slide, which the summary reports).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shoutout_gen.deck import TEMPLATE_PATH
from shoutout_gen.generate import AUTO_FONT_SIZES_PT, LAYOUT_MODES, generate, output_name_for
from shoutout_gen.sheet import SheetFormatError, read_shoutouts


def _font_size(value: str) -> float | None:
    """argparse type: "auto" -> None (size ladder), otherwise a point size."""
    if value.lower() == "auto":
        return None
    size = float(value)
    if size <= 0:
        raise argparse.ArgumentTypeError("font size must be positive")
    return size


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sheet", type=Path, help="CSV or XLSX with a timestamp column and a shout-out column")
    parser.add_argument("--gm", type=int, required=True, help="general meeting number, e.g. 25")
    parser.add_argument("--date", required=True, help="date as written in deck names, e.g. 9.10")
    parser.add_argument("--out", type=Path, default=Path("output"), help="output folder")
    parser.add_argument(
        "--font-size", type=_font_size, default=None,
        help=f"point size, or 'auto' (default) to try {', '.join(f'{s:g}' for s in AUTO_FONT_SIZES_PT)} until one slide fits",
    )
    parser.add_argument("--layout", choices=LAYOUT_MODES, default="auto")
    parser.add_argument("--seed", type=int, default=0, help="change for a different arrangement")
    parser.add_argument("--text-column", help="force the shout-out column (header name)")
    parser.add_argument("--time-column", help="force the timestamp column (header name)")
    parser.add_argument("--template", type=Path, default=TEMPLATE_PATH)
    args = parser.parse_args(argv)

    try:
        shoutouts = read_shoutouts(args.sheet, args.text_column, args.time_column)
    except (SheetFormatError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not shoutouts:
        print("error: the sheet has no non-empty shout-outs", file=sys.stderr)
        return 2
    untimed = sum(1 for s in shoutouts if s.timestamp is None)

    result = generate(
        [s.text for s in shoutouts],
        args.out / output_name_for(args.gm, args.date),
        font_size_pt=args.font_size,
        layout_mode=args.layout,
        seed=args.seed,
        template_path=args.template,
    )
    print(f"{len(shoutouts)} shout-outs -> {result.output}")
    print(f"slides: {result.slide_count} (per slide: {list(result.per_slide_counts)}), font {result.font_size_pt:g}pt")
    if untimed:
        print(f"note: {untimed} row(s) had no parseable timestamp; they were placed last in sheet order")
    if result.slide_count > 1:
        hint = "try a smaller --font-size" if args.font_size is not None else f"even at {result.font_size_pt:g}pt"
        print(f"note: did not fit on one slide ({hint}); the overflow is on the extra slide(s)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
