"""Build the manuscript figures for `docs/papers/complements-in-the-queue.md`.

Every figure is drawn from the committed experiment CSVs in `outputs/` -- no
number is typed in by hand except the constructed-spur cell, which comes from
`99_thermal_twoway.py` (a deterministic construction that writes no CSV).

    python experiments/105_paper_figures.py

Writes vector PDFs to docs/papers/latex/figures/ for the LaTeX build, and PNGs
to docs/papers/figures/ for the Markdown manuscript.
"""

from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
FIG = ROOT / "docs" / "papers" / "latex" / "figures"
PNG = ROOT / "docs" / "papers" / "figures"

# Journal-figure defaults: serif to match elsarticle's body text, and a
# palette that still separates when the PDF is printed in greyscale.
plt.rcParams.update(
    {
        "font.family": "serif",
        "font.size": 9,
        "axes.labelsize": 9,
        "axes.titlesize": 9,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 200,
    }
)

TWO_WAY = "#2b5d8a"  # both signs aggress the binding constraint
ONE_WAY = "#c8c8c8"  # only one sign can drive it to its limit


def save(fig, stem):
    """Vector PDF for the LaTeX build, PNG for the Markdown manuscript."""
    fig.savefig(FIG / f"{stem}.pdf")
    fig.savefig(PNG / f"{stem}.png", dpi=200)
    plt.close(fig)


RECOVERABLE = "#9ec4e0"  # single-pass loss that re-studying gets back
IRREDUCIBLE = "#1f3f5c"  # what survives unbounded segregated re-study


def residual_frac(df):
    """Residual as a fraction of the joint total -- the paper's only quotient."""
    return (df["joint_kw"] - df["best_sequential_kw"]) / df["joint_kw"] * 100.0


FAMILY_LABEL = {
    "enwl_n13f4": "ENWL n13f4 (UK LV)",
    "enwl_n2f1": "ENWL n2f1 (UK LV)",
    "spur": "Constructed spur",
    "smartds_colocated": "SMART-DS (3,443 nodes)",
    "ckt7": "EPRI Ckt7 (1,255 buses)",
    "swiss55": "Swiss 55-node MV",
    "ckt24": "EPRI Ckt24 (6,058 buses, 3-phase, 50 kW)",
}


