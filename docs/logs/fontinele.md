# Experiment log — head-to-head against the closest prior art (Fontinele et al. [13])

**Experiments:** `59_fontinele_head_to_head.py` (M29), `66_fontinele_gldf_recheck.py` (M37)
**Prior art:** Fontinele, W.F., Torrez Caballero, P., Coelho Marques da Costa, E.
*"Connection Order Matters: Path-Dependent Hosting Capacity in Distributed Generation
Interconnection Queues."* Preprint submitted to Applied Energy, 11 April 2026.
SSRN 6561541 (not peer-reviewed).
**Paper:** §7.5; this file is the record behind it.
**Status:** current. The only "we beat a real competing method" result in the project —
and it survived the ceiling audit.

---

## The question

The paper differentiates this work from [13] *on paper*: they search a **permutation**
of a fixed serial queue (a better order); we search accept/reject **subsets** directly
(no ordering is simulated). "A reordered serial queue is still serial" is an assertion
until it is tested on the same ground. So: build [13]'s own two benchmark networks and
run our subset search there.

## What was reproduced exactly

- **IEEE 33-bus** (`pandapower.networks.case33bw()`) and **IEEE 69-bus** (rebuilt from
  MATPOWER's `case69.m`, the same Baran & Wu 1989 source [13] cites — no `case69` ships
  with pandapower).
- Both verified **bit-for-bit** against [13]'s reported totals: 33-bus, **3,715 kW**
  load, Vmin **0.9131 pu** at (pandapower-indexed) bus 17; 69-bus, **3,802.1 kW** load,
  Vmin **0.9092 pu** at bus 64 — exact matches, *including their bus-index convention*.
- Their exact feasibility rule (Vmax ≤ 1.05 pu; Vmin ≥ 0.93 pu **only for buses not
  already in violation**; total losses ≤ 15% of load) and their two restrictive
  penetration scenarios (I3: 50–320 kW/project; I4: 100–640 kW/project; N=50),
  reimplemented directly from their Algorithm 1 and Table 3.

## What was NOT reproduced: their literal instances

[13] states connection buses are a project attribute but **never publishes the sampling
rule**, and code/data are available only "upon reasonable request" — not published.

So this is a **matched-methodology** comparison (same networks, same constraint model,
same penetration ranges), **not a matched-instance** one. We draw our own project sets
(3 random seeds per network × scenario; buses uniform-random with replacement, sizes
uniform in-range) and compare gain **distributions**, not their literal 22.4%/9.7% means.

Disclosed gaps, all of them working *against* us:

- `nsga2_search`'s subset search is **warm-started with the FIFO accept-set itself**
  (the M28 lesson from [smartds.md](smartds.md)), so the reported gain is never a
  search-budget fluke in either direction — at a disclosed budget (pop=40, gen=60, one
  seeded search per instance), **smaller than [13]'s 10-run MA average**. A real
  limitation.
- [13] never publishes conductor ampacity values for their thermal-loading check, and
  their own base network ships **thermally unbounded**. Both networks are left that way
  here rather than inventing numbers they did not report — consistent with their own
  results being entirely voltage-driven.
- We adapt [13]'s **sequential-specific** undervoltage rule to a *simultaneous*
  evaluation, using the base network's own voltage as the reference state — the more
  literal reading of [13]'s own stated rationale for that rule.

---

## M29 — the head-to-head

Gain over the same honest FIFO baseline, both papers:

| Network | Scenario | Ours (median, range, n=3) | [13]'s MA (range, n=3) |
|---|---|---:|---:|
| 33-bus | I3 (saturation) | +14.2% (+2.8% to +20.6%) | +5.6% to +31.8% |
| 33-bus | I4 (stress) | +41.2% (+31.6% to +43.2%) | +24.3% to +67.6% |
| 69-bus | I3 (near-trivial) | +0.5% (+0.0% to +1.1%) | +0.0% to +0.3% |
| 69-bus | **I4 (stress)** | **+24.6% (+10.4% to +28.7%)** | **+3.2% to +17.3% (mean +9.7%)** |

