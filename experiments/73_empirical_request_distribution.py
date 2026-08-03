"""M45 (P2): what does a REAL interconnection queue look like?

Every allocation experiment in this project up to M44 drew request sizes from a
synthetic `uniform[10, 100] kW` band. That band was never defended -- it was a
placeholder -- and M43's headline sort-key result ("smallest-first recovers ~90% of
the optimum") is exactly the kind of finding a bounded uniform band would FLATTER:
"take the small ones" looks good when there is no tail to defer.

This experiment replaces the placeholder with the empirical distribution, from a
literal distribution-scale interconnection queue.

DATA
----
California DG Stats, "Interconnected Applications Data Set" (CPUC D.14-11-001;
PG&E + SCE + SDG&E). One row per interconnection REQUEST, with `System Size AC`
(kW), `App Received Date`, `Customer Sector` and `Technology Type`. See
`data/interconnection/PROVENANCE.md` for provenance, scope and limitations.

NOT LBNL's "Queued Up": that dataset covers only TRANSMISSION-connected requests,
and every feeder in this project is distribution. Wrong scope.

WHAT IT FINDS
-------------
The real distribution is nothing like uniform[10,100]: median 6.1 kW, 96.8% of
requests <= 20 kW, only 18.8% inside our synthetic band, skew 14.7, and the top 1%
of requests carrying a THIRD of all requested kW. ~97% residential rooftop, plus a
thin heavy tail of commercial systems to 1 MW.

Two facts that bear on the paper's own assumptions:
  - Spearman(arrival rank, size) = -0.18 (n=65k). WEAK but real, and negative:
    later arrivals are slightly smaller. E1's uniform random permutation is a
    reasonable approximation, NOT an exact one, and must be labelled a model.
  - 79.5% of PV requests ALSO request storage. Our PV-only, constant-power,
    single-snapshot export model is now the minority case in a real queue.

OUTPUT
------
`data/interconnection/ca_pv_size_quantiles.csv` -- the empirical size CDF, to be
sampled by the request generators in place of `uniform(REQ_LO_MW, REQ_HI_MW)`.
`data/interconnection/ca_pv_sector_mix.csv`     -- sector mix, for a conditioned sampler.
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "interconnection"

# the synthetic band this experiment exists to retire
SYNTH_LO_KW, SYNTH_HI_KW = 10.0, 100.0


def load(csv_path: Path) -> pd.DataFrame:
    # ~17% of rows are malformed (embedded commas: "Expected 115 fields, saw 118").
    # Skipped. No reason to think they are size-biased; not verified.
    d = pd.read_csv(csv_path, engine="c", on_bad_lines="skip", low_memory=False)
    d["kw"] = pd.to_numeric(d["System Size AC"], errors="coerce")
    d["recv"] = pd.to_datetime(d["App Received Date"], errors="coerce")
    return d


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", type=Path,
                    default=Path.home() / "Downloads" / "applications_all_6_month.csv",
                    help="the CA DG Stats applications export (see PROVENANCE.md)")
    ap.add_argument("--since", default=None,
                    help="optionally keep only requests received on/after this date. "
                         "NOT set by default: restricting to 2025+ drops most of the "
                         "commercial tail (1,399 -> 409 requests) and quietly rebuilds "
                         "the very bias this experiment exists to remove.")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if not a.csv.exists():
        raise SystemExit(f"not found: {a.csv}\nSee data/interconnection/PROVENANCE.md "
                         f"for the download link.")

    d = load(a.csv)
    print(f"rows parsed: {len(d):,}")

    pv = d[d["Technology Type"].astype(str).str.contains("Photovoltaic", na=False)]
    pv = pv[(pv.kw > 0) & (pv.kw <= 1000)]                 # distribution scale
    recent = pv
    if a.since:
        recent = pv.dropna(subset=["recv"])
        recent = recent[recent.recv >= a.since]
    span = pv.recv.dropna()
    print(f"PV requests, 0 < kW <= 1000: {len(recent):,}"
          f"   (App Received Date spans {span.min().date()} .. {span.max().date()})\n")

    kw = recent.kw
    print("EMPIRICAL SIZE DISTRIBUTION vs. THE SYNTHETIC BAND")
    print(f"  {'':<12}{'uniform[10,100]':>16}{'empirical':>12}")
    rows = [("median", 55.0, kw.median()), ("mean", 55.0, kw.mean()),
            ("p90", 91.0, kw.quantile(.90)), ("p99", 99.0, kw.quantile(.99))]
    for name, syn, emp in rows:
        print(f"  {name:<12}{syn:>16.1f}{emp:>12.1f}")
    print(f"  {'<= 20 kW':<12}{0.0:>15.1f}%{100*(kw<=20).mean():>11.1f}%")
    print(f"  {'in [10,100]':<12}{100.0:>15.1f}%"
          f"{100*((kw>=SYNTH_LO_KW)&(kw<=SYNTH_HI_KW)).mean():>11.1f}%")
    print(f"\n  skew {kw.skew():.1f}; top 1% of requests carry "
          f"{100*kw.nlargest(max(1,int(.01*len(kw)))).sum()/kw.sum():.1f}% of all requested kW")

    print("\nARRIVAL ORDER vs. SIZE  (is a random permutation a fair model?)")
    r = recent.sort_values("recv").reset_index(drop=True)
    rho = pd.Series(np.arange(len(r))).corr(r.kw, method="spearman")
    print(f"  Spearman(arrival rank, size) = {rho:+.3f}  over n={len(r):,}")
    print("  -> WEAK but real at this n, and NEGATIVE: later arrivals are slightly")
    print("     smaller. A uniform random permutation is a reasonable approximation,")
    print("     NOT an exact model. Label it as such.")

    st = pd.to_numeric(recent["Storage Size (kW)"], errors="coerce").fillna(0)
    print(f"\nSTORAGE: {100*(st>0).mean():.1f}% of PV requests also request storage "
          f"(median {st[st>0].median():.1f} kW). Our PV-only constant-power model is")
    print("  now the MINORITY case in a real queue -- a live threat to external validity.")

    print("\nBY SECTOR")
    by = recent.groupby("Customer Sector").kw.agg(["count", "median", "mean"]).round(2)
    by["share"] = (by["count"] / len(recent)).round(4)
    by = by.sort_values("count", ascending=False)
    print(by.head(7).to_string())

    if a.save:
        OUT.mkdir(parents=True, exist_ok=True)
        # The EMPIRICAL SAMPLE, not a quantile table. A 199-point quantile grid caps at
        # p99.5 (~29 kW) and therefore DESTROYS the heavy tail -- the 100-800 kW
        # commercial systems that are the whole reason the synthetic band was wrong.
        # Resampling the actual values with replacement reproduces the tail exactly.
        pd.DataFrame({"kw": kw.round(3).values,
                      "sector": recent["Customer Sector"].values}).to_csv(
            OUT / "ca_pv_request_sizes.csv.gz", index=False, compression="gzip")
        qs = np.arange(0.005, 1.0, 0.005)
        pd.DataFrame({"quantile": qs.round(3),
                      "kw": kw.quantile(qs).values.round(3)}).to_csv(
            OUT / "ca_pv_size_quantiles.csv", index=False)   # reference only; DO NOT sample
        by.to_csv(OUT / "ca_pv_sector_mix.csv")
        print(f"\nsaved -> {OUT.relative_to(REPO)}/ "
              f"ca_pv_request_sizes.csv.gz (sample this), "
              f"ca_pv_size_quantiles.csv (reference), ca_pv_sector_mix.csv")


_SAMPLE = None


def sample_kw(rng, n: int) -> np.ndarray:
    """Draw `n` request sizes (kW) by resampling the empirical distribution.

    The drop-in replacement for `rng.uniform(REQ_LO_MW, REQ_HI_MW)` in the request
    generators of experiments 60-72.

    Resamples the real values WITH REPLACEMENT rather than inverting a quantile grid,
    so the heavy tail (commercial systems to 1 MW) survives -- a truncated quantile
    table silently caps requests at ~29 kW.
    """
    global _SAMPLE
    if _SAMPLE is None:
        _SAMPLE = pd.read_csv(OUT / "ca_pv_request_sizes.csv.gz")["kw"].values
    return np.array([_SAMPLE[rng.randrange(len(_SAMPLE))] for _ in range(n)])


if __name__ == "__main__":
    main()