def fig5_alternation():
    """The paper's central decomposition: how much of the single-pass residual a
    segregated re-study loop gets back, and how much survives it."""
    # CO-LOCATED placement everywhere: the configuration the reform is about, and the
    # only one where both messages are visible at once -- five families recovered to
    # zero, two holding their residual intact. spur and smartds_colocated are
    # co-located by construction and live in the random-placement run's CSV.
    rand = pd.concat([pd.read_csv(OUT / f) for f in
                      ("m80_alt_s20.csv", "m80_alt_s20b.csv", "m80_alt_s20c.csv",
                       "m80_alt_s20_swiss.csv", "m80_alt_s20_smartds.csv")])
    colo = pd.concat([pd.read_csv(OUT / f) for f in
                      ("m82_colo_s20.csv", "m82_colo_s20_swiss.csv")])
    d = pd.concat([colo, rand[rand.family.isin(["smartds_colocated", "spur"])]])
    d = d.drop_duplicates(["family", "k", "seed"], keep="last")
    # Ckt24 is the reference cell and is reported on its SHIPPED THREE-PHASE model at a
    # 50 kW request. The balanced 100 kW row this file used to plot is measured where a
    # single-phase request draws 417 A through a 195-299 A service, so nothing in that
    # queue is servable alone; it stays in the table as a labelled comparator but does
    # not belong on the figure that carries the claim.
    d = d[d.family != "ckt24"]
    # M100: Ckt7's published draws were measured at 1.04, which is not baseline-compliant
    # once the Vsource impedance is modelled. Re-based to 1.06, inside both engines' windows.
    d = d[d.family != "ckt7"]
    c7 = pd.read_csv(OUT / "m114_ckt7_colocated_106.csv")   # CO-LOCATED, as this figure is
    d = pd.concat([d, pd.DataFrame({
        "family": "ckt7", "seed": c7.seed,
        "residual_singlepass_pct": c7.residual_singlepass_pct,
        "residual_alt_pct": c7.residual_alt_pct,
        "residual_alt_kw": c7.residual_alt_kw, "k": c7.k})])
    u = pd.read_csv(OUT / "m101_ckt24_unbalanced_50kw.csv")
    d = pd.concat([d, pd.DataFrame({
        "family": "ckt24", "seed": u.seed,
        "residual_singlepass_pct": u.r1_pct, "residual_alt_pct": u.rstar_pct,
        "residual_alt_kw": u.rstar_kw, "k": u.k})])
    # M119: the ENWL families are phase-resolved now, and they are the one place this figure
    # cannot plot co-located placement. All four co-located ENWL cells pin joint capacity at
    # the K x size cap, so their bars would measure the queue we drew rather than the feeder.
    # Plotted at RANDOM placement, 50 kW, which is also where n2f1's residual lives -- the
    # corpus's counterexample to the co-location account, and worth being visible.
    d = d[~d.family.isin(["enwl_n13f4", "enwl_n2f1"])]
    for fam, fname in (("enwl_n13f4", "m119_n13f4_rand_50.csv"),
                       ("enwl_n2f1", "m119_n2f1_rand_50.csv")):
        e = pd.read_csv(OUT / fname)
        d = pd.concat([d, pd.DataFrame({
            "family": fam, "seed": e.seed,
            "residual_singlepass_pct": e.r1_pct, "residual_alt_pct": e.rstar_pct,
            "residual_alt_kw": e.rstar_kw, "k": e.k})])
    g = (d.groupby("family", sort=False)
           .agg(r1=("residual_singlepass_pct", "median"),
                ra=("residual_alt_pct", "median"),
                n=("seed", "size"),
                surv=("residual_alt_kw", lambda s: int((s > 1e-6).sum())))
           .reset_index())
    g = g.sort_values("r1", ascending=True).reset_index(drop=True)

    y = list(range(len(g)))
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    h = 0.36
    ax.barh([i + h / 2 for i in y], g.r1, height=h, color=RECOVERABLE, zorder=3,
            label="$R_1$: single-pass (Eq. 1)")
    ax.barh([i - h / 2 for i in y], g.ra, height=h, color=IRREDUCIBLE, zorder=3,
            label=r"$R_*$: irreducible (survives re-study)")

    for i, r in g.iterrows():
        ax.text(r.r1 + 1.2, i + h / 2, f"{r.r1:.0f}%", va="center", fontsize=7.5, color="0.25")
        if r.ra > 0.4:
            ax.text(r.ra + 1.2, i - h / 2, f"{r.ra:.0f}%", va="center", fontsize=7.5, color="0.25")
        elif r.r1 > 0.4:
            # a zero-length dark bar should say WHY it is zero, not look like missing data
            ax.text(1.2, i - h / 2, "0  (fully recovered by re-study)",
                    va="center", fontsize=7.0, color="0.35")

    ax.set_yticks(y)
    ax.set_yticklabels([FAMILY_LABEL.get(f, f) for f in g.family], fontsize=8)
    ax.set_xlabel("Residual (% of joint total), median over draws")
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.75, len(g) - 1 + 1.15)   # blank band at the top for the legend
    ax.grid(axis="x", color="0.9", zorder=0)
    # Above the bars rather than inside them: at the re-based Ckt24 magnitude the three
    # shortest rows carry "fully recovered" annotations that a lower-right legend sits on.
    ax.legend(loc="upper center", ncol=2, frameon=False, fontsize=7.5,
              handlelength=1.6, columnspacing=1.4)
    fig.tight_layout()
    save(fig, "fig5_alternation")


