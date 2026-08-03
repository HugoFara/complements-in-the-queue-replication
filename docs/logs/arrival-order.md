# Experiment log — M43/M44/M46, the arrival-order lottery

**Experiments:** `71_arrival_order_lottery.py` (M43, EPRI Ckt7),
`72_ckt24_arrival_order.py` (M44, EPRI Ckt24), both re-run under the empirical request
distribution (M46, `--empirical`)
**Paper:** Appendix A, *the arrival-order lottery* — see
[`docs/papers/complements-in-the-queue.md`](../papers/complements-in-the-queue.md).

> # ⚠ READ M46 FIRST — M43/M44's headline does not survive the real request distribution
>
> Everything in M43/M44 below was measured with request sizes drawn from a **synthetic
> `uniform[10,100] kW` band**. [M45](../../data/interconnection/PROVENANCE.md) replaced it
> with the real thing (70,665 CA interconnection requests: median **6.1 kW**, 96.8% under
> 20 kW, only 18.8% inside our band). Re-running with real sizes and the **same buses**:
>
> - **On Ckt24 the lottery is GONE.** All 200 arrival orders return **1145.3 kW** — a
>   **1.00× spread**, identical to the decimal, 149/150 seated, FCFS at **100% of the
>   optimum in every order.** The queue does not bind.
> - **On Ckt7 it survives but is much weaker** (mean 78.6% of optimum, vs 43.2% before).
> - **"No cheap sort key works" is FALSE under real sizes**: smallest-first now reaches
>   **86–100%** on both feeders, and the feeder-to-feeder *flip* disappears.
>
> **The synthetic band made requests ~5× too large (mean 55 vs 11 kW) and manufactured the
> binding regime that produced the entire result.** M43/M44 are retained below as the
> record of what was measured, not as findings. **Do not quote them.**

---

## What this replaces

M42 claimed *"the optimum belongs to the feeder; FCFS's variance is luck of arrival
order."* **That was not what M42 measured** — its three seeds were three different
*request sets*, each with one arrival order, so both varied. Retracted. This experiment
isolates arrival order properly: the request set is held **fixed** and only the queue is
permuted.

## Setup

| | Ckt7 | Ckt24 |
|---|---|---|
| buses | 1,255 | 6,058 |
| requests (MODERATE, seed 0) | 150 on 100 distinct buses | 150 on 146 distinct buses |
| requested | 8,176 kW | 8,259 kW |
| **certified optimum** | **1,130.3 kW** | **4,057.5 kW** |
| binding at the optimum | **voltage only** (vmax 1.0500; thermal 26.5pp slack) | **voltage AND thermal** (vmax 1.0500; trafo 99.4%, 0.6pp slack) |

The optimum is M41/M42's re-linearized fixed point: **AC-verified, margin zero, no
repair step** — a *certified* allocation, hence a **lower bound** on the true optimum.

> **Every "% of optimum" below is an UPPER bound on that rule's true share.** The
> denominator is certified, not optimal, so a rule's real performance can only be
> *worse* than stated. The direction is conservative.

---

## E1 — the lottery. **Kill criterion missed by an order of magnitude, on both feeders.**

The roadmap said: *if the p5–p95 spread is under 15%, there is no lottery and the paper
does not exist.* 1,000 random arrival orders per feeder:

| | Ckt7 | Ckt24 |
|---|---:|---:|
| worst order | 157.7 kW (14.0%) | 140.6 kW (3.5%) |
| p5 | 251.7 (22.3%) | 677.7 (16.7%) |
| **median** | **488.7 (43.2%)** | **1,881.8 (46.4%)** |
| p95 | 758.1 (67.1%) | 2,234.9 (55.1%) |
| luckiest order | 911.9 (80.7%) | 3,664.1 (90.3%) |
| **p95 / p5** | **3.0×** | **3.3×** |
| full range | 5.8× | 26.1× |
| projects seated | 4–30 of 150 | 3–80 of 150 |

Same feeder, same requests, same physics. **Only the order in the queue differs** — and
realized capacity swings by a factor of three between a typical unlucky queue and a
typical lucky one.

**The regularity replicates.** Two feeders of very different size, topology, voltage
class and binding physics both give **p95/p5 ≈ 3×** and a **median queue at 43–46% of
achievable**. The median queue leaves *more than half* the capacity on the floor.

## E2 — the sort key. **No cheap rule recovers the loss, and the knapsack model is wrong.**

The roadmap derived that under GLDF the knapsack value-density ratio is
`value/weight = |p| / (s(bus)·|p|) = 1/s(bus)` — independent of request size — so the
optimal greedy key should be **purely locational**, and size should be irrelevant.

**The algebra is right. The conclusion is false.**

| Rule | Ckt7 (w/ repl.) | Ckt7 (no repl.) | **Ckt24** |
|---|---:|---:|---:|
| smallest-first | **88.2%** | **93.1%** | 55.1% |
| value-density (knapsack ratio) | 57.1% | 22.3% | **65.2%** |
| least-sensitive-first | 51.8% | 22.3% | 45.6% |
| largest-first | 55.2% | 32.2% | 18.3% |
| arrival order (random, mean) | 43.7% | — | 41.6% |
| `binding_bus_density*` (see below) | 65.0% | 63.6% | **4.6%** |
| MILP optimum | 100% | 100% | 100% |

