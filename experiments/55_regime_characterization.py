"""M26: a predictive rule -- when is running the joint search worth it?

M22 (corpus) and M23 (controlled request sweep) between them say the joint-vs-
FCFS advantage is not a constant but a function of the request set. This
experiment turns that into an operational decision rule a utility could apply
*before* paying for a joint study, and reports the rule's precision/recall.

The rule is built in two layers, matching the two datasets:

  * **Necessary gate (from the corpus).** Joint search can only beat skip-and-
    continue if the request set actually forces a rejection -- i.e. the feeder
    is *bound* (skip-continue seats < K requests). Every real gain is bound by
    construction; the question is how well a cheap, pre-study feature
    (request/headroom ratio) predicts boundness, so the expensive joint search
    is only run where it can possibly pay.

  * **Magnitude (from the controlled sweep).** Among bound cases, how the
    advantage scales with the two request-set properties a utility can read off
    a cluster application before studying it: aggregate pressure
    (request/headroom) and spatial concentration.

Consumes outputs/m22_honest_corpus.csv (from experiment 52) and any
outputs/m23_request_sensitivity_*.csv (from experiment 54).

Run:  uv run python experiments/55_regime_characterization.py
"""

from __future__ import annotations

import csv
import glob
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def f(row, key, default=0.0):
    try:
        return float(row[key])
    except (KeyError, ValueError, TypeError):
        return default


def corpus_gate() -> None:
    path = OUT / "m22_honest_corpus.csv"
    if not path.exists():
        print("  (m22_honest_corpus.csv not found -- run experiment 52 --save first)")
        return
    rows = [r for r in load(path) if r.get("status") == "ok"]
    n = len(rows)
    bound = [r for r in rows if r.get("bound") == "True"]
    gains = [r for r in rows if r.get("classification") == "real_gain"]
    regr = [r for r in rows if r.get("classification") == "regression"]
    print(f"CORPUS ({n} baseline-feasible feeders):")
    print(f"  bound (request set forces a rejection): {len(bound)}/{n} ({100*len(bound)/n:.0f}%)")
    print(f"  real gains vs skip-continue:            {len(gains)}/{n} ({100*len(gains)/n:.0f}%)")
    print(f"  regressions (convex approx artifact):   {len(regr)}/{n}")

    # necessary condition: is every real gain a bound feeder?
    all_gains_bound = all(r.get("bound") == "True" for r in gains)
    print(f"  every real gain is on a bound feeder:   {all_gains_bound}  (the gate is exact)")
    if bound:
        print(f"  P(real gain | bound):                   {len(gains)}/{len(bound)} = "
              f"{100*len(gains)/len(bound):.0f}%")

    # can request/headroom predict boundness cheaply (before a joint study)?
    print(f"\n  request/headroom ratio as a pre-study predictor of boundness:")
    for thr in (1.0, 1.05, 1.1, 1.25):
        pred_bound = [r for r in rows if f(r, "req_over_skip", 0) > thr]
        if not pred_bound:
            continue
        tp = sum(1 for r in pred_bound if r.get("bound") == "True")
        actual_bound = len(bound)
        prec = tp / len(pred_bound)
        rec = tp / actual_bound if actual_bound else float("nan")
        print(f"    rule 'req/headroom > {thr:.2f}': flags {len(pred_bound):3d} feeders, "
              f"precision={prec*100:3.0f}% recall={rec*100:3.0f}%")

    # what distinguishes the gainers?
    if gains:
        print(f"\n  gainers' features (median): req/headroom="
              f"{statistics.median(f(r,'req_over_skip') for r in gains):.2f}, "
              f"skip_saturation={statistics.median(f(r,'skip_saturation') for r in gains):.2f}, "
              f"binding={'/'.join(sorted(set(r.get('binding','?') for r in gains)))}")
        others = [r for r in rows if r.get("classification") != "real_gain"]
        print(f"  non-gainers' features (median): req/headroom="
              f"{statistics.median(f(r,'req_over_skip') for r in others):.2f}, "
              f"skip_saturation={statistics.median(f(r,'skip_saturation') for r in others):.2f}")

    # THE SECOND GATE: among bound feeders, does binding TYPE predict the gain?
    print(f"\n  constraint-type gate (among the {len(bound)} bound feeders):")
    for btype in ("voltage", "thermal"):
        b = [r for r in bound if r.get("binding") == btype]
        g = [r for r in b if r.get("classification") == "real_gain"]
        if b:
            print(f"    bound & {btype:7s}: {len(b)} feeders, {len(g)} gain "
                  f"({'all gain' if len(g)==len(b) else 'none/some gain'})")
    print("    -> voltage-limited feeders are where the joint advantage lives; a")
    print("       thermal bottleneck (one shared trunk) leaves little subset freedom.")


def sweep_magnitude() -> None:
    paths = sorted(glob.glob(str(OUT / "m23_request_sensitivity_*.csv")))
    if not paths:
        print("\n  (no m23_request_sensitivity_*.csv -- run experiment 54 --save first)")
        return
    print(f"\nCONTROLLED SWEEP -- how the margin scales (from {len(paths)} network(s)):")
    for path in paths:
        name = Path(path).stem.replace("m23_request_sensitivity_", "")
        rows = load(path)
        # bin by boundness and by concentration
        bound_cells = [r for r in rows if f(r, "frac_bound") >= 0.99]
        unbound_cells = [r for r in rows if f(r, "frac_bound") <= 0.01]
        print(f"  [{name}]")
        if unbound_cells:
            print(f"    fully-unbound cells: {len(unbound_cells)}  "
                  f"mean gap={statistics.mean(f(r,'gap_pct') for r in unbound_cells):+.2f}%  "
                  f"(should be ~0 -- FCFS suffices)")
        if bound_cells:
            gaps = [f(r, "gap_pct") for r in bound_cells]
            print(f"    fully-bound cells:   {len(bound_cells)}  "
                  f"mean gap={statistics.mean(gaps):+.1f}%  max={max(gaps):+.1f}%")
            # concentration effect: clustered vs spread among bound cells
            wf = sorted(set(f(r, "window_frac") for r in bound_cells))
            if len(wf) >= 2:
                clus = [f(r, "gap_pct") for r in bound_cells if f(r, "window_frac") == wf[0]]
                spr = [f(r, "gap_pct") for r in bound_cells if f(r, "window_frac") == wf[-1]]
                if clus and spr:
                    print(f"    clustered (w={wf[0]:.2f}) mean gap={statistics.mean(clus):+.1f}%  vs  "
                          f"spread (w={wf[-1]:.2f}) mean gap={statistics.mean(spr):+.1f}%")


def main() -> None:
    print("M26: regime characterization -- a predictive 'is joint search worth it' rule\n")
    corpus_gate()
    sweep_magnitude()
    print("\n" + "=" * 66)
    print("PROPOSED RULE (fill final thresholds from the numbers above):")
    print("  1. GATE (dominant, robust): run the joint search only where the queue")
    print("     plausibly binds -- request/headroom > ~1 (aggregate requested capacity")
    print("     exceeds what a skip-continue FCFS walk can already seat). Below that,")
    print("     the joint answer equals FCFS; do not pay for the study.")
    print("  2. MAGNITUDE grows with pressure (req/headroom) and with fewer/larger")
    print("     requests. Spatial concentration is a SECONDARY, binding-type-dependent")
    print("     modulator (on voltage-limited feeders SPREAD requests give the larger")
    print("     gap, not clustered) -- do not use it as a general prioritization rule.")
    print("=" * 66)


if __name__ == "__main__":
    main()