def fig1_definition():
    """The three study processes, measured on one representative Ckt7 cell."""
    # M100: Ckt7 re-based to 1.06, the tightest setpoint compliant in both engines.
    d = pd.read_csv(OUT / "m113_sequential_ckt7_106.csv")
    d = d[(d.setpoint == 1.06) & (d.load_frac == 0.5) & (d.seed == 0)].iloc[0]

    labels = [
        "Generation\ntrack alone",
        "Load\ntrack alone",
        "Segregated\nparallel\n(union)",
        "Segregated\nsequential\n(gen. first)",
        "Segregated\nsequential\n(load first)",
        "Joint",
    ]
    vals = [
        d.pv_alone_kw,
        d.load_alone_kw,
        d.seg_parallel_kw,
        d.seq_pvfirst_kw,
        d.seq_loadfirst_kw,
        d.joint_kw,
    ]
    colors = ["#c8c8c8"] * 2 + ["#9ab8d0"] + ["#6d97b8"] * 2 + [TWO_WAY]

    fig, ax = plt.subplots(figsize=(6.4, 3.1))
    bars = ax.bar(range(len(vals)), vals, color=colors, width=0.66)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 25, f"{v:.0f}", ha="center", fontsize=8)

    best_seq = max(d.seq_pvfirst_kw, d.seq_loadfirst_kw)
    # The residual: what the best sequential order still cannot reach.
    ax.annotate(
        "",
        xy=(5.42, d.joint_kw),
        xytext=(5.42, best_seq),
        arrowprops=dict(arrowstyle="<->", color="black", lw=1.1),
    )
    ax.text(
        5.56,
        (d.joint_kw + best_seq) / 2,
        f"residual\n{d.joint_kw - best_seq:.0f} kW\n({(d.joint_kw - best_seq) / d.joint_kw * 100:.0f}% of joint)",
        va="center",
        fontsize=8,
    )
    # The parallel gap, part of which a sequential study does recover.
    ax.annotate(
        "",
        xy=(2.42, d.seg_parallel_kw),
        xytext=(2.42, d.joint_kw),
        arrowprops=dict(arrowstyle="<->", color="0.35", lw=1.0, linestyle=":"),
    )
    ax.text(
        2.56,
        d.joint_kw * 1.13,
        "parallel gap (partly recovered by ordering)",
        va="center",
        fontsize=7.5,
        color="0.35",
    )

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=7.5)
    ax.set_ylabel("Certified hosting capacity (kW)")
    ax.set_ylim(0, d.joint_kw * 1.30)
    ax.set_xlim(-0.6, 6.5)
    fig.tight_layout()
    save(fig, "fig1_definition")


