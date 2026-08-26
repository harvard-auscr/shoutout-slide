# HAUSCR shout-out slides

Turns the weekly shout-out form export into a PowerPoint slide: every shout-out
is its own textbox, **nothing overlaps**, and each one **appears on its own
click** in the order it was submitted — the same look as the club's past GM
slides, minus the collisions.

```
python generate_shoutouts.py input/shoutouts.csv --gm 25 --date 9.10
# -> output/GM #25 9.10---shoutouts.pptx
```

## Setup

Python 3.10+.

```
git clone <this repo>
cd hauscr-shoutouts
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python generate_shoutouts.py samples/example_form.csv --gm 1 --date 9.17   # smoke test
```

## Weekly use

1. Export the shout-out form responses from Google Sheets (**File → Download →
   CSV** or `.xlsx`) and save it in `input/` (git-ignored).
2. Run `python generate_shoutouts.py input/<file> --gm <n> --date <m.d>`.
3. Open `output/GM #<n> <m.d>---shoutouts.pptx`, check it, paste the slide into
   the GM deck.

The sheet needs a timestamp column and a message column; they are detected from
the headers (`Timestamp`, `Shout-out`, …). If detection guesses wrong:
`--text-column "<header>"` / `--time-column "<header>"`.

| option | default | meaning |
|---|---|---|
| `--font-size` | `auto` | 12pt, stepping down to 11 then 10 until everything fits one slide; a number pins it (a huge week then spills to a second slide and the summary says so) |
| `--layout` | `auto` | `compact` (rows with jitter) if it fits one slide, else `dense` |
| `--seed` | `0` | different number = different arrangement |
| `--out` | `output` | output folder |

Optional, Windows + PowerPoint only — open the deck in real PowerPoint, check the
*rendered* text bounds pairwise, count the animations, export PNGs:

```
python verify_render.py "output/GM #25 9.10---shoutouts.pptx" --png-dir renders
```

## Data: what goes where

| folder | contents | in git? |
|---|---|---|
| `input/` | weekly form exports (`.csv` / `.xlsx`) | no |
| `dataset/` | historical GM decks, named `General Meeting #<n> <m.d>.pptx` — only needed to rebuild the corpus/template or run the calibration test | no |
| `output/` | generated decks; `output/scraped/` and `output/shoutouts_corpus.*` when the corpus tools are run | no |
| `samples/` | `example_form.csv` (synthetic, safe to publish); CSVs built from real shout-outs stay local | example only |
| `template/` | `shoutouts_template.pptx` — the club theme with a single `ONE_COLUMN_TEXT` layout | yes |
| `fonts/` | `Roboto-Regular.ttf` (Apache 2.0, see `fonts/LICENSE.txt`) used to measure text | yes |

Everything derived from members' submissions is ignored by default because it
contains names, photos and in-jokes. If this repo is private and you want the
history versioned, delete the corresponding lines from `.gitignore`.

## Corpus tools (one-off, need `dataset/`)

```
python scrape_shoutouts.py     # dataset/ -> output/scraped/GM #<n> <m.d>---shoutouts.pptx
python build_corpus.py         # output/scraped/ -> output/shoutouts_corpus.{xlsx,csv}
python make_template.py        # output/scraped/<deck> -> template/shoutouts_template.pptx
```

## How it works

| step | module | what it does |
|---|---|---|
| read | `shoutout_gen/sheet.py` | load CSV/XLSX, detect columns, order by timestamp |
| measure | `shoutout_gen/metrics.py` | wrap each message in Roboto exactly as the slide will, with a 10% width slack calibrated on 531 real boxes so the renderer never wraps earlier than predicted |
| pack | `shoutout_gen/layout.py` | place boxes on a 0.05in raster using prefix-sum feasibility, then stretch the layout to span the slide (scaling positions up can never create an overlap) |
| write | `shoutout_gen/deck.py` | textboxes + one click-Appear animation per box, from the template |

Why we trust "no overlaps": boxes are placed only on cells proven free (and
re-checked pairwise before saving); the text model never under-predicts line
count on the historical corpus; and `verify_render.py` confirms the rendered
text bounds in real PowerPoint.

## Tests

```
python -m pytest tests -q
```

Layout, measurement, sheet and deck tests always run. The corpus calibration and
end-to-end tests need `dataset/` + the corpus tools' output and skip otherwise.
CI (`.github/workflows/tests.yml`) runs the suite and the quickstart on 3.11/3.12.

## License

MIT (see `LICENSE`). Roboto is bundled under the Apache License 2.0
(`fonts/LICENSE.txt`).
