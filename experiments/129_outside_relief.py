"""M101 -- OPERATOR INDEPENDENCE BY EXHAUSTION, NOT BY ASSUMPTION

WHY
---
Proposition 1 says: if the constraint blocking a mixed pair is PRIVATE to that pair, then no
segregated process seats either half at any number of rounds. The paper then asserts privacy
from topology -- the two halves share a point of common coupling on a large feeder -- and
never bounds it. Two referees in a row have refused that, and the third put it sharply:

  "That claim follows from Proposition 1 only under exact privacy, which is asserted from
  topology and never bounded -- and n2f1 is the existence proof that a fixed-point certificate
  does NOT imply operator-independent irreducibility on a shared-constraint feeder. Compute
  the bound you already derived: the maximal relief any approval set outside the pair can
  supply on the blocking constraint, and show it is smaller than each half's shortfall."

The inverse admittance matrix is dense, so exact privacy is FALSE everywhere and only the
magnitude decides. exp121 role A answers this in the linear model: sigma < 1 means the summed
relief of the whole opposite track cannot cover the shortfall. That has two defects. It is a
GLDF quantity, so it inherits the linearization the rest of the certificate refuses to trust
(M42); and its numerator credits every opposite-sign request at once, which no feasible
accept-set can do, so it is a necessary condition reported as if it were the bound.

This file computes the bound itself, with no linear model anywhere:

  for every subset S of the requests outside the pair,
      is S feasible          (is it a base case a segregated process could actually reach?)
      and is S + half feasible   (does that base case seat the half alone?)

by exact AC power flow, exhaustively. If no feasible S seats the half, then the half is
unreachable by ANY segregated process -- not because the constraint is private, but because
every base case outside the pair has been tried. Proposition 1's hypothesis is replaced by a
measurement, and the "no segregated process at any number of rounds" claim stops depending on
an unverified topological premise.

THE RELIEF POOL, AND WHY IT IS NOT SIMPLY THE OPPOSITE TRACK
------------------------------------------------------------
The natural pool is the opposite-sign requests outside the pair: those are what relieve a
constraint the half worsens. Same-sign requests should only compete for the same headroom.
"Should" is not measured, and on Ckt24 it is not even obviously true -- the substation
non-worsening rule is RELIEVED by generation, so a second generation request could in
principle buy off a generation half's substation shortfall while worsening its voltage one.

So the pool is built adaptively. Every same-sign request outside the pair is first tested
alone against the half: if it strictly REDUCES the half's violation, it joins the pool. The
enumeration then covers every subset of (opposite track outside the pair) + (same-sign
helpers). `pool_truncated` records the cells where that exceeded --max-bits and the weakest
helpers were dropped, so a bounded claim is never reported as an exhaustive one.

WHAT EACH ROW MEANS
-------------------
  n_masks              subsets enumerated = 2**pool_bits
  n_base_feasible      of those, how many are base cases a segregated process could stand on
  n_seat               feasible base cases that make the half individually feasible.
                       n_seat = 0 is the certificate. n_seat > 0 REFUTES operator
                       independence for that half and the pair must be reported differently.
  vio_alone            the half's violation with no relief at all (the shortfall)
  vio_best             the smallest violation any feasible base case leaves it at
  relief_max           vio_alone - vio_best, the measured maximum relief. The reviewer's
                       requested comparison is relief_max < vio_alone, i.e. vio_best > 0.

Also recorded per pair, for the n2f1 exception the paper cannot explain: whether the halves
share a bus, their phase relationship, and the hop and metric distance between their buses.

Run:
  uv run python experiments/129_outside_relief.py --cells smartds --save
  uv run python experiments/129_outside_relief.py --cells ckt24_50 --seeds 0 1 2 3 4 --save
  uv run python experiments/129_outside_relief.py --cells n2f1_50 --save
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_alt = __import__("109_alternating_rounds")
_cert = __import__("110_simultaneity_certificate")
_share = __import__("120_certified_share")
_u24 = __import__("125_ckt24_unbalanced")
_uen = __import__("122_enwl_unbalanced")

OUT = Path(__file__).resolve().parents[1] / "outputs"


# ------------------------------------------------------------------------ cells
def cell_ckt24_50(n_jobs):
    """The paper's reference cell: the shipped three-phase model, co-located, 50 kW."""
    f = _u24.Ckt24DSSFeeder(_u24.MASTER)
    fam = _u24.make_family(f, "EPRI Ckt24 UNBALANCED, co-located, 50 kW")
    return fam, f, 24, 50.0