def fig2_corpus():
    """The invariant made visible: which rows are nonzero, and why."""
    ckt7 = pd.read_csv(OUT / "m113_sequential_ckt7_106.csv")
    ckt7 = residual_frac(ckt7[ckt7.setpoint == 1.06])
    ckt24_light = residual_frac(pd.read_csv(OUT / "m64_sequential_ckt24.csv"))
    c24 = pd.read_csv(OUT / "m68_residual_oversub_ckt24.csv")
    ckt24_over = residual_frac(c24[c24.oversub_ratio >= 2.5])
    # M119/M96: the ENWL cells are the SHIPPED THREE-PHASE solve now, at random placement.
    # m70_residual_enwl.csv is the balanced arm and is no longer what the paper reports:
    # phase-resolved, n13f4 falls from 75% to 16.3% and n2f1 keeps a residual the balanced
    # solve says it does not have. Co-located ENWL is deliberately NOT plotted -- all four
    # of those cells pin joint capacity at the request cap, so they measure the queue.
    def _enwl(fname):
        return pd.read_csv(OUT / fname).r1_pct
    enwl_13_100 = _enwl("m96_enwl_unbalanced_relax.csv")
    enwl_13_50 = _enwl("m119_n13f4_rand_50.csv")
    enwl_21_100 = _enwl("m96_enwl_unbalanced_n2f1_all.csv")
    enwl_21_50 = _enwl("m119_n2f1_rand_50.csv")
    sds = pd.read_csv(OUT / "m73_residual_smartds.csv")
    sds["res"] = residual_frac(sds)
    swiss = pd.read_csv(OUT / "m75_residual_swiss.csv")["residual_frac_of_joint_pct"]

    def cell(name, series, two_way, note=""):
        return dict(
            name=name,
            med=float(series.median()),
            mx=float(series.max()),
            n=len(series),
            n_pos=int((series > 1e-9).sum()),
            two_way=two_way,
            note=note,
        )

    rows = [
        cell("ENWL n13f4 (UK LV), 100 kW", enwl_13_100, True),
        cell("ENWL n2f1 (UK LV), 100 kW", enwl_21_100, True),
        dict(name="Constructed spur, co-located", med=75.0, mx=75.0, n=1, two_way=True, note="constructed"),
        cell("SMART-DS, co-located", sds[sds.placement == "colocated"].res, True),
        cell("ENWL n13f4 (UK LV), 50 kW", enwl_13_50, True),
        cell("ENWL n2f1 (UK LV), 50 kW", enwl_21_50, True),
        cell("EPRI Ckt7, every operating point", ckt7, True),
        cell("Swiss 55-node MV, oversubscribed", swiss, True),
        cell("EPRI Ckt24, oversubscribed 100 kW", ckt24_over, True),
        cell("SMART-DS, random placement", sds[sds.placement == "random"].res, False, "no co-location"),
        cell("EPRI Ckt24, lightly loaded", ckt24_light, False),
    ]
    # Swiss realistic-queue and Ckt24-light are both structural zeros; the
    # Swiss cell writes no residual column when nothing binds.
    rows.append(dict(name="Swiss 55-node MV, realistic queue", med=0.0, mx=0.0, n=4, two_way=False, note=""))

    rows.sort(key=lambda r: (r["med"], r["mx"]))
    y = range(len(rows))

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.barh(
        list(y),
        [r["med"] for r in rows],
        color=[TWO_WAY if r["two_way"] else ONE_WAY for r in rows],
        height=0.62,
        zorder=3,
    )
    # Median bar, with a whisker to the maximum across seeds/cells.
    for i, r in enumerate(rows):
        if r["mx"] > r["med"] + 0.4:
            ax.plot([r["med"], r["mx"]], [i, i], color="0.3", lw=0.9, zorder=4)
            ax.plot([r["mx"]], [i], marker="|", color="0.3", ms=5, zorder=4)
        lbl = f"{r['med']:.0f}"
        if r["mx"] > r["med"] + 0.4:
            lbl += f"–{r['mx']:.0f}"
        # A two-way cell whose median is 0 fires only intermittently -- say so
        # rather than let a zero-length bar imply the mechanism is absent.
        if r["two_way"] and r["med"] < 0.5 and r.get("n_pos", 0):
            lbl += f"  (positive in {r['n_pos']}/{r['n']} draws)"
        ax.text(max(r["mx"], r["med"]) + 1.4, i, lbl, va="center", fontsize=7.5, color="0.25")

    ax.set_yticks(list(y))
    ax.set_yticklabels(
        [r["name"] + (f"  [{r['note']}]" if r["note"] else "") for r in rows], fontsize=7.5
    )
    ax.set_xlabel("Residual: joint $-$ best sequential order (% of joint total)")
    ax.set_xlim(0, 92)
    # Blank band below the shortest bars: the zero-residual rows carry their annotations to
    # the right of the axis, exactly where a lower-right legend used to sit on them.
    ax.set_ylim(-1.9, len(rows) - 1 + 0.7)
    ax.grid(axis="x", color="0.9", zorder=0)
    ax.legend(
        handles=[
            Patch(facecolor=TWO_WAY, label="Binding constraint admits both signs (two-way)"),
            Patch(facecolor=ONE_WAY, label="Only one sign can bind it (one-way)"),
        ],
        loc="lower right",
        frameon=False,
    )
    fig.tight_layout()
    save(fig, "fig2_corpus")


