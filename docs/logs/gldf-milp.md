# Method log — the Generalized-LinDistFlow MILP

**Experiments:** `64_ckt24_gldf_bound.py` (M35), `65_smartds_gldf_recheck.py` (M36),
`66_fontinele_gldf_recheck.py` (M37), `67_eulv_gldf_recheck.py` (M38),
`68_ckt7_gldf_headline.py` (M39), `69_ckt24_relinearize.py` (M41),
`70_ckt7_relinearize.py` (M42)
**Paper:** §4.2 *A certified feasible lower bound* (the instrument), §5.9 *The regime
belongs to the queue* (the scale effect); the standing caveats are §7 *Limitations*.
**Status:** current. This is the method every headline in Part II now rests on.

This log is **cross-cutting**: it documents the model, how it is built, how it was
validated, and the two ways it can fool you. The per-network results live in
[ckt24.md](ckt24.md), [ckt7.md](ckt7.md), [smartds.md](smartds.md),
[fontinele.md](fontinele.md) and [small-network-checks.md](small-network-checks.md).

---

## Why the classic convex route was not enough

Every capacity claim before M35 was *relative* — joint beats FCFS by x%, a hill-climb
beats NSGA-II by 2–14.6×, a swap operator beats bit-flip by +7–18pp. None of them says
how much capacity is *achievable*, so none says how much is still being left behind. A
search that beats FCFS by +88% might be at 95% of the ceiling or at 40% of it, and
nothing measured before M35 could tell those apart.

The existing convex/MILP route (`convex.py`) cannot answer that on Ckt24. It asserts a
**radial, shunt-free, charging-free** network, and Ckt24 violates all three: 2 genuine
topological loops (25-bus and 52-bus) plus 6 redundant parallel branches, 3 shunt
capacitor banks, and non-zero charging on all 5,222 lines. This is not incidental —
the capacitors (−3.30 Mvar) plus line charging (2.17 Mvar) amount to **63.8% of
Ckt24's reactive load** (8.58 Mvar against 50.94 MW, power factor 0.986). Classic
Baran–Wu LinDistFlow discards every bit of it, so an optimality guarantee under that
relaxation would be a guarantee about a materially different network.

## The model

