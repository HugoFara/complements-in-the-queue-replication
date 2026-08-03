"""M95 -- RUNNING THE SIGMA SCREEN, IN BOTH OF THE ROLES IT WAS DERIVED FOR

WHY
---
The manuscript defines a relief ratio (Eq. 11) in the blind-classification section, argues it
is the right object, notes it is computable inside the blind information set -- and does not
evaluate it. Both second-round referees refuse that:

  AE reviewer, comment 3: "'That diagnosis names a remedy we did not test' was a defensible
  sentence in a paper without a compute budget; in a paper that enumerates 131,072 AC solves
  per cell, it is not. Run it both ways or delete Eq. (sigma)."

  Adversarial referee, comment 11: "Either evaluate it (including on the co-located cells,
  where it should return sigma < 1 for the private pairs -- a falsifiable prediction of the
  paper's own mechanism) or cut it."

The AE reviewer also points out that one computation closes two flanks, because the
quantitative privacy margin the adversarial referee demands in comment 4 -- "cross-sensitivity
from all seatable remote approvals, summed, against the pair's shortfall" -- IS sigma < 1 with
the relief pool restricted to requests outside the pair. So this file runs one quantity twice:

  ROLE A (privacy certificate).  For each half of each certified mixed pair from exp120, with
  the relief pool = every opposite-sign request OUTSIDE the pair: sigma < 1 says nothing the
  rest of the queue can offer covers that half's shortfall. That is Proposition 1's exact
  hypothesis ("g_c depends only on the pair") replaced by a measured margin, which is what a
  connected network can actually supply -- the inverse admittance matrix is dense, so exact
  privacy is false everywhere and only the magnitude decides.

  ROLE B (blind screen).  On the cells where the two binary tests of the blind section failed,
  with the relief pool = the whole opposite track at each track's solo optimum: does the
  MAGNITUDE test separate Ckt7 from Ckt24 where constraint identity could not? The manuscript
  claims it might. Either it does, and the negative result narrows to binary tests; or it does
  not, and the negative result is about the information set after all. Both are publishable;
  asserting neither is not.

WHAT SIGMA IS, EXACTLY
----------------------
Every GLDF/thermal constraint the MILP enforces is two-sided; split each into one-sided rows
a.x <= b, matching `gldf_milp` term for term (voltage rows carry m_cols * (-p/base_mva)
against (VMAX^2 - v_base) and (VMIN^2 - v_base); thermal rows carry -p over the branch's
subtree against p_base +/- r_lim). For a request i and a row c, at operating point x:

    shortfall_c(i) = a_ci - (b_c - a_c . x)        how far adding i alone pushes past c
    relief_c(i)    = sum_j max(-a_cj, 0)           over the relief pool
    sigma_c(i)     = relief_c(i) / shortfall_c(i)

CONVENTIONS, both of which the AE reviewer's minor comments ask for explicitly:

  * A row with shortfall <= 0 does not block i and is skipped. This is the negative-denominator
    case; sigma is not defined there because there is nothing to relieve.
  * i needs EVERY blocking row relieved, so sigma(i) = min over blocking rows. The reported
    row label is the argmin -- the row that is hardest to buy off.
  * sigma >= 1 is NECESSARY, not sufficient: the numerator credits every opposite-sign request
    at once, which no feasible accept-set can do. So sigma < 1 is the informative verdict
    (definitely unrelievable) and sigma >= 1 only fails to rule relief out.

Run:
  uv run python experiments/121_sigma_screen.py --role privacy --save
  uv run python experiments/121_sigma_screen.py --role blind --save
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

_x = __import__("78_corpus_law")
_alt = __import__("109_alternating_rounds")
_share = __import__("120_certified_share")

OUT = Path(__file__).resolve().parents[1] / "outputs"


def _random_cell(name, k, size_kw=100.0, setpoint=None):
    def build(n_jobs):
        fam = _alt.get_family(name, n_jobs, placement="random", setpoint=setpoint)
        return fam, fam.make_feeder(), k, size_kw
    return build


# The blind section's decisive pair: Ckt7 and lightly-loaded Ckt24 receive identical verdicts
# from both binary tests and have opposite residuals. Those are RANDOM-placement cells, so
# they are not in exp120's co-located map and are added here.
#
# BLIND_CELLS below is exp106's seven-cell corpus, one for one, so the sigma verdict can be
# put beside the strict/relief verdict on the same draw of the same cell. Names and (k,
# size, setpoint) match `106_regime_classifier.CELLS`; the two 1.04/1.05 Ckt7 cells are
# excluded there and here, being outside Ckt7's legal window once the source Thevenin
# impedance is modelled.
#
# CAVEAT, and it is not small. The four ENWL entries build the BALANCED pandapower feeder,
# because that is what exp106 classified and what the residuals in m70_residual_enwl.csv
# were measured on. The paper's ENWL corpus has since been re-based onto the shipped
# three-phase models (exp122), where n13f4 loses its residual and n2f1 gains one. So a sigma
# verdict on these four is a verdict about the blind section as published, not about the
# corpus as it now stands. The decisive pair -- Ckt7 against lightly-loaded Ckt24 -- is
# unaffected, and that pair is what Eq. (11) was proposed to separate.
BLIND_CELLS = dict(
    ckt7_1_06=_random_cell("ckt7", 17, 100.0, setpoint=1.06),
    ckt24_light=_random_cell("ckt24", 24, 100.0),
    ckt24_oversub=_random_cell("ckt24", 44, 100.0),
    enwl_n13f4_100=_random_cell("enwl_n13f4", 24, 100.0),
    enwl_n13f4_50=_random_cell("enwl_n13f4", 24, 50.0),
    enwl_n2f1_100=_random_cell("enwl_n2f1", 24, 100.0),
    enwl_n2f1_50=_random_cell("enwl_n2f1", 24, 50.0),
)

CELLS = dict(_share.CELLS,
             ckt7_random=_random_cell("ckt7", 17),
             ckt24_random=_random_cell("ckt24", 24),
             **BLIND_CELLS)


def has_linear_hooks(f):
    """sigma needs a GLDF linearization and a way to re-anchor it. It does NOT need a thermal
    model: MixedDSSFeeder (SMART-DS) ships no `build_thermal` because that corpus disables
    ampacity outright (max_loading_percent = 1e6), so its constraint set is voltage-only and
    the thermal rows are legitimately empty rather than missing."""
    return all(hasattr(f, h) for h in ("build_gldf", "anchored_context"))


def _get(g, key):
    """Ckt7 and the ENWL families return the GLDF pieces as a dict; Ckt24 returns exp64's
    GLDF dataclass. Same fields, two containers."""
    return g[key] if isinstance(g, dict) else getattr(g, key)


# ------------------------------------------------------------------ the rows
def one_sided_rows(f, ctx, projects, tol=1e-12):
    """Every constraint the MILP enforces, as one-sided rows (label, a, b) meaning a.x <= b.

    Mirrors `68_ckt7_gldf_headline.gldf_milp` exactly -- if that function changes, this one
    must change with it, or sigma is measured against a model the certifier does not use.
    """
    buses = sorted({int(p.bus) for p in projects})
    # MixedDSSFeeder resolves m_cols through `_keys`; it is per-queue, so it must be set for
    # THIS project list or the columns silently come back empty.
    keys = {p.bus: p.gldf_key for p in projects if hasattr(p, "gldf_key")}
    if keys:
        f._keys = keys
    g = f.build_gldf(ctx, buses)
    th = f.build_thermal(ctx, projects) if hasattr(f, "build_thermal") else None
    K = len(projects)

    pq, m_cols = _get(g, "pq"), _get(g, "m_cols")
    base_mva, v_base = _get(g, "base_mva"), _get(g, "v_base")
    A = np.zeros((len(pq), K))
    for r, p in enumerate(projects):
        col = m_cols.get(int(p.bus))
        if col is not None:
            A[:, r] = col * (-p.p_mw / base_mva)
    hi = np.maximum(f.VMAX_PU ** 2 - v_base, 0.0)
    lo = np.minimum(f.VMIN_PU ** 2 - v_base, 0.0)

    rows = []
    for n in np.flatnonzero(np.abs(A).max(axis=1) > tol):
        rows.append((f"vmax@pq{n}", A[n].copy(), float(hi[n])))
        rows.append((f"vmin@pq{n}", -A[n], float(-lo[n])))

    if th is not None:
        p_base, r_lim = _get(th, "p_base"), _get(th, "r_lim")
        members = _get(th, "members")
        labels = (th.get("labels") if isinstance(th, dict) else getattr(th, "labels", None)) or []
        At = np.zeros((len(p_base), K))
        for b, mem in enumerate(members):
            for r in mem:
                At[b, r] = -projects[r].p_mw
        for b in range(len(p_base)):
            if np.abs(At[b]).max() <= tol:
                continue
            lab = labels[b] if b < len(labels) else f"branch{b}"
            rows.append((f"thermal+@{lab}", At[b].copy(), float(p_base[b] + r_lim[b])))
            rows.append((f"thermal-@{lab}", -At[b], float(-(p_base[b] - r_lim[b]))))
    return rows


def sigma(rows, x, i, pool, prefix=None):
    """min over rows blocking i of (relief from `pool`) / shortfall. See conventions above.

    `prefix` restricts to a constraint class ("vmax"/"vmin"/"thermal"), which matters more
    than it looks. A request on a lateral of its own is blocked by thermal rows whose subtree
    holds no opposite-sign request at all, so their relief is exactly zero and they win the
    min outright -- a true sigma = 0, but one that says "this lateral is private" and hides
    whatever the feeder-wide voltage band is doing. Reporting per class keeps both.

    Returns (sigma, row_label, shortfall, relief). sigma is inf when no row of the class
    blocks i -- for a certified pair that means the linear model thinks the half fits alone
    while AC says it does not, which is model error and is reported rather than hidden.
    """
    pool = np.asarray(sorted(pool), dtype=int)
    best = (np.inf, "none", 0.0, 0.0)
    for lab, a, b in rows:
        if prefix is not None and not lab.startswith(prefix):
            continue
        short = float(a[i] - (b - a @ x))
        if short <= 1e-9:
            continue                                  # row does not block i
        relief = float(np.maximum(-a[pool], 0.0).sum()) if len(pool) else 0.0
        s = relief / short
        if s < best[0]:
            best = (s, lab, short, relief)
    return best


def sigma_by_class(rows, x, i, pool):
    """sigma over all rows, and separately over the voltage rows and the thermal rows."""
    allc = sigma(rows, x, i, pool)
    volt = min((sigma(rows, x, i, pool, p) for p in ("vmax", "vmin")), key=lambda t: t[0])
    therm = sigma(rows, x, i, pool, "thermal")
    return allc, volt, therm


# ------------------------------------------------------------- role A: privacy
def role_privacy(src, cells, n_jobs):
    """sigma on each half of each CERTIFIED pair, relief pool = opposite sign outside the pair."""
    d = pd.read_csv(src)
    rows_out = []
    for cell in cells:
        sub = d[d.cell == cell]
        if not len(sub):
            print(f"  {cell}: absent from {src.name}, skipped")
            continue
        fam, f, k, size_kw = CELLS[cell](n_jobs)
        if not has_linear_hooks(f):
            print(f"  {cell}: no pandapower GLDF hooks (OpenDSS-native), skipped")
            continue
        print(f"\n  {fam.desc}")
        hdr = (f"  {'seed':>4} {'pair':>16} {'half':>6} | {'sigma':>8} {'volt':>8} "
               f"{'therm':>8} | {'binding row':>22}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for _, r in sub.iterrows():
            pairs = [p for p in str(r.certified_pairs).split(";") if "+" in p]
            if not pairs:
                continue
            projects = fam.make_projects(f, int(r.seed), k, size_kw)
            idx = {p.id: n for n, p in enumerate(projects)}
            acc = {s for s in str(r.fixed_point_ids).split(";") if s}
            # linearize where the segregated process actually stopped
            ctx = f.anchored_context(f.ctx, projects, acc)
            rows = one_sided_rows(f, ctx, projects)
            x = np.zeros(len(projects))
            for rid in acc:
                x[idx[rid]] = 1.0
            for pr in pairs:
                u, v = pr.split("+")
                for half, other in ((u, v), (v, u)):
                    hi_, oi = idx[half], idx[other]
                    sgn = np.sign(projects[hi_].p_mw)
                    pool = {n for n, p in enumerate(projects)
                            if np.sign(p.p_mw) != sgn and n not in (hi_, oi)}
                    (s, lab, short, relief), (sv, lv, _, _), (st, lt, _, _) = \
                        sigma_by_class(rows, x, hi_, pool)
                    rows_out.append(dict(role="privacy", cell=cell, seed=int(r.seed),
                                         pair=pr, half=half, sigma=s, sigma_voltage=sv,
                                         sigma_thermal=st, shortfall=short, relief=relief,
                                         row=lab, row_voltage=lv, row_thermal=lt,
                                         pool_size=len(pool)))
                    print(f"  {int(r.seed):>4d} {pr:>16} {half:>6} | {s:>8.3f} {sv:>8.3f} "
                          f"{st:>8.3f} | {lab:>22}", flush=True)
    return rows_out


# --------------------------------------------------------------- role B: blind
def role_blind(cells, n_jobs, seeds):
    """sigma at each track's solo optimum, relief pool = the whole opposite track."""
    rows_out = []
    for cell in cells:
        fam, f, k, size_kw = CELLS[cell](n_jobs)
        if not has_linear_hooks(f):
            print(f"  {cell}: no pandapower GLDF hooks (OpenDSS-native), skipped")
            continue
        print(f"\n  {fam.desc}")
        hdr = (f"  {'seed':>4} {'track':>5} {'blocked':>8} | {'min sigma':>9} {'median':>8} "
               f"{'>=1':>5} | {'verdict':>16}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for seed in seeds:
            projects = fam.make_projects(f, seed, k, size_kw)
            gen = [n for n, p in enumerate(projects) if p.p_mw < 0]
            ld = [n for n, p in enumerate(projects) if p.p_mw > 0]
            for tname, track, opp in (("gen", gen, ld), ("load", ld, gen)):
                sub = [projects[n] for n in track]
                fam.reset(f, sub)
                ids, _kw = fam.certify(f, sub, n_jobs)
                seated = {projects[n].id for n in track} & set(ids)
                ctx = f.anchored_context(f.ctx, projects, seated)
                rows = one_sided_rows(f, ctx, projects)
                x = np.zeros(len(projects))
                for n in track:
                    if projects[n].id in seated:
                        x[n] = 1.0
                sig = []
                for n in track:
                    if projects[n].id in seated:
                        continue                       # not blocked: it got seated
                    (s, lab, short, relief), (sv, lv, _, _), (st, lt, _, _) = \
                        sigma_by_class(rows, x, n, set(opp))
                    if np.isfinite(s):
                        sig.append(s)
                        rows_out.append(dict(role="blind", cell=cell, seed=seed, track=tname,
                                             request=projects[n].id, sigma=s, sigma_voltage=sv,
                                             sigma_thermal=st, shortfall=short, relief=relief,
                                             row=lab, row_voltage=lv, row_thermal=lt))
                if not sig:
                    print(f"  {seed:>4d} {tname:>5} {0:>8d} | {'-':>9} {'-':>8} {'-':>5} | "
                          f"{'track fully seated':>16}")
                    continue
                mn, md = float(np.min(sig)), float(np.median(sig))
                n_ok = int(np.sum(np.asarray(sig) >= 1.0))
                verdict = "relievable" if n_ok else "unrelievable"
                print(f"  {seed:>4d} {tname:>5} {len(sig):>8d} | {mn:>9.3f} {md:>8.3f} "
                      f"{n_ok:>5d} | {verdict:>16}", flush=True)
    return rows_out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--role", choices=["privacy", "blind"], required=True)
    ap.add_argument("--cells", nargs="+", default=None)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(4)))
    ap.add_argument("--src", default="m94_certified_share.csv")
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--out", default=None)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    if a.cells is None:
        a.cells = (["ckt24", "n13f4_rescale"] if a.role == "privacy"
                   else ["ckt7_random", "ckt24_random"])

    print(f"\n  M95 -- SIGMA, ROLE = {a.role.upper()}\n")
    print("  sigma < 1  : relief from the whole pool cannot cover the shortfall (informative)")
    print("  sigma >= 1 : relief MIGHT cover it -- necessary, not sufficient, since the")
    print("               numerator credits every opposite-sign request simultaneously\n")

    t0 = time.time()
    if a.role == "privacy":
        rows = role_privacy(OUT / a.src, a.cells, a.n_jobs)
    else:
        rows = role_blind(a.cells, a.n_jobs, a.seeds)
    d = pd.DataFrame(rows)
    if not len(d):
        print("\n  no rows produced\n")
        return

    print("\n" + "=" * 84)
    print("\n  SUMMARY\n")
    if a.role == "privacy":
        g = d.groupby("cell").agg(
            halves=("sigma", "size"),
            median_sigma=("sigma", "median"),
            max_sigma=("sigma", "max"),
            under_1=("sigma", lambda s: int((s < 1).sum())),
            median_volt=("sigma_voltage", "median"),
            volt_under_1=("sigma_voltage", lambda s: int((s < 1).sum())),
            median_therm=("sigma_thermal", "median"),
            infinite=("sigma", lambda s: int(np.isinf(s).sum())))
        print(g.to_string())
        print("\n  Every half with sigma < 1 is a quantitatively certified private constraint:")
        print("  the entire opposite track outside the pair cannot buy off what blocks it.")
        print("  A half with sigma >= 1 is one Proposition 1's hypothesis may not cover, and")
        print("  an INFINITE sigma is a half the linear model thinks fits alone while AC says")
        print("  it does not -- model error, to be reported as such.\n")
    else:
        g = d.groupby(["cell", "track"]).agg(
            blocked=("sigma", "size"),
            median_sigma=("sigma", "median"),
            n_ge_1=("sigma", lambda s: int((s >= 1).sum())),
            median_volt=("sigma_voltage", "median"),
            volt_ge_1=("sigma_voltage", lambda s: int((s >= 1).sum())),
            median_therm=("sigma_thermal", "median"),
            therm_ge_1=("sigma_thermal", lambda s: int((s >= 1).sum())))
        print(g.to_string())
        print("\n  The decisive comparison is Ckt7 against Ckt24: the two binary tests give")
        print("  them identical verdicts and their residuals are opposite. If sigma separates")
        print("  them, the blind negative result narrows to constraint-identity tests. If it")
        print("  does not, it is about the single-signed information set itself.\n")

    print(f"  {time.time() - t0:.1f} s\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        out = a.out or f"m95_sigma_{a.role}.csv"
        d.to_csv(OUT / out, index=False)
        print(f"  saved -> outputs/{out}\n")


if __name__ == "__main__":
    main()