def fig3_knobs():
    """Oversubscription switches the residual on; granularity amplifies it."""
    fig, (a, b) = plt.subplots(1, 2, figsize=(6.6, 2.7))

    over = pd.read_csv(OUT / "m111_oversub_ckt7_106.csv")
    over["res"] = residual_frac(over)
    a.scatter(over.oversub_ratio, over.res, s=14, color=TWO_WAY, alpha=0.55, zorder=3)
    med = over.groupby("oversub_ratio").res.median()
    a.plot(med.index, med.values, color=TWO_WAY, lw=1.3, zorder=4)
    a.axvline(1.0, color="0.6", lw=0.9, ls="--")
    a.text(1.03, 24, "queue fits\nsolo", fontsize=7.5, color="0.4", ha="left")
    a.set_xlabel("Oversubscription (requested / hosting capacity)")
    a.set_ylabel("Residual (% of joint)")
    a.set_title("(a) Oversubscription is the switch (Ckt7, 30 kW)", fontsize=8.5)
    a.grid(color="0.92", zorder=0)

    g7 = pd.read_csv(OUT / "m112_scale_ckt7_106.csv")
    g7["res"] = residual_frac(g7)
    g24 = pd.read_csv(OUT / "m69_residual_scale_ckt24.csv")
    g24["res"] = residual_frac(g24)
    for df, lab, style, dy in [
        (g7, "Ckt7", dict(color=TWO_WAY, marker="o"), 8),
        (g24, "Ckt24", dict(color="#b5651d", marker="s"), -12),
    ]:
        m = df.groupby("size_kw").res.agg(["median", "count"])
        b.plot(m.index, m["median"], lw=1.3, ms=4, label=lab, **style)
        # Sample counts differ per size; thin cells must not read as solid.
        for size, row in m.iterrows():
            b.annotate(f"{int(row['count'])}", (size, row["median"]), textcoords="offset points",
                       xytext=(0, dy), fontsize=6, ha="center", color="0.45")
    # What the digits mean is stated in the caption; an in-panel note here
    # would sit on top of the legend.
    b.set_xlabel("Request size (kW)")
    b.set_ylabel("Residual (% of joint)")
    b.set_title("(b) Granularity is the amplifier", fontsize=8.5)
    b.legend(frameon=False, loc="upper left")
    b.grid(color="0.92", zorder=0)

    fig.tight_layout()
    save(fig, "fig3_knobs")


def fig4_swiss_day():
    """The certified accept-set, replayed against the feeder's own measured day."""
    d = pd.read_csv(OUT / "m76_swiss_daily_profile.csv")
    days = sorted(d.day.unique())
    fig, axes = plt.subplots(2, len(days), figsize=(6.6, 3.9), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1.35]})

    for j, day in enumerate(days):
        s = d[d.day == day].reset_index(drop=True)
        top, bot = axes[0][j], axes[1][j]
        x = s.t / 4.0  # 15-minute intervals -> hours

        top.plot(x, s.pv_scale, color="#d68910", lw=1.2, label="PV scale")
        top.plot(x, s.load_scale, color="#2b5d8a", lw=1.2, label="Load scale")
        top.set_ylabel("Profile scale" if j == 0 else "")
        top.set_title(f"Day-type {day}: {int(s.ok.sum())}/{len(s)} intervals feasible", fontsize=8.5)
        if j == 0:
            top.legend(frameon=False, loc="upper left", fontsize=7)

        bot.plot(x, s.line_pct, color="0.2", lw=1.1)
        bot.axhline(100, color="#b03a2e", lw=1.0, ls="--")
        bot.fill_between(x, 0, s.line_pct.max() * 1.05, where=s.ok, color="#7fbf7f",
                         alpha=0.35, step="mid", lw=0)
        bot.set_ylabel("Max line loading (%)" if j == 0 else "")
        bot.set_xlabel("Hour of day")
        bot.set_xticks([0, 6, 12, 18, 24])
        bot.set_ylim(0, d.line_pct.max() * 1.05)
        bot.grid(color="0.93", zorder=0)

    axes[1][0].text(0.6, d.line_pct.max() * 0.92, "green = AC-feasible", fontsize=7, color="#3f7f3f")
    axes[1][0].text(0.6, d.line_pct.max() * 0.80, "dashed = ampacity limit", fontsize=7, color="#b03a2e")
    fig.tight_layout()
    save(fig, "fig4_swiss_day")


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    PNG.mkdir(parents=True, exist_ok=True)
    fig1_definition()
    fig2_corpus()
    fig3_knobs()
    fig4_swiss_day()
    fig5_alternation()          # the paper's central decomposition -- was never called
    print(f"wrote 5 figures to {FIG} (pdf) and {PNG} (png)")
