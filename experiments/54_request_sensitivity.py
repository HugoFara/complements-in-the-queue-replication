"""M23: WHEN does joint allocation beat FCFS? A request-distribution phase diagram.

M22 shows the joint-vs-serial gap is ~zero on most corpus feeders -- but only
under one fixed request distribution (uniform 3-10 kWp, K<=50, spatially spread,
seed 0). That gap is not a property of the method; it is a property of the
*request set*. This experiment makes the request distribution the independent
variable and maps the regimes where joint search materially beats the honest
skip-and-continue FCFS baseline, and where FCFS already suffices.

Three axes, the ones a utility actually varies between clusters:

  * **Pressure** -- how much total capacity the queue asks for, swept via mean
    request size (kWp) at fixed count. Normalized per feeder as the
    request/headroom ratio (total requested / what skip-continue can seat), so
    the transition is comparable across feeders.
  * **Spatial concentration** -- whether requests pile onto electrically-close
    buses (one stressed lateral) or spread across the feeder. Controlled by a
    contiguous window in distance-from-source order: window fraction 1.0 = whole
    feeder (spread), small = a tight electrical cluster. No bus coordinates are
    needed -- distance is summed line length along the graph path to the slack.
  * **Count** -- number of requests K at fixed total pressure (many small vs.
    few large), a secondary 1-D sweep.

Search is the fast AC-reverified convex route (`lindistflow_search_iterative`),
so a dense grid is affordable; because it is a lossy relaxation it can only
*under*-report the joint advantage, making every "joint helps here" cell a
conservative lower bound. A handful of cells are NSGA-II spot-checked. Each
cell averages over several request-draw seeds to smooth sampling noise.

Run:  uv run python experiments/54_request_sensitivity.py [--network eulv|enwl] [--seeds N] [--save]
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
import time
from pathlib import Path

import networkx as nx
import pandapower.topology as ppt
from gridsynth import import_opendss

from allocation import (
    AllocationProblem,
    Request,
    lindistflow_search_iterative,
    nsga2_search,
    skip_continue_baseline,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"

NETWORKS = {
    "eulv": REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss",
    "enwl": CACHE / "network_15" / "Feeder_4" / "Master_snapshot.dss",
    "enwl_small": CACHE / "network_1" / "Feeder_4" / "Master_snapshot.dss",
}


def bus_distances(net) -> dict[int, float]:
    """Electrical distance (summed line length, km) from the slack bus to every
    bus, along the network graph -- a coordinate-free proxy for 'where on the
    feeder' used to build spatially clustered vs. spread request sets."""
    g = ppt.create_nxgraph(net, respect_switches=True, calc_branch_impedances=False)
    slack = int(net.ext_grid.bus.iloc[0])
    # graph edges carry a 'weight' (line length km, 0 for trafos/switches)
    lengths = nx.single_source_dijkstra_path_length(g, slack, weight="weight")
    return {b: lengths.get(b, float("inf")) for b in net.load.bus.unique()}


def build_requests(load_buses, dist, k, mean_kw, window_frac, seed) -> list[Request]:
    """K rooftop-PV export requests. ``mean_kw`` sets the size band (uniform
    +-40% around the mean). ``window_frac`` in (0,1] sets spatial concentration:
    load buses are ordered by distance-from-source and a contiguous window of
    ``window_frac`` of them is chosen (at a seeded random offset), then K buses
    are sampled from that window -- window_frac=1.0 spreads across the whole
    feeder, small window_frac clusters them electrically together."""
    rng = random.Random(seed)
    ordered = sorted(load_buses, key=lambda b: dist[b])
    n = len(ordered)
    win = max(k, int(round(window_frac * n)))
    win = min(win, n)
    start = rng.randint(0, n - win)
    window = ordered[start:start + win]
    buses = rng.sample(window, k)
    lo, hi = mean_kw * 0.6, mean_kw * 1.4
    return [Request(id=f"pv{i}", bus=b, p_mw=-rng.uniform(lo, hi) / 1000.0, queue_order=i)
            for i, b in enumerate(buses)]


def eval_cell(net, load_buses, dist, k, mean_kw, window_frac, seeds) -> dict:
    """Average the joint-vs-skip margin over ``seeds`` request draws at one grid
    cell."""
    gaps, skips, joints, bounds, reqs_kw = [], [], [], [], []
    for s in seeds:
        requests = build_requests(load_buses, dist, k, mean_kw, window_frac, s)
        problem = AllocationProblem(net=net, requests=requests)
        skip = skip_continue_baseline(problem)
        try:
            joint = lindistflow_search_iterative(problem).result
        except Exception:
            continue
        if not joint.feasible:
            continue
        skip_kw = skip.accommodated_mw * 1000
        joint_kw = joint.accommodated_mw * 1000
        skips.append(skip_kw)
        joints.append(joint_kw)
        gaps.append((joint_kw - skip_kw) / skip_kw * 100 if skip_kw > 0 else 0.0)
        bounds.append(len(skip.accepted_ids) < k)
        reqs_kw.append(sum(abs(r.p_mw) for r in requests) * 1000)
    if not gaps:
        return {}
    return dict(k=k, mean_kw=mean_kw, window_frac=window_frac,
               gap_pct=statistics.mean(gaps), gap_pct_sd=statistics.pstdev(gaps),
               skip_kw=statistics.mean(skips), joint_kw=statistics.mean(joints),
               frac_bound=statistics.mean(1.0 if b else 0.0 for b in bounds),
               req_over_headroom=statistics.mean(reqs_kw) / statistics.mean(skips) if statistics.mean(skips) else float("inf"),
               n_seeds=len(gaps))


