# Scope: Joint hosting-capacity allocation — the engine build log, M0–M21

> This log was written against `ROADMAP.md`, the project's internal planning
> document, which is not part of this archive. Its milestone bars ("done when…")
> and its Hard Parts / Licensing sections are quoted below wherever they were
> load-bearing, so the references to it do not resolve but the reasoning they
> carried is reproduced in place.

Status: **M0-M15 done** — the roadmap's thin slice (M0-M2), M3 (scale to a
larger real feeder + report artifact), M4 (write-up, `docs/paper.md`
Part II), M5 (a convex-relaxation MILP alternative to NSGA-II), M6 (a
real, catalogue-priced reinforcement-cost proxy), M7 (continuous request
sizing), M8 (multi-feeder validation across ENWL's full 128-feeder,
25-network corpus), M9 (queue-order fairness as a genuine Pareto
objective), M10 (constraint-oracle caching + NSGA-II parallelization), M11
(warm-starting the AC solve, investigated and found not to help), M12
(fairness extended to the convex-MILP and continuous-sizing search
variants), M13 (non-radial/meshed network validation — the general
NSGA-II route checked, not assumed, to still beat the baseline on a meshed
network; the MILP route's radial-only guard checked to actually fire on a
real feeder), M14 (unbalanced-network validation on a genuinely
symmetric 3-phase topology — a new `runpp_3ph`-based oracle variant, with
true North-American single-phase-lateral topology found to be blocked on
an upstream pandapower gap, not just unbuilt), and M15 (an OpenDSS-direct
oracle, sidestepping that pandapower gap entirely — true North-American
topology, IEEE13/123, validated directly against OpenDSS's own solver,
faster per-solve than either pandapower-based oracle). Owner: Hugo.
Last updated 2026-07-09.

## The problem (formalization)

Given one shared, capacity-constrained network and a pool of N competing
interconnection requests, which *subset* (and at what size) can be
accommodated *together*, under power-flow constraints?

- **Decision variables.** Accept/reject per request — binary in both M0 and
  M1. Accepting at a reduced size (a fractional/continuous variable) is a
  natural later extension (would turn the NSGA-II bitstring into a mixed
  binary/real chromosome); not needed to clear the M1 bar, so deferred.
- **Objectives.** (1) Σ accommodated MW — the sum of *requested* capacity
  actually granted, not net power flow (a demand request and an export
  request should each count toward "how much interconnection got built", not
  cancel each other out). (2) A reinforcement-cost proxy. (3) A queue-order
  fairness term — deferred through M0-M8 (the greedy baseline was itself the
  queue-order-respecting alternative the search was compared against, so a
  fairness *objective* stayed deferred until there was a reason to trade it
  off against accommodated MW directly); implemented in M9 below as
  `queue_fairness_violations` plus `nsga2_search_fair`.
- **Constraints.** Per-bus voltage band (`vmin_pu`/`vmax_pu`) and line/
  transformer thermal loading (`max_loading_percent`), evaluated by a
  `pandapower.runpp` solve on the candidate subset. A non-converging solve is
  treated as infeasible.
- **The `problem` object.** `allocation.AllocationProblem` (`src/allocation/
  problem.py`) wraps a base pandapower network + a list of
  `allocation.Request(id, bus, p_mw, q_mvar, queue_order)`.
  `AllocationProblem.evaluate(accepted_ids)` deep-copies the base network,
  attaches a load per accepted request (sign carries demand vs. export),
  solves, and returns an `AllocationResult` with `feasible`, `accommodated_mw`,
  `vmin_pu`/`vmax_pu`, max line/trafo loading, and two **soft** violation
  margins (`voltage_violation_pu`, `thermal_violation_percent`) rather than a
  hard reject-only signal — per ROADMAP.md's Hard Parts note, a GA needs a
  gradient to climb out of infeasibility, not just a wall.
- **Reinforcement-cost proxy.** M0 keeps this deliberately trivial:
  `reinforcement_cost_proxy` = `thermal_violation_percent` +
  `voltage_violation_pu * 100`, i.e. monotonic in "how far past the limits
  this subset is" (both violation kinds, put on a comparable percentage-point
  scale) rather than a catalogue upgrade price. Both terms matter: the EULV
  demo scenario below binds on overvoltage with thermal loading nowhere near
  its limit, so a thermal-only proxy would read 0 for every subset and give
  M1's optimizer nothing to trade accommodated MW against. This is a
  placeholder, not a modeling claim — replacing it with an actual
  km-of-conductor/added-transformer-MVA price (ROADMAP.md's "What 'cost'
  means") is scoped to M3, once reinforcement is a *lever* the search can
  apply rather than just a violation to avoid.

Separate package from `gridsynth` on purpose (`src/allocation/`, not
`src/gridsynth/`): per ROADMAP.md's Licensing section, `gridsynth` is the
permissive, adoption-maximizing core and the allocation engine is the
candidate AGPL/sellable half. Keeping them as separate importable packages
from the start makes that eventual split mechanical.

## Test network + request design

**Network:** the IEEE European LV feeder, imported via `gridsynth.import_opendss`
(`data/opendss/eulv/Master_snapshot.dss`) — reused, not re-validated, per
ROADMAP.md's "reuse the validated models" note. Experiment 10 already showed
this import matches pandapower's native `ieee_european_lv_asymmetric()` copy
exactly and round-trips to ~1 pp of OpenDSS under stress. 907 buses, 55 house
loads, one 800 kVA MV/LV transformer, source bus held at 1.05 pu.

**Requests:** K synthetic rooftop-PV export requests (`experiments/
24_joint_allocation_problem.py`), one per a seeded sample of existing house
buses, sized 3–10 kWp (typical UK/EU residential PV) — the DER-hosting-capacity
scenario ROADMAP.md's market section is built on (PVcase/Nira/Paces' bread and
butter, but scored jointly here instead of per-site). `p_mw` is negative
(export), `queue_order` is arrival index. Seed is a CLI flag, so results are
reproducible.

## M0 result: the oracle genuinely binds

`experiments/24_joint_allocation_problem.py --k 40 --seed 0`:

| requests accepted (queue order) | feasible | vmax (pu) | max line loading % | accommodated (kW) |
|---:|:---:|---:|---:|---:|
| 0 (baseline) | yes | 1.0500 | — | 0.0 |
| 5 | yes | 1.0500 | 9.8 | 32.5 |
| 15 | yes | 1.0654 | 18.3 | 100.3 |
| 25 | yes | 1.0841 | 37.0 | 165.7 |
| 35 | yes | 1.0998 | 58.4 | 236.8 |
| 40 (all) | **no** | 1.1102 | 67.2 | 266.1 |

The feeder's own source bus already sits at 1.05 pu, so this lands in the
classic overvoltage-limited hosting-capacity regime, not a thermal one:
line loading stays under 70% even when the subset is already infeasible on
voltage. Accepting requests in queue order crosses the 1.10 pu planning limit
between N=35 and N=40 — some subsets of this pool are feasible, some are not,
on the same network, with no reinforcement. That is the M0 bar: the joint
problem is real here, not just formalized on paper, so *which* subset (not
merely how many requests) is a genuine question — the one M1's optimizer is
for.

## M1: does a search beat the serial baseline?