def cell_smartds(n_jobs):
    return _share.cell_smartds(n_jobs)


def _enwl_cell(network, feeder, label):
    """Any ENWL feeder in the arm the published n2f1 cell uses: phase-resolved, RANDOM
    placement, 50 kW, `relax` treatment (limits clamped to the feeder's own baseline)."""
    def build(n_jobs):
        master = _uen.master_for(network, feeder)
        f1 = _uen.ENWLDSSFeeder(master, load_mult=1.0)
        v = _uen.baseline(f1)
        f = _uen.ENWLDSSFeeder(master, load_mult=1.0,
                               vmin=min(0.94, v[0]), vmax=max(1.10, v[1]))
        fam = _uen.make_family(f, f"ENWL {label} UNBALANCED, random placement, 50 kW, relax",
                               placement="random")
        return fam, f, 24, 50.0
    return build


CELLS = {"ckt24_50": cell_ckt24_50, "smartds": cell_smartds,
         "n2f1_50": _enwl_cell("network_2", "Feeder_1", "n2f1"),
         # the pre-registered out-of-sample feeders (docs/papers/enwl-preregistration.md),
         # so the exhaustion test can ask whether their residual is simultaneity or the
         # same operator-level path dependence n2f1's turns out to be
         "n1f1_50": _enwl_cell("network_1", "Feeder_1", "n1f1"),
         "n4f1_50": _enwl_cell("network_4", "Feeder_1", "n4f1"),
         "n18f1_50": _enwl_cell("network_18", "Feeder_1", "n18f1"),
         "n21f1_50": _enwl_cell("network_21", "Feeder_1", "n21f1")}


# ------------------------------------------------------------------- topology
def bus_graph(f):
    """Bus adjacency straight off the DSS context: lines and transformers, undirected.

    Ckt24's feeder already carries `adj`; the ENWL and SMART-DS ones do not, and rebuilding it
    here from the same two element classes keeps one definition of distance across the corpus.
    """
    if getattr(f, "adj", None):
        return {b: [n for n, _k, _nm in nb] for b, nb in f.adj.items()}
    c = f._ctx
    g: dict = {}
    lengths: dict = {}
    for nm in c.Lines.AllNames():
        c.Lines.Name(nm)
        a = c.Lines.Bus1().split(".")[0].upper()
        b = c.Lines.Bus2().split(".")[0].upper()
        g.setdefault(a, []).append(b)
        g.setdefault(b, []).append(a)
        lengths[(a, b)] = lengths[(b, a)] = float(c.Lines.Length())
    for nm in c.Transformers.AllNames():
        c.Circuit.SetActiveElement(f"Transformer.{nm}")
        bs = [x.split(".")[0].upper() for x in c.CktElement.BusNames()]
        for i in range(len(bs) - 1):
            g.setdefault(bs[i], []).append(bs[i + 1])
            g.setdefault(bs[i + 1], []).append(bs[i])
            lengths[(bs[i], bs[i + 1])] = lengths[(bs[i + 1], bs[i])] = 0.0
    f._bus_lengths = lengths
    return g


def hops_between(g, a, b, lengths=None):
    """(hops, metres) on the shortest path; (-1, nan) if the two buses are not connected."""
    a, b = a.upper(), b.upper()
    if a == b:
        return 0, 0.0
    seen, q = {a: (0, 0.0)}, deque([a])
    while q:
        u = q.popleft()
        for v in g.get(u, ()):
            if v in seen:
                continue
            d, m = seen[u]
            seen[v] = (d + 1, m + (lengths.get((u, v), 0.0) if lengths else 0.0))
            if v == b:
                return seen[v]
            q.append(v)
    return -1, float("nan")


