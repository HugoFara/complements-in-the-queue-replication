"""M80 -- DOES MULTI-ROUND ALTERNATION RECOVER THE RESIDUAL?

THE ATTACK THIS ANSWERS
-----------------------
Eq. (1) of the paper defines the residual against `max(gen-first, load-first)` -- the best of
TWO SINGLE-PASS block orders. Two independent adversarial reviews made the same objection, and
it is the strongest one either of them raised:

    A segregated utility never runs one pass. Each track periodically RE-STUDIES against a base
    case that already contains the other track's prior approvals. That is multi-round alternation
    of single-signed batches, and it costs no tariff reform at all -- it is a re-study loop.
    One pass cannot recover {g1 infeasible alone, l1 feasible, g2 feasible given l1}; two
    alternating passes can. So the quantity called "the capacity no study order recovers" is an
    UPPER BOUND on the truly order-proof residual, and the paper's central noun is mislabeled.

Only requests needing SIMULTANEOUS mixed approval at some step survive alternation. The reviews
predicted co-located cells survive and diffuse voltage-band cells (Ckt7, ENWL) may not.

WHAT THIS RUNS
--------------
The alternating fixed point, using each family's OWN certifier and anchor helper -- the same
machinery that produced Table 2, so the numbers are directly comparable:

    round 1a: certify track A vs base                    -> accept, bake in permanently
    round 1b: certify track B vs base+A                  -> accept, bake in permanently
    round 2a: certify track A's LEFTOVERS vs base+A+B    -> accept, bake in
    round 2b: certify track B's leftovers vs base+A+B+A' -> ...
    ... until a full round adds nothing (the fixed point).

Approvals are permanent and only ever added, which is what a real queue does -- a utility does
not revoke an approved interconnection because a later study found a better packing. The first
two half-rounds ARE the single-pass sequential study, so alternation >= sequential by
construction and residual_alt <= residual_singlepass always. Both start signs are run and the
better fixed point is taken, exactly as Eq. (1) takes the better block order.

Reported per draw:
    residual_singlepass = JOINT - max(seq_genfirst, seq_loadfirst)      the paper's Eq. (1)
    residual_alt        = JOINT - max(alt_genfirst, alt_loadfirst)      the reviewers' baseline
    recovered           = the fraction of the single-pass residual that alternation reaches

PLACEMENT, AND A PRE-REGISTERED PREDICTION
------------------------------------------
The first pass ran RANDOM placement everywhere except SMART-DS, and the result was that only
SMART-DS's CO-LOCATED cells kept a residual under alternation. That left the paper's central
claim resting on one family, and it confounded two things: whether the feeder matters, or
whether co-location does. `--placement colocated` puts one generation and one load request at
the SAME bus on any pandapower family, so the two can be separated.

The mechanism says alternation fails exactly when the constraint blocking a pair is PRIVATE to
that pair's site, and succeeds when it is SHARED, because a shared element is relieved by any
approval anywhere. Written down BEFORE running (the classification in the paper's Figure 2 was
assigned post hoc, and a referee rightly objected):

  * Ckt7   (1,255 buses, end-of-lateral overvoltage, per-site service transformers)
        -> co-located pairs should be PRIVATE  -> PREDICT R* > 0
  * Ckt24  (6,058 buses, 50.9 MW, same structure, larger)
        -> PREDICT R* > 0
  * ENWL n13f4 / n2f1 (UK LV, electrically short, feeder-wide voltage rise dominates)
        -> a pair's node voltage is strongly coupled to the whole feeder, so the binding
           constraint is closer to SHARED -> PREDICT R* ~ 0, or small
  * Swiss 55-node MV (line ampacity on shared upstream trunk, only 55 nodes)
        -> PREDICT R* = 0, fully recovered

If Ckt7 and Ckt24 come back at zero while SMART-DS stays positive, the finding is about that
one feeder rather than about co-location, and the paper's claim has to narrow accordingly.

Run:
  uv run python experiments/109_alternating_rounds.py --families ckt7 enwl_n13f4 --save
  uv run python experiments/109_alternating_rounds.py --families ckt7 ckt24 --placement colocated \
      --out m82_colocated_alternating.csv --save
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("68_ckt7_gldf_headline")            # Project, VMIN_PU/VMAX_PU/LOADING_MAX_PCT
_x = __import__("78_corpus_law")                    # Feeder, load_feeder
_s = __import__("83_setpoint_sweep")                # SlackFreeFeeder, _build_mixed
_seq7 = __import__("91_sequential_segregation")     # certify, feeder_with_anchor  (Ckt7)
_seg24 = __import__("88_ckt24_segregation")         # Ckt24Feeder, build_mixed
_seq24 = __import__("92_sequential_segregation_ckt24")  # ckt24_with_anchor
_enwl = __import__("98_residual_enwl")              # build_mixed, three_way, enwl_with_anchor
_spur = __import__("99_thermal_twoway")             # build_toy

OUT = Path(__file__).resolve().parents[1] / "outputs"

MAX_ROUNDS = 12

# Ckt7's source setpoint. M100: 1.04 is NOT baseline-compliant once the Vsource's Thevenin
# impedance is modelled -- vmin 0.9199 against a 0.94 floor. The compliant windows are
# [1.060, 1.120] balanced and [1.055, 1.120] on the shipped three-phase model; 1.06 is the
# tightest compliant point common to both, and the closest available to the published cell.
# Set to 1.04 to reproduce the pre-M100 runs.
CKT7_SETPOINT = 1.06


# ------------------------------------------------------------------ families
def build_colocated_pp(f, k, seed, size_kw, mismatch=1.0):
    """K requests as k/2 CO-LOCATED generation+load pairs on a pandapower feeder: at each of
    k/2 randomly drawn load buses, one export and one draw at the SAME bus.

    The pandapower analogue of exp101's `build_colocated`, which until now existed only for the
    SMART-DS oracle. That asymmetry is why the irreducible residual was a one-family result.

    `mismatch` scales the LOAD leg against the generation leg. At the default 1.0 the pair
    cancels exactly, so its joint feasibility is arithmetic and a referee can fairly call it a
    no-op the network never sees; below 1.0 the pair leaves a net injection and has to earn it.
    """
    rng = random.Random(seed)
    buses = [rng.choice(f.load_buses) for _ in range(max(1, k // 2))]
    out = []
    for b in buses:
        out.append(_c.Project(id=f"pv{len(out)}", bus=int(b), p_mw=-size_kw / 1000.0))
        out.append(_c.Project(id=f"ld{len(out)}", bus=int(b),
                              p_mw=+size_kw * mismatch / 1000.0))
    return out


def _reset(f, projects=()):
    """Drop the cached staged-projects handle so the next certify re-stages cleanly."""
    f._proj = None


def _reset_dss(f, projects=()):
    """SMART-DS's oracle caches a staged circuit AND a bus->GLDF-key map keyed by the SUBSET
    about to be certified (exp101's three_way does the same before every certify call)."""
    f._staged = None
    f._keys = {p.bus: p.gldf_key for p in projects}


class Family:
    """A feeder family's hooks: build the feeder, build a draw, anchor, certify, reset."""

    def __init__(self, name, make_feeder, make_projects, with_anchor, certify, desc,
                 reset=_reset):
        self.name, self.desc = name, desc
        self.make_feeder, self.make_projects = make_feeder, make_projects
        self.with_anchor, self.certify = with_anchor, certify
        self.reset = reset


def _ckt7(placement="random", setpoint=None):
    sp = CKT7_SETPOINT if setpoint is None else float(setpoint)
    return Family(
        "ckt7",
        lambda: _s.SlackFreeFeeder.at_setpoint(sp, _c.VMIN_PU, _c.VMAX_PU, _c.LOADING_MAX_PCT),
        _builder(placement, lambda f, seed, k, size: _s._build_mixed(f, k, seed, 0.5, size)),
        _seq7.feeder_with_anchor, _seq7.certify,
        f"EPRI Ckt7 @{sp:.2f}, {placement} placement")


def _ckt24(setpoint=1.05, placement="random", mismatch=1.0):
    return Family(
        "ckt24",
        lambda: _seg24.Ckt24Feeder.at_setpoint(setpoint),
        _builder(placement, lambda f, seed, k, size: _seg24.build_mixed(f, k, seed, 0.5, size),
                 mismatch),
        _seq24.ckt24_with_anchor, _seg24.certify,
        f"EPRI Ckt24 @1.05, {placement} placement"
        + (f", load leg x{mismatch:.2f}" if mismatch != 1.0 else ""))


def _enwl_family(name, n_jobs, placement="random"):
    def mk():
        _ctx, f, _rel, _mk, _desc = _x.load_feeder(name, n_jobs)
        return f
    return Family(
        name, mk,
        _builder(placement, lambda f, seed, k, size: _enwl.build_mixed(f, k, seed, 0.5, size)),
        _enwl.enwl_with_anchor, _seq7.certify,
        f"ENWL {name.split('_')[1]} (real UK LV, EN 50160), {placement} placement")


def _spur_family(spur_ka=0.040, size_kw=500.0, n_each=6):
    def mk():
        net, site, _rating = _spur.build_toy(spur_ka, vn_kv=11.0)
        f = _x.Feeder("thermal-twoway-toy", net, sizer=None, vmin=0.90, vmax=1.10, loading=100.0)
        f._site = site
        return f

    def mkp(f, seed, k, size):
        # deterministic by construction -- co-located pairs at one site, no randomness.
        # The 500 kW size is fixed by the 40 A spur rating that creates the mechanism (exp99),
        # so this family ignores --size-kw rather than silently breaking its own physics.
        pv = [_c.Project(id=f"pv{i}", bus=f._site, p_mw=-size_kw / 1000.0) for i in range(n_each)]
        ld = [_c.Project(id=f"ld{i}", bus=f._site, p_mw=+size_kw / 1000.0) for i in range(n_each)]
        return pv + ld

    return Family("spur", mk, mkp, _enwl.enwl_with_anchor, _seq7.certify,
                  "constructed co-located spur (the strict-complement case)")


def _swiss_family(mat, placement="random"):
    _sw = __import__("103_residual_swiss")
    return Family(
        "swiss55",
        lambda: _sw.load_swiss(mat, 21.0, "peakload", 0.95, 1.05),
        _builder(placement, lambda f, seed, k, size: _enwl.build_mixed(f, k, seed, 0.5, size)),
        _enwl.enwl_with_anchor, _seq7.certify,
        f"Swiss 55-node MV oversubscribed, {placement} placement")


def _smartds_family(master, colocated=True, mismatch=1.0):
    """`mismatch` scales the EV leg against the PV leg, as on Ckt24: exp101's builder already
    takes the two sizes separately, so only the call site needed opening up."""
    _sd = __import__("101_residual_smartds")
    build = _sd.build_colocated if colocated else _sd.build_mixed
    return Family(
        "smartds_colocated" if colocated else "smartds_random",
        lambda: _sd.load(master),
        (lambda f, seed, k, size: build(f, k, seed, size, size * mismatch)) if colocated
        else (lambda f, seed, k, size: build(f, k, seed, 0.5, size, size * mismatch)),
        _sd.with_anchor, _seq7.certify,
        f"SMART-DS {'co-located' if colocated else 'random'} (OpenDSS-native, unbalanced)"
        + (f", load leg x{mismatch:.2f}" if mismatch != 1.0 else ""),
        reset=_reset_dss)


def _builder(placement, random_builder, mismatch=1.0):
    """Pick the draw: the family's own random-placement builder, or co-located pairs."""
    if placement == "colocated":
        return lambda f, seed, k, size: build_colocated_pp(f, k, seed, size, mismatch)
    return random_builder


def get_family(name, n_jobs, mat=None, master=None, placement="random",
               mismatch=1.0, setpoint=None):
    if name == "ckt7":
        return _ckt7(placement=placement, setpoint=setpoint)
    if name == "ckt24":
        return _ckt24(placement=placement, mismatch=mismatch)
    if name == "spur":
        return _spur_family()                      # already co-located by construction
    if name == "swiss55":
        if not mat:
            raise SystemExit("swiss55 needs --mat")
        return _swiss_family(mat, placement=placement)
    if name in ("smartds_colocated", "smartds_random"):
        if not master:
            raise SystemExit("smartds needs --master")
        return _smartds_family(master, colocated=name.endswith("colocated"),
                               mismatch=mismatch)
    if name.startswith("enwl_"):
        return _enwl_family(name, n_jobs, placement=placement)
    raise SystemExit(f"unknown family {name!r}")


# ------------------------------------------------------- the alternating loop
def alternate(fam, f, projects, n_jobs, start="gen", max_rounds=MAX_ROUNDS):
    """The fixed point of alternating SINGLE-SIGNED studies against a growing base case.

    Returns (total_kw, n_accepted, half_rounds, rounds_that_added, per_round_kw).
    """
    gen = [p for p in projects if p.p_mw < 0]
    ld = [p for p in projects if p.p_mw > 0]
    tracks = [gen, ld] if start == "gen" else [ld, gen]
    pending = [list(tracks[0]), list(tracks[1])]
    accepted: list = []
    half_rounds, productive, per_round = 0, 0, []

    for _rnd in range(max_rounds):
        progressed = False
        for t in (0, 1):
            if not pending[t]:
                continue
            # anchor on EVERYTHING approved so far, always rebuilt from the pristine base
            fa = fam.with_anchor(f, accepted, {p.id for p in accepted}) if accepted else f
            fam.reset(fa, pending[t])
            ids, kw = fam.certify(fa, pending[t], n_jobs)
            half_rounds += 1
            if ids:
                newly = [p for p in pending[t] if p.id in ids]
                accepted.extend(newly)
                pending[t] = [p for p in pending[t] if p.id not in ids]
                per_round.append(round(kw, 3))
                productive += 1
                progressed = True
        if not progressed:
            break

    total = sum(abs(p.p_mw) for p in accepted) * 1000.0

    # END-TO-END AC CHECK. Feasibility of the fixed point is inductive -- each round certifies
    # against a base case containing every prior approval -- but the whole point of this paper is
    # that certified means AC-verified, so we verify the union directly against the PRISTINE
    # feeder rather than trusting the induction.
    fam.reset(f, projects)
    ok, _mw, _vio, info = f.evaluate(f.ctx, projects, {p.id for p in accepted})
    return total, len(accepted), half_rounds, productive, per_round, bool(ok), info


def three_way_generic(fam, f, projects, n_jobs):
    """The paper's Eq. (1) quantities, family-agnostic: parallel, both single-pass orders, joint."""
    gen = [p for p in projects if p.p_mw < 0]
    ld = [p for p in projects if p.p_mw > 0]

    fam.reset(f, gen)
    g_ids, g_kw = fam.certify(f, gen, n_jobs)
    fam.reset(f, ld)
    l_ids, l_kw = fam.certify(f, ld, n_jobs)
    fam.reset(f, projects)
    _j, joint_kw = fam.certify(f, projects, n_jobs)

    fg = fam.with_anchor(f, gen, g_ids)
    fam.reset(fg, ld)
    _l2, l2_kw = fam.certify(fg, ld, n_jobs)
    fl = fam.with_anchor(f, ld, l_ids)
    fam.reset(fl, gen)
    _g2, g2_kw = fam.certify(fl, gen, n_jobs)

    return dict(gen_alone_kw=g_kw, load_alone_kw=l_kw, seg_parallel_kw=g_kw + l_kw,
                seq_genfirst_kw=g_kw + l2_kw, seq_loadfirst_kw=l_kw + g2_kw,
                best_sequential_kw=max(g_kw + l2_kw, l_kw + g2_kw), joint_kw=joint_kw)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--families", nargs="+",
                    default=["ckt7", "enwl_n13f4", "enwl_n2f1", "ckt24", "spur"])
    ap.add_argument("--k", type=int, default=None, help="default: 17 Ckt7, 24 ENWL/Ckt24, 12 spur")
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--mat", default=None, help="Swiss 55-node .mat (for family swiss55)")
    ap.add_argument("--master", default=None, help="SMART-DS Master.dss (for family smartds_*)")
    ap.add_argument("--placement", choices=["random", "colocated"], default="random",
                    help="colocated puts one generation and one load request at the SAME bus")
    ap.add_argument("--mismatch", type=float, default=1.0,
                    help="co-located load leg as a fraction of the generation leg (ckt24)")
    ap.add_argument("--ckt7-setpoint", type=float, default=None,
                    help=f"Ckt7 source pu (default {CKT7_SETPOINT}; 1.04 reproduces pre-M100)")
    ap.add_argument("--out", default="m80_alternating_rounds.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M80 -- MULTI-ROUND ALTERNATION vs THE SINGLE-PASS RESIDUAL\n")
    print("  Eq. (1) maxes over two single-pass block orders. A segregated utility can instead")
    print("  re-study each track against the other's prior approvals, repeatedly, with no tariff")
    print("  reform at all. If that fixed point recovers the residual, the paper's central noun")
    print("  is wrong. Alternation >= single-pass sequential by construction.\n")

    default_k = {"ckt7": 17, "ckt24": 24, "spur": 12, "swiss55": 48,
                 "smartds_colocated": 16, "smartds_random": 16}
    default_size = {"spur": 500.0, "swiss55": 500.0}
    rows = []
    for fname in a.families:
        fam = get_family(fname, a.n_jobs, mat=a.mat, master=a.master,
                         placement=a.placement, mismatch=a.mismatch,
                         setpoint=a.ckt7_setpoint)
        k = a.k if a.k else default_k.get(fname, 24)
        seeds = [0] if fname == "spur" else a.seeds     # spur is deterministic
        size_shown = default_size.get(fname, a.size_kw)
        print(f"  {fam.desc}   (K={k}, {size_shown:.0f} kW)")
        hdr = (f"  {'seed':>4} | {'JOINT':>8} {'bestSEQ':>8} {'bestALT':>8} | "
               f"{'resid 1-pass':>12} {'resid alt':>10} {'recovered':>10} | {'rounds':>6} {'verify':>7}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))

        f = fam.make_feeder()
        for seed in seeds:
            t0 = time.time()
            projects = fam.make_projects(f, seed, k, size_shown)
            r = three_way_generic(fam, f, projects, a.n_jobs)

            alt_g, ng, hg, pg, trg, okg, infg = alternate(fam, f, projects, a.n_jobs, "gen")
            alt_l, nl, hl, pl, trl, okl, infl = alternate(fam, f, projects, a.n_jobs, "load")
            best_alt = max(alt_g, alt_l)
            # the AC verdict on whichever fixed point we actually report
            ok_best, inf_best = (okg, infg) if alt_g >= alt_l else (okl, infl)

            joint, best_seq = r["joint_kw"], r["best_sequential_kw"]
            res_1 = joint - best_seq
            res_a = joint - best_alt
            # what fraction of the single-pass residual did alternation actually recover?
            rec = (100.0 * (best_alt - best_seq) / res_1) if res_1 > 1e-9 else np.nan

            r.update(family=fname, seed=seed, k=k, size_kw=size_shown,
                     placement=a.placement, mismatch=a.mismatch,
                     alt_genfirst_kw=alt_g, alt_loadfirst_kw=alt_l, best_alternating_kw=best_alt,
                     residual_singlepass_kw=res_1, residual_alt_kw=res_a,
                     residual_singlepass_pct=(100 * res_1 / joint if joint > 0 else np.nan),
                     residual_alt_pct=(100 * res_a / joint if joint > 0 else np.nan),
                     recovered_by_alternation_pct=rec,
                     alt_rounds_genfirst=hg, alt_rounds_loadfirst=hl,
                     alt_productive_genfirst=pg, alt_productive_loadfirst=pl,
                     alt_accepted_genfirst=ng, alt_accepted_loadfirst=nl,
                     alt_ac_feasible_genfirst=okg, alt_ac_feasible_loadfirst=okl,
                     alt_ac_feasible=ok_best,
                     alt_vmax=inf_best.get("vmax"), alt_vmin=inf_best.get("vmin"),
                     alt_line_pct=inf_best.get("line_pct"), alt_trafo_pct=inf_best.get("trafo_pct"),
                     dt=time.time() - t0)
            rows.append(r)
            recs = "     n/a" if not np.isfinite(rec) else f"{rec:9.1f}%"
            acs = "AC ok" if ok_best else "AC FAIL"
            print(f"  {seed:>4d} | {joint:>8.0f} {best_seq:>8.0f} {best_alt:>8.0f} | "
                  f"{res_1:>11.0f} {res_a:>10.0f} {recs} | {max(hg, hl):>6d} {acs:>7}", flush=True)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)
        print()

    d = pd.DataFrame(rows)
    print("=" * 78)
    print("\n  SUMMARY -- residual as % of joint, single-pass (Eq. 1) vs alternating fixed point\n")
    print(f"  {'family':>12} {'n':>3} {'1-pass':>8} {'alt':>8} {'recovered':>11} {'survives':>9} {'AC ok':>7}")
    print("  " + "-" * 64)
    for fname, g in d.groupby("family", sort=False):
        r1, ra = g.residual_singlepass_pct.median(), g.residual_alt_pct.median()
        rec = g.recovered_by_alternation_pct.median()
        recs = "n/a" if not np.isfinite(rec) else f"{rec:.1f}%"
        surv = f"{int((g.residual_alt_kw > 1e-6).sum())}/{len(g)}"
        acok = f"{int(g.alt_ac_feasible.sum())}/{len(g)}"
        print(f"  {fname:>12} {len(g):>3d} {r1:>7.1f}% {ra:>7.1f}% {recs:>11} {surv:>9} {acok:>7}")
    if a.save:
        print(f"\n  saved -> outputs/{a.out}")
    print()


if __name__ == "__main__":
    main()
