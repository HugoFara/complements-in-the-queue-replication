# Experiment log — the SMART-DS negative, and the three attempts to kill it

**Experiments:** `57_smartds_voltage_regime.py` (M22/M26/M27),
`58_smartds_seeded_nsga2.py` (M28), `65_smartds_gldf_recheck.py` (M36)
**Paper:** the corpus result is a headline row of §5.1 *The residual across the corpus*;
the *audit* of it is §5.4 *Discharging the privacy hypothesis by enumeration*. This file
is the record behind both. The strawman correction below was an appendix of the
superseded draft and is not carried in the current manuscript.
**Status:** current. **The negative survived every attempt to overturn it.** This is
the load-bearing half of the regime map.

---

## The corpus

14 voltage-limited SMART-DS US feeders (700–5,000 buses), a second corpus chosen
specifically because the ENWL LV corpus is 91% *thermal*-limited while Sandia's
216-feeder MV study found 72% *voltage*-limited — so the regime rule, which gates on
voltage-limiting, had to be tested somewhere it should *work*.

**It corrected the rule rather than confirming it.** Large voltage-limited feeders show
no joint advantage either — the coupling gate, not just the voltage gate, is what
matters. And, search-independently, the corpus showed this project's **largest
previously-reported margins (+192%, +1367%) were strawman artifacts.**

## Appendix B's correction — where the giant margins came from

Earlier drafts headlined margins of **+192% (IEEE13)** and **+1367%** (a SMART-DS
feeder) against the **stop-dead** `greedy_baseline_dss`. Both were artifacts of the
baseline, not evidence the method is an order of magnitude better.

On the feeder that produced +1367%: **one 12.9 kW request sitting third in the queue is
individually infeasible** and, under stop-dead FCFS, blocks *every request behind it* —
stop-dead seats 2/40 (28.4 kW). The honest `skip_continue_baseline` simply skips that
request and seats **38/40 (432.3 kW)**. The "+1367% joint advantage" was almost entirely
capacity a real FCFS process **never loses in the first place**.

Across the 14 feeders: switching stop-dead → skip-continue recovers a **median +29%** (up
to +1420%); **skip-continue seats a median 95% of requested kW**; and against *that*
baseline the joint search shows **no advantage at all (0/5 feeders)**.

These numbers belong in the record as a **correction**, not as headline margins.

## M28 — ruling out a search-budget explanation for the 0/5

The 0/5 could in principle mean either "no exploitable room" or "NSGA-II's random
initial population never got close enough within budget." These feeders have thousands
of candidate buses, so a blind `BinaryRandomSampling()` population has essentially no
chance of resembling a good accept-set.

`nsga2_search_dss_seeded` tests this by warm-starting generation 0 with the
skip-continue **and** stop-dead accept-sets, at the identical budget. Because NSGA-II's
survival selection is elitist, **the seeded run cannot score worse than its own seeds**
on any feeder.

Empirically it recovers the *entire* unseeded gap (median +6.4%, up to +13.0%, across
the 4/5 feeders where unseeded search fell short) while finding **exactly 0.0% beyond
skip-continue on all 5 feeders** — never more.

So the unseeded gap *was* a real search-budget artifact — and correcting it changes
nothing about the substantive conclusion. This is also the origin of the **"seed the
search with FCFS"** lesson applied to every later experiment in the project.

---

## M36 — re-checking the negative with the GLDF-MILP

M35 ([gldf-milp.md](gldf-milp.md)) cast doubt on every NSGA-II-derived number in the
project, and **the most load-bearing one is this negative**. If NSGA-II systematically
leaves half the achievable capacity behind, "we searched and found nothing" needs
re-testing rather than trusting.

Three checks. The negative survives all three — and **the decisive one requires no
power-flow model at all.**

### 1. A rigorous, model-free ceiling

No method, however optimal, can accept more than *all* the requests. Skip-continue's
seated kW against the total requested kW therefore bounds every possible gain, exactly,
with **zero modelling**:

| Feeder | skip-continue | **Max conceivable gain** | Seeded NSGA-II | GLDF-MILP | Exact-AC 1-swap |
|---|---|---:|---:|---:|---:|
| uhs2·udt10746 | 437.4 kW (38/40) | **+7.4%** | +0.0% | +0.0% | +0.0% |
| uhs2·udt12473 | 439.1 (38/40) | **+6.9%** | +0.0% | +0.0% | +0.0% |
| uhs4·udt11389 | 480.2 (**40/40**) | **0.0%** | +0.0% | +0.0% | +0.0% |
| uhs4·udt12982 | 472.5 (39/40) | **+2.6%** | +0.0% | +0.0% | +0.0% |
| uhs7·udt9675 | 436.9 (38/40) | **+6.3%** | +0.0% | +0.0% | +0.0% |

Skip-continue already seats **38–40 of 40** requests, so **even a perfect oracle wins at
most 0–7.4% here — and on one feeder, literally nothing.** Compare Ckt24, where FCFS
left +150% to +490% on the table. The 0/5 cannot be a search artifact of any
consequence: **there is structurally almost nothing to find.**

### 2. The GLDF-MILP: 0/5

Same model as M35 but built from **OpenDSS's own admittance matrix**, since this corpus
runs the OpenDSS-direct oracle. On every feeder, at any margin where its answer
AC-reverifies, **the MILP's own optimum converges to exactly skip-continue's
accept-set.**

Two OpenDSS-specific gotchas had to be got right first (both caught by the correctness
gate, both documented in [gldf-milp.md](gldf-milp.md)): loads must be kept **out of
`Y`**, and — importantly — GLDF here **does not converge to zero error as the
perturbation vanishes**, under-predicting voltage drop by a persistent **3.6–6%**
because it drops the feedback through the existing constant-power loads. That error was
harmless on Ckt24 (1–3 mpu) but here is **comparable to the entire ~7% of available
room**. So "the MILP found nothing" is *not self-sufficient evidence* on this corpus,
and we did not treat it as such.

### 3. An exact-oracle check with no model error: 0/5

We enumerate the **full single-swap and single-add neighbourhood of skip-continue's
accept-set directly against the real AC oracle** — exhaustively, no model, ~70 solves at
K=40 — using the swap move set that [ckt7.md](ckt7.md) (M32) identified as the one this
problem actually needs.

**It finds no improvement on any feeder.** Skip-continue's allocation is a **certified
local optimum of the exact problem.**

### The mechanism, which is the interesting part

At zero safety margin the MILP *wants* 39 of the 40 requests on udt10746 (**+3.5%**) —
but the real AC oracle **rejects** that set (vmin = 0.9369, below the 0.94 limit). The
apparent room is a **model artifact**. It is not there in the real physics.

---

## Why this matters more than the positive results

Both halves of the regime map sharpen, in **opposite** directions:

- M35 showed the **positive** half was badly **understated** by the metaheuristic
  (Ckt24's real opportunity is +150% to +490%, not +8.7% to +168%).
- M36 shows the **negative** half is **real physics, not a search artifact** — and it
  can now be defended with a *model-free bound* rather than an appeal to "our
  metaheuristic did not find anything."

The "did you simply under-search?" objection — the first one a reviewer raises — is now
closed on **both** sides. A regime map whose negative half collapsed under a better
search would have been worthless; this one does not.

## Reproduce

```sh
uv run python experiments/57_smartds_voltage_regime.py --save   # corpus + strawman collapse
uv run python experiments/58_smartds_seeded_nsga2.py --save     # M28: rules out a search-budget artifact
uv run python experiments/65_smartds_gldf_recheck.py --save     # M36: MILP + model-free bound + exact-AC swap; survives 0/5
```
