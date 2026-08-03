# Experiment logs — joint hosting-capacity allocation (Part II)

These files are the **record of how the results were obtained**: the budgets, the wrong
turns, the confounds caught, the milestones superseded, and the one milestone retracted.

They exist because the manuscript had grown into a chronological log of them. It states
**what is true and why it matters**; these logs state **how we found out**. Where the two
ever disagree, **the log is the record and the paper is wrong** — the logs are written
against the CSVs in `outputs/`.

## Where things live

| Document | Purpose |
|---|---|
| `docs/papers/complements-in-the-queue.md` | The paper: claims, and the numbers that support them |
| `docs/logs/` (here) | How each result was obtained; what was superseded or retracted |
| `docs/joint-allocation-scope.md` | The engine build log, M0–M21 (before any of this) |
| `outputs/*.csv` | The data every number here is quoted from |

Some milestones below predate the current manuscript and were written against its
superseded "regime map" predecessor; that draft and the publication-decision memo are
internal documents, not part of this archive.

## The logs

| Log | Covers | Milestones |
|---|---|---|
| [gldf-milp.md](gldf-milp.md) | **The method.** The model, how to build it from pandapower/OpenDSS, its validation gates, and the **three** ways it will fool you | M35–M42, M48 |
| [ckt24.md](ckt24.md) | **EPRI Ckt24** — 6,058 buses, the real utility-scale positive; the ceiling; re-linearization | M30, M35, M41 |
| [ckt7.md](ckt7.md) | **EPRI Ckt7** — the second real feeder; the swap-operator **dead end**; the **retraction**; the **empty thermal model** | M31–M34, M39, M40, M42, **M48** |
| [smartds.md](smartds.md) | **The negative half of the regime map**, and the three attempts to kill it | M22, M26–M28, M36 |
| [fontinele.md](fontinele.md) | **Head-to-head vs. the closest prior art** on their own two benchmark networks | M29, M37 |
| [small-network-checks.md](small-network-checks.md) | External validation (Sandia, ENWL), the honest negatives, the EULV re-check | M38 |

## Reading order, if you are new to this

1. [smartds.md](smartds.md) — why the deflationary headline is real, and why the
   strawman-baseline correction had to come first.
2. [ckt24.md](ckt24.md) — the one real feeder where joint allocation is worth a great
   deal, and how a search-budget confound nearly hid it.
3. [gldf-milp.md](gldf-milp.md) — the method that measured the ceiling and retired the
   metaheuristic.
4. [ckt7.md](ckt7.md) — the most instructive file here: a well-evidenced,
   internally-consistent, **wrong** research direction, and a retracted claim.

## The synthesis: NSGA-II's failure is a scale effect

The ceiling audit (M35–M38) re-checked all four headline claims with the same MILP. The
pattern only becomes visible once you can measure against a ceiling at all:

| Network | Binaries | Buses | NSGA-II reaches |
|---|---:|---:|---|
| EULV | 40 | 907 | ~ceiling (FCFS itself is at 97.7%) |
| Fontinele 33/69-bus | 50 | 33 / 69 | **98.2–100.3%** |
| SMART-DS | 40 | 700–5,000 | ceiling (only ≤ 7.4% room exists at all) |
| **EPRI Ckt24** | **150–300** | **6,058** | **≤ 27.4–68.2%** (AC-certified bound, M41) |

**On small problems NSGA-II finds the optimum.** It collapses precisely where the most
consequential claim lives — a real, utility-scale feeder with a large request set. Every
benchmark-scale result **stands unchanged**; the utility-scale one was badly
**understated**.

The methodological warning worth carrying: *an off-the-shelf population metaheuristic is
adequate for benchmark interconnection studies and materially inadequate for real ones,
and you cannot see the difference without measuring against a ceiling.*

## Things in here that are wrong, on purpose

Kept because how they went wrong is more useful than the results were:

- **M32–M34, the swap-operator line** ([ckt7.md](ckt7.md)) — a plausible, well-evidenced,
  internally-consistent answer to the *wrong question* ("which search wins?" rather than
  "how much capacity is achievable?"). Superseded wholesale by M35.
- **M40, retracted** ([ckt7.md](ckt7.md)) — "GLDF was never broken on Ckt7; our request
  sampler was." The comparison behind it set a random-accept-set error against an
  at-the-optimum error. A surrogate must be validated **on the trajectory the optimizer
  actually follows**; measuring it on random draws misled five consecutive experiments.
- **M43/M44, the arrival-order lottery** ([arrival-order.md](arrival-order.md)) — a strong,
  clean, *reproducible* result that turned out to be an artifact of a synthetic request-size
  band (`uniform[10,100] kW`) that nobody had ever defended. Real requests have a median of
  **6.1 kW** (M45). What replaced it — the **onset** — is better.
- **M46's "the yardstick is soft in this regime"** ([arrival-order.md](arrival-order.md)) —
  a correct observation given an invented mechanism. The real cause was a **bug** (M48).

### The recurring failure mode, stated once

Three of the five entries above have the same shape: **a plausible mechanism was attached
to a real observation and never tested, and the mechanism then steered the next milestone.**
Twice the invented mechanism was "the linear model is inaccurate" — and GLDF has been
innocent **every single time** (M40, M42, M48). The lesson is in
[gldf-milp.md](gldf-milp.md#trap-0): *suspect the plumbing — the anchor, the graph, the
constraint set — before the physics*, and keep a **model-free check** (can a greedy queue
walk beat your optimum?) that does not share the model's assumptions.

The other two share the *other* shape: **an unvalidated input distribution.** The same
undefended `uniform[10,100] kW` band both manufactured the arrival-order lottery *and* kept
a dead thermal constraint out of sight for five milestones.

## Standing caveats on everything in here

- **Every result is a single seeded run unless stated otherwise.** The paper's §5
  comparisons are distributions (≥20 seeds); the corpus sweep, the fairness fronts and
  the engine extensions are single-seed — reproducible, but not characterized as
  mean-and-spread.
- **The domain-correctness flags in the paper's §8 apply to every number here.** They are
  load-bearing and *not* independently verified by the author; they need an
  electrical-engineering co-author before any journal submission.
- **"mpu"** = milli-per-unit (1 mpu = 0.001 pu), our own shorthand for voltage error, not
  standard EE usage. It is used throughout these logs; it should be converted before any
  external submission.