def heatmap(cells, mean_kws, window_fracs) -> None:
    """Print the (concentration x pressure) grid as an ASCII heatmap of the
    joint-vs-skip margin %."""
    def sym(g):
        if g is None:
            return "  . "
        if g < 0.1:
            return "  · "  # gap vanishes -- FCFS suffices
        if g < 2:
            return f"{g:4.1f}"
        if g < 10:
            return f"{g:4.1f}"
        return f" {min(g,999):3.0f}"
    lut = {(c["mean_kw"], c["window_frac"]): c["gap_pct"] for c in cells}
    print("\n  joint-vs-skip margin % :   rows = spatial concentration (top=clustered), cols = mean request kWp")
    header = "  window\\kWp " + "".join(f"{m:5.0f}" for m in mean_kws)
    print(header)
    for w in window_fracs:
        cellstr = "".join(sym(lut.get((m, w))) + " " for m in mean_kws)
        tag = "clustered" if w <= window_fracs[0] + 1e-9 else ("spread" if w >= window_fracs[-1] - 1e-9 else "")
        print(f"  w={w:4.2f}    {cellstr}  {tag}")
    print("  ( · = <0.1% : FCFS already suffices )")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=NETWORKS, default="eulv")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--k", type=int, default=40)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--nsga2-check", action="store_true", help="NSGA-II spot-check a few cells")
    args = ap.parse_args()

    master = NETWORKS[args.network]
    net = import_opendss(str(master))
    load_buses = list(net.load.bus.unique())
    dist = bus_distances(net)
    k = min(args.k, len(load_buses))
    seeds = list(range(args.seeds))
    print(f"M23: request-sensitivity phase diagram -- {args.network} "
          f"({len(net.bus)} buses, {len(load_buses)} load buses, K={k}, {args.seeds} draws/cell)\n")

    mean_kws = [3, 6, 10, 15, 22, 30]
    window_fracs = [round(x, 2) for x in (max(k / len(load_buses), 0.1), 0.25, 0.5, 1.0)]
    window_fracs = sorted(set(window_fracs))

    cells = []
    t0 = time.time()
    for w in window_fracs:
        for m in mean_kws:
            c = eval_cell(net, load_buses, dist, k, m, w, seeds)
            if c:
                cells.append(c)
                print(f"  w={w:4.2f} mean={m:2d}kWp  bound={c['frac_bound']*100:3.0f}%  "
                      f"skip={c['skip_kw']:6.1f}  joint={c['joint_kw']:6.1f}  "
                      f"gap={c['gap_pct']:+5.1f}% (+-{c['gap_pct_sd']:.1f})  req/headroom={c['req_over_headroom']:.2f}")
    print(f"\n  grid done in {time.time()-t0:.0f}s")
    heatmap(cells, mean_kws, window_fracs)

    # secondary: count sweep at fixed total pressure (many small vs few large)
    print("\n  count sweep (fixed ~total pressure, spread w=1.0): many small vs few large")
    total_target = 30 * k  # kW, the high-pressure corner
    for kk in sorted(set([max(5, k // 4), k // 2, k, min(len(load_buses), k * 2)])):
        mean = total_target / kk
        c = eval_cell(net, load_buses, dist, kk, mean, 1.0, seeds)
        if c:
            print(f"    K={kk:3d} mean={mean:4.0f}kWp  bound={c['frac_bound']*100:3.0f}%  "
                  f"gap={c['gap_pct']:+5.1f}%  req/headroom={c['req_over_headroom']:.2f}")

    if args.nsga2_check:
        print("\n  NSGA-II spot-check (does convex under-report the joint advantage?):")
        for (m, w) in [(mean_kws[-1], window_fracs[0]), (mean_kws[len(mean_kws)//2], window_fracs[-1])]:
            requests = build_requests(load_buses, dist, k, m, w, 0)
            problem = AllocationProblem(net=net, requests=requests)
            skip = skip_continue_baseline(problem)
            conv = lindistflow_search_iterative(problem).result
            pop, gen = (30, 30) if len(net.bus) > 1000 else (40, 40)
            res = nsga2_search(problem, pop_size=pop, n_gen=gen, seed=0)
            feas = [r for r in res if r.feasible]
            nsga = max(feas, key=lambda r: r.accommodated_mw).accommodated_mw * 1000 if feas else float("nan")
            sk = skip.accommodated_mw * 1000
            print(f"    mean={m}kWp w={w}: skip={sk:.1f}  convex={conv.accommodated_mw*1000:.1f} "
                  f"({(conv.accommodated_mw*1000-sk)/sk*100:+.1f}%)  nsga2={nsga:.1f} "
                  f"({(nsga-sk)/sk*100:+.1f}%)")

    if args.save and cells:
        (REPO / "outputs").mkdir(exist_ok=True)
        path = REPO / "outputs" / f"m23_request_sensitivity_{args.network}.csv"
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cells[0].keys()))
            w.writeheader()
            w.writerows(cells)
        print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
