# Empirical interconnection-request distribution (P2)

**What this is.** The real size distribution of distribution-connected interconnection
requests, replacing the synthetic `uniform[10, 100] kW` band used in every allocation
experiment up to M44. It is what Appendix A's arrival-order results are measured
against; see `docs/logs/arrival-order.md`.

## Source

**California DG Stats — Interconnected Applications Data Set** (CPUC Decision 14-11-001;
PG&E, SCE, SDG&E; published monthly by the CA IOUs).

- Portal: <https://www.californiadgstats.ca.gov/downloads/>
- Full set (149 MB): <https://www.californiadgstats.ca.gov/download/interconnection_rule21_applications/>
- Data key: <https://www.californiadgstats.ca.gov/get_file/interconnected_applications_key/>

**File actually used:** `applications_all_6_month.zip` → `applications_all_6_month.csv`
(48 MB, 91,599 rows), the site's rolling 6-month export, downloaded 2026-07-12. The full
1982–present set was not needed: the 6-month export already yields **70,665 PV
applications**, which is ample for a size distribution.

**Why this dataset and not LBNL's *Queued Up*.** LBNL's queue data — the obvious first
thought, and what a reviewer suggested — **"only includes requests seeking to connect to
the *transmission* grid (not distribution-connected or behind-the-meter projects)."**
Every feeder in this project (ENWL LV, SMART-DS, EPRI Ckt7/Ckt24) is *distribution*.
Wrong scope. The CA Rule 21 / NEM set is a literal **distribution-scale interconnection
queue**, one row per request, with the request size *and* the arrival date.

## Derived artefacts in this directory

| File | Contents |
|---|---|
| **`ca_pv_request_sizes.csv.gz`** | **The empirical sample itself** — all 70,665 request sizes (kW) with sector. **This is the one to sample** (with replacement), via `sample_kw()` in `experiments/73`. |
| `ca_pv_size_quantiles.csv` | 199-point quantile grid (0.5%–99.5%). **Reference only — do NOT sample from it.** |
| `ca_pv_sector_mix.csv` | Customer-sector mix (count / median / mean kW / share). |

> **Why the sample and not the quantile table.** A 199-point quantile grid caps at p99.5
> ≈ 29 kW, so inverse-transform sampling from it **destroys the heavy tail** — the
> 100–800 kW commercial systems that are the entire reason the synthetic band was wrong.
> It would have quietly rebuilt the very bias this dataset exists to remove. Resampling
> the actual values reproduces the tail exactly (verified: sampled max 999 kW, top 1% of
> draws carrying 33.1% of kW, matching the source).

Filter: `Technology Type` contains "Photovoltaic", `0 < System Size AC ≤ 1000` kW
(n = **70,665**; `App Received Date` spans 2020-04-20 … 2026-04-29). **No date filter is
applied** — restricting to 2025+ drops most of the commercial tail (1,399 → 409 requests)
and distorts the mix. The raw 48 MB CSV is **not** committed.

## What the data says — and why it invalidates the synthetic band

| | Synthetic `uniform[10,100]` | **Empirical** |
|---|---:|---:|
| median | 55 kW | **6.1 kW** |
| mean | 55 kW | **11.1 kW** |
| p90 | 91 kW | **12.8 kW** |
| p99 | 99 kW | **112.1 kW** |
| share ≤ 20 kW | 0% | **96.8%** |
| share inside [10, 100] kW | 100% | **18.8%** |
| shape | flat | **skew 14.7**; the **top 1% of requests carry 33% of all requested kW** |

**The real queue is ~97% residential rooftop at a median of 5.9 kW, plus a thin, heavy
tail of commercial systems running to 1 MW.** The synthetic band contains under a fifth
of real requests and misrepresents both the centre and the tail.

**This directly threatens M43's headline sort-key result.** "Smallest-first recovers
88–93% of the optimum" was measured on a bounded uniform band — exactly the distribution
that would flatter a take-the-small-ones rule. Under a heavy-tailed distribution where a
handful of requests carry a third of the kW, deferring the tail is far more costly. **The
sort-key experiment (E2) must be re-run against this distribution before any policy claim
is made.**

## Two facts that bear directly on the paper's assumptions

1. **Arrival order is *not* independent of size.** Spearman(arrival rank, size) =
   **−0.21** over all 70,665 requests — weak, but real at this n, and *negative*:
   later arrivals are slightly **smaller**. E1's uniform random permutation is therefore a
   reasonable approximation but **not exact**, and must be labelled a model. (It also
   means "big developers move first" is directionally right, which matters because
   largest-first is *worse than random* on both feeders.)
2. **74.8% of PV requests also request storage** (median 11.5 kW where present). Our
   request model is PV-only, constant-power, single-snapshot export. On this evidence
   that is now the *minority* case in the real queue, and it is a live threat to external
   validity — bigger, arguably, than the voltage-limit question.

## Known limitations of the source

- **Pending and rejected applications are excluded** (the set covers applications that
  interconnected or were later decommissioned). So this is the distribution of requests
  that *made it through*, not of everything submitted. For distribution-scale NEM,
  rejection is uncommon, so the bias on the size distribution is mild — but we never
  observe a withdrawn request, so it cannot by itself support a claim about queue
  *attrition*.
- **~17% of rows are malformed** (embedded commas; `Expected 115 fields, saw 118`) and are
  skipped by the parser. No reason to think the malformed rows are size-biased, but this
  is not verified.
- **California, not the EPRI feeders' own territory.** Ckt7/Ckt24 are anonymised EPRI
  Green Circuits; we do not know their utility. This is a US distribution-scale proxy, not
  the feeders' own queue.
- Finest location field is **ZIP** — there is no circuit/feeder identifier, so requests
  cannot be mapped to a real feeder. We use the distribution, and keep assigning buses on
  our own feeders as before.

## Reproduce

```sh
# download applications_all_6_month.zip from the portal above, then:
uv run python experiments/73_empirical_request_distribution.py --save
```

## Licence

The three derived files in this directory are licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); attribute using
[`../../CITATION.cff`](../../CITATION.cff). The grant covers this derived
summary, not the underlying CPUC data set, which is public and cited above.
See [`../../LICENSES.md`](../../LICENSES.md) for the rest of the archive.