def bus_of(p):
    """Bus name as the DSS knows it. Projects carry `gldf_key` = 'BUS.PHASE' on the DSS
    feeders; the pandapower ones carry an integer bus index and no phase."""
    k = getattr(p, "gldf_key", None)
    if k and "." in str(k):
        return str(k).split(".")[0].upper()
    return str(getattr(p, "bus", ""))


def phase_of(p):
    k = getattr(p, "gldf_key", None)
    if k and "." in str(k):
        return str(k).split(".", 1)[1]
    return ""


# -------------------------------------------------------------- the enumeration
_W: dict = {}


def _init_worker(fam, f, projects):
    _W["fam"], _W["f"], _W["projects"] = fam, f, projects
    fam.reset(f, projects)
    f.evaluate(f.ctx, projects, set())          # warm the staged circuit once per worker


def _chunk(args):
    """AC-evaluate a block of accept-sets, returning feasibility and violation for each.

    The circuit is staged once per worker rather than per solve. exp125 documents why that is
    safe here: the only history-dependent reading on this corpus is the substation power sum,
    it drifts by at most 4e-4 percentage points, and the oracle's tolerance is 25x that.
    """
    lo, sets = args
    f, projects = _W["f"], _W["projects"]
    ok = np.zeros(len(sets), dtype=np.uint8)
    vio = np.zeros(len(sets), dtype=float)
    for j, acc in enumerate(sets):
        o, _mw, v, _info = f.evaluate(f.ctx, projects, acc)
        ok[j], vio[j] = bool(o), float(v)
    return lo, ok, vio


def evaluate_sets(fam, f, projects, sets, n_jobs, chunk=64, serial_below=48):
    """Feasibility + violation for an explicit list of accept-sets."""
    n = len(sets)
    ok = np.zeros(n, dtype=np.uint8)
    vio = np.zeros(n, dtype=float)
    if n == 0:
        return ok, vio
    if n <= serial_below or n_jobs <= 1:
        _init_worker(fam, f, projects)
        _lo, o, v = _chunk((0, sets))
        return o, v
    parts = [(i, sets[i:i + chunk]) for i in range(0, n, chunk)]
    ctx = mp.get_context("fork")
    with ctx.Pool(n_jobs, initializer=_init_worker, initargs=(fam, f, projects)) as pool:
        done, mark = 0, 0
        for lo, o, v in pool.imap_unordered(_chunk, parts):
            ok[lo:lo + len(o)] = o
            vio[lo:lo + len(v)] = v
            done += len(o)
            if n > 2000 and done >= mark:      # quarters only; these logs are read later
                print(f"      {done}/{n} solves", flush=True)
                mark += n // 4
    return ok, vio