Three things fall out, in increasing order of importance.

### 1. The algebra is confirmed, cleanly

With **zero** ties (Ckt7 without replacement), `value_density` and
`least_sensitive_first` return **identical** results (253.8 kW both) — they are the same
rule, exactly as derived. Ties are the only thing that separated them.

### 2. No deployable rule dominates, and the ranking *flips* between feeders

Smallest-first is best on Ckt7 (88–93%) and mediocre on Ckt24 (55%). Value-density is
best on Ckt24 (65%) and poor-to-terrible on Ckt7 (22–57%). **There is no single cheap
sort key you could put in a regulatory rule.** And on Ckt24 — the larger, real,
multi-constrained feeder — the *best* cheap key still leaves **35%** of the capacity
unclaimed.

### 3. **Why the knapsack framing fails — and this is the result**

A first hypothesis (bus-sharing: the ratio can't separate requests at the same bus) was
**tested and falsified** — removing all ties made value-density *worse* (57.1% → 22.3%),
not better.

The real reason is that **the weight was wrong, and then that there is no weight at all.**

- The knapsack weight is *not* a request's own bus sensitivity `max_j M[j,i]`. It is how
  much the request loads the constraint that **actually binds**, `M[crit,i]`. On Ckt7
  these are **uncorrelated: r = −0.03.** A bus's own voltage sensitivity tells you
  essentially *nothing* about how much it consumes the scarce resource.
- Fixing the weight nearly **triples** the rule on Ckt7 (22.3% → 63.6%). So the knapsack
  framing does carry real information — **but it still loses to physics-blind
  smallest-first (93.1%), and it needs the binding bus, which you only learn by solving
  the problem you were trying to avoid solving.** (Hence the `*`: it is an upper bound on
  what the knapsack idea can do, not a deployable rule.)
- **On Ckt24 the same correctly-weighted rule collapses to 4.6% — far worse than
  random.** Because Ckt24's optimum is bound by **voltage *and* thermal simultaneously**
  (vmax exactly 1.0500 pu *and* transformer at 99.4%, 0.6pp from its limit). Sorting by
  coupling to the voltage-binding bus seats requests that immediately blow the
  transformer.

> **A knapsack presupposes one scarce resource. A real feeder has thousands of
> constraints of two different physical types, and *which* of them binds depends on the
> accept-set you choose. There is no scalar weight to sort by, so no greedy ordering rule
> — however clever — can be optimal.**
>
> **This is the empirical case for joint allocation.** The loss is *not* recoverable by
> reordering the queue. Pollitt et al. frame the connection queue as a knapsack: the
> instinct (FCFS misallocates a scarce resource) is right, and the model is wrong — and
> we can now show exactly how it fails, on a real feeder, with real power flow.

## E3 — position value. **A project's fate is its slot, not its physics.**

P(seated) by queue-position decile, over 1,000 permutations × 150 projects:

| decile | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | first/last |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Ckt7** | 45.4 | 13.7 | 7.8 | 5.8 | 4.5 | 3.6 | 3.0 | 2.2 | 1.9 | 1.5 | **30×** |
| **Ckt24** | 72.9 | 66.6 | 59.1 | 37.9 | 7.4 | 1.6 | 0.8 | 0.4 | 0.3 | 0.2 | **391×** |

Of the 150 requests:

| | always seated (>95%) | never seated (<5%) | **coin flip (5–95%)** |
|---|---:|---:|---:|
| Ckt7 | 1 | 67 | **82** |
| Ckt24 | 0 | 37 | **113** |

**On both feeders a majority of projects are coin flips** — their outcome is determined
by where they landed in the queue, not by their own physical merit. On Ckt24, *no*
project is guaranteed a connection on merit alone.

This is the equity claim, measured rather than asserted, and it retires the
queue-inversion/displacement metric nobody outside this project cared about.

---

## Two bugs and a falsified hypothesis — recorded, because they cost real time

1. **`density = size/(sens·size)` instead of `1/sens`.** Algebraically identical, but
   float rounding turned *exact* ties (same-bus requests) into pseudo-random tie-breaks,
   and the two spellings of the **same rule** differed by 16%. Fixed. It did surface a
   real property: 50 of Ckt7's 150 requests share a bus, and the tie-break alone is worth
   ~16% of the answer.
2. **Ckt24's workers must not rebuild the network.** Its substation has a **regulator**,
   so importing the OpenDSS model makes the engine write `REG_subxfmr_regulator.csv` into
   the CWD; N workers racing on that file fail with OpenDSS error #303. The workers never
   needed OpenDSS — they only run `pp.runpp` on an imported pandapower net — so the
   parent builds the context once and `fork` shares it. (Ckt7 has no regulator, which is
   why exp71 never tripped it.)
3. **The bus-sharing hypothesis was wrong.** "Value-density loses on Ckt7 because 33% of
   its queue is tied" was plausible, consistent with both feeders, and **falsified by the
   direct test** (removing ties made it worse). *This is the third time this project has
   had a mechanism story that fit the data and failed a direct test — cf. the M40
   retraction. State mechanisms as hypotheses and test them before they reach a document.*

## Honest limits — what this does NOT yet establish

1. **The request size distribution is synthetic** (uniform [10, 100] kW). Smallest-first's
   strong showing on Ckt7 is exactly the kind of result a bounded uniform band would
   flatter, and it may not survive a heavy-tailed real distribution. **P2 in the roadmap
   (an empirical request distribution) is the critical path.**
2. **Smallest-first is trivially gameable** — split one 100 kW project into ten 10 kW ones.
   Any policy recommendation must confront that; it may be fatal to it.
3. **One request set per feeder for E1/E3** (seed 0), 3 for E2 on Ckt7. Needs ≥50.
4. **A uniform random permutation is a model of arrival order**, and a crude one. Note
   *largest-first* — a plausible proxy for "big developers move first" — is **worse than
   random on both feeders** (18.3% and 32–55%). If that holds, real arrival order may be
   *systematically worse* than the lottery, which strengthens the paper.
5. Single snapshot, constant-power PV, no Volt/VAr — unchanged, and it bites here too.

## Reproduce

```sh
uv run python experiments/71_arrival_order_lottery.py --permutations 1000 --n-jobs 8 --save  # Ckt7:  E1+E2+E3
uv run python experiments/72_ckt24_arrival_order.py  --permutations 1000 --n-jobs 8 --save  # Ckt24: E1+E2+E3
uv run python experiments/71_arrival_order_lottery.py --no-replacement --sortkeys-only      # the zero-ties control
```

---

# M46 — the same experiments under the REAL request distribution

**What changed:** request *sizes* now come from the CA interconnection queue
([M45](../../data/interconnection/PROVENANCE.md)) instead of `uniform[10,100] kW`. The
**bus draw is identical**, so every difference below is attributable to the size
distribution alone. Run as `--empirical`.

The synthetic band said 150 requests = **8,176 kW**. The real distribution says the same
150 requests = **1,304 kW**. That is the whole story.

## E1 — the lottery

| | **Ckt24** (6,058 buses) | **Ckt7** (1,255 buses) |
|---|---|---|
| FCFS over 200 arrival orders | **1145.3 – 1145.3 kW** | 177.3 – 864.1 kW |
| spread (full range) | **1.00×** | 4.87× |
| FCFS as % of optimum | **100% in every order** | mean 78.6% |
| requests seated | **149–149** of 150 | 19–133 of 150 |
| *(synthetic, for contrast)* | *26.1× spread, median 46.4%* | *5.8× spread, median 43.2%* |

**On Ckt24 the lottery does not shrink — it disappears.** Every one of 200 permutations
returns the identical allocation, to the decimal. The queue never binds: 149 of 150
requests fit regardless of order, and **FCFS is already optimal**.

This is precisely the roadmap's **kill criterion** ("if the FCFS distribution is tight,
there is no lottery and this paper does not exist"), **met on the flagship feeder.**

On Ckt7 — smaller, tighter, ~1 MW of hosting capacity against 1.3 MW of requests — a
lottery remains, but a much weaker one.

## E2 — the sort key. **M44's conclusion reverses.**

| Rule | Ckt7 MOD | Ckt7 STRESS | **Ckt24 MOD** | Ckt24 STRESS |
|---|---:|---:|---:|---:|
| **smallest-first** | **90.6%** | **86.1%** | **100.0%** | **96.3%** |
| binding-bus density\* | 87.4% | 77.2% | 100.0% | 62.1% |
| value-density | 75.6% | 56.4% | 100.0% | 88.0% |
| least-sensitive-first | 74.7% | 57.0% | 100.0% | 88.0% |
| largest-first | 42.4% | 23.0% | 100.0% | 57.9% |
| MILP optimum | 100% | 100% | 100% | 100% |

Two of M44's three conclusions are **withdrawn**:

- **"No cheap sort key recovers the loss."** False. Smallest-first reaches **86–100%**
  everywhere, at zero compute and zero process change.
- **"The ranking flips between feeders."** Gone — smallest-first is now best on both. The
  flip was an artifact of the band (smallest-first read 55.1% on Ckt24 with fake sizes and
  100% with real ones).

What **survives**: **largest-first is still bad** (23–58%) — and that is the realistic
proxy for *"big developers move first"*, which M45 shows is directionally what real queues
do (Spearman(arrival rank, size) = −0.21).

**A caveat that must travel with any policy reading:** smallest-first on a queue that is
97% residential means *"residential first, commercial last"* — and it is **trivially
gameable** by splitting one project into ten.

## The yardstick broke on Ckt7 — ~~and we do not hide it~~ **and M48 found out why: a bug**

> **⚠ SUPERSEDED BY M48.** The diagnosis below ("the linearization is loose in the
> many-small-requests regime") is **wrong**, and it was the fourth consecutive time this
> project blamed the linear model for something else. Kept because the *symptom* was real
> and correctly reported; the *explanation* was not. See [ckt7.md](ckt7.md#m48).

Ckt7's **luckiest arrival order reaches 865.5 kW against the MILP's 852.4 kW — 101.6%
(1,000 orders).** FCFS beat our "optimum".

~~The certified allocation is only a *lower* bound, and in the many-small-requests regime
it is a **loose** one: the re-linearization loop converges to an AC-infeasible point, the
repair step drops 28 requests, and a lucky queue walk clears the result.~~

**The actual cause (M48): the Ckt7 thermal model was EMPTY.** `build_thermal`'s spanning
tree was built from `net.line` + `net.trafo` and skipped Ckt7's **9 closed bus-bus
switches**, which severs the graph at the substation — the walk reached **6 of 1,255
buses** and produced **one constraint row**. Under real request sizes the MILP stacks
~135 tiny systems behind a 500 kVA service transformer, overloads it to 106–108%, and the
AC oracle rejects every iterate. The loop never re-enters the feasible region and returns
the *empty-anchor* MILP. FCFS, which consults the real oracle at every step, respects the
transformer — **so a blind optimizer lost to a sighted greedy walk.** Nothing to do with
linearization error: GLDF's voltage optimism at the optimum is **−1.19 → 0.00 mpu**.

**After the fix**, on the same instance (K=150, seed 0, 1,000 arrival orders):

| | pre-M48 | **fixed** |
|---|---:|---:|
| Yardstick | 852.4 kW | **866.5 kW** |
| Best of 1,000 FCFS orders | 865.5 kW | 865.5 kW |
| **Beaten by a queue walk?** | **YES (101.6%)** | **No (99.9%)** |
| AC-maximal? | no (7 of 16 rejected still fit) | **yes** |
| Median FCFS | 776.8 kW | **776.8 kW = 89.7%** |

**The arithmetic is now self-consistent, and the "% of achievable" figures on
Ckt7-empirical are usable.** Two honesty notes that must travel with them:

1. **The margin is 1.0 kW.** The yardstick beats the luckiest of 1,000 random orders by
   **0.1%**. It is a *certified lower bound* on achievable capacity, not a proven optimum —
   so it is safe (`FCFS/D ≥ FCFS/O`, hence the reported loss **understates** the true
   loss), but it is not comfortable. On Ckt7 at K=150 the ceiling is essentially
   **reachable by luck**; what the lottery costs is the *downside*, not the upside.
2. The model-free metric — the **spread of FCFS outcomes across arrival orders** — needed
   no yardstick at all, and is why **M47's onset survived this bug intact.** It remains
   what the paper should lead with.

## ~~A regression the empirical distribution exposed~~ — **RETRACTED (M48)**

> **This section was wrong.** It is kept, struck through, because the mistake is the
> point: a *correct observation* was given a *plausible mechanism* that was never tested,
> and that mechanism then misdirected the next milestone.

~~**M41/M42's "the fixed point is AC-feasible at margin zero, 6/6, with zero repair drops"
does not survive real request sizes.** That was measured on ~40 large accepted requests.
Under the empirical distribution the MILP accepts ~190 tiny ones, **linearization error
accumulates across them**, and the fixed point lands outside the AC limit.~~

The observation was right — the fixed point *did* land AC-infeasible under real request
sizes. The mechanism was invented. **Linearization error accumulates across nothing here:
the voltage prediction is exact to 0.00 mpu.** The fixed point landed outside the AC limit
because it was **overloading a transformer the model could not see** (M48). With the
thermal graph fixed, the loop is AC-feasible on **13 of 15** iterates and needs neither a
margin nor a repair step — M41/M42's claim survives real request sizes after all.

*(What does survive from this section: `relinearized_optimum` in exp71/72 had **no repair
fallback** — I dropped it when re-implementing M42's loop — so Ckt7 STRESS silently
returned "optimum = 0.0 kW". That was a genuine bug, and it is fixed.)*

## Where this leaves the paper

The claim *"FCFS is a lottery"* was, to a large extent, **an artifact of a request-size
band nobody had ever defended.** What remains is a sharper and more honest question, and
it is the regime map with an empirically-grounded axis:

> **How much real request pressure does it take before a feeder's queue starts to bind —
> and what does arrival order cost once it does?**

At 150 real requests Ckt24 is nowhere near binding. PV penetration is rising, so this is a
forward-looking question rather than a deflationary one. **M47 (the pressure sweep) tests
it.**

---

# M47 — the pressure sweep: the lottery is a THRESHOLD, not a property

**Experiment:** `74_pressure_sweep.py`. Empirical request sizes throughout (M45). Queue
length **K** is swept; the request set is fixed at each K and only the arrival order is
permuted (40 orders per K).

**The headline metric is model-free.** The spread of FCFS realized capacity across random
arrival orders needs no optimum, no MILP, no power-flow model beyond the AC oracle — which
matters, because M46 showed the MILP yardstick is unreliable in exactly this regime.

## Result: below a threshold, arrival order does not matter AT ALL

**200 arrival orders per K** (the n=40 first pass is superseded — see *Honest limits*).

> **⚠ The Ckt7 `optimum` column below (and every "% of opt" derived from it) is
> SUPERSEDED — it was computed with the broken thermal model (M48) and is understated.**
> At K=150 the corrected yardstick is **866.5 kW, not 852**. **Everything else in these
> tables is model-free** — the FCFS walks consult the real AC oracle — so the spreads, the
> IQR ratios, the CVs, the seated fractions and *the onset itself* are unaffected. **That
> is precisely why the headline survived the bug**, and why the paper leads with the
> model-free metric. M49 (`76_onset.py`) re-measures the yardstick columns on a finer grid.

| Ckt7 | K | requested | ~~optimum~~ | **p95/p5** | **IQR ratio** | **CV** | ~~median FCFS~~ | seated |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| | 50 | 338 kW | ~~338~~ | **1.00×** | 1.00 | 0.00 | ~~100% of opt~~ | 100% |
| | 100 | 912 | ~~664~~ | **1.00×** | 1.00 | 0.00 | ~~100% of opt~~ | 98% |
| **← onset →** | **125** | **1,148** | ~~812~~ | **3.55×** | **1.13** | 0.26 | ~~87% of opt~~ | 84% |
| | 150 | 1,304 | ~~852~~ **866.5** | 3.74× | 1.14 | 0.32 | **89.7%** | 77% |
| | 175 | 1,524 | ~~882~~ | 3.27× | 1.13 | 0.21 | ~~80%~~ | 58% |
| | 200 | 1,703 | ~~908~~ | 3.10× | 1.13 | 0.19 | ~~79%~~ | 52% |
| | 250 | 2,477 | ~~967~~ | 2.90× | 1.15 | 0.20 | ~~75%~~ | 42% |
| | 400 | 4,445 | ~~1,115~~ | 3.22× | 1.12 | 0.20 | ~~67%~~ | 27% |

| Ckt24 | K | requested | optimum | **p95/p5** | **IQR ratio** | **CV** | median FCFS | seated |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| | 150 | 1,304 kW | 1,145 | **1.00×** | 1.00 | 0.00 | 100% of opt | 99% |
| **← onset →** | **300** | **3,589** | **2,113** | **1.70×** | **1.57** | 0.23 | 59% | 58% |
| | 600 | 6,254 | 4,276 | 3.39× | **2.04** | 0.36 | 78% | 75% |

### 1. The onset is real and sharp

**Below it the spread is exactly 1.00× — every arrival order returns the identical
allocation, to the decimal.** That is not a small effect measured noisily; it is the
*absence* of an effect, and no amount of sampling could manufacture it. FCFS is simply
optimal there, and joint allocation is worth nothing.

Ckt7's onset sits between **K = 100 and 125**; Ckt24's between **K = 150 and 300**.

### 2. The n=40 "magnitude curve" was noise — and the truth is more interesting

At n=40 Ckt7's spread above the onset was ragged (3.58 → 1.42 → 2.72 → 1.90) and I read
it as physics. At n=200 it is a flat **~3× plateau**. The raggedness was sampling noise in
p5, exactly as suspected.

### 3. But `p95/p5` was hiding the actual shape, and the two feeders differ

The robust statistics — now saved, and stable at this n — say something `p95/p5` cannot:

- **On Ckt7 the lottery is a LEFT-TAIL RISK, not a coin flip.** The IQR ratio is only
  **1.12–1.15** — the middle half of arrival orders lands within ~13% of each other, and
  the **median order already achieves 67–88% of the optimum**. The 3× headline is driven
  entirely by a thin tail: ~5% of orders collapse to 220–290 kW against a median of ~750.
  *Most queues are fine; a minority are catastrophic.*
- **On Ckt24 the dispersion is genuinely broad.** IQR ratio **1.57 → 2.04**, CV 0.23 →
  0.36. Here the whole distribution is spread out, and the median order gives up 22–41% of
  the achievable capacity.

**This matters for how the result is stated.** "FCFS is a 3× lottery" would be a real
overstatement of the *typical* loss on Ckt7. The defensible claims are: *below the onset,
arrival order costs nothing; above it, the median queue gives up 12–41% of achievable
capacity, and the unluckiest few percent give up 70%+.*

## Where do real feeders sit today? Below the threshold.

M45's arrival data: a median of **30** requests per service ZIP (p90 = 135, max = 572) over
~1.3 years of arrivals. Ckt7's threshold is at **K ≈ 100–125**; Ckt24's is between **150
and 300**.

> **At today's real request rates, both feeders sit at or below the onset. FCFS is
> currently fine. The arrival-order lottery is a problem that switches on as penetration
> rises — which makes this a forward-looking result, not a deflationary one.**

That is the paper: *how much request pressure does it take before the queue starts to
bind, and what does arrival order cost once it does?*

## A finding nobody was looking for: hosting capacity is not a scalar

Ckt24's certified optimum grows from **1,145 kW at K=150** to **7,039 kW at K=1000** (978
of 1000 requests seated). The *same feeder* hosts **six times more PV** when the same
energy is spread across ~978 connection points instead of ~149.

**Hosting capacity is not a fixed property of a feeder — it is a function of how many
distinct points you spread the injection over.** This reframes the whole exercise: the
scarce resource is not "MW of headroom" but *headroom at a given location*, and it is why
spreading (which smallest-first does by accident, and the MILP does deliberately) wins.

## Honest limits

1. **RESOLVED.** The first pass used n=40, where `p95/p5` is a 2nd-order statistic; its
   ragged above-onset curve was sampling noise. Re-run at **n=200** with the raw walks
   retained; `exp74` now defaults to 200 permutations and reports the IQR ratio and CV
   alongside. **Quote the IQR/CV and the median, not `p95/p5` alone** — on Ckt7 the tail
   ratio overstates the typical loss by a wide margin (3.2× vs an IQR of 1.13).
2. **"Pressure" (requested ÷ optimum) is not a clean control variable**, because the
   optimum itself grows with K (see above). Ckt24's pressure column is non-monotonic
   (1.14 → 1.70 → 1.46 → 1.59) for that reason. **Use K, or requested kW, as the x-axis.**
3. ~~**The MILP yardstick remains soft in this regime** (M46).~~ **RESOLVED by M48** — and
   it was not "soft", it was **broken**: an empty thermal model on Ckt7. The yardstick is
   now AC-maximal and dominates all 1,000 arrival orders. **The sweep never depended on
   it** — the spread metric is model-free, which is the only reason the onset survived the
   bug. The Ckt7 `optimum` column above is superseded; M49 re-measures it.
4. Ckt24's K=1000+ points were cut from the n=200 re-run — cost scales ~K² (K=600 alone
   took 2 h at n=200) and the onset, which is the scientifically interesting part, is
   already bracketed on both feeders. The n=40 pass did reach K=1000 (spread 4.43×,
   37% seated), consistent with continued growth on Ckt24.
5. ~~One request set per K (seed 0). The onset location needs replication across seeds.~~
   **DONE (M49) — and the replication changed the result.** See below.

## Reproduce

```sh
uv run python experiments/74_pressure_sweep.py --feeder ckt7  --permutations 200 --save
uv run python experiments/74_pressure_sweep.py --feeder ckt24 --ks 150 300 600 1000 --permutations 200 --save
```

---

# M49 — the onset, located properly. **There are TWO thresholds, and M47 only saw one.**

**Experiment:** `76_onset.py`. Ckt7, **9 queue lengths × 5 request draws × 200 arrival
orders** (45 cells). Empirical request sizes (M45) throughout. **Every headline statistic
here is model-free** — the FCFS walks consult only the AC oracle. The certified yardstick
(M48) is computed alongside, to express *magnitude*, and both M48 invariants are asserted
at every cell: **AC-maximal on 45/45, never beaten by a queue walk on 45/45.**

M47 located the onset with **one request draw** and **one K either side of it**. Replicating
across five draws splits what looked like a single threshold into two distinct ones with
different causes — and shows that M47's headline metric (the capacity spread) **cannot see
the one that matters.**

## Result

| K | draws with a lottery | capacity spread | **% of applicants who are coin-flips** | median order gets | seated |
|---:|---:|---|---:|---|---:|
| 25 | **0 / 5** | 1.00× | 0% | **100% of achievable** | 100% |
| 50 | 1 / 5 | 1.00–2.00× | 0% | 100% | 98–100% |
| 75 | 2 / 5 | 1.00–2.80× | 0% | 87–100% | 81–100% |
| 100 | 4 / 5 | 1.00–3.68× | 0% | 73–100% | 67–98% |
| **110** | **5 / 5** | 1.12–3.57× | 3.6% | 67–100% | 62–97% |
| 120 | 5 / 5 | 1.17–3.85× | **43%** | 73–99% | 62–95% |
| 130 | 5 / 5 | 1.36–4.02× | **78%** | 71–86% | 56–82% |
| 140 | 5 / 5 | 1.52–3.60× | **86%** | 70–91% | 56–83% |
| 150 | 5 / 5 | 1.57–3.74× | **85%** | 71–87% | 54–77% |

*(coin-flip = a request whose seat rate across the 200 orders is between 25% and 75% — its
fate is decided by luck, not by its size or its location.)*

### 1. **The capacity threshold** — K ≈ 50–110, and it depends on the DRAW

The total kW the feeder hosts starts depending on arrival order somewhere between K=50 and
K=110 — but **where** depends on which requests you happened to draw:

| draw | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| onset K | 110 | 100 | **50** | 75 | 100 |

M47's single-draw estimate (100–125) was on the high side and, worse, **hid a factor-of-two
spread across draws.** Draw 2's queue starts gambling at **K=50**.

### 2. **The equity threshold** — K ≈ 110–130, and it is SHARP

The fraction of applicants whose seating is decided by luck is **exactly zero up to K=100**,
then: **3.6% → 43% → 78% → 86%** at K = 110, 120, 130, 140. It saturates around **85% of all
applicants**.

**This is the result M47 could not see**, because it is invisible to the capacity spread:

- **K=110, draw 0:** capacity spread **3.45×** — yet **zero** coin-flips. The feeder's total
  swings wildly, but *who* gets seated is still decided by physics.
- **K=100, draw 3:** capacity spread only **1.46×** — yet **75 of 100** applicants are
  coin-flips. The total is nearly stable while the seated *set* is close to random.

> **Aggregate capacity and individual fairness are different failure modes with different
> causes and different thresholds.** Queue pressure moves the *total*; it is a separate,
> later, sharper transition that randomizes *who gets in*. A queue can look perfectly
> efficient in MW and still be a lottery for the people standing in it — **and the equity
> threshold is the one a regulator has to act on.**

### 3. Below the pressure onset, **one marginally-feasible large request is the whole story**

At K ≤ 100, grouping the 20 cells by the fate of the queue's **largest** request:

| largest request | cells | spread | coin-flips | median order gets |
|---|---:|---:|---:|---:|
| **always** fits (seat rate ≥ 95%) | 9 | **1.00×** | 0 | **100%** |
| **never** fits (seat rate ≤ 5%) | 8 | **1.01×** | 0 | **100%** |
| **MARGINAL** (5–95%) | 3 | **1.46×** | 8 | **86.5%** |

**17 of 20 low-pressure cells are exactly optimal in every single arrival order.** The three
that are not are precisely the three whose largest request *might or might not* fit.

The mechanism, from the cell that first exposed it (K=50, draw 2 — 50 requests, 401 kW,
median 6.1 kW, **one request of 74.2 kW = 18.5% of the queue**, seat rate **32%**):

- arrives **late** → rejected → all 49 small projects fit → **326.3 kW**
- arrives **early** → seated → eats the headroom and **blocks the small ones behind it**
  (seated count collapses from 49 to as low as **13**) → **as low as 152.2 kW**

**A marginally-feasible large request is a BLOCKER, and it manufactures a lottery at a queue
length where none otherwise exists.** Note the direction: seating the big project makes the
feeder host *less in total*. The median outcome equals the *best* outcome — **the lottery is
pure downside.**

**Size alone is not the variable; marginal feasibility is.** A request that *always* fits or
*never* fits is decided by physics, however large:

| largest request | share of queue | seat rate | lottery? |
|---|---:|---:|---|
| 17–19 kW | ~5% | 100% | **no** (1.00×) |
| **74 kW** | **18.5%** | **32%** | **YES (2.00×)** |
| 90 kW | 22.4% | 0% | **no** (1.00×) |
| 794 kW | 62.5% | 0% | **no** (1.00×) |

On the full grid `top_share` is only a weak predictor of the spread (ρ = +0.37, p = 0.05)
precisely because it is confounded — it does not distinguish "too big to fit" from "big
enough to hurt". **Marginal feasibility is an interaction between the request and the
feeder's headroom, and that is what a size-only statistic misses.**

### 4. Being big **hurts** you in a serial queue

**Spearman(request size, seat rate) = −0.30** (median over 45 cells; range −0.46 to −0.07).
Larger requests are systematically *less* likely to be seated under FCFS.

That is the opposite of the "big developers capture the queue" worry, and it cuts both ways:
big projects mostly lose — but on the occasions when one *does* arrive early, it blocks
everyone behind it and the feeder hosts less in aggregate. It is also why M46 found
largest-first to be the **worst** sort key (23–58% of achievable), and why it matters that
real queues are mildly largest-first (M45: Spearman(arrival rank, size) = −0.21).

## Where does this leave the paper?

M47's story — *"a threshold in request pressure, below which arrival order costs nothing"* —
**survives as the primary axis** (Spearman(spread, K) = **+0.70**, p < 0.0001). But it is now
the *less* interesting of two findings, and the sharper one is:

> **The queue becomes a lottery for the people in it at a later, sharper threshold than it
> becomes a lottery for the feeder's total capacity — and below that threshold, the only
> thing that can randomize outcomes is a single request that might or might not fit.**

## Honest limits

1. **Ckt7 only.** Ckt24 is running (K = 50…300 × 3 draws); until it lands, the two-threshold
   claim rests on one feeder.
2. **Only 3 low-pressure MARGINAL cells.** The contrast is stark (1.46× vs 1.00×/1.01×, and
   86.5% vs 100.0%), but the blocker claim needs more draws to be quantitative rather than
   directional. It is *cheap* to get: the statistic is model-free.
3. The coin-flip band (25–75%) and the contested band (5–95%) are thresholds on a continuum.
   **Raw per-request seat rates are saved** (`m49_seatrates_ckt7.csv.gz`), so any band can be
   re-derived without re-running the sweep — a direct lesson from M47, which had to be re-run
   once because only summary statistics were kept.
4. Uniform random permutation is still a *model* of arrival order (P2c: real queues have
   Spearman(rank, size) = −0.21).

## Reproduce

```sh
uv run python experiments/76_onset.py --feeder ckt7  --save     # 45 cells, ~3 h
uv run python experiments/76_onset.py --feeder ckt24 --seeds 0 1 2 --save
```

---

# M49b — Ckt24. The two thresholds replicate, **and they falsify M47's "pressure" axis**

Ckt24: K = 50…300 × 3 draws × 200 orders (18 cells).

| K | draws with a lottery | spread | **% coin-flips** | median order gets |
|---:|---:|---|---:|---|
| 50 / 100 / 150 | **0 / 3** | 1.00× | 0% | **100% of achievable** |
| 200 | 1 / 3 | 1.00–1.24× | 0–0.5% | 82–100% |
| 250 | 1 / 3 | 1.00–1.49× | 0–5.2% | 69–100% |
| 300 | 1 / 3 | 1.00–**1.70×** | 0–**87.3%** | **59.5**–100% |

The **equity threshold replicates** with the same shape — on draw 0, coin-flips go
**0 → 0.5% → 5.2% → 87.3%** at K = 200, 250, 300: later and sharper than the capacity
threshold, exactly as on Ckt7. (Draw 0 also reproduces M47's Ckt24 numbers to the decimal:
K=150 → 1.00×, K=300 → 1.70×.)

**But two of the three draws never gamble at all, even at K = 300** — and that is the
interesting part.

---

# M50 — **the lottery is NOT a pressure threshold.** It is a *reachable*-pressure threshold

**Experiment:** `77_effective_pressure.py`.

## Our own M47 headline, falsified

| Ckt24, K=300 | requested | achievable | **pressure** | lottery? |
|---|---:|---:|---:|---|
| draw 0 | 3,589 kW | 2,113 | **1.70×** | **YES** — 1.70× spread, 262 coin-flips |
| draw 1 | 3,918 | 2,054 | **1.91×** | **no** — 1.00×, every order identical |
| draw 2 | 4,282 | 2,030 | **2.11×** | **no** — 1.00× |

**The two most over-subscribed queues have no lottery; the least over-subscribed one gambles.**
Across Ckt24's 18 cells, **Spearman(spread, pressure) = −0.07.** M47's axis does not merely
work poorly here — it does not work at all.

## Why, and the statistic that does work

Draws 1 and 2 seat **98% of their requests** while realizing ~50% of requested kW. Their
queues are **bimodal**: ~294 tiny requests that obviously all fit, plus a handful of
674–793 kW monsters that **never** fit (seat rate 0.000, in every cell, in every order).
Nothing sits near the feasibility boundary, so **physics decides everything — at 2× pressure.**

A request that can never be seated inflates the queue's kW and contributes no contest. So
discount it:

> **effective pressure := (kW of requests that can be seated *at all*) ÷ (kW achievable)**

where *"can be seated at all"* is tested **one request at a time, alone, on the base feeder**.
That is **K AC solves** — no MILP, no linearization, no permutations.

## Result — pooled over all 63 cells of M49, both feeders

| predictor | Ckt7 | Ckt24 | **pooled (63 cells)** |
|---|---:|---:|---:|
| M47's raw pressure | +0.60 | **−0.07** | +0.358 |
| queue length K | +0.70 | +0.55 | +0.331 |
| **effective pressure** | **+0.81** | **+0.75** | **+0.892** (p = 9e-23) |

And unlike raw pressure it has a **threshold, exactly where theory says it must be — at 1.0:**

| effective pressure | cells | lottery | median spread | median order gets |
|---|---:|---:|---:|---:|
| < 1.00 | 5 | **0%** | 1.00× | **100%** |
| 1.00–1.02 | 26 | 12% | 1.00× | **100%** |
| **1.02–1.10** | 10 | **100%** | 1.36× | 82% |
| **> 1.10** | 22 | **100%** | 1.87× | 75% |

**Every cell above 1.02 is a lottery (32/32). Every cell below 1.01 is not.**

> ## The law
> **A connection queue becomes a lottery when the requests that CAN be seated collectively
> exceed what the feeder can host.** Requests that can never be seated are noise: they inflate
> the queue, and they are rejected in every arrival order by *physics*, not by luck.

## This subsumes M49's blocker finding

A "marginally feasible" large request is exactly one that **counts toward the reachable mass
and pushes it past the ceiling**. A request too big to ever fit does not count, **however
large**. That is why:

| largest request | share of its queue | seat rate | lottery? |
|---|---:|---:|---|
| 74 kW | 18.5% | 32% (marginal) | **YES — 2.00×** |
| 90 kW | 22.4% | 0% (never fits) | no — 1.00× |
| **794 kW** | **62.5%** | 0% (never fits) | **no — 1.00×** |

Size was never the variable. **Reachability is.**

## Why this matters practically — it is a *deployable* test

The median real queue carries **21% of its requested kW in requests that can never be seated**
— and those are just **1% of requests by count** (the M45 heavy tail). Ignoring them is exactly
what makes raw pressure useless.

Every other diagnostic in this project needs either a power-flow model (GLDF/MILP — and M48 is
a cautionary tale about trusting one) or hundreds of AC queue walks. **This needs K AC solves
and nothing else.** A DNO can run it on a live queue in seconds and learn whether their FCFS
process is currently a lottery or merely a formality.

## Honest limits

1. The **level** of the threshold is sharp pooled (32/32 above 1.02), but Ckt24's own cells all
   sit in 1.00–1.04 — its queues are barely over-subscribed in *reachable* terms even at K=300,
   because ~98% of its requests fit. So Ckt24 constrains the threshold's location weakly; the
   pooled sharpness leans on Ckt7.
2. `achievable` in the denominator is the M48 certified allocation — a *lower* bound, so
   effective pressure is a mild **over**-estimate. It needs a yardstick; the *screen* (which
   requests can never be seated) does not.
3. Two feeders, 63 cells, one snapshot. The law is clean and mechanistic, but it is not yet
   corpus-scale.

## Reproduce

```sh
uv run python experiments/77_effective_pressure.py --feeder ckt7  --save
uv run python experiments/77_effective_pressure.py --feeder ckt24 --save
```
