# LaTeX build — "Complements in the Queue"

Self-contained: `elsarticle.cls` and both `.bst` files are vendored here (from
CTAN's `elsarticle` package, LPPL-licensed) so this compiles without any
system-wide LaTeX package install.

```
python ../../../experiments/105_paper_figures.py   # regenerates figures/*.pdf
pdflatex complements-in-the-queue
bibtex   complements-in-the-queue
pdflatex complements-in-the-queue
pdflatex complements-in-the-queue
```

**Figures.** `figures/*.pdf` are generated from the experiment CSVs in
`outputs/` by `experiments/105_paper_figures.py`; the same script also writes
PNG copies to `docs/papers/figures/` for the Markdown manuscript. No figure
number is hand-entered, so rerun the script after any result moves rather than
editing a figure by hand.

**Venue-specific citation style.** The `.tex` currently uses
`\documentclass[review,authoryear,...]{elsarticle}` with
`elsarticle-harv.bst` — Elsevier's author-date house style, matching
*Energy Policy*. For an *Applied Energy* submission (numbered/Vancouver
style), drop `authoryear` from the `\documentclass` line and change
`\bibliographystyle{elsarticle-harv}` to `\bibliographystyle{elsarticle-num}`
— no other change is needed, since every citation in the body uses natbib's
`\citep`/`\citet`/`\citealp`.

**Before submission:** the `review` class option (double-spaced, line
numbers, one-column) is for the peer-review copy. Elsevier's own submission
system re-typesets the final version; do not hand-tune the two-column
production layout yourself.