def relief_certificate(fam, f, projects, half_id, partner_id, n_jobs, max_bits=13):
    """Exhaustive search for a feasible base case outside the pair that seats `half_id`."""
    by = {p.id: p for p in projects}
    sgn = np.sign(by[half_id].p_mw)
    opposite = [p.id for p in projects
                if np.sign(p.p_mw) != sgn and p.id not in (half_id, partner_id)]
    same = [p.id for p in projects
            if np.sign(p.p_mw) == sgn and p.id not in (half_id, partner_id)]

    # 1. the shortfall, and which same-sign requests (if any) relieve rather than compete
    probe = [{half_id}] + [{j, half_id} for j in same]
    ok0, vio0 = evaluate_sets(fam, f, projects, probe, n_jobs)
    vio_alone = float(vio0[0])
    helpers = [(float(vio_alone - vio0[i + 1]), j) for i, j in enumerate(same)
               if vio0[i + 1] < vio_alone - 1e-12]
    helpers.sort(reverse=True)

    # 2. the pool, capped so an exhaustive claim is never reported for a truncated search
    pool = list(opposite) + [j for _r, j in helpers]
    truncated = False
    if len(pool) > max_bits:
        keep = max_bits - len(opposite)
        pool = list(opposite)[:max_bits] + [j for _r, j in helpers[:max(0, keep)]]
        truncated = True

    # 3. every subset of the pool, with and without the half
    n = len(pool)
    masks = range(1 << n)
    subsets = [{pool[i] for i in range(n) if m >> i & 1} for m in masks]
    sets = subsets + [s | {half_id} for s in subsets]
    ok, vio = evaluate_sets(fam, f, projects, sets, n_jobs)
    base_ok, base_vio = ok[:len(subsets)], vio[:len(subsets)]
    with_ok, with_vio = ok[len(subsets):], vio[len(subsets):]

    feas = base_ok.astype(bool)
    seat = feas & with_ok.astype(bool)
    vio_best = float(with_vio[feas].min()) if feas.any() else float("nan")
    best_mask = int(np.argmin(np.where(feas, with_vio, np.inf))) if feas.any() else -1
    return dict(half=half_id, partner=partner_id,
                pool_bits=n, pool_opposite=len(opposite), pool_helpers=len(pool) - len(opposite),
                pool_truncated=truncated, n_masks=len(subsets),
                n_base_feasible=int(feas.sum()), n_seat=int(seat.sum()),
                vio_alone=vio_alone, vio_best=vio_best,
                relief_max=vio_alone - vio_best if feas.any() else float("nan"),
                best_relief_size=int(bin(best_mask).count("1")) if best_mask >= 0 else -1,
                half_alone_ok=bool(ok0[0]))