**Generalized LinDistFlow (GLDF)** — Huang, Cui, Zhou & Bernstein (NREL), *A
Generalized LinDistFlow Model for Power Flow Analysis*, IEEE CDC 2021,
[arXiv:2104.02118](https://arxiv.org/abs/2104.02118).

It generalizes LinDistFlow to multiphase networks of *generic* topology — radial **or
meshed** — around an **arbitrary linearization point**, and reduces exactly to classic
LinDistFlow for a radial network linearized at zero injection.

```
v      = diag(conj(E)) E + M p + N q + Lambda
Z     := Y_LL^-1                      (slack row/col removed)
E     := -Y_LL^-1 Y_LS V_S            (open-circuit voltage)
M     :=  2 Re( diag(E) conj(Z) diag(E)^-1 )
N     := -2 Im( diag(E) conj(Z) diag(E)^-1 )
Lambda := |V*|^2 - (diag(conj(E)) E + M p* + N q*)    (fit at the linearization point)
```

`v` = **squared** voltage magnitudes; `p`, `q` = bus power **injections** (loads
negative). Linear in `(p, q)`, hence embeddable directly in a MILP over accept/reject
binaries.

**The decisive structural point:** GLDF is built from the **bus-admittance inverse**,
not a radial subtree sum. Meshing, shunt capacitors, line charging and parallel
branches are all *already inside* `Y`. Every one of the three blockers above dissolves,
with **no network surgery and no dropped physics**.

**A closely related model, LinDistS** — Lin, Shen, Guo et al., *A linear Distflow model
considering line shunts…*, Applied Energy 357:122467 (2024) — reaches the same place
from the other direction (it adds line shunts to LinDistFlow explicitly, with
extensions for ZIP loads, weakly-meshed topology and unbalanced three-phase). Worth
noting both as corroboration that the shunt/charging gap is a real, recognized
deficiency of the classic model and because it appeared in one of this project's own
target venues. We use GLDF because a single admittance-inverse formulation covers all
of our blockers at once; **LinDistS is the natural comparator if this route is
developed further.** Not implemented.

## Building it from pandapower — the practical recipe

- After `pp.runpp(net)`, **`net._ppc["internal"]["Ybus"]` already contains** line
  charging, shunts, transformer taps and parallel branches — exactly the admittances
  the AC oracle itself uses. Map pandapower bus → ppc index via
  `net._pd2ppc_lookups["bus"]`; take `baseMVA` from `ppc["baseMVA"]`. The MILP
  therefore sees precisely what the oracle sees.
- **Linearize at the baseline operating point**, not at zero injection: set `v_base` =
  AC baseline `vm_pu**2`. `Lambda` is then implicit and the model is *exact* at the
  baseline.
- **Do not form the dense inverse.** You only need the *columns* of `Z` at the
  request/injection buses. `splu(Y_LL)` once, then one `lu.solve(e_j)` per bus of
  interest. (On Ckt24 this avoids a 6,057² complex matrix ≈ 590 MB.)
- **Thermal limits bind too, and must be modelled.** A first, voltage-only version of
  the MILP returned accept-sets that AC-reverified at **128% transformer loading**.
  Requests are pure-P, so each branch's apparent-power limit collapses to two linear
  bounds on active flow. The thermal model sums power over a spanning tree, so
  loop-closing and parallel branches carry no constraint of their own and subtree
  losses are ignored — the same approximation LinDistFlow itself makes. AC
  reverification is what catches the resulting error, and does.
- **⚠ Build that spanning tree from the network, not from `net.line` + `net.trafo`.**
  This is the one hand-rolled graph in the whole pipeline and it is where the worst bug
  in Part II lived (M48). Ckt7 connects through **9 closed bus-bus switches**
  (`net.switch`, `et == "b"`); omit them and the walk from the root reaches **6 of 1,255
  buses**, leaving a thermal model with **one constraint row** — i.e. no thermal
  constraints at all. It went unnoticed for five milestones because under the synthetic
  request band Ckt7's optimum was voltage-limited with thermal slack, so the missing
  constraint never bound. **Traverse closed bus-bus switches (they fuse two buses and
  carry no rating of their own, so they add no row); also check `net.impedance` and
  `net.trafo3w`.** Assert that the walk reaches the buses you expect — a silently tiny
  `len(p_base)` is the tell. Everything else in this pipeline comes from pandapower's own
  `Ybus`/`create_nxgraph` and was never wrong.

## Validation gates

1. **The correctness gate (this one is non-negotiable).** GLDF's predicted voltage must
   converge to AC's as the perturbation vanishes, as any valid linearization must — a
   single-bus injection scaling sweep. On Ckt24: **0.640 vs 0.653 mpu at a 1 kW
   injection.** Passed.
2. **Accuracy on the quantity that actually binds** (maximum bus voltage), on accept-sets
   *spanning the operating range*: **+1.1 to +3.2 mpu** on Ckt24, about 0.3%.

Gate 1 is the one that tells you the implementation is *correct*. Gate 2, as the next
section shows, is the one that will lie to you about whether it is *usable*.

---

## The three traps. The first two cost real time; the third cost a milestone

### Trap 0 — DON'T ASSUME IT'S THE LINEAR MODEL. Three times out of three, it wasn't.

Every time the MILP has produced a wrong or AC-infeasible answer in this project, the
first reflex has been *"GLDF is inaccurate here"*, and **every time that reflex has been
wrong**:

| Symptom | Blamed on | **Actually was** |
|---|---|---|
| Zero-margin optimum AC-infeasible 6/6 (Ckt24) | model error | linearizing at the **empty queue** (M41) |
| "+18 to +136 mpu on Ckt7" | model error | error measured on **random accept-sets** the optimizer never visits (M40, **retracted**) |
| Every loop iterate AC-infeasible under real request sizes | model error | the thermal spanning tree **omitted bus-bus switches**; 1 constraint row (M48) |

GLDF's voltage prediction *at the optimum* has measured **+0.00 to +0.77 mpu** on both
utility feeders every single time it has been checked properly. **Suspect the plumbing —
the anchor, the graph, the constraint set — before the physics.**

### The cheap check that would have caught all three: *can a greedy queue walk beat you?*

skip-continue FCFS is **model-free** (it consults only the AC oracle) and it terminates
**AC-maximal** (it tried every request, so nothing more fits). It is therefore a *free
lower-bound oracle* for the MILP, and:

> **A certified optimum must never lose to a greedy queue walk.** If it does, you have a
> bug — not a modelling limitation.

On Ckt7-empirical the luckiest of 1,000 arrival orders reached **865.5 kW against a
"certified optimum" of 852.4 kW (101.6%)**. That single arithmetic contradiction was the
entire diagnosis; we spent a milestone reading it as "the yardstick is soft in this
regime" instead. The companion check is **maximality**: take the MILP's answer and try to
add each rejected request back. If *any* fits, the optimum is not even locally maximal.
Both checks cost one AC solve per request and belong in the test suite of **any**
allocation optimizer.

### Trap 1 — the optimizer EXPLOITS the model error

On *random* accept-sets GLDF **over**-predicts vmax by +1 to +3 mpu (i.e. it is
conservative). **At the MILP's own optimum the sign flips**: it *under*-predicts by
1.9–3.5 mpu, because the search hunts for exactly the accept-sets where the
linearization is optimistic in its favour.

Expect this from **any** optimization against a linear power-flow surrogate. It is a
finding, not a nuisance: it is the failure mode a reviewer should expect, and it is why
the zero-margin optimum was AC-infeasible on 6/6 Ckt24 instances and why the safety
margin and the greedy AC repair were originally load-bearing.

### Trap 2 — MEASURE THE ERROR WHERE THE OPTIMIZER GOES, NOT ON RANDOM DRAWS

**This one cost five experiments and produced a retracted milestone** (M40 — see
[ckt7.md](ckt7.md) for the full forensics).

GLDF looked *terrible* on Ckt7 — +18 to +136 mpu against a ~110 mpu-wide band, versus
+1–3 mpu on Ckt24 — and on that basis it was demoted to a mere "proposal" step there,
with an AC repair and a local search bolted on to do the real deciding. But that error
was measured on **random accept-sets** taking 25–50% of the queue: 1.9–8.7 MW of PV on
a feeder that hosts ~1.1–1.5 MW, i.e. **three to eight times outside the feasible
region**, where the AC solve mostly diverges. **No optimizer ever evaluates such a
point.**

**At the MILP's own optimum, Ckt7's error is +0.01 to +0.77 mpu, and its zero-margin
optimum is AC-feasible 6/6.** The model was fine all along.

The mechanism: injection *concentration* genuinely does explain the random-set error (a
199-bus pool sampled with replacement stacks 228–313 kW on one bus) — **but the
optimizer avoids stacking, because stacking is expensive in voltage.** It accepts only
35–55 of 150–300 requests, at most 82–135 kW on any bus. **Stacking is a property of
the drawn queue, never of an accepted set.**

> **The rule.** Validate a surrogate **on the trajectory the optimizer actually
> follows** — at its optimum, and at the incumbents it passes through. Random-set error
> is easy to compute and can be actively misleading in *either* direction: Trap 1 says
> at-the-optimum error is *worse* than random-set error; Trap 2 says it can be
> *dramatically better*. Neither statistic substitutes for the other, and only the
> at-the-optimum one decides whether the answer is correct. (Gate 1 above — the
> vanishing-perturbation sweep — remains the right test for whether the *implementation*
> is correct.)

---

## Re-linearization: re-anchoring, not re-sloping

GLDF is defined at an **arbitrary** linearization point — precisely what "generalized"
buys over Baran–Wu — and the first implementations threw that freedom away, linearizing
always at the **empty queue** and then asking the MILP to make a decision several MW of
PV export away from it.

**The loop (successive linear programming).** Solve → run the real AC power flow on the
answer → **re-linearize GLDF there** → re-solve → iterate to a fixed point. No-good cuts

```
Σ_{i∈S} (1 − x_i) + Σ_{i∉S} x_i ≥ 1        (forbids exactly the vertex S)
```

are added **only to prevent cycling** — they are always valid, assume no monotonicity,
and do no optimizing.

**What re-linearization can and cannot do — less than the name suggests.** `M` depends
only on `E` and `Z = Y_LL⁻¹`, and **pandapower keeps constant-power loads in `Sbus`, not
in `Ybus`** — so adding requests **does not change `Y`**. Verified numerically rather
than assumed (`--check-invariance`): `M` rebuilt at a 150-request anchor is
**bit-identical** to `M` at the empty queue (max difference **0.0e+00**), while `v_base`
moves by 0.23.

> **Re-linearizing RE-ANCHORS the affine model; it cannot RE-SLOPE it.** The first-order
> sensitivities never improve, wherever you linearize. The entire "arbitrary
> linearization point" freedom lives in the affine offset.

So the question becomes empirical: is the residual error *anchor* error (fixable this
way) or *slope* error (not)? **It is anchor error.** On both real utility feeders the
loop is AC-feasible **at margin zero, 6/6, with zero repair drops**, in 3–8 iterations —
and capacity *rose*. The safety margin and the AC repair are not reduced but
**eliminated**. **Do this before reaching for a MISOCP.**

**Two traps in reading the loop's own output:**

1. **The 0.00 mpu error at the fixed point is tautological.** At convergence `x ==
   anchor`, so the model reproduces the AC solution *by construction*. That is the
   definition of convergence, not evidence of accuracy. Quote the **out-of-sample**
   one-step error instead (model anchored at iteration 0, predicting iteration 1's
   *different* optimum): **+0.08 to +0.67 mpu**, against −1.87 to −3.62 mpu from the
   empty anchor.
2. **The converged solution is a certified AC-feasible allocation**, which makes it a far
   better denominator for "% of achievable" than a model-derived ceiling — the M35
   ceiling was itself AC-infeasible on 6/6, hence an *inflated* denominator. See the
   epistemic argument in [ckt24.md](ckt24.md).

## Compare model and oracle on the SAME bus set

The model only ever speaks about **PQ buses** (the slack row/col is removed from
`Y_LL`), but a naive `res_bus.vm_pu.max()` is over **all** buses. If the slack sits at
the limit — **Ckt24's is at exactly 1.0500 pu** — it *masks* the true PQ maximum
whenever the network is within limits, silently corrupting any model-error measurement
in exactly the regime you care about. An assertion that the anchored model reproduces
the anchor's AC voltages caught this; it is armed in `69_ckt24_relinearize.py`.

## Building GLDF on OpenDSS (the SMART-DS corpus) — two extra gotchas

Both were caught by the correctness gate rather than assumed. Details in
[smartds.md](smartds.md).

1. **Loads must be kept out of `Y`.** GLDF represents loads as power *injections*, but
   OpenDSS folds each load's Norton admittance into its system admittance matrix. Build
   `Y` with every load disabled, and verify the resulting open-circuit voltage
   reproduces the loads-disabled AC solve.
2. **On this corpus GLDF does *not* converge to zero error as the perturbation
   vanishes.** It under-predicts voltage drop by a persistent **3.6–6%**, because it
   drops the feedback through the *existing* constant-power loads, whose negative
   incremental admittance makes the real network weaker than GLDF's load-free admittance
   believes. (Alternatives ruled out: load admittance in `Y`, fixed above; and regulator
   retapping — these feeders have no regulators.) Harmless on Ckt24 (1–3 mpu), but here
   comparable to the entire ~7% of available room — which is why the SMART-DS negative is
   defended with a **model-free** bound and an **exact-AC** neighbourhood search instead.

## Standing scope limits

- GLDF is a linear **approximation**, not a convex **relaxation** of AC power flow. Its
  feasible set is not provably a superset of the true one, and (Trap 1) the optimizer
  actively exploits where it is optimistic. **"Provably optimal" remains unclaimable**,
  and a converged fixed point is a self-consistent *local* optimum.
- Convergence of the SLP loop is **empirical** (6/6 within 3–8 iterations on both
  utility feeders), not guaranteed: the no-good cuts bound the loop but do not prove
  termination.
- What *is* certified is the AC-verified column: the real AC oracle accepts those
  allocations. Any figure quoted as a fraction of a **model-derived ceiling** is
  model-dependent; any figure quoted as a fraction of the **converged fixed point** is
  not.

## Reproduce

```sh
uv run python experiments/64_ckt24_gldf_bound.py --save       # M35: the ceiling; retires NSGA-II
uv run python experiments/65_smartds_gldf_recheck.py --save   # M36: OpenDSS build; SMART-DS negative survives
uv run python experiments/66_fontinele_gldf_recheck.py --save # M37: Fontinele re-check
uv run python experiments/67_eulv_gldf_recheck.py --save      # M38: EULV re-check
uv run python experiments/68_ckt7_gldf_headline.py --save     # M39: Ckt7 headline
uv run python experiments/69_ckt24_relinearize.py --save      # M41: SLP loop; margin + repair retired
uv run python experiments/69_ckt24_relinearize.py --check-invariance   # proves M is operating-point invariant
uv run python experiments/70_ckt7_relinearize.py --save       # M42: same loop, second feeder
```
