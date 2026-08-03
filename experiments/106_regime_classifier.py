"""M77 -- THE REGIME CLASSIFIER, COMPUTED BLIND TO THE RESIDUAL (falsifiability)

The invariant -- "the residual is nonzero exactly when the active constraint admits both
generation and load as independent aggressors" -- is worthless if "admits both signs" is only
ever decided after seeing the residual it is supposed to predict. Then it is not a rule, it is a
restatement, and no observation can refute it.

This file makes it a rule. `classify` consumes ONLY single-signed information:

    1. Certify the generation-only queue against the baseline feeder.  -> accept-set A_g
    2. Certify the load-only queue against the baseline feeder.        -> accept-set A_l
    3. For each REJECTED request of a sign, ask what blocked it at that sign's own optimum.
       The constraints that block are the ones that sign can drive to its limit ON ITS OWN.
    4. TWO-WAY iff the generation track and the load track drive the SAME constraint class to
       its limit. Otherwise ONE-WAY.

Step 3 is the operational content of "independent aggressor": not "this constraint is near its
limit" (a tolerance we would have to tune) but "this constraint is what stops this sign from
seating more", which the oracle answers exactly. Constraint classes are the two-sided voltage
band (an upper and a lower limit of ONE band -- generation pinning vmax and load pinning vmin
are aggressors on the same constraint), and each thermal element (line, transformer) separately.

`classify` never receives, computes, or imports a joint or sequential result. The prediction is
emitted first; the residual is looked up afterwards, from the CSVs the earlier experiments wrote.
Any cell where the two disagree is a falsification and is printed as one.

Run:
  .venv/bin/python experiments/106_regime_classifier.py --save
  .venv/bin/python experiments/106_regime_classifier.py --cells ckt7_1.04 --n-jobs 8
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_x = __import__("78_corpus_law")                  # Feeder, load_feeder
_seq = __import__("91_sequential_segregation")    # certify
_res = __import__("98_residual_enwl")             # build_mixed (ENWL)
_c7 = __import__("68_ckt7_gldf_headline")         # Ckt7 limits
_sp = __import__("83_setpoint_sweep")             # SlackFreeFeeder, _build_mixed (Ckt7)
_c24 = __import__("88_ckt24_segregation")         # Ckt24Feeder, build_mixed (Ckt24)

OUT = Path(__file__).resolve().parents[1] / "outputs"


# Each family keeps its own feeder constructor and request generator, so a cell here is
# bit-identical to the cell the residual experiments already measured.
def make_ckt7(spec, seed):
    f = _sp.SlackFreeFeeder.at_setpoint(spec["setpoint"], _c7.VMIN_PU, _c7.VMAX_PU,
                                        _c7.LOADING_MAX_PCT)
    return f, _sp._build_mixed(f, spec["k"], seed, spec["load_frac"], spec["size_kw"])


def make_ckt24(spec, seed):
    f = _c24.Ckt24Feeder.at_setpoint(spec["setpoint"])
    return f, _c24.build_mixed(f, spec["k"], seed, spec["load_frac"], spec["size_kw"])


def make_enwl(spec, seed):
    _ctx, f, _rel, _mk, _desc = _x.load_feeder(spec["feeder"], 8)
    return f, _res.build_mixed(f, spec["k"], seed, spec["load_frac"], spec["size_kw"])


MAKERS = {"ckt7": make_ckt7, "ckt24": make_ckt24}

# Constraint identity. Two granularities, and the difference is load-bearing.
#
# The voltage band is ONE constraint with two sides on purpose: generation pinning the upper
# limit and load pinning the lower limit are aggressors on the same two-sided band, which is
# exactly the asymmetry with a one-sided thermal rating.
#
# Thermal ratings are identified PER ELEMENT. The paper's two-way thermal case is "a *shared*
# line or transformer that a generation-heavy accept-set loads one direction and a load-heavy
# accept-set loads the other" -- shared, i.e. the same element. Classifying at the coarse class
# level ("some transformer") instead merges a feeder's thousands of service transformers with
# its substation transformer, and then reads a PV overloading its own service transformer and a
# load overloading a different one as though they were aggressors on one constraint. On Ckt24
# that coarse test predicts two-way where the measured residual is 0 in 9/9 cells; at element
# level it predicts one-way, correctly. This refinement was made after Ckt24 contradicted the
# coarse version and before the remaining cells were run.
VOLTAGE_BAND = "voltage_band"


def violating_elements(f, info, tol=1e-9):
    """Identity of every constraint over its limit at the solution `f.evaluate` just left in
    `f._enet` -- voltage as one band, thermal per element, honouring each feeder subclass's own
    exclusions (Ckt24's pre-broken transformers, its non-worsening substation rule)."""
    net = f._enet
    out = set()
    if info["vmax"] > f.VMAX_PU + tol or info["vmin"] < f.VMIN_PU - tol:
        out.add(VOLTAGE_BAND)
    lp = net.res_line.loading_percent.drop(getattr(f, "excl_lines", []), errors="ignore")
    out.update(f"line:{i}" for i in lp.index[lp > f.LOADING_MAX_PCT + tol])
    skip = [i for i in (getattr(f, "sub_idx", None), *getattr(f, "bad_trafo_idx", ()))
            if i is not None]
    tp = net.res_trafo.loading_percent.drop(skip, errors="ignore")
    out.update(f"trafo:{i}" for i in tp.index[tp > f.LOADING_MAX_PCT + tol])
    if hasattr(f, "sub_base_pct") and info.get("sub_pct", 0.0) > f.sub_base_pct + tol:
        out.add("substation_trafo")
    return out


def margins(f, info):
    """Signed slack of every constraint at the solution just evaluated; negative = violated.
    Keyed by the same identities `violating_elements` uses, so the two compose."""
    net = f._enet
    out = {VOLTAGE_BAND: min(f.VMAX_PU - info["vmax"], info["vmin"] - f.VMIN_PU)}
    lp = net.res_line.loading_percent.drop(getattr(f, "excl_lines", []), errors="ignore")
    out.update({f"line:{i}": f.LOADING_MAX_PCT - v for i, v in lp.items()})
    skip = [i for i in (getattr(f, "sub_idx", None), *getattr(f, "bad_trafo_idx", ()))
            if i is not None]
    tp = net.res_trafo.loading_percent.drop(skip, errors="ignore")
    out.update({f"trafo:{i}": f.LOADING_MAX_PCT - v for i, v in tp.items()})
    if hasattr(f, "sub_base_pct"):
        out["substation_trafo"] = f.sub_base_pct - info.get("sub_pct", 0.0)
    return out


def relieved_by_other(f, cand, accepted_ids, other, eps=1e-4, max_probes=4):
    """Is there a single request of the OPPOSITE sign that opens slack on a constraint blocking
    this track?

    The strict test below asks whether each sign can drive the same constraint to its limit from
    its own side. That is the right question for a shared thermal element, but too strong for a
    voltage band: on Ckt7 the load track is stopped by its own service transformers long before
    it could reach the band's LOWER limit, yet the loads it seats still suppress the overvoltage
    that stops generation. Relief, not counter-violation, is what makes a request jointly
    feasible.

    The probe is PAIRWISE -- one blocked request against one opposite-sign request at a time --
    for a reason the corpus forced. Probing with the other track's whole certified solo set fails
    wherever that set is empty, and on the ENWL feeders load-alone certifies 0 kW, so there would
    be nothing to probe with even though generation's presence later seats plenty of load. A
    single opposite-sign request is always available and is the minimal statement of the
    complementarity being tested.
    """
    probes = 0
    for p in cand:
        if p.id in accepted_ids or probes >= max_probes:
            continue
        f._proj = None
        ok, _mw, _v, i0 = f.evaluate(f.ctx, cand, set(accepted_ids) | {p.id})
        if ok or i0.get("diverged"):
            continue
        m0 = margins(f, i0)
        blocked = [c for c, m in m0.items() if m < 0]
        if not blocked:
            continue
        probes += 1
        pool = list(cand) + list(other)
        for q in other:
            f._proj = None
            _ok, _mw, _v, i1 = f.evaluate(f.ctx, pool, set(accepted_ids) | {p.id, q.id})
            if i1.get("diverged"):
                continue
            m1 = margins(f, i1)
            if any(m1.get(c, m0[c]) > m0[c] + eps for c in blocked):
                return True
    return False


def blocking_constraints(f, cand, accepted_ids):
    """Which constraints stop this single-signed track from seating more?

    For every rejected request, re-solve the track's optimum plus that request and record which
    constraints the AC solution violates. A constraint appears iff it actually blocks something,
    which is the operational meaning of "this sign can drive it to its limit on its own" -- no
    tolerance on nearness-to-limit to tune.
    """
    found = set()
    for p in cand:
        if p.id in accepted_ids:
            continue
        f._proj = None
        ok, _mw, _vio, info = f.evaluate(f.ctx, cand, set(accepted_ids) | {p.id})
        if ok or info.get("diverged"):
            continue
        found |= violating_elements(f, info)
    return found


def summarize(constraints):
    """Compact label: the band by name, thermal elements by class and count."""
    if not constraints:
        return "(nothing)"
    parts = []
    if VOLTAGE_BAND in constraints:
        parts.append("voltage")
    for cls, label in (("line:", "line"), ("trafo:", "trafo")):
        n = sum(1 for c in constraints if c.startswith(cls))
        if n:
            parts.append(f"{n} {label}{'s' if n > 1 else ''}")
    if "substation_trafo" in constraints:
        parts.append("substation")
    return "+".join(parts)


def classify(f, projects, n_jobs):
    """The a-priori prediction. Sees only the two single-signed tracks -- never the joint or
    sequential study whose residual it is predicting."""
    gen = [p for p in projects if p.p_mw < 0]
    load = [p for p in projects if p.p_mw > 0]

    f._proj = None
    gen_ids, gen_kw = _seq.certify(f, gen, n_jobs)
    f._proj = None
    load_ids, load_kw = _seq.certify(f, load, n_jobs)

    gen_binds = blocking_constraints(f, gen, gen_ids)
    load_binds = blocking_constraints(f, load, load_ids)
    shared = gen_binds & load_binds

    # The relief test, on the same single-signed information.
    gen_helped = relieved_by_other(f, gen, gen_ids, load)
    load_helped = relieved_by_other(f, load, load_ids, gen)

    return dict(
        n_gen=len(gen), n_load=len(load),
        gen_alone_kw=gen_kw, load_alone_kw=load_kw,
        gen_helped_by_load=gen_helped, load_helped_by_gen=load_helped,
        predicted_relief="two-way" if (gen_helped and load_helped) else "one-way",
        gen_aggresses=summarize(gen_binds),
        load_aggresses=summarize(load_binds),
        shared_constraint=summarize(shared) if shared else "(none)",
        shared_detail=";".join(sorted(shared)),
        predicted_strict="two-way" if shared else "one-way",
    )


# ---------------------------------------------------------------- the corpus cells
# Each entry names the residual CSV and the filter that isolates the same cell, so the
# measured residual is LOOKED UP after the prediction rather than recomputed here.
CELLS = {
    "ckt7_1.04": dict(feeder="ckt7", k=17, size_kw=100.0, load_frac=0.5, setpoint=1.04,
                      csv="m63_sequential_ckt7.csv", where=dict(setpoint=1.04)),
    "ckt7_1.05": dict(feeder="ckt7", k=17, size_kw=100.0, load_frac=0.5, setpoint=1.05,
                      csv="m63_sequential_ckt7.csv", where=dict(setpoint=1.05)),
    # M100: the compliant setpoint. 1.04 and 1.05 are both outside Ckt7's legal window once
    # the source Thevenin impedance is modelled, so this is the cell the paper now argues on;
    # the two above are kept only because the published section reports them.
    "ckt7_1.06": dict(feeder="ckt7", k=17, size_kw=100.0, load_frac=0.5, setpoint=1.06,
                      csv="m113_sequential_ckt7_106.csv", where=dict(setpoint=1.06)),
    "ckt24_light": dict(feeder="ckt24", k=24, size_kw=100.0, load_frac=0.5, setpoint=1.05,
                        csv="m64_sequential_ckt24.csv", where={}),
    "ckt24_oversub": dict(feeder="ckt24", k=44, size_kw=100.0, load_frac=0.5, setpoint=1.05,
                          csv="m68_residual_oversub_ckt24.csv", where=dict(k=44)),
    "enwl_n13f4_100": dict(feeder="enwl_n13f4", k=24, size_kw=100.0, load_frac=0.5,
                           csv="m70_residual_enwl.csv",
                           where=dict(feeder="enwl_n13f4", size_kw=100.0)),
    "enwl_n13f4_50": dict(feeder="enwl_n13f4", k=24, size_kw=50.0, load_frac=0.5,
                          csv="m70_residual_enwl.csv",
                          where=dict(feeder="enwl_n13f4", size_kw=50.0)),
    "enwl_n2f1_100": dict(feeder="enwl_n2f1", k=24, size_kw=100.0, load_frac=0.5,
                          csv="m70_residual_enwl.csv",
                          where=dict(feeder="enwl_n2f1", size_kw=100.0)),
    "enwl_n2f1_50": dict(feeder="enwl_n2f1", k=24, size_kw=50.0, load_frac=0.5,
                         csv="m70_residual_enwl.csv",
                         where=dict(feeder="enwl_n2f1", size_kw=50.0)),
}


def measured_residual(spec):
    """The residual this cell actually has -- read only AFTER `classify` has committed."""
    path = OUT / spec["csv"]
    if not path.exists():
        return dict(measured_pct=np.nan, measured_n=0, measured_n_pos=0)
    d = pd.read_csv(path)
    for col, val in spec["where"].items():
        d = d[np.isclose(d[col], val)] if isinstance(val, float) else d[d[col] == val]
    if not len(d):
        return dict(measured_pct=np.nan, measured_n=0, measured_n_pos=0)
    res = (d.joint_kw - d.best_sequential_kw) / d.joint_kw * 100.0
    return dict(measured_pct=float(res.median()), measured_max_pct=float(res.max()),
                measured_n=int(len(res)), measured_n_pos=int((res > 1e-9).sum()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cells", nargs="+", default=list(CELLS))
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--out", default="m77_regime_classifier.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M77 -- REGIME CLASSIFICATION FROM SINGLE-SIGNED RUNS ONLY\n")
    print("  The prediction below is computed from the generation-only and load-only studies.")
    print("  No joint or sequential result enters it. The measured residual is looked up after.\n")

    hdr = (f"  {'cell':>16} {'gen aggresses':>18} {'load aggresses':>20} | {'strict':>8} "
           f"{'relief':>8} | {'residual':>9} {'strict?':>8} {'relief?':>8}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows, disagreements = [], []
    for name in a.cells:
        spec = CELLS[name]
        make = MAKERS.get(spec["feeder"], make_enwl)
        for seed in a.seeds:
            t0 = time.time()
            f, projects = make(spec, seed)
            pred = classify(f, projects, a.n_jobs)          # <-- blind
            meas = measured_residual(spec)                  # <-- only now
            positive = meas["measured_n_pos"] > 0
            ok_strict = (pred["predicted_strict"] == "two-way") == positive
            ok_relief = (pred["predicted_relief"] == "two-way") == positive
            r = dict(cell=name, seed=seed, **spec_public(spec), **pred, **meas,
                     strict_agrees=ok_strict, relief_agrees=ok_relief, dt=time.time() - t0)
            rows.append(r)
            if not ok_relief:
                disagreements.append(r)
            if seed == a.seeds[0]:
                print(f"  {name:>16} {pred['gen_aggresses']:>18} {pred['load_aggresses']:>20} | "
                      f"{pred['predicted_strict']:>8} {pred['predicted_relief']:>8} | "
                      f"{meas['measured_n_pos']:>4}/{meas['measured_n']:<4} "
                      f"{'yes' if ok_strict else 'NO':>8} {'yes' if ok_relief else 'NO':>8}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)

    print("\n" + "=" * 96)
    print(f"  strict (both signs drive the SAME constraint to its limit): "
          f"{int(d.strict_agrees.sum())}/{len(d)} cell-draws agree")
    print(f"  relief (each sign's binding constraint is relieved by the other): "
          f"{int(d.relief_agrees.sum())}/{len(d)} cell-draws agree")
    if disagreements:
        print("\n  FALSIFICATIONS of the relief test:")
        for r in disagreements:
            print(f"    {r['cell']} seed {r['seed']}: predicted {r['predicted_relief']}, "
                  f"residual positive in {r['measured_n_pos']}/{r['measured_n']} draws")
    else:
        print("\n  No cell falsifies the relief test on this corpus.")
    print("\n  Note the rule predicts POSSIBILITY at cell level, not sign at draw level: a")
    print("  two-way cell may still show a zero residual in an individual draw where no")
    print("  complementary pair happens to be electrically close enough to help.\n")
    if a.save:
        print(f"  saved -> outputs/{a.out}")


def spec_public(spec):
    return {k: v for k, v in spec.items() if k in ("feeder", "k", "size_kw", "load_frac", "setpoint")}


if __name__ == "__main__":
    main()
