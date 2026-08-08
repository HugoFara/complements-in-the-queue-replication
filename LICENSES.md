# Licensing

This archive is not under one licence. Code, results and the manuscript carry
different terms, and several third-party components carry their own.

| What | Where | Terms |
|---|---|---|
| Code | `src/`, `experiments/`, `test/`, `tools/` | MIT — [`LICENSE`](LICENSE) |
| Results | `outputs/` | CC BY 4.0 |
| Derived data | `data/interconnection/` | CC BY 4.0 |
| Laboratory notebooks and the engine build log | `docs/logs/`, `docs/joint-allocation-scope.md` | CC BY 4.0 |
| The manuscript | `docs/papers/` | © 2026 Hugo Farajallah. All rights reserved. |

## Code — MIT

Everything in `src/`, `experiments/`, `test/` and `tools/`, under the terms in
[`LICENSE`](LICENSE).

## Results, derived data and notebooks — CC BY 4.0

Creative Commons Attribution 4.0 International:
<https://creativecommons.org/licenses/by/4.0/>
(full text: <https://creativecommons.org/licenses/by/4.0/legalcode>)

Reuse the numbers, replot the figures, quote the notebooks — commercially or
not — provided you attribute. [`CITATION.cff`](CITATION.cff) carries the
citation; the archive DOI is
[10.5281/zenodo.21774931](https://doi.org/10.5281/zenodo.21774931).

`data/interconnection/` is a summary the author derived from a public data set,
and the CC BY grant covers that derived summary rather than the source. The
source, its exact export and its scope limits are in
[`data/interconnection/PROVENANCE.md`](data/interconnection/PROVENANCE.md).

## The manuscript — all rights reserved

`docs/papers/` — the LaTeX source, the compiled PDF, the generated Markdown
reading copy, and the figure files — is © 2026 Hugo Farajallah. No reuse licence
is granted.

This is deliberate rather than an omission. It is an unrefereed preprint with no
venue yet; an open licence cannot be withdrawn, and granting one now would
foreclose the publishing agreement that follows acceptance. Cite and quote it as
you would any preprint.

## Third-party material, under none of the above

- **Feeder models** — EPRI Ckt7 and Ckt24, SMART-DS, the ENWL LV corpus, the
  IEEE European LV test case, and the EPFL DESL Swiss 55-node model. None is
  redistributed here. Each carries its own terms from its own source;
  [`REPLICATION.md`](REPLICATION.md) lists where to obtain each, and notes that
  the Swiss repository ships no licence file at all.
- **`elsarticle.cls`, `elsarticle-harv.bst`, `elsarticle-num.bst`** in
  `docs/papers/latex/` — Elsevier's LaTeX class and styles, under the LaTeX
  Project Public License. The licence notice is in each file.
