"""M85 -- DOES NET-EXPORT CONTROL RESCUE A CO-LOCATED ACCEPT-SET?

THE CARVE-OUT THIS TESTS
------------------------
Section 6.2 of the paper asserts, and does not model, that the co-located cells are reported
for "dispatchable generation under net-export control" -- the Energy Park object the DOE notice
and Ireland's rule are about, where a control mechanism holds the cancellation at all hours by
construction. That carve-out is load-bearing: the cells carrying the irreducible residual
(Ckt24 and SMART-DS, co-located) are the SAME cells the diurnal replay shows to be most fragile
in time, violating the voltage band at both corners of the daily cycle. Asserting the control
fixes it is not evidence that it does.

THE MODEL
---------
A co-located site holds one generation request and one load request, each rated `size_kw`. The
network only ever sees their SUM. Uncontrolled, that sum ranges over the full box:

    net = P_load - P_gen  in  [-size_kw, +size_kw]

  net = -size_kw : generation at nameplate, load off  (midday PV, the overvoltage corner)
  net = +size_kw : load at nameplate, generation off  (evening, the undervoltage corner)

A net-exchange band of width beta constrains every accepted site to |net| <= beta * size_kw,
with beta = 0 perfect cancellation (the site is electrically invisible and the feeder sees only
its own native load) and beta = 1 no control at all. Note the control needs generation that can
be DISPATCHED UP, not merely curtailed: curtailment alone cannot serve the evening corner, which
is exactly why Ireland requires the onsite generation to be dispatchable rather than just
present. We model the band, not the resource that delivers it.

WHY CHECKING THE TWO EXTREMES SUFFICES
-------------------------------------
The accept-set enters the power flow only through the per-site net injections, so a band on
those injections bounds the whole family of admissible dispatches. We test both uniform extremes
(every site at -beta*size, every site at +beta*size). Feasibility at both is the conservative
answer for that band: it is the worst case for the overvoltage side and for the undervoltage
side respectively, and mixed dispatches sit between them.

WHAT IT REPORTS
---------------
The two directions SEPARATELY, because they are not symmetric and a single band hides the
finding. beta_export is how far every site may swing towards export, beta_import towards
import, each the widest value keeping the accept-set AC-feasible. Read as a connection
condition: "hold each co-located site between -beta_export and +beta_import times rated".

The asymmetry is structural rather than incidental. The accept-set is certified at the JOINT
OPTIMUM, so the feeder has been packed until something binds. Whatever binds on the load side
-- a service transformer at its cap, a substation at its non-worsening reference -- leaves no
import headroom at all, while the export side may still have room. A band reported as a single
symmetric number would average those two and describe neither. Which constraint fails, and in
which direction, is recorded per row.

Run:
  uv run python experiments/111_net_export_control.py --families ckt24 --save
  uv run python experiments/111_net_export_control.py --families swiss55 \
      --mat cache/swiss/55nodes_real_swiss_distribution_grid.mat --save
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

_c = __import__("68_ckt7_gldf_headline")
_alt = __import__("109_alternating_rounds")

OUT = Path(__file__).resolve().parents[1] / "outputs"

BETAS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def site_net_projects(accepted, beta, size_kw, sign):
    """Replace each accepted CO-LOCATED PAIR by one net injection at its site.

    `accepted` is the certified accept-set; a site is a bus carrying both signs. Sites that
    ended up with only one sign accepted are passed through at their own full rating, since no
    partner exists there to cancel against and a band cannot be met by cancellation.
    """
    # WHAT COUNTS AS ONE SITE. On the pandapower families `p.bus` is the attach point, so
    # co-located requests share it. On the SMART-DS oracle `p.bus` is a per-project INDEX and
    # the real attach point lives in `gldf_key` (bus name plus phase) -- grouping on `p.bus`
    # there would put every request on its own "site" and pair nothing.
    def site_of(p):
        return getattr(p, "gldf_key", None) or int(p.bus)

    by_bus: dict = {}
    for p in accepted:
        by_bus.setdefault(site_of(p), []).append(p)

    out = []
    for _site, ps in by_bus.items():
        bus = int(ps[0].bus)
        has_gen = any(p.p_mw < 0 for p in ps)
        has_load = any(p.p_mw > 0 for p in ps)
        if has_gen and has_load:
            mag = beta * size_kw / 1000.0          # controllable pair: band applies
        else:
            mag = sum(abs(p.p_mw) for p in ps)     # unpaired: uncontrollable, full rating
        if mag <= 0:
            continue
        p = _c.Project(id=f"net{bus}", bus=bus, p_mw=sign * mag)
        # The OpenDSS oracle keys its staged circuit and GLDF columns off attributes the
        # pandapower Project does not carry. A synthetic net injection stands at the same
        # attach point as the pair it replaces, so it inherits them from either half.
        for attr in ("bus_name", "phase", "gldf_key"):
            if hasattr(ps[0], attr):
                setattr(p, attr, getattr(ps[0], attr))
        out.append(p)
    return out


def _binding(f, info):
    """Which limit the AC solution is at, for the row that failed.

    Written defensively: the SMART-DS oracle is scoped to the voltage axis and declares no
    thermal cap at all, so every limit is read through getattr rather than assumed present.
    """
    out = []
    vmax, vmin = getattr(f, "VMAX_PU", None), getattr(f, "VMIN_PU", None)
    loading = getattr(f, "LOADING_MAX_PCT", None)
    if vmax is not None and info.get("vmax") is not None and info["vmax"] > vmax + 1e-9:
        out.append("vmax")
    if vmin is not None and info.get("vmin") is not None and info["vmin"] < vmin - 1e-9:
        out.append("vmin")
    if loading is not None:
        if info.get("line_pct") is not None and info["line_pct"] > loading + 1e-9:
            out.append("line")
        if info.get("trafo_pct") is not None and info["trafo_pct"] > loading + 1e-9:
            out.append("trafo")
    sub, ref = info.get("sub_pct"), getattr(f, "sub_base_pct", None)
    if sub is not None and ref is not None and sub > ref + 1e-9:
        out.append("substation(non-worsening)")
    return "+".join(out) if out else "(unattributed)"


def feasible_at(fam, f, accepted, beta, size_kw):
    """Both uniform extremes of the band, by exact AC power flow."""
    res = {}
    for sign, name in ((-1.0, "export"), (+1.0, "import")):
        pr = site_net_projects(accepted, beta, size_kw, sign)
        if not pr:
            res[name] = (True, {}, "")
            continue
        fam.reset(f, pr)
        ok, _mw, _vio, info = f.evaluate(f.ctx, pr, {p.id for p in pr})
        res[name] = (bool(ok), info, "" if ok else _binding(f, info))
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--families", nargs="+", default=["ckt24"])
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--betas", type=float, nargs="+", default=BETAS)
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--mat", default=None)
    ap.add_argument("--master", default=None)
    ap.add_argument("--out", default="m85_net_export_control.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M85 -- NET-EXPORT CONTROL ON A CO-LOCATED ACCEPT-SET\n")
    print("  Each accepted co-located site is held to |net| <= beta * rated.")
    print("  beta=0 is perfect cancellation (site invisible); beta=1 is no control.")
    print("  Both uniform extremes are AC-checked; beta_max is the widest feasible band.\n")

    default_k = {"ckt7": 16, "ckt24": 24, "swiss55": 48,
                 "smartds_colocated": 16}
    default_size = {"swiss55": 500.0}
    rows = []
    for fname in a.families:
        fam = _alt.get_family(fname, a.n_jobs, mat=a.mat, master=a.master,
                              placement="colocated")
        k = a.k if a.k else default_k.get(fname, 24)
        size = default_size.get(fname, a.size_kw)
        f = fam.make_feeder()
        print(f"  {fam.desc}   (K={k}, {size:.0f} kW)")
        hdr = (f"  {'seed':>4} {'accepted':>9} | "
               + " ".join(f"{b:>4.1f}" for b in a.betas)
               + " | band      first import failure")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))

        for seed in a.seeds:
            t0 = time.time()
            projects = fam.make_projects(f, seed, k, size)
            fam.reset(f, projects)
            ids, joint_kw = fam.certify(f, projects, a.n_jobs)
            accepted = [p for p in projects if p.id in ids]

            b_exp, b_imp, marks, why = -1.0, -1.0, [], ""
            for b in a.betas:
                r = feasible_at(fam, f, accepted, b, size)
                eok, iok = r["export"][0], r["import"][0]
                if eok:
                    b_exp = b               # betas ascend; last feasible one wins
                if iok:
                    b_imp = b
                if not iok and not why:
                    why = r["import"][2]
                marks.append(("E" if eok else ".") + ("I" if iok else "."))
                rows.append(dict(family=fname, seed=seed, k=k, size_kw=size, beta=b,
                                 joint_kw=joint_kw, n_accepted=len(accepted),
                                 export_ok=eok, import_ok=iok,
                                 export_binding=r["export"][2], import_binding=r["import"][2],
                                 export_vmax=r["export"][1].get("vmax"),
                                 export_trafo_pct=r["export"][1].get("trafo_pct"),
                                 import_vmin=r["import"][1].get("vmin"),
                                 import_trafo_pct=r["import"][1].get("trafo_pct")))
            print(f"  {seed:>4d} {joint_kw:>8.0f}kW | " + "  ".join(marks) +
                  f" | -{max(b_exp,0):.1f}/+{max(b_imp,0):.1f}  {why}", flush=True)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)
            del t0
        print()

    d = pd.DataFrame(rows)
    print("=" * 78)
    print("\n  Admissible band per family, as a fraction of each site's rated capacity\n")
    for fname, g in d.groupby("family", sort=False):
        e, i = [], []
        for _seed, gs in g.groupby("seed"):
            oe, oi = gs[gs.export_ok], gs[gs.import_ok]
            e.append(oe.beta.max() if len(oe) else 0.0)
            i.append(oi.beta.max() if len(oi) else 0.0)
        e, i = np.array(e), np.array(i)
        binds = [b for b in g.import_binding.dropna().unique() if b]
        print(f"  {fname:>18}: export up to {np.median(e):.1f} x rated, "
              f"import up to {np.median(i):.1f} x rated  (medians over {len(e)} draws)")
        if binds:
            print(f"  {'':>18}  import limited by: {', '.join(sorted(binds))}")
    print("\n  The two directions are not symmetric. An accept-set certified at the joint")
    print("  optimum has been packed until something binds, and whatever binds on the load")
    print("  side leaves no import headroom -- so the operable condition is closer to")
    print("  'never draw net power' than to a symmetric band.\n")
    if a.save:
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
