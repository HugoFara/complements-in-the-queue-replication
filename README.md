# Complements in the Queue — replication material

Code, experiments and numerical outputs for:

> **Complements in the Queue: What Re-Studying Recovers, and What Only a Joint
> Interconnection Study Can**
> Hugo Farajallah, Independent Research — <research@hugofara.net>
> ORCID [0009-0001-0769-4434](https://orcid.org/0009-0001-0769-4434)

The paper defines the **residual** — `JOINT − max(sequential study orders)` — as
distribution-feeder hosting capacity that is only jointly feasible, and therefore
that no segregated interconnection study recovers however it orders its queues.
It measures the residual across four independently sourced feeder families with
an AC-verified Generalized-LinDistFlow instrument that certifies a feasible lower
bound rather than a relaxation.

- **Manuscript:** [`docs/papers/latex/complements-in-the-queue.pdf`](docs/papers/latex/complements-in-the-queue.pdf)
  (source alongside it; a Markdown copy is in [`docs/papers/`](docs/papers/))
- **How to run any of this, and where to get the feeder data:**
  [`REPLICATION.md`](REPLICATION.md)

```sh
uv sync
uv run python experiments/129_outside_relief.py --help
```

Feeder models are third party and are not redistributed here; `REPLICATION.md`
lists each source and the path the scripts expect it at.
[`outputs/MANIFEST.md`](outputs/MANIFEST.md) indexes every result file.

The code is MIT-licensed ([`LICENSE`](LICENSE)); the feeder models and the
vendored LaTeX class carry their own terms.

This archive is deposited at
[doi:10.5281/zenodo.21774931](https://doi.org/10.5281/zenodo.21774931); cite it
with the metadata in [`CITATION.cff`](CITATION.cff).