# ------------------------------------------------------------------- the driver
def run_cell(name, fam, f, k, size_kw, seeds, n_jobs, max_bits, geom=None, save_to=None):
    g = bus_graph(f)
    lengths = getattr(f, "_bus_lengths", None)
    rows = []
    for seed in seeds:
        t0 = time.time()
        projects = fam.make_projects(f, seed, k, size_kw)
        fam.reset(f, projects)

        best, best_ids = -1.0, set()
        for start in ("gen", "load"):
            acc = _cert.fixed_point_accept_set(fam, f, projects, n_jobs, start)
            tot = sum(abs(p.p_mw) for p in acc) * 1000.0
            if tot > best:
                best, best_ids = tot, {p.id for p in acc}
        c = _cert.certificate(fam, f, projects, best_ids)
        mixed = c.pop("_mixed_pairs")
        taken, cert_kw = _share.certified_disjoint_pairs(fam, f, projects, best_ids, mixed)
        print(f"    seed {seed}: fixed point {best:.0f} kW, {len(mixed)} mixed pairs, "
              f"{len(taken)} credited ({cert_kw:.0f} kW), {time.time() - t0:.0f}s")

        by = {p.id: p for p in projects}
        # geometry of EVERY certified mixed pair, credited or not: the paper reports the
        # count (39 on n2f1) and a referee asks what those pairs look like electrically.
        if geom is not None:
            for u, v in sorted(mixed):
                bu, bv = bus_of(by[u]), bus_of(by[v])
                hops, metres = hops_between(g, bu, bv, lengths)
                geom.append(dict(cell=name, seed=seed, pair=f"{u}+{v}",
                                 credited=((u, v) in taken),
                                 bus_a=bu, bus_b=bv, same_bus=(bu == bv),
                                 phase_a=phase_of(by[u]), phase_b=phase_of(by[v]),
                                 same_phase=(phase_of(by[u]) == phase_of(by[v])),
                                 hops=hops, metres=metres))
        if not taken:
            continue

        for u, v in sorted(taken):
            bu, bv = bus_of(by[u]), bus_of(by[v])
            hops, metres = hops_between(g, bu, bv, lengths)
            for half, partner in ((u, v), (v, u)):
                t1 = time.time()
                r = relief_certificate(fam, f, projects, half, partner, n_jobs, max_bits)
                r.update(cell=name, seed=seed, k=k, size_kw=size_kw, pair=f"{u}+{v}",
                         fixed_point_kw=best, certified_kw=cert_kw,
                         bus_a=bu, bus_b=bv, same_bus=(bu == bv),
                         phase_a=phase_of(by[u]), phase_b=phase_of(by[v]),
                         same_phase=(phase_of(by[u]) == phase_of(by[v])),
                         hops=hops, metres=metres, dt=time.time() - t1)
                rows.append(r)
                verdict = "CERTIFIED" if r["n_seat"] == 0 else f"REFUTED ({r['n_seat']})"
                print(f"      {u}+{v} half {half:>6}: {r['n_masks']:>5} subsets, "
                      f"{r['n_base_feasible']:>5} feasible bases, {verdict:>14}  "
                      f"vio {r['vio_alone']:.2e} -> {r['vio_best']:.2e}  "
                      f"[{time.time() - t1:.0f}s]", flush=True)
        # per seed, not per cell: a 13-bit pool is minutes per half, and a run stopped
        # halfway should still leave the seeds it finished on disk
        if save_to is not None and rows:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(save_to, index=False)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cells", nargs="+", default=["smartds"], choices=list(CELLS))
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(10)))
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--max-bits", type=int, default=13,
                    help="cap on the enumerated pool; 2**max_bits AC solves per half")
    ap.add_argument("--out", default="m122_outside_relief.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M101 -- CAN ANY APPROVAL SET OUTSIDE THE PAIR SEAT EITHER HALF?\n")
    print("  Exhaustive over every subset of the requests outside the pair, by exact AC")
    print("  power flow. n_seat = 0 means no segregated process reaches that half at any")
    print("  number of rounds -- measured, not inferred from a privacy assumption.\n")

    rows, geom = [], []
    for name in a.cells:
        t0 = time.time()
        fam, f, k, size_kw = CELLS[name](a.n_jobs)
        print(f"  {fam.desc}  (k={k}, {size_kw:.0f} kW, built in {time.time() - t0:.1f}s)")
        rows += run_cell(name, fam, f, k, size_kw, a.seeds, a.n_jobs, a.max_bits, geom,
                         save_to=(OUT / a.out) if a.save else None)
        if a.save:
            OUT.mkdir(exist_ok=True)
            if rows:
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)
            if geom:
                pd.DataFrame(geom).to_csv(OUT / a.out.replace(".csv", "_pairs.csv"),
                                          index=False)
        print()

    if geom:
        dg = pd.DataFrame(geom)
        print("  GEOMETRY OF EVERY CERTIFIED MIXED PAIR\n")
        print(dg.groupby(["cell", "same_bus"]).agg(
            pairs=("pair", "size"), credited=("credited", "sum"),
            same_phase=("same_phase", "sum"), median_hops=("hops", "median"),
            max_hops=("hops", "max"), median_m=("metres", "median")).to_string())
        print()

    d = pd.DataFrame(rows)
    if not len(d):
        print("  no certified pairs on any draw -- nothing to bound\n")
        return

    print("=" * 92)
    print("\n  SUMMARY\n")
    gr = d.groupby("cell").agg(
        halves=("n_seat", "size"),
        pairs=("pair", "nunique"),
        subsets=("n_masks", "sum"),
        feasible_bases=("n_base_feasible", "sum"),
        seated=("n_seat", "sum"),
        certified=("n_seat", lambda s: int((s == 0).sum())),
        median_relief=("relief_max", "median"),
        truncated=("pool_truncated", "sum"))
    print(gr.to_string())
    print("\n  certified = halves no feasible outside base case seats. Those are")
    print("  operator-independent by exhaustion; Proposition 1 is not needed for them.")
    print("  seated > 0 on any half refutes the claim for its pair.\n")
    if "same_bus" in d:
        print(d.groupby(["cell", "same_bus"]).agg(
            halves=("n_seat", "size"), seated=("n_seat", "sum"),
            median_hops=("hops", "median"), median_m=("metres", "median")).to_string())
        print()
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