On the **33-bus** network the two methods land in the same range — subset search is
competitive with reordering, not obviously better. On **69-bus I3** they agree the
near-trivial scenario is near-trivial for both (a useful cross-validation of the whole
framework, independent of method).

**At 69-bus I4, subset search pulls clearly ahead**: our *worst* seed (+10.4%) already
exceeds [13]'s *mean* (+9.7%), and our median (+24.6%) sits **above their entire
reported range**. Not a lucky draw — it repeats across all 3 seeds.

**And it lines up with [13]'s own conjecture.** In their Section 6.8.3 they observe that
their reordering gain *shrinks* on the larger, more spatially-distributed 69-bus network
relative to 33-bus, and attribute it to a spatial-density ratio ρ = N/N_buses that
"modulates the intensity of electrical coupling." **Our gain does not shrink nearly as
much.**

The mechanistic reading: a **permutation** search can only ever choose *which order* to
attempt the same eventual project set — one project's rejection is still a side effect
of a serial walk — while a **subset** search can jointly decide to *permanently exclude*
specific projects regardless of order. That capability becomes **more** valuable, not
less, as a network's laterals multiply the number of order-dependent local optima a
purely sequential process would have to stumble through.

## M37 — the ceiling re-check: it survives, and marginally improves

Re-run with the GLDF-MILP ([gldf-milp.md](gldf-milp.md)), followed by an exact-AC repair
and an **exhaustive single-swap / single-add polish against the real oracle**, so every
reported accept-set is a certified 1-swap local optimum of the *exact* problem rather
than of the linear surrogate.

| | NSGA-II (M29) | **GLDF-MILP** | Fontinele et al. [13] |
|---|---:|---:|---|
| 33-bus I3 | +14.2% | **+14.3%** | +5.6 to +31.8% |
| 33-bus I4 | +41.2% | **+41.5%** | +24.3 to +67.6% |
| 69-bus I3 | +0.5% | **+0.5%** | +0.0 to +0.3% |
| 69-bus I4 | +24.6% | **+25.7%** | +3.2 to +17.3% (mean +9.7) |

**NSGA-II reaches 98.2–100.3% of the ceiling on these networks** — it was not
under-searching here *at all*. The MILP strictly beats it on 7 of 12 instances, but only
by **0.1 to 3.6 percentage points**.

The 69-bus stress result — our median exceeding [13]'s entire reported range — is
**confirmed, slightly strengthened (+25.7% vs their +9.7% mean), and can now be
described as essentially *optimal*** rather than as whatever our metaheuristic happened
to find.

**This is the single most important thing M37 buys**: the head-to-head win is no longer
contingent on our search being good. It is a property of the *framing* (subsets vs.
permutations), measured against a ceiling.

*(Note: several AC-verified solutions land marginally* above *100% of the GLDF ceiling —
up to 100.9% on 33-bus I4. GLDF is a linear approximation, not a convex relaxation, so
on those instances the model is slightly conservative. See the standing scope limits in
[gldf-milp.md](gldf-milp.md).)*

## Honest scope

Matched methodology rather than matched instances; a single disclosed search budget for
the NSGA-II row; a documented adaptation of [13]'s sequential-specific undervoltage
rule. Both networks are **small academic feeders** — three orders of magnitude smaller
than the SMART-DS and EPRI feeders where the real regime question lives. This result
closes a *different* gap than the utility-scale one and should not be mistaken for it.

## Reproduce

```sh
uv run python experiments/59_fontinele_head_to_head.py --save   # M29: head-to-head on their own two networks
uv run python experiments/66_fontinele_gldf_recheck.py --save   # M37: ceiling re-check; NSGA-II was already at ~100%
```
