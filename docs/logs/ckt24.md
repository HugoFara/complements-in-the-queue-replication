# Experiment log — EPRI Ckt24, the real utility-scale positive

**Experiments:** `60_ckt24_utility_scale.py` (M30), `64_ckt24_gldf_bound.py` (M35),
`69_ckt24_relinearize.py` (M41)
**Network provenance:** `data/opendss/ckt24/PROVENANCE.md`
**Paper:** §4.2 *A certified feasible lower bound* (the method) and §5.1 *The residual
across the corpus* / §5.2 *How much of the residual re-studying recovers* (the results —
Ckt24 is the reference cell). This file is the record behind them.
**Status:** current. The headline allocation is the re-linearized GLDF-MILP fixed
point (M41); everything before it is retained here because the *gap* between the
methods is itself a finding, not because those numbers are still the answer.

---

## Why this feeder exists in the corpus

The SMART-DS result (14 voltage-limited US feeders, 0/5 room beyond FCFS) left one
gap open: every network where joint search had beaten FCFS by a wide margin (EULV,
Fontinele's two benchmarks) was a small academic feeder. The one *real*,
utility-scale corpus tested found nothing. Ckt24 tests a second real,
utility-scale network directly, rather than concluding from one negative corpus
that the positive regime does not exist at scale.

**The network.** EPRI's Ckt24, one of the "Green Circuits" — a real 34.5 kV feeder
(230 kV → 34.5 kV) serving 3,891 customers (EPRI's published figure: 3,885),
~50.9 MW peak declared load, **6,058 buses** after import. The largest single
network in this project's corpus by a wide margin.

Structurally it is also *not* a network the classic convex route can handle: 2
genuine topological loops (a 25-bus and a 52-bus one), 6 redundant parallel
branches, 3 shunt capacitor banks, and non-zero charging on all 5,222 lines. See
[gldf-milp.md](gldf-milp.md) — this feeder is the reason that method exists.

## Three disclosed baseline adaptations

Full detail in `data/opendss/ckt24/PROVENANCE.md`. Decided *before* the experiment
was built (1–2), or a mechanical same-reasoning extension found necessary while
smoke-testing (3) — not post-hoc tuning to produce a result.

1. **37 of 843 transformers (4.4%, serving ~7.8% of total load) are individually
   overloaded (101–310%) on Ckt24's own declared load** — the same EPRI
   allocation-factor-vs-nameplate artifact already documented for Ckt5. Those 241
   loads' buses are excluded from the candidate request pool, and the 37
   transformers are dropped from the "every other transformer ≤ 100%" feasibility
   check. Their pre-existing overload has nothing to do with which new requests
   are accepted.
2. **The substation transformer (45 MVA) sits at 111.06% loading** on the full
   declared peak — a modest structural overload consistent with EPRI selecting a
   feeder that runs close to capacity, not a severe break like the SMART-DS
   Greensboro feeder or Ckt5 (both ruled out entirely in earlier work). Checked
   rather than assumed: PV export requests *relieve* this loading (111.06% →
   108.77% after 1 MW spread over 200 sites), so the experiment requires the
   substation transformer to stay at or below its own baseline loading rather than
   the network's normal 100% cap.
3. **One secondary-service line** inside an already-excluded transformer's
   downstream pocket is dropped from the line-loading check, for the same reason
   as (1).

Every other line/transformer/bus keeps the normal ≤ 100% / 0.94–1.05 pu rule.

**Scenarios.** MODERATE (N=150 requests, ~8.25 MW) and STRESS (N=300 — the same
draws as MODERATE plus 150 more, ~16.5 MW), 3 request-draw seeds each. Uniform
[10, 100] kW request sizing. 6 instances total.

---

## M30 — the NSGA-II pass, and a search-budget confound caught by the result looking wrong

A first pass (pop=30/gen=40, one seeded search) returned **+0.1% median gain on
STRESS** — suspiciously close to zero given that MODERATE, on a strict *subset* of
the same candidate pool, found +10.4% median. Any accept-set achievable in
MODERATE's space is also achievable in STRESS's larger one, so STRESS's true
optimum cannot be below MODERATE's. A fixed budget that does not scale with the
doubled gene count was diluting exploration — the same mechanism behind the
continuous-NSGA-II convergence gap in
[small-network-checks.md](small-network-checks.md).

Confirmed directly: re-running the single most suspicious instance at pop=60/gen=100
with 4 restarts recovered a **+149.9%** gain on the very first restart alone. The
full experiment was re-run at pop=60/gen=100 uniformly.

**A restart-redundancy check on the hardest case found a single restart at this
budget already sufficient** — the other 3 restarts found nothing better. (This is
where Ckt24 and Ckt7 diverge; see [ckt7.md](ckt7.md).)

| Scenario | Seed 0 | Seed 1 | Seed 2 | Median |
|---|---:|---:|---:|---:|
| MODERATE (N=150) | +23.9% | +168.0% | +88.5% | +88.5% |
| STRESS (N=300) | +25.1% | +149.9% | +8.7% | +25.1% |

**6/6 instances positive** against the honest skip-continue FCFS baseline — at the
time, the first utility-scale, real-feeder positive result in the project, and by a
far wider margin than anything else in the paper.

These numbers are **superseded as headlines** by M35/M41 below. They are kept
because the gap between them and the MILP's is the "NSGA-II is the bottleneck"
finding.

## M35 — measuring the ceiling: the GLDF-MILP retires NSGA-II

Every capacity claim to this point was *relative* — joint beats FCFS by x%. None
said how much capacity was *achievable*, so none said how much was still being left
behind. A search that beats FCFS by +88% might be at 95% of the ceiling or at 40%
of it.

The model, the build recipe, its validation gates and its two error-measurement
traps are documented in [gldf-milp.md](gldf-milp.md). Results:

| Instance | FCFS | NSGA-II (M30) | GLDF-MILP (AC-verified) | GLDF ceiling |
|---|---:|---:|---:|---:|
| MODERATE 0 | 1,766 kW | 2,188 (+23.9%) | **4,056 (+129.7%)** | 4,473 |
| MODERATE 1 | 1,077 | 2,887 (+168.0%) | **4,430 (+311.2%)** | 4,984 |
| MODERATE 2 | 1,724 | 3,249 (+88.5%) | **4,762 (+176.2%)** | 5,304 |
| STRESS 0 | 1,766 | 2,209 (+25.1%) | **6,128 (+247.0%)** | 8,281 |
| STRESS 1 | 1,093 | 2,731 (+149.9%) | **6,482 (+493.1%)** | 8,140 |
| STRESS 2 | 1,724 | 1,874 (+8.7%) | **6,446 (+274.0%)** | 8,233 |

As a fraction of the ceiling: FCFS reaches 13–40%; **NSGA-II reaches only
22.8–61.3%**; the GLDF-MILP reaches **74.0–90.7%** — in roughly **1–4 seconds** of
MILP time against NSGA-II's **tens of minutes** per instance.

This *reframes* M30 rather than contradicting it. "Joint search beats FCFS 6/6 by
+8.7% to +168.0%" stays true, but drastically understates the opportunity: on
STRESS seed 2, NSGA-II's +8.7% sits against an achievable +274.0%. The positive
half of the regime map is substantially stronger than first reported, and the
*search method* — not the regime — was the binding constraint.

**At this stage the MILP still needed two crutches**: a 0.002 pu safety margin and
a greedy AC repair dropping up to 15 requests, because the zero-margin optimum was
AC-infeasible on 6/6. M41 removes both.

## M41 — re-linearization: the MILP gets fixed instead of repaired

The margin is blunt (it costs capacity everywhere to cure an error that binds in a
few places) and the repair can only ever *remove*. Both exist to compensate for
model error, so: solve → run the real AC power flow on the answer → **re-linearize
GLDF there** → re-solve, iterating to a fixed point. No-good cuts prevent cycling
and do no optimizing. Algebra and the crucial re-anchor-vs-re-slope distinction are
in [gldf-milp.md](gldf-milp.md).

The residual turned out to be **anchor error, not slope error.** The loop reaches a
fixed point in **3–6 iterations** (15–58 s per instance) and is **AC-feasible at
margin zero on 6/6, with no repair drops at all**:

| Instance | FCFS | Margin + repair (M35) | drops | **Re-linearized (M41)** | vs. M35 |
|---|---:|---:|---:|---:|---:|
| MODERATE 0 | 1765.7 kW | 4055.6 kW | 0 | **4057.5 kW** | +0.0% |
| MODERATE 1 | 1077.4 | 4430.4 | 1 | **4493.6** | +1.4% |
| MODERATE 2 | 1723.7 | 4761.5 | 0 | **4761.5** | +0.0% |
| STRESS 0 | 1765.7 | 6127.6 | 15 | **6277.9** | +2.5% |
| STRESS 1 | 1093.0 | 6482.0 | 12 | **6764.5** | +4.4% |
| STRESS 2 | 1723.7 | 6446.3 | 13 | **6844.6** | +6.2% |

The safety margin and the AC repair are not merely reduced but **eliminated**.
Capacity never decreases (4/6 improved, 2/6 tied) and the gain over honest FCFS
becomes **+129.8% to +518.9%**. The largest improvements land exactly where the
repair had been most destructive (the STRESS instances, 12–15 drops) — the
mechanism behaving as predicted.

**One number here is circular, and we flag it rather than quote it.** At the fixed
point the accept-set equals the anchor, so the model reproduces the AC solution *by
construction* and its error is exactly 0.00 mpu. That is the definition of
convergence, not evidence of accuracy. Three non-circular facts carry the claim:

1. the fixed point **AC-verifies at margin zero**, which the oracle was free to
   reject and did not;
2. the **out-of-sample** error — the model anchored at iteration 0's answer,
   predicting iteration 1's *different* optimum — is **+0.08 to +0.67 mpu**, against
   −1.87 to −3.62 mpu from the empty anchor: a 5–30× reduction at a point the model
   was not pinned to;
3. **capacity rose**, which a model that had merely learned to agree with itself
   could not achieve through an unchanged AC oracle.

### The epistemic consequence — the reason this milestone matters most

M35's "ceiling" is the zero-margin MILP evaluated at the empty anchor, and it is
**AC-infeasible on 6/6**. It is an over-estimate manufactured by model error, so
"NSGA-II reaches only 22.8–61.3% of the ceiling" rested on an *inflated
denominator*.

The converged fixed point, by contrast, is a **certified AC-feasible allocation**,
hence a genuine *lower* bound on the true optimum. Dividing by it yields an *upper*
bound on any method's real fraction of achievable capacity:

> **NSGA-II attains at most 27.4–68.2% of the achievable capacity on Ckt24.**

Both numerator and denominator are AC-verified allocations, so this claim **depends
on no power-flow approximation whatsoever** — unlike the percentage it replaces.
The figures rose (from 22.8–61.3%) and the claim simultaneously became *stronger*,
because it is no longer model-contingent. This is the form in which the result
should be read.

---

## Why this feeder and not SMART-DS — a mechanistic hypothesis, not yet confirmed

Network size alone does not predict the regime: SMART-DS feeders span the same
700–5,000-bus range and found zero room ([smartds.md](smartds.md)). The candidate
mechanism is **unevenness of remaining headroom, not scale**. Ckt24's real,
EPRI-allocated load is documented — by the very 37-transformer artifact this
experiment excludes — to be genuinely uneven across the feeder, so which candidate
buses have slack varies a great deal. That is exactly the condition under which
choosing *which* subset to accept, rather than walking the queue in arrival order,
has the most room to matter. A synthetic-but-realistic SMART-DS feeder, provisioned
more uniformly by construction, may simply not have that unevenness to exploit.

Plausible and mechanistically consistent; **not independently verified**. It rests
on one real, heterogeneous, utility-scale feeder and would need a second one —
ideally without EPRI's own allocation artifact, so the "unevenness" driving it is
unambiguously a real feeder property — to become a general claim.

## Honest scope limits

- **One feeder, six instances.** Existence proof that the positive regime exists at
  utility scale; *not* a base rate for how often it occurs there.
- The uniform [10, 100] kW request-sizing band and the exclusions above are
  disclosed modeling choices, not tuned to this result.
- The fixed point is a self-consistent *local* optimum. GLDF remains a linear
  approximation rather than a convex relaxation, so global optimality is
  unclaimable. Convergence is empirical (6/6 within six iterations), not guaranteed
  — the no-good cuts bound the loop but do not prove termination.
- The thermal model sums power over a spanning tree, so the 2 loop-closing and 6
  parallel branches (of 6,065) carry no constraint of their own, and subtree losses
  are ignored — the same approximation LinDistFlow itself makes. AC reverification
  is what catches the resulting error, and does.

## Reproduce

```sh
uv run python experiments/60_ckt24_utility_scale.py --save    # M30: NSGA-II pass (superseded as headline)
uv run python experiments/64_ckt24_gldf_bound.py --save       # M35: GLDF-MILP ceiling, beats NSGA-II 6/6
uv run python experiments/69_ckt24_relinearize.py --save      # M41: re-linearization, margin + repair retired
uv run python experiments/69_ckt24_relinearize.py --check-invariance   # M is bit-identical at any operating point
```