`allocation.greedy_baseline` (`src/allocation/baseline.py`) is the status-quo
process ROADMAP.md describes: accept requests in ascending `queue_order`,
stop at the first violation. `allocation.nsga2_search`
(`src/allocation/search.py`, built on pymoo's `NSGA2`) searches accept/reject
subsets directly — one gene per request, objectives `[-accommodated_mw,
reinforcement_cost_proxy]`. Infeasible individuals are *not* hard-rejected:
`reinforcement_cost_proxy` (0 exactly when feasible, positive otherwise) is a
real objective the GA can trade off, so NSGA2's constraint-domination ranking
still breeds from near-feasible individuals — the "soft constraint so the
search can climb out" ROADMAP.md's Hard Parts section asks for.

`experiments/25_joint_allocation_search.py`, same 40-request EULV problem as
experiment 24, seed 0, `pop_size=40, n_gen=40` (1600 `runpp` solves, ~102 s
serial — no caching/warm-start/parallelization yet, per the roadmap's noted
scaling risk):

| | accepted | accommodated (kW) | vmax (pu) | reinforcement cost |
|---|---:|---:|---:|---:|
| greedy baseline (queue order) | 35/40 | 236.8 | 1.0998 | 0.000 |
| NSGA-II best feasible | 37/40 | **245.4** | 1.0996 | 0.000 |

**M1 bar met:** the search finds a fully feasible (cost 0) 37-request subset
accommodating 8.5 kW more than the 35-request serial baseline, at equal
(zero) reinforcement cost, reproducible from `seed=0`. It does this by
accepting a *different-shaped* subset, not a longer arrival-order prefix —
the concrete demonstration that *which* requests, not just how many, is the
thing worth searching over. Only 2 of the 40 final individuals were fully
feasible (the rest sit just past the boundary, mid-search-not-yet-converged),
so the Pareto front here is thin, not a rich accommodated-MW/cost trade-off
curve — a larger `n_gen`/`pop_size` or a cost-aware selection pressure is the
natural next tuning step if a richer front is needed, but wasn't required to
clear the M1 bar itself.

## Tests

- `test/test_allocation.py` — offline, a minimal two-bus radial feeder (no
  EULV data file needed): empty-subset feasibility, a small request staying
  feasible, a large demand tripping the thermal margin, an export request
  provably raising `vmax_pu` relative to a demand-only baseline,
  `accommodated_mw` counting only accepted requests, and rejection of
  unknown/duplicate request ids.
- `test/test_allocation_search.py` — same fixture: the greedy baseline stops
  exactly where the next request would violate, accepts everything when
  nothing binds, NSGA-II returns feasible results and is reproducible for a
  fixed seed, and `compare_to_baseline`'s domination logic is checked directly
  against fabricated `AllocationResult`s (no solve needed).
- `test/test_allocation_locational.py` — a 3-bus radial fixture (near bus +
  farther bus behind a second line): `locational_hosting_capacity`'s bisection
  matches an analytically-known thermal ceiling, respects the export/demand
  sign convention, and `feeder_hosting_capacity` correctly picks the *worse*
  (not average) of two candidate locations once the fixture is tuned so
  voltage, not the shared trunk's thermal rating, is what actually binds.

## M2: validated against Sandia's 216-feeder study

**External ground truth:** Sandia SAND2015-9712C (Reno & Broderick,
"Statistical Analysis of Feeder and Locational PV Hosting Capacity for 216
Feeders", 2015; free via osti.gov/servlets/purl/1581690). 216 real US MV
distribution feeders (4-34.5 kV, 0.6-28.5 MW peak load each), individually
OpenDSS-simulated for PV hosting capacity (HC) — "the largest amount of PV
that can be placed anywhere on the feeder", i.e. the *minimum* locational
hosting capacity (LHC) over every candidate bus. Headline findings: median
feeder HC 1.4 MW, mean 2.05 MW, and **72% of feeders' HC is limited by
over-voltage** (26% line overload, ~0% transformer overload).

`allocation.locational_hosting_capacity`/`feeder_hosting_capacity`
(`src/allocation/locational.py`) implement exactly Sandia's HC definition as a
bisection built on `AllocationProblem.evaluate`: the largest single request
one bus can host alone, minimized over a sampled set of buses.

**What was tried first and why it changed course.** The most direct
validation would be an exact same-feeder reproduction, so EPRI's **Ckt5**
(a real 12.47 kV, 1,379-customer feeder — one of Sandia's 216, cited
elsewhere, e.g. Coogan/Reno/Grijalva 2014, at ~1.6 MW HC, over-voltage-limited)
was vendored (`data/opendss/ckt5`, freely available via the official EPRI/
OpenDSS test-circuit distribution, no signup) and test-imported. That surfaced
and fixed a real importer bug — `gridsynth.import_opendss` had no support for
OpenDSS `Reactor` elements, which is how EPRI models the substation's
Thevenin-equivalent source impedance on Ckt5; without it the entire feeder
downstream came back electrically unsupplied (confirmed via
`pandapower.topology.unsupplied_buses`: 2997 of 2998 buses). Fixed in
`_add_reactors`, with regression tests. Ckt5 also needed a relaxed power-flow
tolerance (`AllocationProblem.runpp_kwargs`, added for this) to converge at
all — ~170 near-zero-impedance metering-zone jumper lines make it numerically
stiff for pandapower's default Newton-Raphson settings.

Getting Ckt5 to *solve* was not enough to get a fair number, though: its
declared nominal load already overloads 21 of 592 tiny residential service
transformers at baseline (100-161%), before any PV is added at all. Scaling
load down to a plausible light/minimum-daytime level (which is when
over-voltage actually tends to bind — Sandia's own Table II finds 80% of
feeder HC violations first appear under *minimum* daytime load, not peak)
clears the transformer overloads, but then exposes a fixed-tap voltage-rise
effect: the importer bakes in each transformer's *solved* tap (a documented
approximation, `_add_one_transformer`'s tap-capture note) from the original,
higher-load condition, which then over-boosts voltage once demand is scaled
down — a real, well-known distribution-engineering phenomenon (peak-calibrated
LTC + fixed capacitors causing light-load overvoltage), not a bug, but not
something this importer's static single-tap import can represent faithfully
either. Modeling that properly (per-loadshape minimum-daytime scaling, live
regulator control) is open-ended real feeder-modeling work, not a thin-slice
task. **Decision (with Hugo): validate the pattern, not chase an exact number
on a feeder whose regulator/capacitor behavior isn't faithfully captured.**

**What was actually validated:** `experiments/26_m2_hosting_capacity_validation.py`
runs `feeder_hosting_capacity` on EULV (the network experiments 24/25 already
trust, no tap/loadshape complications) over a 20-bus sample, seed 0:

| | value |
|---|---:|
| our engine's sampled feeder HC | 99.7 kW (bus 899, worst of 20 sampled) |
| binding constraint at that limit | **over-voltage** (vmax crosses 1.10 pu; line loading only 31.7%, trafo 5.5%) |
| Sandia's 216-feeder over-voltage-limited share | 72% |

Robust across seeds (0, 1, 2 all land on over-voltage, HC 99-106 kW).

**The comparison, honestly scoped:**
- *Magnitude* is not directly comparable — EULV is a 400 V LV secondary
  network behind one 800 kVA transformer (55 houses), a full class below
  Sandia's MV primary feeders. The sampled HC (99.7 kW) is ~12.5% of the
  transformer's own rating, which lands in the same direction as Sandia's own
  voltage-class stratification (their Fig. 12/14: "<5 kV feeders generally
  have a HC less than 1 MW", the lowest of their four voltage classes) — EULV,
  one class smaller again, landing lower still is the expected trend, not a
  contradiction, but it is not a number-for-number check.
- *Violation type* **matches**: the engine's binding constraint is
  over-voltage, the same dominant failure mode Sandia found on 72% of 216 real
  feeders. This is the qualitative check a thin slice can actually make
  honestly — the engine fails the same *way* real hosting-capacity studies do.

**Ties back to the roadmap's thesis, now against a named external metric:**
Sandia's own HC metric is explicitly single-site worst-case (min LHC over all
buses). On this same EULV network, that single-site number is 99.7 kW — but
experiment 25's NSGA-II search already found a **245.4 kW** fully-feasible
joint accept-set across 40 simultaneous smaller PV requests (M1). That's 2.5x
the single worst-case site's own standalone capacity, because spreading
requests across many buses avoids concentrating voltage rise at one point.
Sandia's own metric, applied as designed, systematically undercounts what a
feeder can host *jointly* — which is exactly the gap this repo's roadmap
exists to close, now demonstrated against a metric definition taken from the
published literature rather than an internal-only comparison.

## M2 follow-up: checked against ENWL's own capacity-register open data

Once an ENWL (Electricity North West, the same UK DNO behind this repo's
conductor-inference corpus) opendatasoft API key became available, the
obvious next check was: does our engine's LHC number hold up against a real
DNO's own published "capacity" data, not just Sandia's academic study?
`experiments/27_enwl_capacity_register_check.py` pulled
`sp-enw-capacity-distribution-tx` — real per-transformer generation headroom,
covering every ENWL distribution transformer, including 2,230 rated at
exactly 800kVA (EULV's own transformer size — the same asset class, unlike
Sandia's MV feeders).

**Result: it isn't a power-flow hosting-capacity figure, so it can't be used
as a number-for-number check — reported honestly rather than forced.**
`distribution_tx_generation_headroom_kva` is exactly `rating -
currently_connected_generation_kva`. Confirmed by querying it against ENWL's
own demand-utilisation labels: even the 77 real 800kVA transformers ENWL
itself flags **"No Capacity"** (avg. demand utilisation 144%, i.e. already
overloaded) report a median generation headroom of **100% of rating**. The
field is unconditional on load or voltage by construction — no power flow
ever touches it. `sp-enw-capacity-11kv-network`'s `section_headroom_kva`
(`firm_capacity - estimated_max_load`, thermal-only) follows the identical
pattern. Both are capacity-*register* subtractions, not load-flow-simulated
studies like Sandia's or this engine's.

**Still a useful, real-data result — just a different one than a validation
number.** It's direct evidence for the roadmap's own "why now" argument: a
register that never runs a power flow reads "almost fully free" (798-800 of
800kVA) at *every* 800kVA transformer regardless of how loaded it already is,
while this engine's power-flow-computed answer on the identical asset class
(EULV) is one to two orders of magnitude smaller — 99.7kW (12.5% of rating)
single worst-case, 245.4kW (30.7%) joint accept-set. That gap, quantified
with a real DNO's own numbers rather than an academic study, is exactly what
FERC Order 2023 cluster studies and GridCARE's funding are a response to: a
capacity register is a queue starting point, not a substitute for the joint
study.

## M3: scale + report, on a real feeder 5x EULV's size

**Network choice, and two rejections.** ROADMAP.md's M3 names SMART-DS or an
ENWL feeder. Both were checked directly, not assumed:
- An **NREL SMART-DS** feeder (`rhs0_1247--rdt1527`, experiment 09's
  3,294-bus rural Greensboro feeder) was pulled and evaluated through
  `AllocationProblem` with its declared ("no_loadshapes") snapshot load: it
  does not converge at pandapower's default tolerance, and even with the
  relaxed tolerance Ckt5 needed (M2), the *empty*-subset baseline (zero new
  requests) is already infeasible — vmin 0.887 pu, max line loading 188%,
  max transformer loading 206%. Same trap as Ckt5: a snapshot already broken
  before any interconnection request is added can't support a hosting-
  capacity framing without open-ended load-scaling/regulator work M2 already
  decided was out of scope for a thin slice.
- **EPRI's Ckt5** (already vendored for M2) has the identical problem for the
  same already-documented reason (declared load overloads its own service
  transformers at baseline).
- Seven real **ENWL** LV feeders (the same real-UK-DNO corpus experiments
  11/12/14/15/16 use for conductor inference) were then checked directly
  against `AllocationProblem`'s *default* bounds, no relaxed tolerances: six
  solved and were baseline-feasible outright. **`network_15/Feeder_4`** was
  picked as the largest of those six — 4,769 buses, 138 real house loads, one
  800 kVA transformer (same transformer size as EULV, 2.5x the customer
  count, 5.3x the bus count) — so M3 tests "how much more can a real feeder
  jointly host", not "can we get a broken snapshot to solve at all".

**Run:** `experiments/28_m3_scale_report.py --save`, 100 candidate rooftop-PV
export requests (same 3-10 kWp band as M0-M2, seed 0) on the real house
buses, greedy baseline vs. NSGA-II (`pop_size=30, n_gen=30` — smaller than
M1's 40/40, because this feeder's `runpp` is ~5x slower per solve; 900 solves,
~101 s):

| | accepted | accommodated (kW) | vmax (pu) | max line loading | cost |
|---|---:|---:|---:|---:|---:|
| greedy baseline (queue order) | 64/100 | 432.3 | 1.0489 | — | 0.000 |
| NSGA-II best feasible | 66/100 | **433.6** | 1.0520 | 99.97% | 0.000 |

**M1's bar still holds at this scale:** the search dominates the serial
baseline (+1.2 kW, equal cost), by accepting a different-shaped 66-request
subset, the same joint-packing effect as M1's EULV result — now demonstrated
on a feeder 5x the size. The margin is much thinner here (+1.2 kW / +0.3%
vs. EULV's +8.5 kW / +3.6%) — reported as observed, not inflated; a thinner
gain on a more slack-constrained network (this feeder's line loading was
already the binding constraint pre-search, closer to saturation than EULV's
voltage margin was) is a plausible, not suspicious, result.

**Binding constraint differs from EULV, honestly reported.** EULV (M2) is
over-voltage-limited, matching Sandia's 72%-of-feeders pattern. This
feeder's accept-set, on this seed's request pool, is **thermally limited on
a line** (99.97% of thermal headroom used vs. 52% of voltage headroom) —
not the same failure mode. Not re-checked across multiple seeds here (unlike
M2's 3-seed robustness check), so this is reported as "what this run found",
not "this feeder is always thermal-limited" — the M3 bar is running
end-to-end on a real feeder, not a second full validation study.

**Report artifact.** `experiments/28_m3_scale_report.py --save` writes two
gitignored files to `outputs/` (this repo's existing convention for
generated experiment artifacts, see experiments 13/20/23's `--save` flag):
- `outputs/m3_report.md` — the ranked accept-set (66 requests, largest
  first), the baseline-vs-search comparison, and the "why this subset"
  paragraph above.
- `outputs/m3_map.png` — a schematic radial-tree diagram (depth = electrical
  hops from the source transformer, angle = subtree position), built with a
  dependency-free iterative BFS/bottom-up layout (`radial_layout` in the
  experiment script) because pandapower's own `create_generic_coordinates`
  needs `igraph` or `pygraphviz`, neither installed, and this ENWL corpus
  ships no bus coordinates at all — unlike the CV-topology corpus this repo
  also uses (real GPS pole positions, a different dataset, different
  purpose), so a real georeferenced map isn't available here and the diagram
  is explicitly labelled as schematic, not GPS, rather than implied
  otherwise.

**M3 "done when" bar (ROADMAP.md): end-to-end on a real feeder, producing the
map/report artifact — met.**

## M4: write-up

`docs/paper.md` was extended with a "Part II — Joint Hosting-Capacity
Allocation on Open Data" write-up (Part I is the existing, unrelated
conductor-assignment benchmark) covering: the FERC Order 2023 / GridCARE /
Pearl Street-Enverus funding backdrop and the specific Grid-Mind gap it
targets (per-request automation, not cluster/joint allocation); the M0-M1
formulation and method; the M2 validation against Sandia's 216-feeder study
and the honest ENWL-capacity-register negative finding; the M3 real-feeder
scaling result, including its thinner margin and differing (thermal, not
voltage) binding constraint, reported as found; a limitations section
(constraint-oracle cost, the reinforcement-cost placeholder, sampled LHC,
inherited balanced-power-flow approximation, thin-slice scope); and a
funding-fit section framing the DOE i2X / EIC pitch around the working,
validated prototype documented above rather than a plan for one.

**M4 "done when" bar (ROADMAP.md): a written draft framing the contribution
and the funding landscape — met.**

## M5: is NSGA-II the right tool, or would a convex relaxation be faster?

**The question, raised directly.** NSGA-II (1994/2002) is an old metaheuristic
by ML-era standards, and it pays one `runpp` per individual — ROADMAP.md's
Hard Parts section already names that as the scaling bottleneck. Would a
"model-based" (convex) approach be both more modern and faster?

**Why a convex relaxation applies here at all.** Every feeder this repo has
imported (EULV, ENWL's `network_15/Feeder_4`) is *radial* — no loops — which
is exactly the structure LinDistFlow (Baran & Wu 1989) needs: with AC losses
dropped, the branch-flow power-balance and voltage-drop equations become
linear in branch power and squared bus voltage. Since every candidate request
here is a fixed-size accept/reject decision (no continuous sizing) with
`q_mvar=0` (every request builder in this repo already builds PV at unity
power factor), the whole feasible region collapses to a single **MILP** — one
binary accept/reject variable per request, one continuous power-flow variable
per branch, one continuous squared-voltage variable per bus — solved once
with `scipy.optimize.milp` (HiGHS). No new dependency: scipy is already a
pandapower prerequisite.

`src/allocation/convex.py` implements this: tree extraction (BFS from
`ext_grid.bus`, rejects non-radial networks), branch R/X/thermal-rating
extraction (validated against pandapower's own `runpp` to 5 significant
figures on EULV's transformer branch), an octagon-linearized thermal limit
(the true `P²+Q²≤S_max²` circle isn't linear; an 8-sided polygon inner
approximation is, and any octagon-feasible point is guaranteed circle-
feasible too), and the MILP builder/solver. **The MILP's own accept-set is
never trusted**: `lindistflow_search` always re-verifies its chosen subset
through the real `AllocationProblem.evaluate` (the same pandapower oracle
NSGA-II uses) — the returned result is exactly as authoritative as any other
search's.

### First pass: fast, but split by binding constraint

`experiments/29_convex_relaxation.py` runs four checks per network: baseline
parity (relaxed model vs. real `runpp` at the empty subset), accept-set
parity (forward-evaluate the greedy baseline's real accept-set through the
relaxed model), a head-to-head against NSGA-II, and an octagon-side sweep.

Speed was dramatic immediately: ~200x faster than NSGA-II (0.4-0.6s vs.
84-106s) on both networks. Quality split by which constraint actually binds:

| network | binding constraint | single-shot MILP (AC-verified) | vs. greedy baseline | vs. NSGA-II |
|---|---|---:|---:|---:|
| EULV | voltage | 243.4 kW | **+6.5 kW** | 99.2% |
| ENWL/M3 | thermal (line) | 426.1 kW | **-6.2 kW** | 98.3% |

On EULV it beat the status-quo baseline. On ENWL it **underperformed the
naive greedy baseline** — a genuine negative result, reported as found, not
smoothed over.

### Diagnosing the gap: the first hypothesis was wrong

The obvious suspect was the octagon's thermal-limit linearization, or the
nominal-voltage approximation used to convert current ratings to power
ratings (the true current-based rating depends on actual, decision-dependent
bus voltage — nonlinear, so nominal voltage v=1.0 pu was substituted). An
octagon-side sweep (8/16/32/64) ruled out polygon coarseness directly: the
gap didn't close (426.1 → 422.5 kW, if anything slightly worse from a
different discrete choice at finer resolution).

Tracing the actual most-loaded edge (the trunk line immediately after the
transformer, carrying the summed current of the *entire* 4,767-edge
downstream tree) found the real local voltage there was only ~1.001 pu —
essentially nominal, since it's electrically close to the fixed-voltage
source. The nominal-voltage thermal approximation was under 0.1 percentage
points of error at the one edge that actually mattered. That hypothesis was
wrong.

Comparing the relaxed model's own flow prediction at that edge against
pandapower's true solved flow (same accept-set) found the actual mechanism:
**pure LinDistFlow drops every edge's own resistive/reactive loss entirely.**
An individual edge's own loss is genuinely negligible (already validated to 5
significant figures on EULV's single-edge transformer branch) — but the
busiest trunk edge carries the *summed* current of its whole downstream
subtree, and losses across ENWL's 4,768 downstream edges compound: true
`q_from` on that edge was 0.0504 MVAr vs. this model's lossless constant of
0.0454 MVAr (~11% low), and the model's own lossless `P_e` was ~3.2% larger
in magnitude than pandapower's true `p_from` — both consistent with real
I²R/I²X dissipation the lossless recursion never subtracts. This is a
depth/edge-count effect the single-edge trafo-branch validation never would
have revealed.

### The fix, and a safety problem it introduced

`allocation.convex.lindistflow_search_iterative` is a fixed-point /
sequential-linear-approximation loop, still a MILP every round: solve once
lossless, estimate each edge's own loss from its solved flow
(`l_e ≈ P_e² + Q_e²`), fold `R_e·l_e` into the next round's power-balance
RHS at that edge's child bus (propagating up through the same subtree-
summing recursion real loads use) and `X_e·l_e` into the next round's
reactive-power aggregation, and re-solve. Repeats until the accept-set
stabilizes.

The first version of this, with no safety margin, found real capacity the
lossless model had left on the table (EULV: 243.4→246.1 kW; ENWL:
426.1→435.2 kW, both *above* NSGA-II's own results) — but the final round's
accept-set came back **AC-infeasible** on both networks: EULV overshot vmax
by 0.00015 pu (limit 1.10), ENWL overshot thermal loading by 1.3 percentage
points. Recovering the compounded-loss capacity had pushed the search close
enough to the true AC boundary that residual model error (untracked
second-order terms, octagon coarseness) could land just past it — the
single-shot model's "always conservative, zero false accepts" property was
gone.

Two guards were added, not one, because either alone was insufficient in
testing:
1. **AC-verify every round**, not just the last, and return the best round
   that is *actually* AC-feasible — cheap (a handful of extra `runpp` calls,
   negligible next to NSGA-II's hundreds). Alone, this just fell back to the
   always-safe but unimproved round-0 result every time (the later, better
   rounds were always the infeasible ones) — no net gain.
2. **A small tunable safety margin** (`voltage_margin_pu`, `thermal_margin_pct`)
   shrinking the search's own feasible region below the true planning limits,
   so its answer is more likely to already land inside the true feasible
   region rather than relying solely on after-the-fact rejection. Tuned
   empirically on the ENWL/seed-0 case (a small sweep over thermal margins
   from 1.0 to 3.0 percentage points found 1.4-1.5pp best — not a monotonic
   relationship, and not a principled/certified bound); shipped default
   `voltage_margin_pu=0.0005`, `thermal_margin_pct=1.5`.

### Final result

| network | greedy | single-shot MILP | **iterative MILP** | NSGA-II |
|---|---:|---:|---:|---:|
| EULV | 236.8 kW | 243.4 kW (99.2%, 0.4s) | **245.4 kW (100.0%, 1.1s)** | 245.4 kW (96s) |
| ENWL/M3 | 432.3 kW | 426.1 kW (98.3%, 0.6s) | **430.9 kW (99.4%, 1.9s)** | 433.6 kW (106s) |

EULV now matches NSGA-II exactly, 86x faster. ENWL closes most (not all) of
the gap — 99.4% of NSGA-II, up from 98.3%, and above the greedy baseline
again — at 55x the speed. Both are always AC-verified feasible in every test
run (single seed formally, seeds 0-2 spot-checked informally, all feasible).

**Honest scope of this validation, matching this repo's standard for a
result like this:**
- Only the same 2 networks M0-M3 already used (EULV, ENWL/M3) — no new
  network family.
- Seed robustness was a light, informal spot-check (0, 1, 2), not a
  committed, reproducible multi-seed sweep like M2's 3-seed check.
- The safety margins are empirically tuned on one network/seed, not derived
  from a principled error bound — a future request pool or network could
  need different values; the margin is a mitigation, not a proof of safety.
- On ENWL the iterative loop hits `max_iters=5` without the accept-set
  strictly stabilizing (`converged=False`) — the best AC-feasible round found
  across those 5 is still returned and is still an improvement, but the
  fixed point isn't cleanly reached on every network.
- Scope is unchanged from the single-shot version: radial networks only,
  `q_mvar=0` requests only, fixed-size accept/reject (no continuous sizing).

**M5 "done when" bar (this document, since M5 wasn't in the original
ROADMAP.md — added in response to a direct question, not a pre-planned
milestone): a working MILP alternative, every result AC-verified through the
same oracle NSGA-II uses, compared head-to-head against NSGA-II on both
existing validation networks for speed and accommodated MW — met.** The
narrower open question is whether the empirically-tuned safety margin
generalizes beyond the two networks/seeds tested here, which M5's validation
does not settle.

## M6: a real, catalogue-priced reinforcement-cost proxy

**The gap, flagged by the write-up itself.** `AllocationProblem.evaluate`'s
`reinforcement_cost_proxy` (problem.py) has always been a placeholder:
monotonic in how far voltage/thermal margins are violated, enough to give
NSGA-II a smooth gradient to climb out of infeasibility with, but explicitly
not a currency figure. `docs/paper.md` Section 5 said so directly
("a placeholder, not a price... turning it into one is scoped, deliberately,
to whenever reinforcement becomes a *lever* the search can actively choose
to apply, rather than only a violation to avoid") and Section 7 listed it as
the second concrete next step. This is also the gap raised earlier in this
same session, when asked whether the engine looked like a commercial
product: no defensible cost model was the first thing named as missing.

**The lever, deliberately narrow.** `src/allocation/reinforcement.py` prices
one thing: replacing an overloaded line with a larger standard conductor, or
an overloaded transformer with the next standard kVA size, from a small
catalogue. Two things are reused rather than invented:

- The line catalogue is exactly `gridsynth.parameters.generic_lv_catalogue`
  — the same standard IEC 60228 LV aluminium cross-sections and R/ampacity
  physics Part I already validated for conductor *inference* — reused here
  for conductor *upgrade sizing*. A change to Part I's validated conductor
  physics flows through to this pricing automatically, since it's the same
  function call, not a re-derived copy.
- Every upgraded network is re-solved through the same `pandapower.runpp`
  oracle the rest of this package trusts, never just trusted from the sizing
  arithmetic — the standing rule since M5. `price_reinforcement` solves
  as-built first, and if a line or transformer is thermally overloaded,
  sizes the smallest catalogue tier that clears the limit (with a small
  safety margin, the same "don't cut it exactly at the boundary" discipline
  M5's iterative solver uses), applies it, and re-solves — repeating up to a
  handful of rounds, since upgrading one element can occasionally shift
  enough current that a previously-fine neighbor now binds.

What it does **not** model, on purpose: voltage-specific devices (voltage
regulators, OLTC, capacitor banks). A pure voltage violation (or a thermal
violation past the largest catalogue tier) has no lever here and is reported
as `feasible_after_reinforcement=False`, `total_cost_eur=inf` — not silently
priced at zero or guessed away. Pricing itself is material/equipment cost
only: conductor mass (cross-section × length × aluminium density) × an
illustrative EUR/kg, or added transformer kVA × an illustrative EUR/kVA.
Labor, trenching, permitting, and regional variation — which typically
dominate a real installed cost by 5-10x for underground LV cable — are
explicitly excluded and documented as such in the module docstring; the
constants are order-of-magnitude, not sourced from a specific vendor quote,
and swappable via the `catalogue` argument for a real utility price sheet.

`allocation.baseline.reinforced_greedy_baseline` demonstrates the lever
concretely: the same queue-order walk as the plain `greedy_baseline`, but
instead of stopping dead at the first violation, it prices reinforcing past
it (`price_reinforcement`) and keeps going if that's affordable (an optional
`budget_eur` cap), skipping only the requests that genuinely can't be
resolved by this catalogue at any price.

### Result: exactly the split M5 predicted

M5 already found EULV's binding constraint is over-voltage and the M3 ENWL
feeder's is thermal (a line). Since this module's lever is thermal-only,
that split predicts directly where it should help — checked, not assumed,
via `experiments/30_reinforcement_cost.py`:

| network | plain greedy | reinforced greedy | NSGA-II (free) | reinforcement bought |
|---|---:|---:|---:|---:|
| EULV (voltage-limited) | 236.8 kW | 236.8 kW, €0 | 245.4 kW | **nothing** — no lever for over-voltage |
| ENWL/M3 (thermal-limited) | 432.3 kW | **521.3 kW, €848** | 433.6 kW | **+87.7 kW** beyond NSGA-II's free ceiling |

On EULV, `reinforced_greedy_baseline` is identical to the plain baseline —
every one of the 5 first-rejected requests individually priced comes back
`feasible_after_reinforcement=False` (`voltage_violation_pu` between 0.0006
and 0.003, `thermal_violation_percent=0.0` every time — voltage is
unambiguously what's binding, and this catalogue has nothing for it).
NSGA-II's free search already reaches more (245.4 kW) than paying for
reinforcement can unlock here, which is the honest, negative half of this
result: a real reinforcement lever is not automatically useful just because
it exists.

On the M3 ENWL feeder, the same lever recovers real capacity a zero-cost
search cannot reach at all: €848 buys 87.7 kW beyond NSGA-II's own
best-feasible result on this network/seed — genuinely new headroom, not
headroom NSGA-II could have found for free with a better search.

**The cost curve is lumpy, not smooth.** Pricing the marginal cost of just
one more request past the greedy baseline (a 5.5 kW PV export) came back
€368 — for **72 line upgrades at once**, not one. This is the same
mechanism M5's loss-compounding investigation found: a chain of trunk lines
near the source, each carrying the summed current of a large downstream
subtree, cross their 100% loading threshold together once demand/export
past a certain point is added, rather than one edge binding at a time. The
next four rejected requests (up to +6.5 kW more) all price at the *same*
€368 — the 72-line upgrade already bought enough headroom that no further
upgrade was needed for several more small requests. A cost curve built from
this catalogue should be expected to look like a step function with large,
infrequent jumps on a feeder shaped like this, not a smooth marginal-cost
curve — a real structural finding about ENWL's topology, not a modeling
artifact of the pricing logic.

**Honest scope of this validation:**
- Only the same 2 networks M0-M3/M5 already used — no new network family.
- Catalogue prices (EUR 8/kg aluminium, EUR 20/kVA transformer) are
  order-of-magnitude illustrative, explicitly material/equipment-only
  (excludes labor, trenching, permitting) — not a vetted regional utility
  price sheet. The *mechanism* (cost scales with conductor cross-section /
  transformer kVA, sized to the smallest catalogue tier that clears the
  violation, AC-reverified) is the deliverable; the specific EUR figures are
  not meant to be trusted as a quote.
- The lever is thermal-only. A network whose binding constraint is voltage
  (like EULV) gets zero benefit from this module, by design, reported
  honestly rather than worked around with an ad hoc voltage-fixing hack.
- `reinforced_greedy_baseline` re-derives the full upgrade plan from scratch
  against `problem.net`'s original ratings on every queue step, rather than
  incrementally reusing a previous round's already-upgraded network state —
  simpler to reason about and correct, at the cost of some redundant
  `runpp` solves (acceptable at this package's problem sizes; ROADMAP.md's
  Hard Parts already names constraint-oracle cost as the thing to watch).
- `reinforcement_cost_proxy` (the original placeholder) is untouched and
  still what NSGA-II's live per-generation search optimizes against — a
  smooth continuous penalty is what a live search needs to climb out of
  infeasibility with, and a step-wise catalogue price (as this section's
  "lumpy cost curve" finding shows directly) is not that. `price_reinforcement`
  is a genuinely separate, currency-denominated tool for pricing a specific
  accept-set after the fact, not (yet) wired into NSGA-II's or the MILP's
  own live objective.

**M6 "done when" bar (ROADMAP.md, this document): a real currency-priced
upgrade-cost function, AC-reverified, demonstrated on both validation
networks, specifically checking whether it helps exactly where M5 found
thermal binds and honestly does nothing where M5 found voltage binds —
met**, with the EULV negative result and the ENWL lumpy-cost-curve finding
both reported as found. Open questions this validation does not settle:
whether the illustrative catalogue prices are close to a real regional
utility's, and whether wiring `price_reinforcement` into the live search
(rather than pricing accept-sets after the fact) would change what either
search finds.

## M7: continuous request sizing

**The gap, flagged by the write-up itself.** Every decision in M0-M6 is
binary: accept a request at its full requested size, or reject it entirely.
`docs/paper.md`'s Future Work section named this directly as the third
concrete next step — "extend the binary accept/reject decision to
continuous request sizing" — and the one-line target at the top of
ROADMAP.md always described the goal as a Pareto front over {accommodated
MW, upgrade cost, queue-order fairness} with *at what sizes* requests are
accepted, not just which ones.

**The core primitive.** `AllocationProblem.evaluate_sizes(sizes: Mapping[str,
float])` generalizes `evaluate`: each request id maps to a fraction in
(0, 1] of its full `p_mw`/`q_mvar` (both scaled by the same fraction,
preserving the request's power factor); `evaluate(accepted_ids)` is now a
one-line wrapper — `evaluate_sizes({rid: 1.0 for rid in accepted_ids})`.
`AllocationResult` gained a `sizes` field alongside the existing
`accepted_ids` (exactly the keys of `sizes`) so every existing caller
(baseline, NSGA-II, the M5 MILP, M6's reinforcement pricing) kept working
unchanged — this was a purely additive change, confirmed by running the
full test suite before touching anything else (89/89 passing beforehand,
still 89/89 after `evaluate_sizes` landed, before any new sizing code was
added).

Three searches were built on top, one per existing binary method:

- **`continuous_greedy_baseline`** — the same serial queue walk as
  `greedy_baseline`, but a request that doesn't fit at full size gets
  bisected down to the largest fraction that does, instead of being skipped
  — the continuous-sizing analogue of `locational.locational_hosting_capacity`'s
  bisection, now applied request-by-request against the network state
  already built up from earlier partial accepts.
- **`nsga2_search_continuous`** — the same two-objective NSGA-II as
  `nsga2_search` (`[-accommodated_mw, reinforcement_cost_proxy]`), but each
  gene is a real-valued fraction in [0, 1] instead of a binary bit, using
  pymoo's `SBX` crossover / `PM` polynomial mutation / `FloatRandomSampling`
  instead of the binary operators.
- **`lindistflow_search(continuous=True)`** — M5's LinDistFlow MILP with the
  accept variables' integrality constraint dropped. This needed almost no
  new code: the power-balance equation already uses `accept_r * p_r` as a
  *linear* term, so a fractional value between 0 and 1 already means
  "fraction of `p_r` accepted" with no reinterpretation — relaxing
  integrality turns the MILP into a plain LP for free. The one real fix
  needed was in `_solve`: accept values were being rounded to `int` before
  being returned (harmless for the binary case, silently wrong for the
  continuous one, since it would discard every fraction and collapse back
  to accept/reject) — caught before it shipped by checking the tiny
  synthetic net's output, not by a failing test.

**The guarantee, checked, not just assumed.** Since {0, 1} is a strict
subset of [0, 1], continuous sizing's true optimum can never be *worse*
than the binary decision's on the same network/requests. This held
empirically for the two deterministic methods on both validation networks:

| network | method | binary | continuous |
|---|---|---:|---:|
| EULV | greedy baseline | 236.8 kW | **237.3 kW** (+0.4 kW) |
| EULV | LinDistFlow LP | 243.4 kW | **246.0 kW** (+2.6 kW) |
| ENWL/M3 | greedy baseline | 432.3 kW | **432.7 kW** (+0.3 kW) |
| ENWL/M3 | LinDistFlow LP | 426.1 kW | **426.2 kW** (+0.1 kW) |

Small gains, and never negative — consistent with both networks already
being tightly packed by the binary search (only 1-5 of 40-100 requests ever
receive a genuine partial fraction; most land at 0 or 1 even in the
continuous formulation), and with M6's "lumpy, not smooth" finding on ENWL
in particular: headroom there tends to run out in a block (many trunk
edges saturating together), leaving little room for a small marginal
fraction to matter.

### A genuine negative result: continuous NSGA-II, at equal budget

`nsga2_search_continuous` on EULV (pop_size=40, n_gen=40 — the same budget
`nsga2_search` uses, ~155s) reached **231.1 kW — worse than binary
NSGA-II's 245.4 kW, and worse than the plain binary greedy baseline's 236.8
kW.** This is not a violation of the {0,1}⊂[0,1] guarantee (that guarantee
is about the *true optimum* of each search space, not about what a
population-based metaheuristic actually finds within a fixed budget) — but
it is a real, reportable finding: giving a search more freedom made it
perform *worse* at the same computational cost.

Diagnosed, not just observed: `FloatRandomSampling` initializes each gene
near 0.5 on average, whereas `BinaryRandomSampling` starts every gene
already *at* one of the two corners (0 or 1) a near-optimal solution for
this kind of linear-objective, near-binding-constraint allocation problem
tends to sit at (most requests end up either fully accepted or fully
rejected even in the continuous formulation, per the table above). SBX
crossover and polynomial mutation are built for smoothly exploring a
continuous landscape, not for driving values to the boundary of a box
constraint quickly — so the continuous search spends much of its 40-
generation budget still migrating away from a bad, diffuse starting
population, while the binary search starts in the right neighborhood by
construction. A follow-up check at 2x the budget (`pop_size=80,
n_gen=80` — 4x the total evaluations) did not finish within 590 seconds
(binary's own full run takes ~155s) — the gap was not confirmed closed,
only that closing it costs substantially more compute than the binary
search needs for a comparable result. This is left as an open question,
not resolved here.

**Practical takeaway:** for continuous sizing specifically, the
deterministic routes (`continuous_greedy_baseline`, and especially the
convex LP) are the reliable way to realize the {0,1}⊂[0,1] advantage on
these networks — cheap, exact for the relaxed model, and never observed to
underperform their binary counterparts. `nsga2_search_continuous` is
provided for completeness and for problems where the convex relaxation's
assumptions don't hold (a non-radial network, or nonzero-Q requests), but
its current default budget is not tuned for the continuous decision space
and should not be assumed to match `nsga2_search`'s reliability without
a larger (unquantified) budget or better-seeded initial population.

### A second AC-verification catch, this time on continuous sizing

The convex LP's own optimum is not just checked for correctness in
principle — it was caught being wrong in practice once, on a tiny
single-edge synthetic feeder (6 requests, used for unit tests): the
unmargined continuous LP's relaxed optimum (0.0346 MW) came back
**AC-infeasible** when re-verified (loading over 100%). This is a different
manifestation of a risk M5 already named for the *iterative* binary
solver: a continuous LP optimum is generically a vertex where multiple
constraints are exactly tight (an LP's nature), which lands closer to the
model's own approximation boundary (dropped losses, octagon linearization,
nominal-voltage thermal rating) than a binary choice typically does — so
the residual gap between the relaxed model and real AC physics is more
likely to tip a continuous result into actual infeasibility. A small
`thermal_margin_pct=2.0` (the same knob `lindistflow_search_iterative`
already exposed) fixed this specific case (0.034 MW, AC-feasible). On both
real validation networks (EULV, ENWL) the unmargined continuous LP came
back AC-feasible in this seed's test — so this was not observed as a
problem on real feeders, only demonstrated as a real, reachable risk on a
constructed one. `lindistflow_search`'s AC-reverification step
(`problem.evaluate_sizes`, not the relaxed model's own belief) is what
caught it — exactly the discipline that made this a non-issue rather than
a silently-wrong result.

**Honest scope of this validation:**
- Only the same 2 networks M0-M6 already used — no new network family.
- NSGA-II continuous was only run once per network at the default budget
  (plus one partial, inconclusive larger-budget check on EULV) — not the
  multi-seed check M2's validation used, since the qualitative finding
  (underperformance at equal budget) was already clear and running it
  repeatedly at larger budgets would cost tens of minutes per attempt for
  a result already understood, not further confirmed.
- `allocation.reinforcement` (M6) remains binary-only — `price_reinforcement`
  and `reinforced_greedy_baseline` were not extended to continuous sizing;
  pricing a *partial* upgrade need is a real but separate question, out of
  scope here.
- `lindistflow_search_iterative` (M5's loss-compounding fix) stays
  binary-only by design — combining continuous sizing with the loss-
  correction fixed-point loop is not validated.
- Physical meaningfulness is not uniform across request types. A fractional
  PV award corresponds to a real, common utility practice (curtailment
  agreements, a smaller inverter, non-firm capacity) — continuous sizing is
  natural there. A fractional EV charger or fixed demand connection is a
  less physically obvious thing to grant at, say, 42% (though it could
  represent a scheduled/curtailable demand agreement, or substituting a
  smaller unit) — the model treats every request uniformly via `p_mw`
  regardless of what it represents, and this note does not attempt to
  distinguish them.
- Queue-order fairness remains deferred, as `docs/paper.md` already flagged
  — continuous sizing widens *what* the Pareto front can reach, it doesn't
  add the third objective.

**M7 "done when" bar (ROADMAP.md, this document): the core sizing
primitive plus all three continuous search variants working and
AC-verified, with the {0,1}⊂[0,1] guarantee checked empirically on both
validation networks — met** for `continuous_greedy_baseline` and the
convex LP (both networks, consistently positive or zero gains, never
negative). **Not met, and reported as such, for `nsga2_search_continuous`**
at its current default budget on EULV — a real negative result, diagnosed
but not resolved, left as an open question rather than smoothed over by
quietly picking a budget that happens to look better.

## M8: multi-feeder validation

**The gap.** Every engine-level claim through M7 — the M1 search-beats-
baseline bar, the M2 voltage-vs-thermal binding-constraint pattern, M5's
convex-LP-vs-NSGA-II approximation quality — was checked on exactly *two*
real networks: EULV, and `network_15/Feeder_4`, one ENWL feeder hand-picked
in M3 as the largest of seven candidates that happened to be baseline-
feasible. `docs/paper.md`'s own Future Work section flags this directly as
the fourth concrete next step: "validate across more than one real-feeder
family, the way Part I validates conductor assignment across 128 feeders
rather than 4." M8 closes that gap using the same corpus Part I already
uses: ENWL's full "Low Voltage Network Solutions" corpus (BSD-3, vendored
via Electa-Git/PowerModelsDistributionStateEstimation.jl, already cached
locally from experiments 11/12's conductor-inference work — no new
download) — 128 real UK LV feeders across 25 physically distinct networks
(substations/areas).

**Tool choice, stated up front.** NSGA-II is the exact-AC-oracle search
(M1), but it pays one `runpp` per individual — ROADMAP.md's own Hard Parts
section names this as the scaling bottleneck, and M3 measured ~101s for one
feeder 5x EULV's size. Running it across the full corpus would cost hours,
not a session. M5 built exactly the alternative this needs:
`lindistflow_search_iterative`, AC-reverified every round (never trusted
from the relaxed model alone), already shown to reach 99.4-100% of NSGA-II's
accommodated MW at 55-86x the speed on the two networks checked so far. This
experiment (`experiments/32_multi_feeder_validation.py`) uses it as the
primary search for the full sweep, and spot-checks NSGA-II agreement on 6
feeders spanning the observed size range — extending M5's own 2-network
approximation-quality check rather than assuming it holds at scale.

**Method.** For each cached feeder: import via `import_opendss`, check
baseline (empty-request) feasibility with `AllocationProblem`'s default
bounds — the same trap M2/M3 already hit with EPRI's Ckt5 and a SMART-DS
feeder (declared load already overloads the network before any
interconnection request is added) is checked here too, not assumed absent.
Feasible feeders get up to 50 candidate rooftop-PV export requests (same
3-10 kWp band as M0-M7), `greedy_baseline` vs.
`lindistflow_search_iterative`, and the M2-style binding-constraint
classification (whichever of voltage headroom or thermal headroom is closer
to its bound at the search's AC-verified result).

**A methodology bug caught and fixed before the real run.** The first full
run used a single shared seed (0) for every feeder's request generation.
Many differently-sized feeders came back with *byte-identical* accommodated
MW to 13 significant figures — investigated directly rather than assumed
coincidental, and traced to a genuine CPython `random.sample()` quirk: for
a fixed `k`, the number of underlying PRNG words `sample()` consumes at each
selection step depends only on the *bit length* of the remaining population
size, not its exact value, so many differently-sized feeder load-bus pools
advance the shared `Random(0)` generator identically and produce the same
downstream `rng.uniform()` draws for PV sizes. Not a bug in the allocation
engine itself, but it defeats the purpose of testing many *independent*
scenarios, so it was fixed: each feeder now gets its own seed
(`SEED + feeder_index`), confirmed to produce genuinely different request
mixes across feeders (re-verified directly, e.g. one feeder's search-vs-
baseline gap changed from +6.3 kW to +31.1 kW between the two runs on the
same network, same electrical model, different independent request draw)
before the real corpus run below.

**Result.** 128 feeders scanned (518s total, `--save` writes
`outputs/m8_multi_feeder.csv`): 121 baseline-feasible and evaluated, 5
already-infeasible at baseline (the same Ckt5/SMART-DS trap, checked not
assumed), 2 with fewer than 3 load buses (too small for "which subset can
we jointly host" to be a meaningful question) — spanning all 25 networks,
65-4,769 buses.

| | count |
|---|---:|
| feeders scanned | 128 |
| baseline-feasible + evaluated | 121 |
| baseline-infeasible (excluded) | 5 |
| too few loads (excluded) | 2 |
| search dominates baseline | 120/121 (99%) |
| — exact ties | 116 |
| — strict gains (up to +32.2 kW) | 4 |
| — regressions | 1 (-1.9 kW, 0.5%) |
| binding constraint: thermal-limited | 110/121 (91%) |
| binding constraint: voltage-limited | 11/121 (9%) |

**The M1 bar holds on 120/121 (99%).** 116 of 121 are exact ties — on most
of these real feeders, the naive queue-order walk already lands on the
AC-optimal accept-set for this request mix, leaving no room for a search to
improve on it (a different, more concentrated finding than M1's original
EULV result or M3's thinner-margin ENWL result, both of which had daylight
between baseline and search). 4 feeders show a real gain, up to +32.2 kW
(`network_5/Feeder_5`) and +31.1 kW (`network_1/Feeder_4`), both
voltage-limited cases — consistent with M1/M3's original finding that the
search's advantage shows up most where reordering can trade a large request
for several smaller ones under a tight, shared limit.

### A genuine regression, investigated rather than hidden

One feeder, `network_7/Feeder_3` (417 buses), shows the search
*underperforming* the greedy baseline: 346.4 kW vs. 348.3 kW (-1.9 kW,
0.5%). Traced directly, not waved away:

- The iterative search's own convergence trace (`accommodated_mw_trace`)
  climbs 333.7 → 345.7 → 346.4 → 346.1 → 346.1 kW over 5 rounds before
  converging (accept-set stops changing) — the relaxed model's own belief
  plateaus at 346.1-346.4 kW, genuinely below the greedy walk's 348.3 kW on
  this specific feeder/request mix, not a bad AC-reverification throwing
  away a better relaxed answer.
- Comparing accepted sets: the greedy baseline uniquely accepts 2 requests
  (`pv7`, `pv8`) that the search's chosen accept-set does not; the search
  uniquely accepts 1 (`pv49`) the baseline does not. The search's LinDistFlow
  view of the network (octagon-linearized thermal limit, nominal-voltage
  branch ratings, iteratively loss-corrected but still an approximation)
  apparently can't "see" that the particular combination the greedy walk
  stumbled into by blind luck is actually AC-feasible and larger.
- This is exactly the approximation gap M5 already documented and partially
  fixed with the iterative loss-correction loop (dropped losses, the
  octagon thermal linearization, nominal-voltage branch ratings) — this
  result shows that fix closes the gap on nearly all feeders (120/121) but
  not provably every one. Reported as found: the iterative search is a very
  good, AC-reverified-safe approximation of the exact-AC search, not a
  proof of exact equivalence.

### A real bug the broader sweep caught, not introduced by it

Investigating the regression above also surfaced that `lindistflow_search`
(the single-shot, non-iterative M5 variant — not used in this milestone's
main sweep, but exercised while diagnosing the regression) crashed outright
on this same feeder: HiGHS's MILP solver returned `1.000000000000004` for a
should-be-`1.0` binary accept variable, and `evaluate_sizes`'s strict
`sizes` range check (`0.0 <= frac <= 1.0`, added in M7) rejected it with a
`ValueError`. `_solve`'s own docstring already documented that MILP binaries
come back only "very close to," not exactly, 0/1 — but nothing clamped the
raw solver value before it reached `evaluate_sizes`. This bug has been
latent since M5 and could in principle have been hit by any real network,
not just this corpus; the 128-feeder sweep is simply what surfaced it
first, the same way M2's Sandia comparison and M3's SMART-DS/Ckt5 checks
surfaced real modeling snags earlier. Fixed with a one-line clamp
(`min(1.0, v)`) in `convex._summarize`, plus a regression test
(`test_summarize_clamps_solver_overshoot_past_one`) reproducing the exact
overshoot value directly rather than relying on hitting the same feeder
again. Full suite re-run after the fix: 103/103 passing.

### The binding-constraint pattern reverses almost entirely from Sandia's

M2 validated against Sandia SAND2015-9712C's 216 real *MV* feeders: 72%
over-voltage-limited, 26% line-overload-limited, ~0% transformer-limited —
and EULV matched that pattern. Across this corpus of 121 real *LV* feeders,
the pattern is nearly the opposite: **91% thermal-limited, 9%
voltage-limited.**

This is reported as found, not smoothed over, and it sharpens a caveat M2
already flagged rather than contradicting anything: M2's own write-up noted
"magnitude isn't directly comparable (EULV is LV secondary, Sandia's
feeders are MV primary)" when the comparison was first made, on a sample of
one LV feeder. With 121 LV feeders now checked, that qualitative difference
looks structural, not incidental — real UK LV secondary feeders, under this
repo's request methodology, are overwhelmingly thermal-constrained, plausibly
because their conductors are sized for local demand rather than the export-
heavy, largely-single-direction current this PV-request corpus adds, while
Sandia's MV primary feeders (longer runs, voltage-regulation equipment,
different conductor economics) hit voltage limits first more often. **A
genuinely interesting side effect: EULV itself, this repo's very first test
network and the one M2 validated against Sandia with, turns out to be the
*atypical* case relative to this real-LV-feeder corpus (voltage-limited,
like only 9% of these 121 feeders), not the representative one.** This
repo's earlier validation choice wasn't wrong — EULV matched Sandia's
*pattern*, which was the M2 bar — but M8 shows that pattern is the minority
case among real LV feeders specifically, a distinction M2's 1-feeder sample
couldn't have surfaced.

### NSGA-II spot-check: M5's approximation-quality finding generalizes

6 feeders spanning the observed size range (65 to 4,769 buses) were also
run through `nsga2_search` (pop/gen 30-40 depending on size, matching M1/M3
conventions) and compared against the convex search's accommodated MW:

| feeder | buses | convex (kW) | NSGA-II (kW) | gap |
|---|---:|---:|---:|---:|
| network_20/Feeder_5 | 65 | 133.0 | 133.0 | +0.0 |
| network_4/Feeder_1 | 375 | 163.6 | 163.6 | +0.0 |
| network_7/Feeder_2 | 605 | 318.0 | 318.0 | +0.0 |
| network_24/Feeder_1 | 1,076 | 322.5 | 322.5 | +0.0 |
| network_13/Feeder_2 | 1,509 | 244.2 | 244.2 | +0.0 |
| network_15/Feeder_4 | 4,769 | 310.9 | 310.9 | +0.0 |

Exact agreement on all 6 — M5's 2-network finding (the convex search
reaches 99.4-100% of NSGA-II's accommodated MW) generalizes across this
larger, more diverse sample, at least on the feeders checked here.

**Honest scope of this validation:**
- One seed per feeder (not M2's 3-seed robustness check on a single
  network) — with 121 independent feeders each contributing one real,
  physically distinct test case, the multi-feeder breadth is itself the
  robustness check here, not a substitute the way a 3-seed check on one
  network would be.
- The request-generation methodology (3-10 kWp rooftop-PV export band, up
  to 50 candidates per feeder) is unchanged from M0-M7 — this validates
  *this repo's own* search-vs-baseline and binding-constraint claims at
  real scale, not a comparison against an external published multi-feeder
  study (unlike M2's Sandia comparison; no such published multi-feeder
  hosting-capacity study covering this exact corpus was available).
- The NSGA-II spot-check is 6 feeders, not all 121 — a sample chosen to
  span the observed size range, not an exhaustive re-validation (which
  would cost hours per the tool-choice reasoning above).
- `continuous_greedy_baseline`/`nsga2_search_continuous`/`lindistflow_search
  (continuous=True)` (M7) were not re-run across this corpus — M8 re-runs
  the binary M1/M2 claims specifically, since those were the ones
  `docs/paper.md` flagged as validated on only 2 networks.

**M8 "done when" bar (ROADMAP.md, this document): the M1 search-beats-
baseline bar and the M2 binding-constraint check both re-run across a real
multi-feeder corpus — met**, with two genuine findings reported plainly
rather than smoothed over: a small (0.5%), explained regression on 1 of 121
feeders, and a near-total reversal of the M2/Sandia binding-constraint split
that sharpens rather than undermines M2's own already-stated LV-vs-MV
caveat.

## M9: queue-order fairness as a genuine Pareto objective

**The gap.** This document's own M0 formalization has always listed a
queue-order fairness term as objective (3), alongside accommodated MW and
reinforcement cost — and just as consistently marked it "deferred." M1
through M8 all optimized (or measured) only the first two. Every milestone
since M6 made the serial baseline *smarter* — `reinforced_greedy_baseline`
(M6) and `continuous_greedy_baseline` (M7) both skip an infeasible request
and keep walking the queue, instead of `greedy_baseline`'s stop-dead
behavior — without ever measuring what that extra capacity cost in
queue-order fairness. This milestone closes that gap: it's the last
unbuilt piece of the *original* problem formulation, not a newly invented
extension.

**The metric.** `queue_fairness_violations(requests, accepted_ids)` counts
queue-order inversions: pairs of requests where the earlier-queued one
(lower `queue_order`) was rejected while a later one was accepted — "how
many times did we jump the queue" relative to strict first-come-first-served.
Computed in O(N log N): sort by `queue_order`, then for each accepted
request add up how many earlier requests were already rejected. Pure
combinatorics over `Request.queue_order` and an accept-set — no `runpp`
needed, so it costs nothing extra wherever it's used as a search objective.

`greedy_baseline` (M1) is fairness-perfect **by construction**: it stops
dead at the first violation, so its accept-set is always a prefix of the
queue-ordered list, which can never contain an inversion. Verified directly
(`test_greedy_baseline_has_zero_violations`), not just argued. The two
"smarter" baselines built since are not — `continuous_greedy_baseline`
bisects a request down instead of stopping, and `reinforced_greedy_baseline`
prices past a violation and keeps going, so a request further back in the
queue can be accepted after an earlier one is dropped. Both were validated
against M1's capacity bar when they were built; neither was ever checked
against this one, because the metric didn't exist yet.

**The search.** `nsga2_search_fair` is `_FairSubsetProblem`: the same
binary accept/reject decision `nsga2_search` (M1) already searches, with
`queue_fairness_violations` added as a genuine third objective —
`[-accommodated_mw, reinforcement_cost_proxy, queue_fairness_violations]`.
A new function, not a change to `nsga2_search` itself: spreading the same
generation budget across a third objective could shift the converged front
away from the max-accommodated-MW point every prior milestone's own
validated numbers rest on, so those stay exactly as they were.

**Result (`experiments/33_fairness_pareto.py`, both existing validation
networks):**

EULV (40 requests, pop=40/gen=40, matching M1's own budget):

| method | accepted | accommodated (kW) | violations |
|---|---:|---:|---:|
| greedy baseline (M1) | 35 | 236.8 | 0 |
| reinforced greedy baseline (M6) | 35 | 236.8 | 0 |
| continuous greedy baseline (M7) | 37 | 237.3 | 0 |
| nsga2_search (M1, plain) | 37 | 245.4 | 72 |
| nsga2_search_fair, best per violation level | 35-37 | 236.8 → 245.1 | 0 → 48 |

`nsga2_search_fair`'s front: 236.8 kW at 0 violations (exactly matching
`greedy_baseline` — a clean cross-check that the fairness-optimal point the
3-objective search finds really is the FCFS answer, not a coincidence), 240.9
kW at 19, 243.7 kW at 31, 245.1 kW at 48. **Demanding zero queue-jumps costs
8.3 kW of the plain search's own 245.4 kW ceiling** — a real, quantified
trade-off, not a theoretical one. The fair search also finds a *better-
balanced* point than the plain search's own answer: 245.1 kW at 48
violations vs. the plain search's 245.4 kW at 72 — 33% fewer queue-jumps
for 0.3 kW less capacity, i.e. once fairness is visible as an objective,
there's real slack to reduce it cheaply that the 2-objective search had no
reason to notice.

ENWL (M3's feeder, 100 requests, pop=30/gen=30, matching M1/M3's own
budget for this network):

| method | accepted | accommodated (kW) | violations |
|---|---:|---:|---:|
| greedy baseline (M1) | 64 | 432.3 | 0 |
| reinforced greedy baseline (M6) | 79 | 521.3 | 8 |
| continuous greedy baseline (M7) | 66 | 432.7 | 2 |
| nsga2_search (M1, plain) | 66 | 433.6 | 1,191 |
| nsga2_search_fair, only feasible point found | 62 | 433.1 | 443 |

Two things worth reporting plainly here, one a genuine win and one a real
scope limit:

- **A previously invisible, wildly disproportionate trade.** M3 already
  reported the plain search's capacity gain over the baseline on this
  thermal-limited feeder as "thinner" than EULV's (+1.2-1.3 kW vs. +8.6 kW)
  — but had no way to say *what that thin gain cost*. It costs 1,191
  queue-order violations. M6's reinforced baseline, too: its headline
  432.3→521.3 kW gain (M6's own result) carries a real, now-measured cost
  of 8 violations — small in absolute count, but previously simply
  unmeasured, not zero.
- **The fair search's front is sparser here, honestly reported.** At the
  same population/generation budget M1/M3 already established for this
  100-request problem, `nsga2_search_fair` converges to only 1 fully
  feasible individual in the final population, not a multi-point front like
  EULV's. Consistent with this repo's now-repeated finding (M7's continuous
  NSGA-II, M8's binding-constraint checks) that a fixed NSGA-II budget
  resolves proportionally less of a larger or higher-dimensional search
  space — a 3rd objective on a 100-request problem is a bigger ask of the
  same 900-eval budget than on EULV's 40-request one. That single point is
  still doing real work, not a failure: 433.1 kW at 443 violations strictly
  beats the plain search's own 433.6 kW at 1,191 — 62% fewer queue-jumps
  for 0.5 kW less capacity, so even a budget-starved fair search finds a
  meaningfully better-balanced answer than a search that never saw
  fairness as an objective, just not a full range of options along the
  way.

**Honest scope limits:**
- `nsga2_search_fair` only extends binary `nsga2_search` (M1) — M7's
  continuous sizing and M5's convex-relaxation MILP were not given a
  fairness objective. The convex/MILP tool in particular would need either
  a linear proxy for the inversion count (the exact pairwise metric is
  quadratic in the accept variables, not directly MILP-representable) or an
  epsilon-constraint reformulation — a real extension, deliberately left
  for if/when the convex tool's speed is needed *and* fairness together,
  which hasn't come up yet.
- Not re-validated across M8's 121-feeder corpus — `queue_fairness_violations`
  itself is trivial to compute anywhere (no `runpp`), but `nsga2_search_fair`
  inherits `nsga2_search`'s per-feeder cost, so a full-corpus fairness sweep
  would face the same budget problem M8 already solved for capacity alone
  by using the convex search instead — doing the same for fairness is a
  clear, scoped follow-up, not attempted here.
- The ENWL front (1 point) is not a rich enough sample to characterize the
  *shape* of the capacity-fairness trade-off there the way EULV's 4-point
  front does — only that a real, favorable trade-off exists at that one
  point.

**M9 "done when" bar (ROADMAP.md, this document): the fairness metric
implemented and validated (`greedy_baseline` scores 0 by construction), a
genuine 3-objective search built on top, and a real capacity-vs-fairness
trade-off demonstrated on both validation networks — met**, with the ENWL
front's sparser resolution at the established budget reported as a real,
scoped limitation rather than papered over.

## M10: constraint-oracle caching + parallelization

**The gap.** ROADMAP.md's "Hard parts / risks" section has, since M0,
named the constraint-oracle cost as the honest scaling risk, with a
three-part suggestion: "cache converged states, warm-start, and parallelize
the evaluation across workers." M5 addressed the *radial-network* case
entirely differently (a MILP that sidesteps `runpp` altogether), but every
NSGA-II run — `nsga2_search`, `nsga2_search_fair`, `nsga2_search_continuous`
— still pays one `runpp` per individual per generation, exactly the cost
that forced M3's `pop_size`/`n_gen` down from M1's defaults on the larger
ENWL feeder. `docs/paper.md`'s own Section 7 lists this as future-work item
(1), the first of the four items it names — the only one of the four not
yet addressed before this milestone (M6 did item 2, M7 did item 3, M8 did
item 4).

**Two independent pieces, checked before being built.**

1. **Caching.** Before writing any caching code, instrumented a real EULV
   `nsga2_search` run (pop=40, gen=40, seed=0) to ask whether it would even
   help: NSGA-II's own `eliminate_duplicates=True` only dedupes *within* one
   generation's newly-sampled offspring, and a surviving elite individual is
   never re-evaluated (its fitness carries forward from when it was first
   computed) — so the only way a cache could pay off is if crossover/
   mutation regenerates a genotype tried and discarded in an *earlier*
   generation. Measured: 121 of 1,640 `runpp` calls (7.4%) were exact
   repeats of an already-evaluated accept-set. Non-trivial, and free to
   catch (a pure function of `self.net` + the granted accept-set, since
   `evaluate_sizes` always works on a fresh `copy.deepcopy` and never
   mutates `self.net`), so `AllocationProblem.evaluate_sizes` now memoizes
   by `frozenset(granted.items())` — covering both binary accept/reject
   (`evaluate`, fraction always 1.0) and continuous sizing (`evaluate_sizes`
   with a genuine fraction) with the same cache. A dict on the instance,
   not shared across instances or processes (see the parallelization
   finding below).
2. **Parallelization.** `nsga2_search`, `nsga2_search_fair`, and
   `nsga2_search_continuous` all gained an `n_jobs` parameter. `n_jobs=1`
   (default) is the exact pre-M10 sequential code path — pymoo's
   `Problem.__init__` default `elementwise_runner` (`LoopedElementwiseEvaluation`)
   is left untouched unless a runner is explicitly passed. `n_jobs>1` spins
   up a `multiprocessing.Pool`, wraps it in pymoo's own
   `StarmapParallelization`, and passes it as `elementwise_runner` to the
   underlying `_SubsetProblem`/`_FairSubsetProblem`/`_SizingProblem` —
   pymoo then dispatches one generation's population evaluation across
   that pool instead of a Python loop. `n_jobs<=0` uses `os.cpu_count()`
   workers. This is deliberately a *parameter* on the three existing
   functions, not a fourth new function the way M7/M9 added
   `nsga2_search_continuous`/`nsga2_search_fair`: those changed what gets
   searched (a different decision space, a third objective), so keeping
   the original function frozen protected already-validated results;
   parallelization changes nothing about what gets searched, only how the
   fixed sequence of per-individual evaluations gets computed, so
   `n_jobs=1`'s behavior is provably identical to every prior milestone's
   own numbers rather than merely believed to be.

**Correctness, not just speed.** A parallelized search is worthless if it
silently changes the answer, so this was checked directly rather than
assumed from "each evaluation is independent": both a small synthetic
fixture (`test_nsga2_search_parallel_matches_sequential`, plus matching
parity tests for `nsga2_search_fair` and `nsga2_search_continuous`) and both
real validation networks confirm `n_jobs=1` and `n_jobs>1` return the
*identical* accept-set population from the identical seed. This holds
because each individual's fitness is a pure function of its own bitstring/
fraction-vector, computed on an independent `copy.deepcopy` of the base
network inside whichever worker process happens to run it — nothing shared,
nothing racy, so which worker executes a given individual doesn't change
its result, only the wall-clock.

**Result (`experiments/34_parallel_search_speedup.py`, both existing
validation networks, this machine's 16 CPUs):**

| network | requests | nominal solves | sequential | cache hits | `n_jobs=16` | speedup | result match |
|---|---:|---:|---:|---:|---:|---:|---|
| EULV | 40 | 1,600 | 93.9s | 81 (5.1%; 1,519 distinct) | 22.0s | **4.3x** | 245.4 kW both |
| ENWL (M3 feeder) | 100 | 900 | 108.2s | 0 (0%; 900 distinct) | 26.1s | **4.2x** | 433.6 kW both |

Both networks: `n_jobs=1` and `n_jobs=16` returned the identical
accept-set population from the identical seed — confirmed, not assumed.
The EULV in-run hit rate (5.1%) reconciles exactly with the 7.4%-of-1,640-
calls figure that motivated building the cache in the first place: that
earlier standalone measurement counted `nsga2_search`'s own post-search
re-evaluation pass (one `evaluate()` call per unique final individual) as
part of its total, and every one of those calls is *guaranteed* a cache hit
— a final-population individual was necessarily evaluated once already
during the search, since that is the only place pymoo calls `_evaluate` at
all. 40 guaranteed hits from that pass + 81 genuine cross-generation
repeats during the search itself = the original 121 (7.4% of 1,640). The
ENWL run's 0% is a genuine, unexplained-further finding, not a bug: this
particular 100-request problem, at this seed and this pop/gen budget,
simply never regenerated an already-tried genotype across its 30
generations — plausible on a much larger combinatorial space (100 genes vs.
EULV's 40) than on a single, fixed sample, but not independently verified
across other seeds here.

**Honest limit, measured directly, not assumed.** `AllocationProblem`'s
cache lives on the instance; `multiprocessing.Pool` sends each worker
process its own pickled *copy* of the whole problem (network, requests,
and an empty cache), so a cache hit in one worker is invisible to every
other worker and to the main process. The parallel runs' own cache-entry
counts confirm this directly: 40 entries on EULV and 30 on ENWL under
`n_jobs=16` — exactly `pop_size` in both cases, meaning only the *final*
re-evaluation pass (which runs in the main process, on the original
`problem` instance, once pymoo returns) ever populated that particular
cache; every evaluation that happened inside a worker during the search
itself left no trace in it. So caching and parallelization are each real,
each independently measured, but they do not compose into one bigger
number — running in parallel trades away most of the cache's own benefit
for the parallelism, and this experiment reports that trade-off rather
than only the (better-looking) side of each feature in isolation.

**Honest scope limits:**
- Warm-starting (`runpp`'s own initial-voltage-guess reuse across similar
  accept-sets, e.g. `init="results"` seeded from a nearby already-solved
  subset) is the one piece of ROADMAP.md's original three-part suggestion
  ("cache, warm-start, parallelize") still untouched here. Checked directly
  in M11, below — it turns out not to help on these networks.
- Speedup was measured at `n_jobs=16` (this machine's full core count) on
  problems with 40-100 requests/pop_size≤40; NSGA-II's own per-generation
  overhead (crossover, mutation, non-dominated sorting, process dispatch)
  doesn't parallelize, so the achievable speedup is capped below `n_jobs`x
  regardless of network size — consistent with what was measured (4.2-4.3x
  at 16 workers, not 16x), not a contradiction of Amdahl's law, but worth
  stating plainly rather than implying a proportional-to-`n_jobs` gain at
  every problem size.
- Not re-validated across M8's 121-feeder corpus, for the same reason M9's
  fairness search wasn't: this milestone's own two speed features are
  themselves the thing that would make a full-corpus NSGA-II-parallel sweep
  newly tractable, but running that sweep is a follow-up, not part of
  measuring whether the features work.
- `multiprocessing.Pool`'s default start method on Linux is `fork`, which
  Python itself warns is unsafe from a process that already has multiple
  threads (numpy/BLAS spin up worker threads on import, before any pool is
  created) — surfaced as a `DeprecationWarning` in the test suite, not
  hidden. No deadlock or incorrect result was observed in any run this
  milestone (parallel and sequential results matched exactly, every time),
  but that is "not observed yet," not a guarantee; a `spawn`-based pool
  would sidestep the warning at the cost of slower worker startup (each
  worker re-imports the process from scratch instead of inheriting memory)
  and was not judged necessary here.

**M10 "done when" bar (ROADMAP.md, this document): both caching and
parallelization measured (not assumed) on both validation networks, with
`n_jobs=1`/`n_jobs>1` correctness checked directly — met**, with the
cache/parallelization non-composition and the still-open warm-start item
reported as real, scoped limitations rather than smoothed into an
unqualified "solved."

## M11: warm-starting the AC solve — investigated, genuine negative result

**The gap.** M10 closed two of the three pieces of ROADMAP.md's original
constraint-oracle suggestion ("cache converged states, warm-start, and
parallelize the evaluation across workers"), leaving warm-starting —
seeding `runpp`'s Newton-Raphson solve with an already-converged nearby
voltage guess (`init="results"`) instead of the default flat 1.0 pu start —
as the one piece still untouched. `experiments/35_warm_start_probe.py`
checks it directly, the same way every other milestone's speed claim in
this document was checked rather than assumed.

**Three angles, not one.** A single ad hoc timing comparison isn't enough
to trust — an early, informal version of this check showed what looked
like a real ~2x "warm" speedup on EULV, which turned out to be a cold-
cache/measurement-ordering artifact (see "caught before being reported,"
below). The committed probe checks three things instead:

1. **Iteration count**, at growing candidate sizes (1, then roughly half,
   then a full realistic accept-set) on both validation networks — does a
   warm start actually let Newton-Raphson stop sooner?
2. **A near-neighbor case** — warm-starting a "child" candidate that
   differs from an already-solved "parent" by only 2 of 40 requests, which
   is what NSGA-II's own mutation/crossover actually produces every
   generation, rather than the more extreme "0 requests -> a full
   accept-set" case (1) checks.
3. **A rigorously ordering-controlled wall-clock A/B benchmark** (30 trials
   per network, which variant — warm or flat — runs first alternated every
   trial, so no systematic drift across the run can masquerade as a real
   effect).

**Result: no measurable benefit, on either network, by any of the three
measures.** Iteration count is identical (3) at every realistic candidate
size tested and in the near-neighbor case, on both EULV and the M3 ENWL
feeder — the only case that ever showed a difference (2 vs. 3 iterations)
was a single tiny request added to an otherwise almost-empty network, not
representative of an actual NSGA-II individual, which always carries dozens
of simultaneous requests. The wall-clock A/B, properly ordering-controlled:

```
eulv: warm mean=12.39ms  flat mean=13.11ms  speedup 1.06x
enwl: warm mean=32.91ms  flat mean=34.25ms  speedup 1.04x
```

1.04-1.06x is noise, not a real effect — compare M5's 55-86x (the convex
relaxation) or M10's own 4.2-4.3x (parallelization). **Caught before being
reported:** an earlier, non-alternating version of this same probe (all
"warm" trials run as one block, then all "flat" trials as a second block)
showed a spurious ~2x "warm" advantage on EULV. Re-running with the order
alternated per trial made the gap vanish entirely — a cold-cache/CPU-
scheduling ordering artifact from running one variant as an unbroken block,
not a real warm-start effect, caught by controlling for it directly rather
than published as a result.

**Diagnosis.** These LV distribution networks are short-lined and well-
conditioned (dense connectivity, generous per-unit impedances relative to
typical loading) — Newton-Raphson already converges in a near-fixed minimal
iteration count (2-3) regardless of starting-guess quality. `runpp`'s own
docstring frames `init="results"` as "useful to accelerate convergence in
iterative load flows like time series calculations" — a regime of *many
small, slowly-varying* steps from one solved state to a barely-different
next one. NSGA-II's search is the opposite: every generation evaluates
substantially different accept-sets (dozens of requests differ), not a
small perturbation of the last one, so the premise warm-starting needs
(the new problem is *close* to the last solved one) doesn't hold here even
before considering the network-conditioning question.

**No source change made.** `AllocationProblem.evaluate_sizes` still uses
pandapower's own default `init="auto"` (via `runpp_kwargs`, unchanged since
M0) — building a warm-start code path measured to do nothing would be
complexity for its own sake, not a fix. The result of this milestone is the
measurement and its documentation, not a diff to `src/allocation/`.

**Side finding, not acted on.** Profiling the probe's own candidate
construction showed something unrelated to warm-starting: building a
50-request candidate's ~50 `pp.create_load` calls in a Python loop cost
roughly as much wall time as the `runpp` solve itself (tens of
milliseconds each, on the ENWL feeder). That's a real, different
optimization target — batching load creation, e.g. via a bulk-insert API,
rather than anything about the AC solve — but it's outside what M11 was
scoped to check, so it's left here as a genuinely open idea rather than
built now.

**M11 "done when" bar (ROADMAP.md, this document): warm-starting measured,
not assumed, on both validation networks, at realistic candidate sizes —
met**, with a genuine negative result reported as found, matching this
document's treatment of M5's ENWL underperformance and M7's continuous-
NSGA-II regression: a checked, closed question, not a hidden one.

## M12: fairness on the convex-MILP and continuous-sizing search variants

**Why this one, of ROADMAP.md's three remaining open items.** After M11,
three items were still open: extending fairness to the convex/continuous
search variants; re-validating fairness/reinforcement across the full
121-feeder corpus; and validating on a non-radial/unbalanced network.
Rather than pick by instinct, this was checked against what is actually
live right now: DOE i2X — the funding body `docs/paper.md`'s Part II is
framed around — states its own mission as enabling "a simpler, faster, and
**fairer** interconnection," naming "fair procedures" and "managing
request queues" explicitly among its solution areas. FERC's own 2023
reform is framed as preventing "undue discrimination" via first-ready,
first-served processing, and — three weeks before this milestone started —
**FERC issued six show-cause orders (2026-06-18)** forcing every RTO to
justify or revise its interconnection *cost-allocation* fairness rules,
alongside a live Senate bill on the same topic. That is close to exactly
the intersection this engine already models (`queue_fairness_violations` +
`price_reinforcement`) — but until this milestone, only the slow
evolutionary search (M9's `nsga2_search_fair`) had any fairness awareness
at all. The fast, production-shaped route — M5's MILP, 55-86x faster than
NSGA-II — had none. The other two items are real but didn't show the same
live external pull: full-corpus validation is internal rigor with no
particular timing driver, and non-radial/unbalanced validation is a bigger,
riskier lift (a new solver, not an added objective) with no evidence it is
urgent this quarter specifically, even though FERC 2023/DOE i2X being
US-specific while every validated network is British remains a real,
separate credibility gap for later.

**Two new functions, not two flags.** Same discipline M7 and M9 already
established: `lindistflow_search_fair` and `nsga2_search_continuous_fair`
are new functions alongside `lindistflow_search` and
`nsga2_search_continuous`, not parameters bolted onto them, so M1-M11's
already-validated numbers on the functions they extend stay untouched.

**The MILP side: an exact linearization, not an approximation.**
`queue_fairness_violations` counts (earlier-queued-and-rejected,
later-queued-and-accepted) pairs — quadratic in the accept variables in
general, since each violation is an AND of two binaries (`accept_b AND NOT
accept_a`). Exactly linearizable here because the accept variables are
binary: for every queue-order pair `(a, b)` with `a` earlier than `b`, one
new continuous variable `z_ab ∈ [0, 1]` with a single constraint `z_ab >=
accept_b - accept_a`. `z_ab` appears in the objective nowhere, only in the
aggregate cap `sum z_ab <= max_violations` — so the solver has no
incentive to ever set a `z_ab` above the true violation indicator, and at
the optimum `z_ab == max(0, accept_b - accept_a)` exactly, for binary
accept values. That equivalence needs binary accept variables, so
`max_violations` raises `NotImplementedError` if combined with
`continuous=True` (`_solve`'s own docstring), the same orthogonal-scope-
limit precedent M5/M7 already set for combining independent extensions
that were never validated together.

Since the MILP's own objective is already single (maximize accommodated
MW), not multi-objective like NSGA-II's, `lindistflow_search_fair` doesn't
trace a front the way one evolutionary run does — it solves once per
`max_violations` threshold (an epsilon-constraint sweep), each solve exact
for that fixed threshold rather than an evolutionary approximation.

**Result, both validation networks, both fully feasible fronts:**

```
eulv (40 requests):  0 violations: 230.7 kW   ...   57 (unconstrained): 243.4 kW
                      8 thresholds solved in 3.1s total (vs. lindistflow_search alone: 0.32s)
enwl (100 requests):  0 violations: 424.8 kW   ...   1,183 (unconstrained): 426.1 kW
                      8 thresholds solved in 49.3s total
```

Demanding zero queue-jumps costs 12.7 kW on EULV but only ~1.3 kW on
ENWL — consistent with M9's own finding that the thermal-limited ENWL
feeder's free-search ceiling already leaves little on the table for
fairness to trade against, now confirmed on the fast route too, not just
the slow one. Every threshold's chosen accept-set respected its cap
exactly, checked directly (not assumed) at every value tested on both
networks — the linearization is correct, not just plausible. A small,
honestly-reported wobble: two adjacent thresholds occasionally return a
very slightly *lower* accommodated MW at a *higher* cap (ENWL's
`max_violations=20` landed 0.1 kW below `max_violations=10`'s) — consistent
with `_solve`'s 0.1% relative MIP gap tolerance producing a slightly
different near-optimal point at each threshold, not a linearization bug
(the cap itself was still respected in both cases, and the two values are
within noise of each other).

**The continuous side: a real check, an inherited limitation.**
`nsga2_search_continuous_fair` found only 1-2 feasible individuals in the
same population/generation budget M1/M3/M9 used, on both networks, all at
0 violations — exactly matching `nsga2_search_continuous`'s own best-found
result (231.1 kW EULV, 432.6 kW ENWL). Reported honestly as inherited from
M7's own already-documented finding (continuous NSGA-II underperforms its
own binary counterpart at this budget, converging to only a couple of
distinct feasible points at all), not as a fresh "fairness is free for
continuous sizing" discovery — the search isn't finding a real trade-off
to report either way at this budget, on either network.

**Caught while measuring, not shipped as a result.** The experiment
script's first run reported `nsga2_search_continuous_fair` finishing in
under a second on EULV — looked like a remarkable free win. It wasn't: M10's
per-instance cache had carried over from a `nsga2_search_continuous` call
immediately before it on the *same* `AllocationProblem` instance. pymoo's
seeded `FloatRandomSampling` generates generation 0's population from the
seed and operators alone, independent of the objective function — so
generation 0's genotypes (and, empirically, much of the run beyond it) were
exact repeats of accept-fraction vectors the prior call had already cached.
Fixed by giving each search its own fresh `AllocationProblem` instance
before reporting a number; the real, standalone cost is ~113s on EULV,
~129s on ENWL, matching the plain continuous search's own cost closely, as
expected (`queue_fairness_violations` adds no `runpp` cost, same as M9's
binary finding). Not a bug in the new code — a genuine, interesting
consequence of M10's cache design surfacing in a place it hadn't been
exercised before, caught by measuring instead of assuming the reported
number was real.

**Honest scope limits:**
- The MILP linearization needs binary accept variables — it does not
  extend to `lindistflow_search`'s `continuous=True` mode or to
  `lindistflow_search_iterative`'s loss-corrected loop; combining any of
  these orthogonal extensions is a real but unvalidated next step, the same
  pattern M5/M7 already left open for their own combinations.
- The continuous-sizing result is bottlenecked by M7's own already-known
  NSGA-II convergence gap at this budget, not newly diagnosed here — a
  larger population/generation budget might resolve a genuine front, the
  same open question M7 itself left unresolved.
- Not re-validated across M8's 121-feeder corpus — one of the two other
  items this milestone was chosen over, still open.

**M12 "done when" bar (ROADMAP.md, this document): both variants measured
(not assumed) on both validation networks — the MILP's linearization
checked to respect its cap exactly, the continuous variant checked for a
real (or genuinely absent) trade-off — met**, with the fast MILP route now
carrying the same fairness awareness the funding landscape's own current
language names as a priority, and the continuous-sizing result reported as
an inherited limitation rather than oversold as a new finding.

## M13: non-radial (meshed) network validation

**Two axes, deliberately not attacked together.** ROADMAP.md's open item (2)
after M12 was "validate on a non-radial or unbalanced network." Reading the
actual code before scoping this made clear these are two independent
things with very different costs, not one item: (a) *non-radial* — does the
oracle/search require a tree? `AllocationProblem.evaluate_sizes` calls
`pandapower.runpp`, a general Newton-Raphson AC solver with no radial
assumption anywhere in it or in `nsga2_search`/`_fair`/`_continuous`; only
`allocation.convex`'s MILP route (`_extract_tree`) is architecturally
radial-only. (b) *unbalanced* — `evaluate_sizes` reads `net.res_bus.vm_pu`/
`net.res_line.loading_percent`, tables that don't exist after
`pandapower.runpp_3ph` (which produces `res_bus_3ph` with per-phase
columns instead); this needs a genuinely new oracle variant, not a
topology check. Axis (a) is a cheap validation of an already-implicit
claim; axis (b) is a real new-code milestone. Checked with the user which
to attack first rather than assuming: non-radial, since it's the cheaper
check and turns an untested assumption into a verified one before
committing to the larger unbalanced-oracle build.

**First attempt, redirected after checking, not assuming, that it would
work.** The literature-standard non-radial test case is the IEEE 123-bus
feeder (5 tie switches, the canonical distribution-automation reconfiguration
benchmark). Vendored it to `data/opendss/ieee123/` from the same
dss-extensions mirror EULV's own test case (`data/opendss/eulv/`) already
came from — `IEEE123Master.dss` (the stock radial case, byte-identical to
the published test feeder, tie switches Sw7/Sw8 left open as published) plus
a separate `IEEE123Master_looped.dss` (`Redirect`s to the stock master, then
closes Sw7 — a new file, not an edit, so the stock case stays untouched).
Importing it worked cleanly (132 buses, 118 lines, 8 switches, 91 loads, via
the existing `import_opendss` unmodified — its switch-import path
(`opendss.py:229-233`) already turns `Line.IsSwitch()` elements into
pandapower bus-bus switches with open/closed state preserved, so closing a
tie switch needed zero importer work). But `pandapower.runpp` on the *stock
radial* import didn't converge (`LoadflowNotConverged` after 10 iterations)
— before any switch was ever touched. That's axis (b)'s problem (the
balanced positive-sequence approximation breaking down on IEEE123's more
severe single-phase-load imbalance, the same class of issue Part I,
Section 7 already named for split-phase US feeders), not axis (a)'s.
Pursuing it further here would have silently conflated the two axes this
milestone was scoped to keep separate. Left the vendored files in place
(52 KB total) rather than deleting them — real, public-domain reference
data, a head start for the unbalanced-axis milestone whenever it's picked
up, not dead weight.

**Fallback: a controlled, synthetic loop on a network already known to
work.** EULV — the exact 907-bus feeder M0/M1 first validated on, already
confirmed to import and solve cleanly — plus one added tie line joining the
two leaves furthest apart (165 hops) in its radial tree, using the
lowest-impedance conductor type already present in the feeder (r=0.089,
x=0.0675 Ω/km) over a 50 m span, the kind of interconnect a real LV
reconfiguration switch would create. `networkx` confirms the result is
non-radial (907 buses, 907 edges, exactly one cycle, still connected) and
`runpp` still converges on the base case. Isolates the one variable
(tree vs. not) that changed, since everything else — buses, requests, seed,
search budget — is identical to experiments 24/25/33's already-published
radial numbers.

**Result, part 1 — a real, unplanned finding.** M1's own request band
(3-10 kWp, seed 0) turned out fully non-binding on the meshed network:
greedy baseline accepted 40/40 (266.1 kW) versus M1's radial 35/40
(236.8 kW). The tie line's redundant path lowers the effective impedance
seen by the previously-stressed leaf enough that the constraint which made
M1's baseline suboptimal in the first place stops binding — a genuine,
explicable topology consequence of adding a loop, reported as found rather
than treated as an inconvenient result to route around.

**Result, part 2 — restressed to get a real comparison.** Widened the band
to 3-14 kWp (same seed, same 40 requests, same bus sample) to restore a
binding regime — 35/40 baseline acceptance, matching M1's own 35/40 almost
exactly, so the comparison is meaningful again:

```
greedy baseline:        35/40 accepted, 312.2 kW, vmax=1.1000 pu
nsga2_search:            38/40 accepted, 336.3 kW, vmax=1.0994 pu  (+7.7% over baseline)
nsga2_search_fair:       10/40 feasible individuals, best carries 56 violations (no zero-violation point found)
lindistflow_search:      raises ValueError -- "network is not a single radial tree ..."
```

`nsga2_search` beats the baseline on the meshed network by a larger margin
(+7.7%) than M1's own radial result (+3.6%) — the search-beats-baseline
claim replicates on non-radial topology, not just a coincidence of the
original network's shape. `nsga2_search_fair` also runs mechanically
unmodified and returns feasible individuals, but at the same
population/generation budget M1/M9 used it didn't reach a zero-violation
point on this harder, more-constrained instance — a budget limit, the same
pattern M9 already documented on the larger ENWL feeder (a sparser front at
fixed compute), not a new defect introduced by meshing. `lindistflow_search`
on the identical problem raised the documented `ValueError` with the exact
expected message — the radial-only guard checked to actually fire on a real
907-bus feeder, not only on `test_non_radial_network_is_rejected`'s
synthetic two-bus fixture.

**No source changes to `allocation/`.** Every check in this milestone ran
through already-existing, already-validated functions unmodified — the
deliverable is the validation itself, the same "checked, not built" shape
M11 had. See `experiments/37_nonradial_validation.py`.

**Honest scope limits:**
- Still balanced (positive-sequence) throughout — the unbalanced axis
  remains fully open, and IEEE123's own convergence failure under the
  balanced approximation is a concrete data point for scoping that
  milestone's difficulty, not a solved sub-problem.
- The MILP/convex route still has no non-radial support at all — extending
  it would need a genuinely different relaxation (e.g. a branch-flow-model
  SOCP relaxation, exact for radial and conic-exact-but-angle-inexact for
  mesh per Farivar & Low 2013), a real research lift, not attempted here.
  NSGA-II remains the only route validated on non-radial networks.
- One network, one synthetic loop, one seed — not a systematic sweep of
  loop count/placement/impedance the way M8 swept 121 real feeders.

**M13 "done when" bar (ROADMAP.md, this document): `nsga2_search`/
`nsga2_search_fair` checked, not assumed, to still function correctly on a
real non-radial network, and `lindistflow_search` checked, not assumed, to
cleanly reject one — met**, closing the cheaper of the two axes ROADMAP.md's
open item (2) bundled together, with the unbalanced axis and the MILP's
mesh-relaxation gap both left explicitly open rather than silently
implied to be solved by this milestone.

## M14: unbalanced-network validation

**The other of M13's two axes — scoped to what's actually achievable,
after three things were checked, not assumed.** M13 left this open:
`evaluate_sizes` reads `net.res_bus.vm_pu`/`net.res_line.loading_percent`,
tables that don't exist after `pandapower.runpp_3ph` (which produces
`res_bus_3ph`/`res_line_3ph` with per-phase columns instead), so this
needs a genuinely new oracle variant. Reading further before writing any
code surfaced three real, separate findings:

1. **`import_opendss`-imported networks can't just be pointed at
   `runpp_3ph`.** Its `ext_grid` lacks the zero/negative-sequence fields
   (`s_sc_max_mva`/`s_sc_min_mva`/`rx_max`/`rx_min`/`r0x0_max`/`x0x_max`/
   `r0x0_min`/`x0x_min`) `runpp_3ph` requires, and every line lacks
   `r0_ohm_per_km`/`x0_ohm_per_km`/`c0_nf_per_km` — the importer only ever
   extracts positive-sequence `R1/X1/C1` (its own module docstring already
   says why: positive-sequence is "sufficient for the conductor-inference
   research this toolkit is built around"). Confirmed by trying it on
   `data/opendss/ieee13/IEEE13Nodeckt.dss`: `runpp_3ph` fails immediately
   with `KeyError: 's_sc_max_mva'`, then `KeyError: 'x0x_max'`, then
   `KeyError: 'r0_ohm_per_km'` once the ext_grid fields are patched in —
   the importer's positive-sequence-only design is a hard wall for this
   path, not a small gap.
2. **The deeper blocker: `runpp_3ph` structurally cannot represent the
   topology IEEE13/123/SMART-DS actually have.** `import_opendss`'s own
   module docstring already names this — "`runpp_3ph` is a sequence-frame
   solver that cannot represent truly unsymmetrical North-American
   topology (single-phase laterals, center-tapped split-phase) —
   pandapower issue #873" — but M13's scoping hadn't yet weighed how much
   that matters for *this* axis specifically. Checked the issue directly:
   it's open, describes the symmetrical-component method as unable "to
   model 1/2 phase load/transformer elements" at all, and pandapower's own
   proposed fix is a from-scratch phase-domain Newton-Raphson solver, not
   a patch — no workaround exists. IEEE13 alone has a dozen single-phase
   laterals; IEEE123 has more. Populating the missing zero-sequence data
   (point 1) would make `runpp_3ph` *run* on these feeders, but the result
   would be methodologically wrong at every single-phase-lateral bus, not
   just approximate — publishing that would be worse than not attempting
   it. This reclassifies true North-American topology from "M14's
   remaining work" to "blocked on an upstream tooling gap," a sharper and
   more defensible statement than M13's original framing.
3. **`runpp_3ph` fails silently, unlike `runpp`.** Empirically overloading
   a probe network (`pp.create_load(net, p_mw=5.0, ...)` on a small LV
   feeder) confirmed `runpp_3ph` does not raise `LoadflowNotConverged` —
   it leaves `net['converged']` `True` and returns an empty, all-NaN
   `res_bus_3ph`. Traced the consequence directly: Python's `max(0.0,
   float('nan'))` evaluates to `0.0` (the NaN comparison is simply false,
   so `max` keeps its first argument), so naively reusing
   `evaluate_sizes`'s violation-margin arithmetic unmodified would report
   a diverged, physically-meaningless solve as *feasible* — a genuine
   silent-corruption risk, caught by testing before it was ever shipped.

**Scoped to the one sub-case these three findings leave achievable now:
unbalanced *loading* on a genuinely symmetric 3-phase topology** — every
bus/line a full 3-phase node throughout (no single-phase laterals), but
loads/generation distributed unevenly across the three phases. This is a
real, common case (residential single-phase PV/EV inverters tapped onto
one phase of an otherwise-3-phase LV trunk, common in Europe as much as
the US), and `runpp_3ph` *can* represent it correctly. Network:
`pandapower.networks.ieee_european_lv_asymmetric()` — the same 907-bus,
55-house topology this repo's own EULV network mirrors, but pandapower's
native asymmetric build, which already carries every zero-sequence field
`runpp_3ph` needs and was already proven compatible with it in Part I
(experiments 01/03/04/05).

**Two new methods plus two new functions, the same discipline every prior
milestone since M7 established.** `AllocationProblem.evaluate_sizes_3ph`/
`evaluate_3ph` mirror `evaluate_sizes`/`evaluate` almost line for line,
but call `pp.runpp_3ph` and fold `res_bus_3ph`/`res_line_3ph`/
`res_trafo_3ph`'s three phase columns into the exact same scalar
`AllocationResult` shape (min/max across phases for `vmin_pu`/`vmax_pu`,
matching what those fields already mean) — no new result fields needed.
They guard explicitly against finding 2's silent-NaN failure mode,
checked directly via
`test_severely_overloaded_request_is_reported_infeasible_not_nan_feasible`.
They use a *separate* cache (`_cache_3ph`) from `evaluate_sizes`'s
`_cache` — the same (id, fraction) genotype means something physically
different solved via `runpp` vs. `runpp_3ph`, so sharing one cache would
silently return a balanced result for an unbalanced query or vice versa.
`Request` gained an optional `phase: str | None = None` field — `"a"`/
`"b"`/`"c"` for a single-phase-tap request (`pp.create_asymmetric_load`),
`None` (the default, every existing call site) for a balanced 3-phase
attach (`pp.create_load`, which `runpp_3ph` itself splits evenly across
phases — confirmed directly, not assumed). `greedy_baseline_3ph` and
`nsga2_search_3ph` are the matching baseline/search analogues, calling
`evaluate_3ph` instead of `evaluate`; the convex/MILP route was never in
scope here (radial-only regardless, and this network's topology question
is orthogonal to M13's).

**Result 1 — a concrete demonstration, not just a mechanical run.** The
same 75 kWp of export (15 requests × 5 kWp), spread evenly across all
three phases, is feasible: `vmax=1.0885` pu. The identical total,
concentrated on phase C alone (already the network's tightest phase in the
unloaded base case — `1.0495`-`1.0680` pu vs. phase B's `0.9962`-`1.0500`
pu), is infeasible: `vmax=1.1725` pu. Critically, evaluating that *exact*
concentrated accept-set through the balanced, positive-sequence
`evaluate()` (which ignores `phase` entirely) reports it feasible:
`vmax=1.0559` pu — the balanced oracle would accept a request set the
unbalanced one correctly rejects. This is the concrete "why this axis
matters" finding, not an abstract one: a joint-allocation search running
on the balanced oracle alone would approve a combination that actually
violates a real per-phase voltage limit.

**Result 2 — the search-beats-baseline bar, replicated on the new
oracle.** On 40 single-phase-tap candidate requests (random phase, same
3-10 kWp band and pop=40/gen=40/seed=0 budget as M1/M13's eulv runs):

```
greedy_baseline_3ph:  19/40 accepted, 121.5 kW, vmax=1.0852 pu
nsga2_search_3ph:      33/40 accepted, 227.2 kW, vmax=1.0990 pu  (+87% over baseline)
```

A much larger margin than M1's own +3.6% or M13's +7.7% — plausibly
because a single early-queued, single-phase-concentrated request can block
many differently-phased requests behind it in the serial queue that would
have packed together fine, giving a joint search more room to reorder
around than the balanced axis's single shared voltage/thermal constraint
does. Only 2 of the final population's 40 individuals were feasible (a
tight budget on this harder instance, the same pattern M9/M13 already
found elsewhere), but the best one found is unambiguous.

**Honest scope limits:**
- True North-American topology (single-phase laterals, split-phase
  secondaries — IEEE13/123, SMART-DS) remains fully open, and is now known
  to be blocked on pandapower issue #873, not merely unbuilt. Closing it
  for real would need a different power-flow engine entirely (a
  phase-domain solver, or driving OpenDSS's own engine directly instead of
  pandapower) — a genuinely separate, larger undertaking than this
  milestone.
- The MILP/convex route has no unbalanced support at all, and this
  milestone didn't attempt one — `lindistflow_search`'s LinDistFlow
  formulation is a single-phase (positive-sequence) relaxation throughout;
  a three-phase version is a further, separate lift.
- One network, one seed, one request band — not a systematic sweep the way
  M8 swept 121 real feeders, and no fairness/continuous-sizing extension
  attempted on this axis (M9/M7's own analogues would each be a further
  "new function" step, not built here).
- Data vendored for the non-radial axis (`data/opendss/ieee123/`) is a
  concrete illustration of finding 1 and 2 together (its balanced import
  didn't converge even before considering finding 2's deeper topology
  block) but was not itself made to work here — left as-is for whoever
  picks up the North-American-topology axis next.

**M14 "done when" bar (ROADMAP.md, this document): a concrete demonstration
that unbalanced loading changes the feasibility answer (not just a
mechanical run), and `nsga2_search_3ph`/`greedy_baseline_3ph` checked, not
assumed, to replicate M1's search-beats-baseline bar on the new oracle —
met**, with the true North-American-topology sub-case reclassified from
"open" to "open, and now known why" — a sharper, more defensible statement
of the US-market credibility gap `docs/paper.md`'s Section 7 already
named.

## M15: OpenDSS-direct oracle — sidestepping pandapower issue #873 for real

**The question, and why "fix it" was the wrong frame.** Prompted directly:
"can you fix #873 for our use case?" The honest answer is no, not by
patching pandapower: the maintainers' own framing of the issue is that its
symmetrical-component method fundamentally cannot model 1/2-phase
elements, and their proposed real fix is a Newton-Raphson solver in phase
(a/b/c) representation instead of symmetrical components — a from-scratch
solver rewrite, essentially duplicating what OpenDSS's own engine already
does. Attempting that here would mean re-deriving and re-validating a
power-flow engine this project already depends on and trusts (`gridsynth`
already uses `opendssdirect` for every import). The right-sized answer,
given "for our use case" specifically: don't fix pandapower, sidestep it —
drive OpenDSS's own solver directly for this axis instead.

**The state-isolation problem, solved by checking the API rather than
assuming it exists.** `AllocationProblem`'s whole design rests on
`copy.deepcopy(net)` per evaluation — cheap, since a pandapower net is a
plain Python/pandas object. An OpenDSS circuit accessed through
`opendssdirect` is not: it's a stateful, mutable engine instance. Checked
directly: `opendssdirect.NewContext()` creates an isolated DSS engine
context ("(API Extension)" per its own docstring) — exactly the primitive
needed. Measured, not assumed, before committing to the per-evaluation-
fresh-context design: redirecting a master file and solving from a fresh
context costs ~20-27 ms on both IEEE13 and IEEE123 — *faster* than
`AllocationProblem.evaluate` (~150 ms) or `evaluate_3ph` (~260 ms), since
OpenDSS's own compiled engine solves the master file directly rather than
through pandapower's Python/pandas data model. Every `AllocationProblemDSS.
evaluate_sizes` call redirects fresh, exactly mirroring `evaluate_sizes`'s
"start from the known-good base state every time" discipline.

**New module, new request type, matching M7/M9/M12/M13/M14's "new
function, not a flag" discipline.** `src/allocation/dss_problem.py`:
`DSSRequest` (``bus: str`` — an OpenDSS bus name, not a pandapower bus
index; ``phase: int`` selects a single line-to-neutral wye-connected phase,
matching every vendored IEEE13/123 master file's own convention for its
single-phase loads) and `AllocationProblemDSS` (`evaluate`/`evaluate_sizes`,
mirroring `AllocationProblem`'s method names and returning the *same*
`AllocationResult` dataclass — no new result fields needed, matching M14's
own precedent of reusing the scalar shape). `greedy_baseline_dss` and
`nsga2_search_dss` are the matching baseline/search analogues.

**Two real bugs caught while building the loading-percent calculation, not
shipped.** First: IEEE13's own base case (zero candidate requests) came
back `feasible=False` — investigated rather than dismissed, and traced to
`max_line_loading_percent=147.9%` and `max_trafo_loading_percent=2263.6%`
on the *unmodified* published test feeder. The trafo number was the real
bug: IEEE13's `Sub` transformer (`wdg=1 bus=SourceBus kv=115 kva=5000`,
`wdg=2 bus=650 kv=4.16 kva=5000`) has two windings at very different
voltage levels, so comparing its raw terminal current against a single
`NormalAmps()` value compares apples to oranges — the 4.16 kV winding
naturally carries ~27x the current of the 115 kV winding for the *same*
real power flow, and `NormalAmps()` reports only one winding's rating.
Hand-derived the expected value to check: terminal power at `Sub` is
~3567 kW / 1736 kvar, `|S|` ≈ 3967 kVA against the declared 5000 kVA
rating ≈ 79.3% — nowhere near 2263%. Fixed by switching transformer
loading to apparent-power-vs-rated-kVA (`Transformers.kVA()` vs. `Powers()`
summed per terminal across conductors), which is voltage-independent and
matches what pandapower's own `res_trafo.loading_percent` measures;
line loading stays current-vs-`NormAmps` (correct there, since both ends
of a line are always the same voltage — verified `CktElement.NormalAmps()`
and `Lines.NormAmps()` agree exactly on every IEEE13 line before relying on
the shared helper). The 147.9% line number turned out to be real, not a
bug — but not meaningful either: IEEE13's vendored master file declares no
line ampacities at all, so every conductor silently gets OpenDSS's uniform
400A default regardless of actual conductor size. Scoped this validation
to the voltage axis instead (`max_loading_percent` relaxed to a no-op),
the same choice M0 made for EULV when thermal wasn't the network's real
constraint.

**Result: both literature-standard single-phase-lateral feeders solve
correctly, including the one that couldn't before.** IEEE123's own base
case: `converged=True feasible=True vmin=0.9792 vmax=1.0500` — the exact
network Section 4.13/M13 found `pandapower.runpp` couldn't even converge
on in its stock radial form. On 38 single-phase-tap EV/demand requests
(a realistic single-phase-tap magnitude band, 50-150 kW, needed to
actually stress this feeder's own quite-stiff `MVAsc3=20000` substation
source):

```
IEEE13  (38 requests): greedy_baseline_dss  13/38 accepted, 1321.1 kW
                        nsga2_search_dss     37/38 accepted, 3862.9 kW  (+192.4%)
IEEE123 (60 requests): greedy_baseline_dss  44/60 accepted, 3851.9 kW
                        nsga2_search_dss     53/60 accepted, 5134.9 kW  (+33.3%)
```

+192.4% on IEEE13 is the largest search-beats-baseline margin measured
anywhere in this project (M1: +3.6%, M13: +7.7%, M14: +87%) — plausibly
because a single early-queued, badly-placed single-phase-tap request can
single-handedly break the voltage floor and block every differently-placed
request behind it in a serial queue, exactly the joint-packing effect a
search reorders around; a smaller (but still large) margin on IEEE123,
where only 2 of the final population's 40 individuals were feasible on
this bigger, harder instance — a tight budget on a harder problem, the
same pattern M9/M13 already found elsewhere, not a new defect.

**Honest scope limits:**
- The convex/MILP route is untouched and out of scope here by
  construction — `lindistflow_search` operates on `AllocationProblem`, not
  `AllocationProblemDSS`; a MILP formulation over OpenDSS-solved single-
  phase-lateral topology would need its own relaxation (three-phase
  branch-flow, not LinDistFlow), a further, separate lift.
- `nsga2_search_dss` parallelizes the same way `nsga2_search` does
  (`n_jobs`, `StarmapParallelization`) since `AllocationProblemDSS` holds
  only picklable state (`master_path`, `requests`) — no live DSS context
  stored on the object — but this wasn't separately re-measured for
  speedup the way M10 measured the pandapower-based route.
- One seed, one request band per network — not a systematic sweep the way
  M8 swept 121 real feeders.
- SMART-DS (the largest, richest US corpus already vendored elsewhere in
  this repo) wasn't attempted here — IEEE13/123 were enough to validate the
  approach; a SMART-DS run is a natural, low-risk follow-up now that the
  oracle itself is built and checked.

**M15 "done when" bar (ROADMAP.md, this document): IEEE13 and IEEE123
checked, not assumed, to solve correctly via the new oracle, and
`nsga2_search_dss`/`greedy_baseline_dss` checked to replicate M1's
search-beats-baseline bar on real single-phase-lateral topology — met**,
closing the credibility gap `docs/paper.md`'s Section 7 named since M13:
every network validated in this project no longer has to be British.

## M16: SMART-DS through the OpenDSS-direct oracle

**Why this, next.** After M15, `docs/paper.md` Section 7 named four
concretely scoped open items and asked which was most externally relevant
before picking one to build (the same discipline M12 already used to choose
its own next step). Running SMART-DS — this repo's richest open US feeder
corpus, already vendored via experiment 09 but never run through
`AllocationProblemDSS` — through the new oracle was the cheapest, most
direct extension of what M15 had just built, with no new design questions;
the other three (a convex/MILP route for single-phase-lateral topology, a
larger-budget continuous-fairness re-check, and re-running the fairness/cost
validations at M8's full-corpus scale) were each either open-ended or a pure
scale-up, not a new capability. User confirmed this choice directly.

**Screening, not assuming, which feeder to use — the same discipline M3
already used for ENWL.** SMART-DS's own "no_loadshapes" snapshot is a fixed
single operating point, not a scaled planning case. Experiment 09's own
cached feeder (`rhs0_1247--rdt1527`, GSO/rural, 3,294 buses) already reads
vmin=0.877 pu at its own existing load, before any new request — the exact
baseline-infeasibility trap M2/M3 hit with EPRI's Ckt5, and M3's own
docstring had already flagged this same SMART-DS feeder as unusable for
that reason. Rather than assume every SMART-DS feeder shares that fate,
screened 7 more directly through raw `opendssdirect` (`Solve()` + a
`puVmagAngle()` scan — no pandapower import needed, cheap enough to check 8
feeders in under a minute): 3 more GSO/rural feeders (same hub and two
neighbors) and 4 GSO/urban-suburban feeders (two different hubs).

```
rhs0_1247--rdt1527 (rural, cached)   3294 buses  vmin=0.8777  INFEASIBLE
rhs0_1247--rdt1534 (rural)           2150 buses  vmin=0.8304  INFEASIBLE
rhs0_1247--rdt1948 (rural)           2735 buses  vmin=0.8772  INFEASIBLE
uhs0_1247--udt12274 (urban-suburban) 3851 buses  vmin=0.9252  INFEASIBLE
uhs0_1247--udt16115 (urban-suburban)  722 buses  vmin=0.9344  INFEASIBLE
uhs1_1247--udt20176 (urban-suburban) 5014 buses  vmin=0.9332  INFEASIBLE
uhs1_1247--udt15926 (urban-suburban) 2758 buses  vmin=0.9620  feasible
uhs0_1247--udt14717 (urban-suburban) 4591 buses  vmin=0.9439  feasible (chosen)
```

A real, reportable pattern falls out of the screen, not just a pass/fail
list: every rural feeder tried is baseline-infeasible (long, sparsely
regulated laterals — vmin 0.83-0.88 pu, well under the 0.94 pu floor), while
urban-suburban feeders sit markedly closer to the floor and half of the
four tried clear it. Chose `uhs0_1247--udt14717`, the largest
baseline-feasible feeder found — 4,591 buses (close to, though slightly
under, the M3 ENWL feeder's 4,769-bus scale, and well above IEEE123's 123),
4,053 lines (81% single-/two-phase — the same order
as rdt1527's own ~80% figure from experiment 09, confirming this is
genuine North-American topology generally, not an artifact of the one
feeder already tried), 542 service transformers, 3,563 loads.

**A second, different baseline-infeasibility trap on the same feeder — the
thermal axis this time, found rather than assumed away.** Unlike IEEE13
(M15: no line ampacities declared at all, so thermal loading isn't a real
constraint as published), SMART-DS declares real per-conductor `normamps`
ratings — genuinely usable data. But applying them as-is still fails the
empty-subset baseline: 127 of 4,047 rated lines already exceed 100%
loading (up to 197.8%) serving the feeder's own existing load, before any
new request. Investigated rather than dismissed: this is localized, not
systemic (median line loading is 20%, only ~3% of rated lines are over
100%) — a real, if partial, reinforcement backlog on the feeder as
published, consistent with the pattern this project's M2 follow-up already
found on ENWL's own open capacity register (declared "headroom" not
matching what a power flow actually supports). The M15 transformer fix
(apparent-power-vs-rated-kVA, not raw current-vs-`NormalAmps`, since a
transformer's two windings sit at different voltage levels) was
re-verified correct on this feeder rather than assumed to still hold.
Scoped this validation to the voltage axis, the same choice M0 made for
EULV and M15 made for IEEE13 — but for a third, distinct reason each time:
EULV's thermal genuinely isn't binding; IEEE13's ratings aren't declared;
here, the published snapshot's *existing* load already exceeds a real
minority of its own real ratings.

**Result: the largest search-beats-baseline margin measured anywhere in
this project.** The chosen feeder clears the voltage floor by only 0.0039
pu (0.39%) at baseline — the thinnest margin of any network in this repo's
corpus, including EULV and both IEEE feeders. On 40 single-phase-tap
EV/demand requests (6-17 kW — the band needed to actually stress a margin
this thin without every request being individually infeasible, found by
sweeping bands the same way M15 tuned IEEE13/123's magnitudes):

```
greedy_baseline_dss:  2/40 accepted,  28.4 kW,  vmin=0.9439 pu
nsga2_search_dss:    36/40 accepted, 417.3 kW,  vmin=0.9445 pu   (+1367.0%)
```

Diagnosed, not just measured: request `ev2` (12.9 kW at bus `udm307`) is
individually infeasible alone (drops vmin to 0.9392, just under the floor)
— checked directly by evaluating it in isolation. Being third in queue
order, it permanently blocks every request behind it under
`greedy_baseline_dss`'s "stop at the first violation" rule, even though 36
of the other 37 are individually fine. `nsga2_search_dss` simply excludes
`ev2` (plus 3 others) and accepts the rest — the same "one badly-placed
early request blocks an otherwise-packable queue" failure mode M15 already
named for IEEE13 (+192.4%), now at its most extreme form because this
feeder's baseline margin is the thinnest measured anywhere in this project.
+1367.0% dwarfs every prior margin (M1: +3.6%, M13: +7.7%, M14: +87%,
M15/IEEE13: +192.4%) — reported as found and diagnosed, not as evidence the
method is 7x better here than on IEEE13; it is evidence this specific
feeder's serial process is 7x more fragile.

**A resource lesson, caught mid-run and corrected, not swept under the
rug.** An early exploratory run used `nsga2_search_dss`'s `n_jobs=16`
parallelization (M10) on this feeder's full pop=40/gen=40 budget — each of
16 worker processes holds an independent OpenDSS engine over a 4,591-bus
model, and the run pushed system memory to ~23 GB before being interrupted.
Corrected by dropping to `n_jobs=4` briefly (confirmed a smaller, bounded,
recoverable spike, ~12 GB above baseline) and then to the committed
script's actual choice: sequential (`n_jobs=1`), pop=20/gen=20 — ~80s on
this feeder, no parallel-process memory cost at all, and it reproduces the
identical accept-set the `n_jobs=4` run found (36/40, 417.3 kW), consistent
with M10's own n_jobs-independence guarantee. Unlike IEEE13/123 (small
enough that M15 never needed to think about this), a feeder this size makes
the memory-vs-speed tradeoff `n_jobs` represents a real, live choice rather
than a free win — recorded here so the next larger-network milestone
doesn't have to rediscover it.

**Honest scope limits:**
- No source changes were needed anywhere in `allocation/` — `AllocationProblemDSS`,
  `greedy_baseline_dss`, and `nsga2_search_dss` are exactly M15's code,
  unmodified; like M11/M13, this milestone is entirely a validation.
- One seed, one request band, one feeder — not a systematic multi-feeder
  sweep the way M8 swept 121 real ENWL feeders. SMART-DS ships many more
  hubs and feeders than the 8 screened here.
- The convex/MILP route remains untouched and out of scope, per M15's own
  note — `AllocationProblemDSS` still has no LinDistFlow-style counterpart.
- The +1367.0% headline is real but shouldn't be read as "SMART-DS is 7x
  more search-friendly than IEEE13" in general — it is a direct, diagnosed
  consequence of this one feeder's unusually thin 0.39% baseline voltage
  margin, not a claim about SMART-DS feeders broadly (the screen itself
  found feeders ranging from comfortably infeasible to this one's
  razor's-edge feasible).

**M16 "done when" bar (ROADMAP.md, this document): SMART-DS run through the
OpenDSS-direct oracle, checked (not assumed) for a baseline-feasible
feeder, and `nsga2_search_dss`/`greedy_baseline_dss` checked to replicate
M1's search-beats-baseline bar on it — met.** `docs/paper.md` Section 7's
lowest-risk-named open item after M15 is closed.

## M17: fairness and reinforcement-cost, re-checked at M8's full-corpus scale

**Why this, next.** M16's own "why this, next" section already named the
three items left after it; user asked to keep going without picking again,
so the same discipline M12/M16 used applies: pick the least speculative
item. A convex/MILP counterpart for `AllocationProblemDSS` and a
larger-budget continuous-fairness re-check are both open-ended design
questions. Re-running M6 (reinforcement cost) and M9 (fairness) across
M8's full 121-feeder corpus is a pure scale-up of two tools that already
exist, unmodified — the same shape of milestone M8 itself was for M1's
core claim, and M9's own "Honest scope limits" section had already named
the exact plan: "a full-corpus fairness sweep would face the same budget
problem M8 already solved for capacity alone by using the convex search
instead — doing the same for fairness is a clear, scoped follow-up, not
attempted here." M12 had, by the time of that follow-up, already built
exactly the tool M9 said this would need: `lindistflow_search_fair`, a
fairness-capped MILP that didn't exist yet when M9 was written.

**Reusing M8's exact corpus, seeds, and requests — not a fresh sample.**
`experiments/41_full_corpus_fairness_cost.py` copies M8's `discover_feeders`
and `build_requests` verbatim (same sort order, same `PV_KW_LO/HI = 3-10`,
same `feeder_seed = SEED + idx`), so every feeder here draws the identical
candidate-request set M8 already classified as voltage- or thermal-limited.
Recomputing baseline-feasibility and the binding classification directly
(cheap — one `runpp` per feeder) rather than trusting the year-old
`outputs/m8_multi_feeder.csv` reproduced it exactly: 121/128 feeders
baseline-feasible, 110 thermal-limited / 11 voltage-limited. This is a
different request sample from M6/M9's own original experiments, though —
those used per-network-tuned `k`/seed (`k=100` on the M3 ENWL feeder
specifically), not M8's `k=min(50, n_load)` scheme, so numbers on the one
feeder both this milestone and M6/M9 touch (`network_15/Feeder_4`) are not
expected to match, and don't.

**Tool choice, same reasoning as M8.** `nsga2_search_fair` (M9) pays one
`runpp` per individual per generation — not viable across 121 feeders in a
session, the same problem M8 solved for the plain search by using
`lindistflow_search_iterative` instead. `lindistflow_search_fair` (M12) is
the fairness-capped analogue, but single-shot only (no loss-corrected
iterative variant exists for it) — both the free (`lindistflow_search`) and
zero-violation (`lindistflow_search_fair(..., max_violations=0)`) calls
here carry the same known lossless-model bias M5/M8 already identified, but
*share* it, which keeps their difference — the kW cost of a strict
zero-queue-jump requirement — a fair internal comparison even where neither
absolute number is as tight as M8's own iterative-search figures.
`reinforced_greedy_baseline` (M6) needed no such caveat — a cheap greedy
walk, not NSGA-II-scale, run at full accuracy across the whole corpus.
`nsga2_search_fair` was spot-checked on 6 feeders spanning the size range,
the same role M8's own NSGA-II spot-check played for the plain search.

**Result 1: the reinforcement lever almost never matters, and binding
classification doesn't predict when it will.** M6's original 2-network
check (EULV voltage-limited/€0 gain, M3-ENWL thermal-limited/+87.7 kW for
€848) reads as a clean prediction: this catalogue is thermal-only, so it
should help thermal-limited feeders and do nothing for voltage-limited
ones. At full-corpus scale that prediction does not hold:

```
voltage-limited (11 feeders): avg reinforcement gain +1.4 kW, 2/11 gained anything
thermal-limited (110 feeders): avg reinforcement gain +0.0 kW, 1/110 gained anything
```

Only 3 of 121 feeders (2.5%) gain *anything* from the lever at all
(+11.9, +5.2, +3.2 kW — one of them, €400, is the only nonzero cost in the
whole corpus), and the tiny handful that do skew, if anything, toward the
voltage-limited group — the opposite direction of the 2-network prediction.
At counts this small (2 vs. 1 event) that is not read as a real inverse
relationship either; the honest conclusion is that a feeder's *aggregate*
binding constraint (M2/M3/M8's own classification, computed at the free
search's overall optimum) simply doesn't predict whether a thermal-only
reinforcement lever helps a serial baseline recover capacity, because that
depends on which constraint the *specific marginal rejected request*
happened to trip during the queue walk — a local, per-request fact the
global classification doesn't carry. The 2-network M6 result wasn't wrong
about either network; it just wasn't a general rule, and the full corpus
is what makes that visible.

**Result 2: fairness is nearly always free, at this corpus's scale.** M9's
EULV front found demanding zero queue-jumps cost 8.3 kW of a 245.4 kW
ceiling (3.4%) — a real, if modest, price. Across M8's 121-feeder corpus,
using the fast MILP fairness route:

```
121/121 feeders: both MILP sides (free, zero-violation) solved and were AC-feasible
114/121 (94%) paid nothing at all for zero queue-jumps
7/121 (6%) paid something: avg 19.0 kW, max 51.8 kW (network_5/Feeder_5)
```

Consistent with, and a stronger version of, what M9 already suspected from
just two data points (EULV: real but small cost; ENWL: search too
budget-starved to even find a zero-violation point) — the dominant behavior
across a real corpus is that first-come-first-served costs nothing, and the
minority of feeders where it does cost something is a genuinely rare,
worth-flagging case rather than the norm.

**Result 3: the NSGA-II spot-check mostly confirms the MILP route, and
surfaces a real budget-vs-network-size limit where it doesn't.** 4 of 6
spot-checked feeders matched the MILP's zero-violation answer exactly
(+0.0 kW gap). The other two are both honest limits of the *spot-check's*
own tool, not the MILP result (which stays AC-reverified regardless):
`network_24/Feeder_1` found no zero-violation feasible individual at all
within `nsga2_search_fair`'s population/generation budget (reported as NaN,
not silently dropped); `network_15/Feeder_4` — M3's own 4,769-bus feeder,
the largest network in this corpus — found a zero-violation point 56.6 kW
*below* the MILP's AC-reverified 310.9 kW. Both are the same "a fixed
NSGA-II budget resolves proportionally less of a larger search space"
pattern this project has now found repeatedly (M7's continuous sizing, M8's
own binding-constraint checks, M9's sparser ENWL front) — not a new
finding, but a fourth confirmation of it, and a direct, concrete instance
of the exact open question `docs/paper.md` Section 7 still lists (whether
continuous-sizing's convergence gap is budget-bounded or fundamental) — the
same tension, on a discrete objective this time.

**Honest scope limits:**
- No source changes were needed anywhere in `allocation/` — `lindistflow_search`,
  `lindistflow_search_fair`, `reinforced_greedy_baseline`, and
  `nsga2_search_fair` are exactly M6/M9/M12's code, unmodified; like
  M8/M11/M13/M16, this milestone is entirely a validation.
- The fairness comparison uses single-shot MILP on both sides (see "Tool
  choice" above) — internally consistent, but the absolute kW figures are
  not as tight as M8's own loss-corrected iterative-search numbers; a
  loss-corrected fairness-capped MILP does not exist yet.
- One request band, one seed scheme (M8's, not M6/M9's own) — the
  `network_15/Feeder_4` numbers here are not directly comparable to M6/M9's
  original figures on that same feeder, by construction (different `k`,
  different seed).
- The 3-feeder reinforcement-benefit and 7-feeder fairness-cost samples are
  both too small to characterize *why* those specific feeders differ from
  the rest of the corpus — flagged as an open, un-investigated pattern, not
  chased further here.

**M17 "done when" bar (ROADMAP.md, this document): M6 and M9 both re-run
across M8's full baseline-feasible corpus, using tools proportionate to
that scale the way M8 itself required, with whatever pattern is found
(confirming or complicating the original 2-network results) reported
plainly — met.** `docs/paper.md` Section 7's first still-open item after
M16 is closed; two remain (a convex/MILP route for `AllocationProblemDSS`,
and the continuous-sizing convergence-gap question this milestone's own
NSGA-II spot-check ran into again).

## M18: a convex/MILP route for `AllocationProblemDSS`

**Why this, next.** Of the two items Section 7 left open after M17, this
one is more concrete: `docs/paper.md` names it directly — "the convex/MILP
route has no counterpart for `AllocationProblemDSS` — LinDistFlow's radial
relaxation doesn't apply to OpenDSS-solved single-phase-lateral topology, so
the fast route stays NSGA-II-only there." The other open item (continuous-
sizing fairness at a larger search budget) is a pure scale-up of an existing
tool with no new design question; this one required an actual decision
about what's achievable, made explicit below before writing any code.

**Scoped down from the start, not discovered by hitting a wall.** An
*exact* multiphase relaxation — tracking the real complex mutual coupling
between phases via each line's own impedance matrix (a Gan & Low 2014-style
branch-flow SOCP) — is genuine research; Section 5 already named this scale
of lift as out of reach when scoping the meshed-network case ("a genuinely
different relaxation … a real research lift not attempted here"). What
*is* achievable in one milestone: drop the mutual-coupling terms and solve
one *decoupled* single-phase LinDistFlow per phase, sharing only the
request accept variables — a request taps exactly one phase
(`DSSRequest.phase`), so a phase's own tree is the only place its accept
variable's power appears. Scoped further to IEEE13 alone, not IEEE123 or
SMART-DS: IEEE123 has voltage regulators interspersed mid-feeder (not just
at the head, like IEEE13's `Reg1/2/3`) and two normally-open tie switches —
either would need real extension work to this milestone's `root_bus`-
rooted, no-open-switches extraction, not attempted here.

**Topology extraction needed building from scratch, not adapting
`convex.py`'s.** There is no pandapower `net` object on this oracle's side
to read tables from — everything comes from a solved `opendssdirect`
context instead. Checked directly rather than assumed from the API's own
docs, since a first guess was wrong: `Lines.RMatrix()/XMatrix()` return a
*per-unit-length rate*, not an already-length-scaled matrix — confirmed by
computing `rate * Lines.Length()` against a hand-derived value from IEEE13's
own line codes (off by exactly the declared length before that was caught).
Each element's own `Bus1`/`Bus2` node-suffix order (not sorted phase order —
e.g. `Line.632645`'s `Bus1=632.3.2` declares phase 3 before phase 2, and its
impedance matrix is indexed in that same order) maps matrix diagonal entries
to phase numbers.

**No `ext_grid` equivalent exists on this side, so `root_bus` is a required,
not auto-detected, parameter.** For IEEE13, `root_bus="650"` — confirmed,
not assumed, to sit within 0.02pu of 1.0 regardless of downstream loading
(checked at both the published base case and an all-loads-disabled solve),
thanks to the vendored master file's own deliberately-crushed substation-
transformer impedance and a stiffened `Vsource` upstream of it. IEEE13's
three single-phase head-end regulators (`Reg1/2/3`, 650→rg60) retap in
response to downstream loading, a control loop this linear model doesn't
represent; any 2-winding transformer whose two windings declare the same
rated kV is treated as a regulator, with its own (already two orders of
magnitude below IEEE13's smallest line impedance) impedance dropped and its
per-phase voltage ratio fixed at whatever the baseline (zero-request) AC
solve actually measured — frozen, not re-solved as requests are accepted,
the same "snapshot, not a control-loop simulation" approximation
`AllocationProblemDSS` itself already makes on every single `evaluate_sizes`
call. Existing per-bus injections are read from load/capacitor nameplate
data, with a delta- or multi-phase-wye load's declared kW/kvar split evenly
across however many phases its own bus spec names — an explicit
approximation for delta connections, not a delta-to-wye electrical
equivalent, mirroring `convex.py`'s own `net.load`/`net.sgen` table reads.

**A real, quantified failure mode, caught before it shipped, not smoothed
over.** The first working version of the model was infeasible even at
IEEE13's own true (AC-feasible) baseline, before any candidate request was
considered — not a bug in the equations, but the dropped mutual-coupling
term turning out to be large on this topology. Checked directly: on
IEEE13's head-end trunk line alone, recomputing the voltage-drop implied by
the *full* impedance matrix vs. the diagonal-only term this model uses,
against that line's actual AC-solved per-phase currents, differs by 3.4–4.5
percentage points of pu voltage from that one edge — real 3-phase mutual
coupling *partially cancels* a correlated multiphase current pattern in a
way a decoupled model can't see at all, and IEEE13's single-phase laterals
and delta loads are unbalanced enough that the cancellation is large.
Compounded down the rest of the feeder, the uncalibrated model's baseline
prediction landed 0.025 pu below the true AC baseline, enough to make the
search's own MILP infeasible outright, since the existing-load-only power
balance is a fixed equality the solver can't choose around.

**Fix: calibrate against the true baseline once, not the angle-exact
relaxation this would really need.** For every non-root bus, a per-node
additive correction to its own voltage-drop equation is solved directly
from the real AC baseline voltages (already read once during extraction,
at every bus, not re-derived) plus the relaxed model's own calibration-
independent baseline power flow. Had to be computed top-down, not as a flat
per-node "true minus this node's own uncalibrated value" difference — that
simpler version silently double-counts a node's parent's own already-
corrected gap into the node's target too, and was caught precisely because
the first version of the fix produced an obviously-wrong 1.24 pu predicted
maximum before the top-down derivation replaced it. This makes the model
agree with the true baseline *exactly* at zero accepted requests, by
construction; its accuracy for a nonzero accept-set rests on new requests
staying individually small next to IEEE13's existing multi-hundred-kW
loads, not proven in general, checked empirically per accept-set the same
way every other route in this note is — the chosen accept-set is always
AC-reverified through `AllocationProblemDSS.evaluate_sizes` regardless.

**Result 1: the calibration holds up under AC reverification, with a real
but modest and quantified drift.** `experiments/42_convex_dss_validation.py`
on 35 single-phase-tap candidate requests (same request band and `k` as
M15's own IEEE13 run):

```
== 1. baseline parity (zero candidate requests) ==
  relaxed : vmin=0.96084 vmax=1.05605
  actual  : vmin=0.96084 vmax=1.05605        (gap: 0.000000 pu, by construction)

== 2. accept-set parity (greedy baseline's own accept-set, 23/35 accepted) ==
  relaxed : vmin=0.93406 vmax=1.05605
  actual  : vmin=0.94464 vmax=1.07467        (gap: 0.0106 pu)

== 4. calibration drift at the MILP's own chosen accept-set (28/35 accepted) ==
  relaxed vmin=0.94108 vs. AC vmin=0.94302   (drift: -0.0019 pu)
```

Baseline parity is exact by construction, the accept-set-parity gap (1.1
percentage points of pu, at 23 real accepted requests) is the honest cost
of the decoupling approximation once real load is on the network, and —
reassuringly — the drift at the MILP's *own* chosen 28-request accept-set
is smaller still (0.19 percentage points), consistent with (not proof of)
the "individually small requests" assumption the calibration rests on.

**Result 2: the MILP beats the serial baseline and is dramatically faster
than the exact-AC search, at a real, honestly-quantified cost in
capacity.**

```
greedy_baseline_dss  :  2380.6 kW  (23/35 accepted)
lindistflow_search_dss:  2928.0 kW  (28/35 accepted, +23.0% over greedy, 0.03s, AC-feasible)
nsga2_search_dss      :  3569.6 kW  (34/35 accepted, 18.7s)
```

The MILP was ~725x faster than `nsga2_search_dss` at this scale, reaching
82.0% of its accommodated kW — a real, attributable shortfall (the
per-phase decoupling approximation, not a search-budget artifact: the
MILP's own optimum is exact given its model, unlike NSGA-II's population/
generation-bounded search) rather than an unexplained gap. This is the same
qualitative pattern Section 4.5 found for the balanced/pandapower case
(MILP fast but short of NSGA-II's exact-AC optimum), now replicated on
genuinely unbalanced single-phase-lateral topology with a different,
larger-magnitude, and now precisely diagnosed source of looseness.

**Honest scope limits:**
- IEEE13 only. IEEE123 and SMART-DS are not attempted — both have topology
  (mid-feeder regulators, open tie switches) this milestone's extraction
  deliberately doesn't handle; a genuinely non-radial *and* unbalanced
  network remains untested on any route, unchanged from Section 7's own
  framing of this gap.
- The decoupling approximation is real and was measured to matter, not
  assumed small — the calibration fix makes the search usable, not exact;
  it's a baseline-anchored correction, not the angle-exact multiphase
  relaxation the gap really calls for.
- Delta-connected loads are split evenly across their declared phases, an
  explicit approximation, not an electrical delta-to-wye equivalent —
  IEEE13 has three such loads (`671`, `646`, `692`).
- Every request must land on a phase the model actually reaches from
  `root_bus`, and the root bus itself can't host a request (its voltage is
  fixed, not a decision variable) — both checked, raising rather than
  silently mishandling, but real constraints on where this route can be
  used compared to `nsga2_search_dss`, which has neither restriction.

**M18 "done when" bar (ROADMAP.md, this document): a convex/MILP search for
`AllocationProblemDSS` exists, is always AC-reverified, beats the serial
baseline on real single-phase-lateral topology, and any modeling gap is
measured and reported rather than assumed away — met.** `docs/paper.md`
Section 7's first still-open item after M17 is closed for the radial case;
one remains open exactly as scoped (a network that is both unbalanced *and*
meshed, on any route), plus the continuous-sizing convergence-gap question.

## M19: a network that is genuinely both unbalanced and meshed

**Why this, next.** The user asked directly: "can you try this network that
is unbalanced and meshed?" — the one cell M18's own write-up left open
("a network that is both unbalanced *and* meshed remains untested on any
route"). Section 4.13/M13 checked non-radial alone (a synthetic loop added
to EULV, itself balanced); Sections 4.14/4.15/M15 checked unbalanced alone
(IEEE13/123, both radial as vendored). This milestone combines the two axes
for the first time.

**IEEE123 is the natural candidate for both at once, not a new network.**
It is already genuinely unbalanced (single-phase laterals, split-phase
secondaries — the exact topology `AllocationProblemDSS` exists for), and
the *published* test feeder itself specifies two normally-open tie switches
(Sw7, Sw8) for reconfiguration studies — closing one turns the same feeder
non-radial without changing anything else about it.
`data/opendss/ieee123/IEEE123Master_looped.dss` already existed, vendored
in M13 when IEEE123 was tried and abandoned for the *pandapower*-import
axis, before this file was ever actually solved by anything.

**A real, previously-uncaught bug was found in that file before it was
ever used.** The published master defines `Sw7` as `Bus1=151 Bus2=300_OPEN`
— `300_OPEN` is a *dummy* bus that exists only so a radial (all-ties-open)
study can still report what voltage the open tie point would see, without
actually being connected to the rest of the circuit. M13's version of the
looped file only closed the switch *contact*, which just energizes that
dummy bus as a dead-end stub off bus 151 — checked directly here, not
assumed correct from "it solves without error": bus 300 (fed from the
opposite direction via `L108`) and bus `300_OPEN` landed on visibly
different solved voltages (differing in the second decimal place), and an
independent `networkx` graph check (edges over solved-closed lines/
transformers only) found the "closed" network still had exactly buses-1
edges — still a tree, no loop at all. Fixed by re-pointing Sw7's own Bus2
at the real bus 300 (`Edit Line.Sw7 Bus2=300`, after the stock master
already defines it) before closing it. Checked, not just fixed and hoped:
bus 300 and bus 151 now land on matching solved voltages, and the graph
check now finds 131 buses, 131 edges, exactly one cycle spanning 27 buses.

**A second, previously-unexercised gap was found in `allocation.dss_convex`
itself, the same way.** Its per-phase BFS (`_bfs_phase_tree`) never
actually checked for a cycle — a plain BFS visits each bus once via
whichever edge reaches it first and silently *skips* any other edge into an
already-seen bus, so it would have quietly picked an arbitrary spanning
tree of a meshed network and returned a *wrong* answer with no error at
all, unlike `convex.py`'s own `_extract_tree`, which explicitly checks
`edges == buses - 1`. Caught by trying the (by-then-corrected) looped
IEEE123 network through `build_lindistflow_model_dss` and getting a clean
solve where a loop-free-network assumption should have failed — not by the
experiment script erroring out on its own. Fixed with a second pass after
the BFS: count every phase-`p` edge whose *both* endpoints landed in the
BFS's own `seen` set; more of those than `len(seen) - 1` means at least one
edge was never needed to reach a new bus, i.e. a real cycle, and now raises
`ValueError` instead of silently mis-solving.

**Result 1: the corrected network is genuinely both unbalanced and meshed,
checked three independent ways.** (1) The `networkx` graph check: 131
buses, 131 edges, one cycle, 27 buses in the loop. (2) `AllocationProblemDSS`
solves the baseline cleanly (converged, feasible, vmin=0.9616, vmax=1.0560
pu) — a real, usable network, not merely "technically connected." (3) The
convex/MILP route (`build_lindistflow_model_dss`) now correctly *refuses*
it (`ValueError`, phase 1's own subnetwork: 97 buses but 97 edges, expected
96) rather than silently mis-solving — the same clean-refusal precedent
Section 4.13/M13 set for the pandapower MILP route on the (balanced)
meshed EULV network.

**Result 2: the general, exact-AC route needs no changes and beats the
baseline by a much larger margin than the same feeder's radial form.**
`greedy_baseline_dss`/`nsga2_search_dss` — architecturally general OpenDSS
solves, no radial assumption anywhere — ran unmodified. On 60 single-
phase-tap candidate requests (the same request band, population, and
generation count M15's own *radial* IEEE123 run used, for a direct
comparison):

```
greedy_baseline_dss :  2120.9 kW  (25/60 accepted, vmin=0.9415 pu)
nsga2_search_dss     :  4678.9 kW  (50/60 accepted, vmin=0.9403 pu, 27s)
                        +120.6% over the serial baseline
```

M15's radial IEEE123 run (same band) found +33.3% (44/60 → 53/60 accepted).
The meshed network's margin is more than 3x larger — the same *direction*
Section 4.13 found on meshed EULV (+3.6% radial → +7.7% meshed), now a much
bigger jump: the redundant tie path gives the search meaningfully more room
to route around a locally-stressed request than it had on the strictly
radial feeder, and IEEE123's own thinner baseline voltage margins (vmin
0.94-0.96 pu across this note's IEEE123 runs, vs. EULV's much larger
headroom) likely make that extra routing freedom worth more here.

**Where this compares to published results.** A literature search turned up
three relevant papers, none an exact match, together bracketing our number
— and, read in full (not just from abstracts), sharpening the claim rather
than leaving it a maybe. Home-Ortiz et al. (IEEE Trans. Industry
Applications, 58(4), 2022) are the only one of the three that studies
genuine closed-loop operation: a MISOCP model maximizing installed PV at
candidate buses under progressively-more-loops topologies — but their
33-/85-node test feeders are modeled single-phase-equivalent, not
genuinely unbalanced (confirmed by reading the formulation directly:
scalar voltage/impedance per node, no phase decomposition anywhere). Jacob
& Zhang (IEEE PESGM 2020) and Fu & Chiang (IEEE Trans. Power Delivery,
33(5), 2018) are both genuinely unbalanced — but both, read in full, turn
out to enforce strict radiality at every step of their reconfiguration:
Jacob & Zhang's `branches - buses + 1 = 0` (eq. 3), and Fu & Chiang's own
"loop-based decimal coding scheme," which pairs every tie switch a
candidate closes with a compensating sectionalizing switch it must open in
the same move specifically to restore radiality. Neither ever actually
operates meshed; both search over which *radial* topology is best. So of
the three, **none combines genuine mesh with genuine unbalance** — now
confirmed by reading all three, not inferred from an abstract. That is
exactly the combination this milestone tests directly.

Fu & Chiang use a modified IEEE123 (130 buses, 135 branches, 6 tie
switches the authors added themselves, not the same 2-tie-switch topology
the standard published test feeder we use ships with) plus a real
1001-node network, and a different metric again: hourly Normalized Hosting
Capacity (NHC) for a *fixed*, already-sited set of 3-6 DG units against a
24-hour forecast, multi-period reconfiguration minimizing switching
operations subject to NHC >= 1.05 at every hour. Not an aggregate kW
figure comparable to the other two, so no percentage is quoted for it --
but the qualitative finding is sharp: at the network's own worst hours,
baseline NHC is *zero* (the already-sited DG output can't be delivered at
all without a violation) at three hours for the IEEE123/3-DG case and one
hour for the 1001-node case; radial reconfiguration alone (4-18 switching
operations, by case size) restores every hour to the threshold. None of
the three treats hosting capacity as a discrete queue of specific
candidate requests to accept or reject -- the same per-request-vs-joint-
cluster gap this project already stakes out against Grid-Mind, confirmed
here within this specific sub-literature too.

```
Home-Ortiz et al., 33-node, positive-sequence, closed-loop:  radial 5,947 kW -> 5 loops 10,504 kW   (+76.6%)
Home-Ortiz et al., 85-node, positive-sequence, closed-loop:  radial 53,578 kW -> 12 loops 65,461 kW  (+22.2%)
Jacob & Zhang, IEEE37, unbalanced, best-radial-topology:     min PVHC 1,120 kW -> 2,136 kW           (+90.7%)
                                                               median 6,260 kW -> 7,208 kW            (+15.1%)
                                                               max 11,400 kW -> 12,280 kW             (+7.7%)
Fu & Chiang, IEEE123 (modified) + 1001-node, unbalanced,
  best-radial-topology, fixed DG siting: worst-hour NHC 0 (infeasible) -> >=1.05 (sufficient), all cases
this milestone, IEEE123, unbalanced AND meshed, request queue: greedy 2,120.9 kW -> nsga2 4,678.9 kW (+120.6%)
```

Our own +120.6% sits above every published figure, but not implausibly so.
Two mechanisms neither paper isolates alone plausibly compound here: a
genuine closed loop is a live second current path, strictly more than
picking the best pre-selected radial topology (Home-Ortiz et al.'s own
radial-to-more-loops progression already moves the same direction, just on
a balanced network; Jacob & Zhang's and Fu & Chiang's own explicit
radiality constraints, now confirmed, mean neither genuinely-unbalanced
paper tests this axis at all); and a discrete accept/reject *queue* is
structurally more exposed to one badly-placed early request breaking the
whole serial process than an aggregate/stochastic metric is — the same
mechanism M15/M16 already diagnosed (IEEE13 +192.4%, SMART-DS +1367.0%).
Two independent, if indirect, results now support that second mechanism.
Jacob & Zhang's *minimum*-PVHC statistic — the tail closest in spirit to
our own "one bad early request" case — gains the most from reconfiguration
(+90.7%), far more than their median (+15.1%) or maximum (+7.7%). Fu &
Chiang's own worst-hour NHC going from literally zero to sufficient is the
same shape again, in a third, independently-designed framing: the payoff
concentrates exactly at the network's most stressed operating point, not
spread evenly — neither paper frames it in these terms, but both show it.

**Honest scope limits:**
- One loop, one feeder. This checks that the joint axis *works*, not how
  the margin scales with loop count, loop size, or feeder topology more
  generally — a single data point, like Section 4.13's own single added
  tie line on EULV.
- The convex/MILP route's refusal is by design, not a gap this milestone
  closes — an angle-exact multiphase branch-flow SOCP relaxation that
  handles both axes at once remains a real research lift, unchanged from
  Section 5's own scoping.
- Two real, previously-shipped-but-never-exercised bugs were found and
  fixed by this milestone (the looped-file dummy-bus mistake, the missing
  cycle check in `dss_convex`) — both existed silently until a genuinely
  meshed-and-unbalanced network was actually tried, the concrete reason
  this milestone was worth doing rather than leaving the gap purely
  theoretical.

**M19 "done when" bar (ROADMAP.md, this document): a real network that is
both non-radial and unbalanced solves via at least one route, the
search-beats-baseline claim is checked (not assumed) to hold there, and the
radial-only route is checked (not assumed) to refuse it cleanly — met.**
`docs/paper.md` Section 7's remaining item after M18 is closed; the
continuous-sizing convergence-gap question is the only one left open.

## M20: validating on a network that ships meshed by design, not a synthetic loop

**Why this, next.** M19's own "Honest scope limits" flagged its result as
"one loop, one feeder" — IEEE123 with a single normally-open tie closed, a
real feeder but a loop *we* added, not a network published as meshed. This
milestone repeats M19's three checks (baseline feasibility, MILP-route
refusal, search-beats-baseline) on a network that is meshed by design: the
IEEE 342-Node Low Voltage Networked Test System, IEEE's own words "the first
distribution test feeder developed by the IEEE that contains unbalanced
networked components" — network protectors and multiple transformers all
feeding into a shared secondary grid, not one added tie. Vendored as
`data/opendss/lvtestcase_na/Master.dss` (`LVTestCaseNorthAmerican` in
`dss-extensions/electricdss-tst`, confirmed by OpenDSS's own author, Roger
Dugan, as the canonical port — see that directory's `PROVENANCE.md`). Unlike
M19's IEEE123 file, this one needed no fix: it solves standalone, no latent
topology bug.

**Result 1: genuinely meshed, checked independently of `allocation/`.** A
plain `networkx` graph check over solved lines/transformers: 390 buses, 460
edges, connected, 71 independent cycles — not one loop, a real mesh. All 624
loads sit on the secondary ("S") side; the mesh itself lives entirely in 243
secondary-to-secondary line connections among 240 low-voltage buses (each of
the network's 70 step-down transformers feeds a *distinct* secondary bus —
the "networked" property comes from those secondary buses then being tied
together by lines, not from several transformers sharing one bus).

**Result 2: `AllocationProblemDSS` solves the baseline cleanly** (converged,
feasible, vmin=0.9910, vmax=1.0498 pu — comfortable headroom on the voltage
axis, unlike some of the SMART-DS feeders M16 screened out).

**Result 3: the convex/MILP route refuses this network too — but checked
against real requests, because the empty-request check M19 used would have
passed here for the wrong reason.** Every one of this network's 70
step-down transformers is delta-connected on its primary side, which
`dss_convex`'s per-phase decoupled tree cannot cross (a pre-existing,
documented limitation of that module, unrelated to mesh detection). That
means the *zero-request* model "builds" without error: the per-phase BFS
from `root_bus` never leaves the small cluster of primary buses reachable
without crossing a transformer, and never reaches the meshed secondary
network at all to find a cycle. `experiments/45_ieee342_validation.py`
therefore checks reachability against real candidate requests on the
secondary side, which correctly raises (`ValueError: request 'ev0': bus
's68' phase 3 not reachable from root_bus 'p1'`) — the right outcome, for a
more fundamental reason (delta-primary transformers, universal on this
network) than M19's per-phase-cycle check needed.

**Result 4: the exact-AC route beats the baseline, at a request band
screened for this network, not reused from IEEE123.** This network mixes
two secondary voltage classes wildly different in scale — residential
120/208V spot loads (median 15.6–36.1 kW, existing-load max 68–144 kW,
92% of load buses) and commercial 277/480V spot loads (median 304–565 kW,
max 653 kW) — so M15/M19's 10–150 kW IEEE123 band isn't meaningful here.
Screened directly, not assumed: at that band, `greedy_baseline_dss` accepts
only 2/60 candidates regardless of band width (this densely-loaded, 42.2 MW
already-committed secondary grid has far tighter thermal headroom than
IEEE123's, not a bug). A 1–5 kW band — matched to the residential secondary
class that dominates candidate points — gives a real, non-degenerate split:

```
greedy_baseline_dss :   102.1 kW  (37/60 accepted, vmin=0.9722 pu)
nsga2_search_dss     :   161.7 kW  (58/60 accepted, vmin=0.9652 pu, 89s)
                        +58.5% over the serial baseline
```

Not a like-for-like number against M15's +33.3% or M19's +120.6% (different
network, different request band, different voltage class) — only the same
*qualitative* claim, now checked on a third, structurally distinct network:
the exact-AC route (`AllocationProblemDSS` + `nsga2_search_dss`) needs no
network-specific changes and beats the serial-queue baseline again.

**Honest scope limits:**
- One network, one request band. Like M19, this establishes that the route
  *works* on a real meshed-by-design network — not how the margin scales
  with mesh density, voltage-class mix, or request band choice.
- The convex/MILP route's refusal here is a *stronger* limitation than
  M19's (delta-primary transformers make almost the entire network
  unreachable, not just the mesh itself) — still the same documented,
  by-design scope boundary from M18, not a new gap this milestone opens.
- The 1-5 kW request band is calibrated to this network's own residential
  secondary load scale, not chosen to maximize the reported margin — see
  `experiments/45_ieee342_validation.py`'s module docstring for the
  screening data (greedy accepts 2/60 at IEEE123's own band, 37/60 here).

**M20 "done when" bar (ROADMAP.md): the network is vendored,
`AllocationProblemDSS` solves its baseline, and `greedy_baseline_dss`/
`nsga2_search_dss` are checked (not assumed) to replicate the
search-beats-baseline bar there — met.** Extends M19's single synthetic
tie-switch closure to a real, industry-standard meshed+unbalanced network.
No open items remain in `docs/paper.md` Section 7 except the continuous-
sizing convergence-gap question M19 already left open.

## M21: does continuous NSGA-II's convergence gap close with budget, or is it a ceiling?

**The question, precisely.** M7 found `nsga2_search_continuous` reaching
231.1 kW on EULV at (pop=40, gen=40) — worse than binary `nsga2_search`'s
245.4 kW, and worse than even the plain binary greedy baseline's 236.8 kW.
Diagnosed, at the time, as `FloatRandomSampling` starting genes near 0.5 on
average, far from the near-bang-bang (mostly-0-or-1) corners this
problem's optima tend to sit at — but M7's own follow-up check at 2x the
budget (pop=80, gen=80, 4x the evaluations) never finished within its own
590s time limit, so the question was left genuinely open, not answered
either way: does more budget close the gap, or is the operator itself
ceilinged regardless of how long it runs? M12 found the same inherited gap
on `nsga2_search_continuous_fair` and left the same question open there
too. This was the sole remaining item in `docs/paper.md` Section 7 after
M20.

**Built, then run in a later session, exactly as planned.** A prior
session built the instrumentation without running the expensive sweep
("we will run later"): `allocation.search` gained a `callback` parameter
on `nsga2_search`/`nsga2_search_continuous`/`nsga2_search_continuous_fair`
— a pure passthrough to pymoo's own per-generation `Callback` hook, no
change to what is searched (checked directly:
`test_callback_is_notified_once_per_generation_and_does_not_change_the_
result` asserts the same seed produces the same result with or without a
callback attached). This turns "does more budget help" into a single long
run sampled at every generation via a running-best-so-far log, rather than
M7's approach of restarting from scratch at each budget level (which is
exactly why M7's own 2x-budget check was too expensive to repeat at
several levels). One real bug surfaced while wiring this up, caught before
it shipped: pymoo's `Algorithm.setup()` overwrites its own internal
no-op `Callback()` default whenever `callback=None` is passed through
explicitly (`self.__dict__[key] = value` for every kwarg, unconditionally)
— so the three `search.py` functions had to include `callback` in the
`minimize()` call only when it isn't `None`, not pass it through
unconditionally. Caught by every existing test in
`test_allocation_sizing.py` failing with `TypeError: 'NoneType' object is
not callable` the first time this was wired up, not discovered later.

`experiments/44_continuous_convergence.py` ran continuous and binary
NSGA-II to gen=320 (8x M7's own gen=40 check) at pop_size ∈ {40, 160} on
EULV (40 candidate PV requests, same band as M7), plus
`nsga2_search_continuous_fair` at pop_size=160 for M12's own version of
the question. `n_jobs=-1` (checked RAM/core headroom first — 53 GB free,
16 cores, and EULV's 907 buses is far smaller than the one network in this
repo's own history that ever got memory-heavy at high parallelism,
SMART-DS's 4,591 buses). Total wall time ≈33 minutes.

**Result: the gap closes with budget — and continuous NSGA-II ends up
*ahead* of binary's own converged optimum, not just level with it.**

```
                          pop=40              pop=160
binary NSGA-II, converged (by gen~30):    245.4 kW            245.4 kW    (flat for the remaining ~290 gens)
continuous NSGA-II, gen=40 (M7's own check):  231.1 kW        --          (reproduces M7's negative result exactly)
continuous NSGA-II, final (gen=320):      248.0 kW            248.4 kW
continuous NSGA-II crosses binary's ceiling at:  gen~155      gen~65

reference ceilings: binary greedy 236.8 kW | continuous greedy 237.3 kW |
                     binary LinDistFlow MILP 243.4 kW | continuous LinDistFlow LP 246.0 kW
```

Binary NSGA-II converges almost immediately in generation-count and then
sits flat; continuous NSGA-II converges far more slowly in generation-count
(consistent with M7's own diagnosis — `FloatRandomSampling` starts far from
the near-bang-bang corners this problem's optima favor) but keeps climbing
well past the point binary has already flattened, crossing binary's own
converged value partway through the budget and finishing above it at both
population sizes. It also finishes *above* the continuous LinDistFlow LP's
own ceiling (246.0 kW) — not a contradiction: that LP is a lossy,
linearized relaxation with real, already-quantified approximation error
(Section on M5/M18), not a strict upper bound on the true AC-exact
continuous optimum continuous NSGA-II searches directly.

**The fair variant's convergence gap closes identically — not merely
similarly, bit-for-bit identically.** `nsga2_search_continuous_fair` at
pop_size=160 produced the *exact same* running-best value as the plain
continuous run at every one of the 32 logged checkpoints (211.8, 232.0,
236.6, ... 248.4 kW). The fairness constraint costs nothing on this
network at this budget — the capacity-optimal individual already has zero
queue-fairness violations — the same "fairness is usually free" pattern
M17 found holding for 94% of feeders at full-corpus scale, now showing up
on the convergence-speed axis too, not just the final-value axis.

**Answer to the open question: not an operator ceiling, a convergence-speed
gap.** Continuous NSGA-II does not need a different initial-sampling
operator (e.g. corner-seeded sampling, the follow-up M7 itself proposed as
the next step if this turned out to be a real ceiling) — it needs roughly
4-8x the generations binary does to reach the same near-bang-bang
neighborhood, and once it gets there it has strictly more freedom (partial
sizes, a superset of binary's {0,1} choices) and ends up ahead. The larger
population (pop=160) reaches the crossover point in fewer generations
(gen≈65) than the smaller one (gen≈155) — more diversity per generation
buys faster convergence in generation-count, at the (already well-
documented, Section 4.10) cost of more evaluations per generation.

**Honest scope limits:**
- One network (EULV), one seed, one request band — same footing as M7's
  own numbers for direct comparability, but not a sweep across networks or
  seeds. The *qualitative* answer (budget closes it) is unlikely to be
  network-specific given the mechanism (generation-count needed to escape
  a near-0.5 starting neighborhood), but not independently checked here.
- "Closes" is read off a running-best-so-far curve that visibly flattens
  near the end of the budget, not a formal convergence proof — a still-
  climbing curve at gen=320 would have left the question open rather than
  answered; this one clearly flattens (second-half improvement ≤2.3 kW at
  both pop sizes, vs. first-half improvement of 83-86 kW).
- The crossover generation (gen≈65-155) is well within a single-digit
  multiple of binary's own budget, cheap enough that "just run continuous
  NSGA-II longer" is a practical recommendation, not merely a theoretical
  one — but this was not stress-tested against a network where the
  feasible region is much smaller relative to the search space, where the
  multiplier could plausibly be larger.

**M21 "done when" (informal, no ROADMAP.md bar pre-existed since this
started as instrumentation, not a scoped milestone): the running-best
curves are actually measured, not left as a diagnosed-but-unresolved
guess, and a verdict is read off them — met.** This was the sole remaining
item `docs/paper.md` Section 7 named as open after M20. No open items
remain.
