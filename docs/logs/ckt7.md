# Experiment log — EPRI Ckt7, and the search-operator dead end

**Experiments:** `61_ckt7_utility_scale.py` (M31), `62_ckt7_local_search_ceiling.py`
(M32), `63_ckt7_swap_mutation_probe.py` (M33/M34), `68_ckt7_gldf_headline.py`
(M39; M40 **retracted**), `70_ckt7_relinearize.py` (M42)
**Network provenance:** `data/opendss/ckt7/PROVENANCE.md`
**Paper:** §7.7 (the results) and §7.10 (the two lessons); this file is the record behind them.
**Status:** current headline is the re-linearized GLDF-MILP (M42). **M32–M34 are a
documented dead end and M40 is retracted** — both are kept in full, because how they
went wrong is the most transferable thing on this feeder.

---

## The network

EPRI's Ckt7, a second real Green Circuits feeder chosen to test whether Ckt24's
result was a fluke of its own load-allocation unevenness: a different location,
topology and voltage class — **115 kV → 12.47 kV** (vs. Ckt24's 230 kV → 34.5 kV),
**1,255 buses**, 906 loads, 161 transformers, **76.6 MW** declared load. After
exclusions, **199 candidate buses**. Its real hosting capacity for this request
model turns out to be only **~1.1–1.5 MW** — a fact that matters enormously later.

**One disclosed exclusion, simpler than Ckt24's three.** No transformer is
overloaded at baseline (0/161, against Ckt24's 37/843) and there is no structural
substation overload needing a non-worsening rule. Only **11 of 1,148 lines (~1%)**
are individually overloaded (115–238%) — all small secondary "service" drop lines,
the same EPRI allocation-factor-vs-nameplate artifact as Ckt24's transformers,
manifesting on lines instead. Traced via a line-graph traversal: **657 of 906 loads
by count** (fine-grained secondary modeling means many buses per customer) but only
**4.7 of 76.6 MW (~6.2%) by magnitude** — comparable to Ckt24's 7.8% exclusion.
Those loads' buses leave the candidate pool; after exclusion the baseline is exactly
feasible (max line loading 56.2%, max transformer loading 61.2%, vmin 0.962 pu).

Same scenarios as Ckt24: MODERATE (N=150) and STRESS (N=300), 3 draw seeds each.

---

## M31 — a second, distinct search-budget lesson: restart diversity

Starting from Ckt24's *corrected* budget (pop=60/gen=100, one seeded search) —
proactively applying the earlier lesson rather than rediscovering it — found only
**2/6 instances positive; 4/6 came back at exactly 0.0% gain.**

This was **not** the same failure mode. Ckt7's gene count (150–300) is no larger
than Ckt24's, so scaling pop/gen with gene count was already accounted for. Direct
diagnosis — re-running the most suspicious instance with 4 independent
search-seed restarts at a much larger pop=120/gen=200 — found:

> **restart 0 → +49.9%. Restarts 1 and 3 → exactly 0.0% again. Restart 2 → +20.7%.**

**Budget size alone does not fix this.** A single seeded NSGA-II run can land in a
local optimum that *exactly reproduces the FCFS solution* and never escape it,
regardless of population or generation count. Only restart *diversity* reliably
escapes it. (Why it gets stuck there is answered by M32.)

**Performance work made this checkable rather than a 2–4 hour blocking wait.** Two
changes, neither altering what is searched: `check_connectivity=False` (pandapower
re-verifies topology connectivity on every solve by default — redundant here, since
trials only ever *add* loads; ~26% faster, measured) and `n_jobs` parallelization
across CPU cores, mirroring the engine's own validated M10 pattern
(`multiprocessing.Pool` + pymoo's `StarmapParallelization`), **deliberately capped
below `os.cpu_count()`** to leave the host usable. Combined: ~2–4 h → ~38 min.

| Instance | FCFS | NSGA-II (best-of-N restarts) |
|---|---:|---:|
| MODERATE 0 | 336.5 kW | +49.9% (1 restart) |
| MODERATE 1 | 780.3 | +6.8% (1 restart) |
| MODERATE 2 | 351.6 | +10.5% (1 restart) |
| STRESS 0 | 413.2 | +40.5% (3 restarts) |
| STRESS 1 | 798.5 | +9.8% (3 restarts) |
| STRESS 2 | 497.6 | +61.4% (1 restart) |
| **Median** | | **+25.5%** |

6/6 positive, so Ckt24's finding does generalize — but with a real, disclosed
difference in how reliably the gain surfaces. Work was paused here (limited compute
availability) with `--search-seeds 3` as the documented next step, *not* a claim
that 3 restarts is verified sufficient the way Ckt24's single restart was.

These numbers were disclosed as **lower bounds**. They were: by a factor of 3 to 20.

## M32 — a cheap local-search probe: NSGA-II leaves 2–14.6× on the table

M31 left an open mechanistic question: is Ckt7's noisier landscape a property of the
*problem* (more, deeper local optima — more restarts of anything would help) or of
the *method* (NSGA-II specifically underperforming)? Only the first would mean "more
compute helps regardless of algorithm."

A structurally unrelated, deliberately much simpler method answers it: randomized
batch hill-climbing — no population, no crossover, no GA machinery — with
independent restarts (one FCFS-seeded, the rest random-init, mirroring NSGA-II's own
seeding) over a toggle+swap neighbourhood.

**A first version using only single-bit toggle moves reproduced NSGA-II's exact
failure**: flat 0.0% gain, never finding an improving move from the FCFS-seeded
start. The reason is structural, not a budget issue: **FCFS has already greedily
accepted everything that fits one at a time**, so a lone ADD rarely finds new room,
and a lone REMOVE is never improving under hill-climbing. The move this problem
actually needs is a **swap** — drop one accepted request, add a previously-rejected
one, trading a small acceptance for a larger one. Adding that single move type
changed the outcome completely:

| Instance | FCFS | NSGA-II (M31) | Hill-climb (best-of-10) | Ratio |
|---|---:|---:|---:|---:|
| MODERATE 0 | 336.5 kW | +49.9% | **+154.7%** | 3.1× |
| MODERATE 1 | 780.3 | +6.8% | **+17.9%** | 2.6× |
| MODERATE 2 | 351.6 | +10.5% | **+153.3%** | 14.6× |
| STRESS 0 | 413.2 | +40.5% | **+163.8%** | 4.0× |
| STRESS 1 | 798.5 | +9.8% | **+19.6%** | 2.0× |
| STRESS 2 | 497.6 | +61.4% | **+66.4%** | 1.1× |
| **Median** | | +25.5% | **+109.9%** | |

**6/6: the simple swap-aware hill-climber beats NSGA-II**, decisively on 5/6
(2–14.6×), marginally on the one instance where NSGA-II had already found its best
relative result. It did so with **less** compute, not more: 10 restarts × 1,500
evaluations ≈ **15,000** oracle calls per instance, versus NSGA-II's ~24,000 for a
*single* pop=120/gen=200 restart, or ~72,000 for the two STRESS instances that
needed three.

Two observations rule out the "just a noisier landscape" reading:

1. **Three of the six restart curves (all STRESS) are perfectly flat from the very
   first try** — the FCFS-seeded restart alone, with zero random-restart diversity,
   already reaches the plateau. If restart diversity were the active ingredient,
   these curves would climb like the other three. They do not.
2. **NSGA-II receives the identical FCFS seed** and still fails to find these moves
   from it, ruling out "bad starting point."

The one remaining structural difference is the move set: `TwoPointCrossover` +
`BitflipMutation` do not encode a direct swap, and do not reliably discover one
through recombination either, at least not within the budget tested.

## M33 — a short pilot: teaching NSGA-II the swap move

Two ways to act on the operator/encoding mismatch: (a) add the missing move to
NSGA-II itself, keeping the multi-objective GA framework (needed elsewhere — the
fairness variants in `src/allocation/search.py` are genuinely three-objective), or
(b) abandon NSGA-II for subset selection. (a) is the cheaper hypothesis to test, and
isolates one variable.

A custom pymoo `Mutation` (`SwapMutation`) mirrors M32's move exactly: with
probability 0.6, trade one accepted gene for one rejected gene in a single paired
flip; otherwise toggle one random gene (also the fallback when a row has no 1s or no
0s to trade). Dropped into the exact same NSGA-II pipeline as M31 — same problem
class, same `TwoPointCrossover`; only `mutation=` changes.

A first probe (900 evaluations/operator/instance) was uninformative: both operators
landed at exactly +0.0%. A second, still-cheap probe (4,800 evals/operator/instance,
roughly a tenth to a twentieth of M31's budget) isolated the operator cleanly:

| Instance | Bit-flip | Swap | NSGA-II (M31, full budget) | Hill-climb (M32) |
|---|---:|---:|---:|---:|
| MODERATE 2 | +0.0% | **+3.2%** | +10.5% | +153.3% |
| STRESS 2 | +0.0% | **+29.4%** | +61.4% | +66.4% |

## M34 — the full-budget run *corrects* the pilot, and confirms the operator effect

Re-run at M31's full recommended budget (pop=120, gen=200, 3 independent restarts;
72,000 evals/operator/instance, ~15× probe 2), same isolated-variable design, same 2
instances:

| Instance | Bit-flip (full budget) | Swap (full budget) | Delta | Hill-climb (M32) |
|---|---:|---:|---:|---:|
| MODERATE 2 | +54.3% | **+72.5%** | +18.2pp | +153.3% |
| STRESS 2 | +84.3% | **+91.5%** | +7.2pp | +66.4% |

**First, the "bit-flip is stuck at exactly 0%" story was itself a restart-budget
artifact, not a property of the stock operator** — at 3 restarts, bit-flip alone
reaches +54.3% and +84.3%. M33's headline is corrected by its own escalation.
**Second, the swap-vs-bit-flip effect survives at matched budget**: +18.2pp and
+7.2pp with restart budget held equal — a genuine operator effect on top of restart
diversity, not a restatement of it. On STRESS 2, swap-NSGA-II's +91.5% even exceeds
the hill-climber's +66.4%.

### …and then M35 retires the entire line

[M35](gldf-milp.md) showed NSGA-II is the wrong tool for this problem **regardless of
its operators** — it leaves over half the achievable capacity on the table on every
Ckt24 instance. M32–M34 were tuning a method that should be replaced, not tuned.
**The swap-operator line is a dead end and was not developed further.** It is kept
here in full because it is a clean worked example of a plausible, well-evidenced,
internally-consistent research direction that was nonetheless the wrong question:
"which search wins?" instead of "how much capacity is actually achievable?"

---

## M39 — the headline, re-run with the MILP

| Instance | FCFS | *NSGA-II (M31)* | *Hill-climb (M32)* | **GLDF-MILP** |
|---|---:|---:|---:|---:|
| MODERATE 0 | 336.5 kW | *+49.9%* | *+154.7%* | **+235.6%** |
| MODERATE 1 | 780.3 | *+6.8%* | *+17.9%* | **+43.3%** |
| MODERATE 2 | 351.6 | *+10.5%* | *+153.3%* | **+204.5%** |
| STRESS 0 | 413.2 | *+40.5%* | *+163.8%* | **+264.1%** |
| STRESS 1 | 798.5 | *+9.8%* | *+19.6%* | **+81.1%** |
| STRESS 2 | 497.6 | *+61.4%* | *+66.4%* | **+224.1%** |
| **Median** | | *+25.5%* | *+109.9%* | **+214.3%** |

The MILP beats NSGA-II **6/6** and the hill-climber **6/6**, lifting the median gain
from +25.5% to **+214.3%**. Every figure is AC-verified against the same oracle and
the same FCFS baseline; only the search changed.

## M40 — **RETRACTED**: "GLDF was never broken on Ckt7; our request sampler was"

**The claim.** M40 reported that GLDF's vmax error on Ckt7 (+18 to +136 mpu against a
~110 mpu-wide band) *collapsed* to ≤0.2 mpu once requests were drawn **without
replacement**, and concluded that the model had never been broken — the request
sampler had. **That conclusion is withdrawn. The comparison was invalid.**

**What was actually compared.** The +18…+136 mpu figure was measured on **randomly
drawn accept-sets**; the ≤0.2 mpu figure was measured **at the MILP's optimum**. Two
different quantities. Setting one against the other manufactured a collapse out of
nothing. Like for like, **with replacement**:

| Accept-set (with replacement) | Accepted | GLDF `vmax` error |
|---|---:|---:|
| Random 25–50% of the queue — *never visited by anything* | 1,861–8,692 kW | +11 to +76 mpu |
| **The MILP's own optimum** | 1,067–1,593 kW | **+0.01 to +0.77 mpu** |
| **The FCFS accept-set** | 337–498 kW | **+0.04 to +0.05 mpu** |

There was no collapse because there was nothing to collapse: **GLDF on Ckt7 was
accurate all along, with or without replacement.**

**Why the random-set error was so bad.** Those sets accept 25–50% of the queue —
1.9–8.7 MW of PV export on a feeder that hosts ~1.1–1.5 MW. They lie **three to eight
times outside the feasible region** (AC voltages 1.08–1.10 pu; the fuller ones do not
converge at all). A linearization about the base point is of course hopeless there.
**But no optimizer ever evaluates such a point.**

**The mechanism, once seen, is mundane.** The MILP accepts only **35–55 of the
150–300 requests**, and concentrating export on one bus is expensive in voltage, so
the optimizer *avoids* the very stacking that breaks the linearization. The 228–313 kW
of stacked export that motivated the whole caveat is a property of the **drawn
queue**, never of any **accepted** set — at the MILP's optimum the most any single bus
carries is **82–135 kW**.

**Consequences — all corrections to earlier work:**

1. **"The MILP only *proposes* on Ckt7" (M39) is wrong.** Its zero-margin optimum is
   AC-feasible 6/6 with zero repair drops. The LP was doing the deciding all along.
2. **"Ckt7 is out of scope for the MILP" (M35) is withdrawn**, not softened. Ckt7 was
   in scope all along.
3. **The stacking mechanism itself is real** and does explain why the *random-set*
   error is far worse on Ckt7 (199 candidate buses) than on Ckt24 (~3,650). It is
   simply not a fact about anything any method does.
4. Sampling **without replacement** remains arguably the better queue model — two
   applications at one service point is an odd thing to draw by accident — but it does
   **not** rescue the model, and we no longer claim it does. (It also does not drop in
   casually: STRESS's 300 requests do not fit a 199-bus pool without clamping.)

**What survives from M40, because it was measured correctly.** Ckt7's binding
constraint is **pure overvoltage** (`vmax = 1.0500` exactly at every optimum, thermal
slack everywhere — re-confirmed with replacement in M42). And on the
without-replacement instances the *achievable* total is nearly **invariant**
(~1.1–1.5 MW) while FCFS swings **247–570 kW**: *the optimum belongs to the feeder;
FCFS's variance is luck of the arrival order.*

> **The transferable lesson.** A linear surrogate inside an optimizer must be
> validated **on the trajectory the optimizer actually follows** — at its optimum and
> at the incumbents it passes through — never on random samples of the decision space.
> Random-set error is easy to compute and was actively misleading for **five
> consecutive experiments**: it made a usable model look unusable and caused us to
> bolt a repair heuristic and a local search onto a program that needed neither. The
> complementary trap is just as real (see [gldf-milp.md](gldf-milp.md)): at its *own*
> optimum the optimizer hunts for exactly the accept-sets where the model is
> optimistic, so error measured there is *worse* than random-set error. Neither
> statistic substitutes for the other, and only the second decides whether the answer
> is right.

## M42 — one method for both utility feeders, and the local search was a crutch

M41 fixed the MILP on Ckt24; Ckt7 was still on the old pipeline, so the project was
reporting two different methods for its two real utility feeders. The comparison is
deliberately isolated: old = `MILP(empty anchor) + repair + climb(3000)`, new =
`MILP(re-linearized to a fixed point) + climb(3000)` — same move set, budget, seed
and oracle. The only variable is **where the model is linearized**.

*(Numbers below are the **M48 re-run**, with the thermal-graph bug fixed. The conclusion
is unchanged; see the note beneath the table.)*

| Instance | FCFS | M39 (margin+repair+climb) | **Re-linearized (M42)** | vs. M39 |
|---|---:|---:|---:|---:|
| MODERATE 0 | 336.5 kW | 1129.3 kW | **1135.2 kW** | +0.51% |
| MODERATE 1 | 780.3 | 1118.1 | **1106.0** | −1.08% |
| MODERATE 2 | 351.6 | 1070.6 | **1089.0** | +1.71% |
| STRESS 0 | 413.2 | 1504.5 | **1519.1** | +0.97% |
| STRESS 1 | 798.5 | 1446.2 | **1457.2** | +0.76% |
| STRESS 2 | 497.6 | 1612.9 | **1626.1** | +0.82% |

**AC-feasible at margin zero on 6/6, zero repair drops**, fixed point in 3–8
iterations, beats M39 on 5/6. Median gain over honest FCFS: **+218.3%** (M39: +214.3%).
*(±0.4% run-to-run — see the reproducibility note below. MODERATE 0 has been observed
between 1130.2 and 1135.2 kW.)*

> **The M48 thermal fix barely moves this table, and that is the point.** Four of the six
> instances are bit-identical; only MODERATE 0 (+0.4%) and MODERATE 1 (−1.1%) move.
> **M42's claim survives real thermal constraints** — which is *not* what the state of the
> evidence in M46 suggested, and is the strongest confirmation that the synthetic band's
> optimum really was voltage-limited with thermal slack. (MODERATE 1 goes *down* because
> the linear thermal model is **conservative** where it binds — it rejects a set the AC
> oracle would accept. That is the safe direction, and it is what the AC-completion pass
> exists to recover.)

### A reproducibility defect found along the way: the MILP is non-deterministic

Runs of the identical command and seed return **1130.2 to 1135.2 kW** — a **0.4% spread**.

Cause: **the MILP is massively degenerate** (many accept-sets tie on total kW) and HiGHS
does not return the same tied optimum every run. **The SLP loop then amplifies it**,
because whichever tied set comes back becomes the *next linearization anchor*. The traces
show this exactly: iteration 0 is bit-identical every time (1117.0 kW, 40 accepted), then
iteration 1 returns **38, 39 or 41** accepted requests — all at ~1130.2 kW — and the
trajectories diverge from there.

**Pinning the solver to one thread does NOT fix this.** I claimed it did after three runs
agreed; that was a coincidence of landing on the same branch, and a fourth run broke it.
Recorded because the mistake is instructive: *three agreeing runs are not a determinism
proof, and I should not have written one down.*

The fix is to stop fighting the degeneracy and exploit it. `certified_allocation` runs the
loop from **several starting anchors** and keeps the best AC-feasible set; since every
iterate is AC-verified, a best-of can only **raise** the certified lower bound. It is also
far more stable — on Ckt7 K=150 all four anchors converge to the same 866.5 kW.

**Consequence for the paper: quote these numbers to ~0.5%, not to 0.1 kW.** The 1.1 MW
Ckt7 figures carry a ±5 kW reproducibility band that the printed precision hides.

**The sharpest finding is what the swap climb now contributes: nothing.** Seeded from
the fixed point, it moves the answer on **1 of 6** instances, by **0.16 kW**. The
local search — the thing M32 and M39 credited with doing the real work — **was only
ever compensating for a badly anchored model.** Remove the bad anchor and it has no
work left.

Ckt7 stress-tests the loop *harder* than Ckt24 does: its optimum is **purely
overvoltage-limited**, sitting exactly on `vmax = 1.0500` with every thermal limit
slack, so there is no thermal headroom in which model error could hide. It converges
anyway.

---

## M48 — the Ckt7 thermal model was **empty**, and had been for five milestones

**Experiment:** `75_milp_maximality.py`. **This is a bug report, and it is the reason
M46/M47's "FCFS beats the certified optimum" anomaly existed.**

### The defect

`build_thermal` builds its own spanning tree from the root, over an adjacency assembled
from `net.line` and `net.trafo`. **Ckt7 also has 9 closed bus-bus switches** (`net.switch`,
`et == "b"`), and they are *structural*: without them the graph is severed at the
substation.

| | |
|---|---|
| Buses reached by the walk (line+trafo only) | **6 of 1,255** |
| pandapower's own `create_nxgraph` | 1,255 nodes, 1,318 edges, **1 component** |
| 1,318 edges = | 1,148 lines + 161 trafos + **9 switches** |
| Constraint rows `build_thermal` returned | **1** |

**The Ckt7 MILP had essentially no thermal constraints.** Only the hand-rolled spanning
tree was wrong; `build_network`'s island exclusion uses pandapower's `create_nxgraph`
(which respects switches) and was always correct, and `build_gldf` is derived from
`Ybus`, which is exact. **The one piece of graph machinery we wrote ourselves is the one
that was broken.**

### Why it stayed latent for five milestones — the synthetic band hid it

A constraint that never binds can be missing from your model **for free**. Under the
synthetic `uniform[10,100] kW` band Ckt7's optimum is genuinely pure-overvoltage-limited
with thermal slack everywhere — M40 measured that against the AC oracle, and M42's fixed
point is AC-feasible 6/6 with zero repair drops. Both were *true*. Neither was evidence
that the thermal model worked.

The **empirical** distribution (M45, median 6.1 kW) activates it. The MILP now seats
~130–190 tiny systems instead of ~40 large ones and stacks export behind **500 kVA
service transformers** — trafo 153 (hv 283 / lv 1056) ends up carrying **497 kW of reverse
flow**. The thermal limit binds hard, and the model cannot see it.

### The symptom, and why it looked like a linearization failure

| | pre-M48 | **fixed** |
|---|---:|---:|
| Thermal constraint rows | 1 | **144** |
| Loop iterates that were AC-feasible | **1 of 15** — and only the *empty* anchor | **48 of 60** (4 anchors) |
| MILP fixed point | 852.4 kW | **866.5 kW** |
| Binding transformer at the answer | 106–108% (invisible to the model) | **100.0%** |
| Rejected requests that still fit (maximality) | 7 of 16 → **not maximal** | **0 of 22 → AC-MAXIMAL** |
| Best of **1,000** FCFS arrival orders | 865.5 kW | 865.5 kW |
| **Was the yardstick beaten by a queue walk?** | **YES — 101.6%** | **No — 99.9%** |

Every iterate after the first overloaded a service transformer, the AC oracle rejected it,
and the loop **never re-entered the feasible region** — so `best_ids` never updated past
iteration 0 and the returned answer was *the naive empty-anchor MILP*. M41/M42's
re-linearization, the thing that fixed Ckt24, was contributing **nothing** on Ckt7.

Meanwhile FCFS consults the real oracle at every step, so it respects the transformer.
**That is the whole of the anomaly: a blind optimizer lost to a sighted greedy walk.**

### The 1.0 kW margin is real — and it reframes what joint allocation is *for*

The fixed yardstick (866.5 kW) beats the luckiest of 1,000 arrival orders (865.5 kW) by
**0.1%**. That is not a weak-search artifact: the loop is run from **four independent
starting anchors** (the empty queue plus three FCFS walks on seeds disjoint from the
measurement sample) and **all four converge to the same 866.5 kW**, which is also
AC-maximal. The number is solid; the margin over a *lucky* queue really is that thin.

Against the same yardstick, the full FCFS distribution (`m47_walks_ckt7_k150.csv`, n=200
raw walks, model-free and so unaffected by the bug) is:

| Arrival order | kW | % of achievable |
|---|---:|---:|
| **worst** | 177.3 | **20.5%** |
| p5 | 221.5 | 25.6% |
| **median** | 754.3 | **87.1%** |
| p95 | 828.5 | 95.6% |
| **best** | 864.1 | **99.7%** |
| best of **1,000** orders | 865.5 | **99.9%** |

> **On Ckt7 at K=150 the optimum is essentially REACHABLE BY LUCK.** Joint allocation buys
> **+0.1%** over the best of 1,000 random queues — but **+15%** over the median one and
> **+389%** over the worst. **Its value is not a higher ceiling; it is the removal of
> variance.**
>
> That is a different product from "our optimizer finds more capacity", and a more
> defensible one: the pitch is ***"you stop gambling"***, not *"you get more"*. It is also
> the pitch a regulator can actually act on, since the thing being eliminated is an
> arbitrary, non-physical dependence of an applicant's outcome on the order the forms
> arrived in.
>
> It also means the *certified-lower-bound* caveat costs us almost nothing here: the
> yardstick cannot be far below the true optimum, because a greedy walk already reaches
> **99.9%** of it.

### What did *not* fix it, and what did

I first assumed the linearization was optimistic near the boundary and built a **measured
per-bus back-off** (an SLP constraint shift, tightening the ceiling by the optimism
actually observed on the optimizer's own trajectory). It found nothing to shift: **GLDF's
voltage optimism at the optimum is −1.19 → 0.00 mpu.** GLDF was innocent — for the *third*
time (M35 → M40, retracted → M42 → here).

An **AC-maximal completion pass** (greedily re-add whatever the real oracle still accepts,
until nothing fits) is a genuine improvement and is kept as a guard — it is what *proved*
non-maximality — but on its own it is **not enough**: applied to the broken model it
reaches 862.6 kW, which the best of 1,000 arrival orders (865.5 kW) still beats. **Only the
graph fix closes the gap.**

### The transferable lessons

1. **A greedy queue walk is a free lower-bound oracle for your MILP.** It is model-free,
   it costs nothing, and *a certified optimum must never lose to it.* When it did, we
   spent a milestone blaming the linear model; the check itself was the diagnosis.
   **"Can FCFS beat you?" belongs in the test suite of any allocation optimizer.**
2. **Build your graph from the tool's own topology routines, not from the element tables.**
   `net.line` + `net.trafo` is not the network. Switches, impedances and `trafo3w` are not
   decorations.
3. **An input distribution you never validated can hide a structural bug indefinitely.**
   The same unexamined `uniform[10,100] kW` band that manufactured the arrival-order
   lottery (M45/M46) also kept a dead constraint out of sight for five milestones. Both
   failures have the same author.

**Ckt24 is unaffected**: zero switches, all 6,058 buses reached, 987 thermal rows.

---

## Open / unrun

- `70_ckt7_relinearize.py --no-replacement` is wired but **unrun** (the control).
- Adopting without-replacement sampling in the paper would require redesigning the
  penetration scenarios (STRESS's 300 requests do not fit a 199-bus pool) and
  re-running NSGA-II on the new instances.

## Reproduce

```sh
uv run python experiments/61_ckt7_utility_scale.py --save          # M31: NSGA-II; restart-diversity lesson
uv run python experiments/62_ckt7_local_search_ceiling.py --save   # M32: swap-aware hill-climb beats NSGA-II 6/6
uv run python experiments/63_ckt7_swap_mutation_probe.py --save    # M33: swap-as-mutation pilot
uv run python experiments/63_ckt7_swap_mutation_probe.py --instances MODERATE:2,STRESS:2 --pop 120 --gen 200 --search-seeds 3 --save   # M34: full budget
uv run python experiments/68_ckt7_gldf_headline.py --save          # M39: headline re-run with the MILP
uv run python experiments/68_ckt7_gldf_headline.py --no-replacement --save   # M40 (retracted): the sampler diagnostic
uv run python experiments/70_ckt7_relinearize.py --save            # M42: re-linearization; margin, repair and climb all retired
```
