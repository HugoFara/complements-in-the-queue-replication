# Experiment log — external validation, the honest negatives, and the EULV re-check

**Experiments:** `26_m2_hosting_capacity_validation.py` (vs. Sandia), `29` (convex==AC),
`30`/`41` (reinforcement), `44` (continuous gap), `67_eulv_gldf_recheck.py` (M38)
**Paper:** §4.2 *A certified feasible lower bound* and §5.3 *Certifying what survives*
(validation), §5.9 *The regime belongs to the queue* (the scale effect). The external
checks and negatives in sections 1–6 below predate the current manuscript's corpus and
are not numbered results in it.
**Status:** current. Nothing here has been overturned.

These are the checks that keep the project honest but do not carry a headline. Grouped
here so the paper does not have to walk through them one by one.

---

## 1. Validation against a published external study (Sandia)

Sandia SAND2015-9712C (Reno & Broderick, 2015) simulated **216 real US MV feeders** for
PV hosting capacity and found **72% over-voltage-limited** (26% line-limited).

We implemented **Sandia's exact HC definition** and computed it on EULV: **99.7 kW,
over-voltage-limited, robust across three seeds** — matching Sandia's dominant
*violation type*. (Magnitude is not comparable across a full voltage class; the type is
what the check is for.)

**This also quantifies the joint premise directly.** The single-site number is 99.7 kW,
but the joint search seats **245.4 kW** across 40 simultaneous smaller requests — **2.5×
the worst single site**, because spreading requests avoids concentrating voltage rise.

**And it sets up the regime rule.** Sandia's 72%-voltage MV finding is the *opposite* of
our corpus's 91%-thermal LV split — which is exactly why the joint advantage (gated on
voltage-limiting) should be far more common on MV networks than on this LV corpus, and
why the SMART-DS corpus ([smartds.md](smartds.md)) had to be tested.

**Scope:** locational hosting capacity is **sampled, not exhaustive** here — the EULV HC
sweep covers a subset of buses, unlike Sandia's exhaustive per-feeder sweep.

## 2. A real DNO's own capacity data — a negative but informative check

We checked the engine against **ENWL's own published "capacity" data**. The result is a
**negative finding reported as such**: `distribution_tx_generation_headroom_kva` is
exactly `rating − currently_connected_generation`, **unconditional on load or voltage**.

Confirmed by observing that even the **77 real 800 kVA transformers ENWL flags "No
Capacity"** (avg **144% demand utilisation**) report **~100%-of-rating generation
headroom** — impossible for a power-flow-derived figure.

It is an **administrative register, not a hosting-capacity study.** It still quantifies
the register-vs-reality gap this whole line of work is about, with a named utility's
open data.

## 3. Convex == AC (a positive cross-check, correctly scoped)

The convex MILP matches NSGA-II's accommodated MW to within a few percent on **radial**
networks (EULV: exact, 245.4 kW, **86× faster**; the larger ENWL feeder: **99.4%** of
NSGA-II, **55× faster**), AC-reverified throughout.

A genuine agreement between two independent methods — **except on thermal feeders at
scale**, where the relaxation's dropped losses make it **lose to FCFS**. That limit is
foregrounded in the paper (§5), not averaged away.

## 4. The reinforcement lever — a near-null real-data result

A catalogue-priced thermal reinforcement lever (`price_reinforcement`) can, in
principle, recover capacity by reconductoring overloaded lines. On the two-network demo
it looked promising.

**At full-corpus scale it is a near-null: only 3 of 121 feeders (2.5%) gain anything
from the lever at all**, and a feeder's aggregate binding constraint does **not** predict
which. The cost constants are order-of-magnitude illustrative, not a vetted utility
price sheet.

Reported as a **negative real-data result, not a validated feature.**

## 5. Continuous NSGA-II's convergence gap

Continuous (partial-award) request sizing **weakly dominates** binary accept/reject by
construction, and in every deterministic-method measurement.

But **continuous NSGA-II *underperforms* binary NSGA-II at equal budget** — real-valued
sampling starts far from the near-bang-bang optima this problem favours. The gap closes
with **4–8× the generations**, after which it ends up slightly ahead.

A **budget artifact, not a ceiling** — a diagnosed negative. (The same failure mode
resurfaces at utility scale in [ckt24.md](ckt24.md): a fixed budget that does not scale
with gene count silently dilutes exploration.)

## 6. The strawman-baseline correction, as a finding

The single most consequential correction in this line of work is methodological: much of
the prior *"beats baseline on 120/121"* was **`skip_continue_baseline`'s own free gain
over stop-dead greedy** (2 of the 4 strict wins), plus a `≥` test **counting 116 ties as
wins**.

Quantifying how much a "smarter" method's apparent advantage is really **its baseline's
handicap** is itself a transferable lesson for this literature. Full forensics — including
the +1367% margin that turned out to be one 12.9 kW request blocking a queue — are in
[smartds.md](smartds.md).

---

## M38 — the EULV re-check: confirmed, and it is a *no-room* feeder

EULV's **+3.0%** is the project's only *surviving* seed-distribution positive (p=5.7e-5,
robust to metaheuristic seed), so the ceiling audit had to test it too.

The GLDF-MILP **reproduces the convex route's figure exactly — +2.99% mean** over the same
25 request draws, against the +3.0% reported in the paper, and reaches **+3.17%** with the
exact-AC swap polish.

**More informative than the margin itself: FCFS already reaches 97.7% of the ceiling** on
average (89.2–101.4% across draws).

> **The +3% is small *because the feeder has almost no room left* — not because the method
> is weak.** That is the same verdict as SMART-DS, not the Ckt24 story.

EULV is radial with no switches, shunts or line charging, so **GLDF here coincides with
classic LinDistFlow**. It contributes no extra physics on this network — only the ceiling
framing.

## Reproduce

```sh
uv run python experiments/26_m2_hosting_capacity_validation.py   # vs. Sandia's HC definition
uv run python experiments/67_eulv_gldf_recheck.py --save         # M38: EULV re-check; FCFS already at 97.7% of ceiling
# convex==AC (29), reinforcement (30/41), continuous gap (44): see docs/joint-allocation-scope.md
```
