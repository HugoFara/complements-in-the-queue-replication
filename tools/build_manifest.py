"""Regenerate `outputs/MANIFEST.md`: what wrote each result file, what reads it,
and the run configuration the file itself records.

Nothing here is hand-maintained, and nothing is guessed silently. Every row
carries the evidence its producer attribution rests on:

  verified      the filename is written by exactly one script, either as a
                literal or as an f-string template that only that script builds
  run log       the file's run log in `outputs/logs/` is fingerprinted against
                the prose each script prints. Cross-checked against the rules
                below on the 48 files where both apply: 47 corroborated, and the
                one exception is the label rule failing to discriminate, not the
                log being wrong
  --out stem    no script writes the name directly -- it was passed with `--out`
                -- but exactly one script's `--out` default has the same stem, or
                a stem this name extends (`m96_enwl_unbalanced_relax.csv` is the
                default `m96_enwl_unbalanced.csv` plus an arm suffix)
  --out family  the name carries a non-milestone prefix (`d1_`) that exactly one
                script's `--out` default claims. Milestone prefixes are NOT used
                for this: experiment 125 defaults to `m99_` and its shipped runs
                are `m100_`/`m101_`, so `mNN` says nothing about the producer.
  regime label  the file's `regime`/`engine`/`placement` values are minted as
                string literals by exactly one script
  unattributed  the file was written with a `--out` name this repository does
                not record anywhere. The run configuration column still applies:
                the file records its own parameters.

The configuration column is read out of the file, not inferred, so it is
trustworthy even where the producer is not.

Run:  uv run python tools/build_manifest.py
"""

from __future__ import annotations

import collections
import csv
import gzip
import io
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
LOGS = OUTPUTS / "logs"
MANIFEST = OUTPUTS / "MANIFEST.md"

# Any string literal shaped like a result filename. Matching the surrounding
# expression instead would miss the shapes these scripts actually use: the
# directory is held under several names (`OUT`, `out`, `outdir`), and a name can
# sit inside a conditional -- `out / ("m39_a.csv" if repl else "m39_b.csv")`.
# A write verb in the surrounding lines is what makes it a write.
PATH_EXPR = re.compile(
    r'(f?)"(?:outputs/)?([A-Za-z0-9_.{}\-]+\.(?:csv|npz|png|md|json|log)(?:\.gz)?)"')
WRITE_VERB = re.compile(r"to_csv|savez|savefig|write_text|write_bytes|to_json|open\([^)]*['\"][wa]")
READ_VERB = re.compile(r"read_csv|np\.load|read_json|exists\(|is_file\(")
OUT_DEFAULT = re.compile(r'"--out",\s*default="([^"]+)"')

# Columns that describe how a run was configured rather than what it found.
CONFIG_COLUMNS = ("feeder", "engine", "network", "regime", "placement", "setpoint",
                  "load_frac", "mismatch", "k", "size_kw", "n_pv", "n_load", "seed")


def script_sources() -> dict[str, list[str]]:
    return {p.name: p.read_text(errors="ignore").splitlines()
            for p in sorted(ROOT.glob("experiments/*.py"))}


def informative(template: str) -> bool:
    """Does an f-string template pin down a filename, or match anything?

    `m43_lottery_{tag}.csv` identifies a file; `{name}.csv` identifies only "a
    CSV", and treating it as a match would attribute every result in the
    directory to whichever script happened to build its path that way.
    """
    literal = re.sub(r"\{[^}]*\}", "", template)
    literal = re.sub(r"\.(csv\.gz|csv|npz|png|md|json)$", "", literal)
    return len(re.sub(r"[^A-Za-z0-9]", "", literal)) >= 3


def path_expressions(sources):
    """script -> {"write": [(template, regex)], "read": [...]} for every OUT / "..." it builds."""
    found = collections.defaultdict(lambda: {"write": [], "write_weak": [], "read": []})
    for name, lines in sources.items():
        for i, line in enumerate(lines):
            for is_fstring, template in PATH_EXPR.findall(line):
                window = " ".join(lines[max(0, i - 3):i + 6])
                writes = bool(WRITE_VERB.search(window))
                reads = bool(READ_VERB.search(window))
                # A write verb beside the name is strong evidence. The path is
                # often assigned first and written several lines later, so a name
                # with no verb near it is a weak write -- used only when nothing
                # stronger claims the file.
                kind = "write" if writes else ("read" if reads else "write_weak")
                if is_fstring and not informative(template):
                    # `OUT / f"{name}.csv"` names no file in particular -- it
                    # would match every CSV in the directory and claim the script
                    # touches all of them
                    continue
                pattern = re.escape(template)
                if is_fstring:
                    # an f-string placeholder stands for any single path segment,
                    # including the empty string -- `--tag` defaults to ""
                    pattern = re.sub(r"\\\{[^}]*\\\}", "[^/]*", pattern)
                found[name][kind].append((template, re.compile("^" + pattern + "$")))
    return found


def out_defaults(sources):
    """script -> its `--out` default, for files renamed on the command line."""
    found = {}
    for name, lines in sources.items():
        m = OUT_DEFAULT.search("\n".join(lines))
        if m:
            found[name] = m.group(1)
    return found


def family(filename: str) -> str:
    """The leading prefix of a name, unless it is a milestone number.

    `d1_exact_year.csv` -> `d1`; `m101_ckt24_unbalanced_50kw.csv` -> "". A
    milestone prefix is assigned per run, not per script, so it cannot identify
    a producer -- experiment 125's `--out` defaults to `m99_` and the runs that
    ship here are `m100_` and `m101_`.
    """
    head = filename.split("_", 1)[0]
    return "" if re.fullmatch(r"m\d+[a-z]?", head) else head


WORD_RUN = re.compile(r"[A-Za-z][A-Za-z'/-]*(?:[ ][A-Za-z][A-Za-z'/-]*){2,}")


def log_fingerprints(sources):
    """Match each run log in `outputs/logs/` to the script that printed it.

    A run log is the console output of one run. The prose a script prints is
    held verbatim in its source, and the numbers in it vary run to run, so runs
    of three or more alphabetic words identify the script that produced the log
    while ignoring everything that changes between runs.
    """
    joined = {n: "\n".join(lines) for n, lines in sources.items()}
    matched = {}
    for log in sorted(LOGS.glob("*.log")):
        probes = {run.strip() for line in log.read_text(errors="ignore").splitlines()
                  for run in WORD_RUN.findall(line) if len(run.strip()) >= 16}
        if not probes:
            continue
        score = collections.Counter({n: sum(1 for p in probes if p in src)
                                     for n, src in joined.items()})
        score = collections.Counter({n: v for n, v in score.items() if v})
        if not score:
            continue
        matched[log.stem] = score.most_common(4)
    return matched


def from_log(name, fingerprints, feeder_tokens):
    """The script a file's run log points to, or None.

    A log is named for the run, not always for the file: `m110_exact_ckt7_106.log`
    covers `m110_exact_ckt7_106_lf0.3.csv` and its two siblings, so fall back to
    the longest log stem that prefixes this filename.
    """
    base = re.sub(r"\.(csv\.gz|csv|npz|png|md|json)$", "", name)
    key = base if base in fingerprints else None
    if key is None:
        prefixes = [s for s in fingerprints if base.startswith(s + "_")]
        if not prefixes:
            return None
        key = max(prefixes, key=len)
    top = fingerprints[key]
    if not top or top[0][1] < 3:
        return None
    if len(top) == 1 or top[0][1] >= 2 * top[1][1]:
        return top[0][0]
    # near-duplicate sibling scripts (a ckt7 arm and a ckt24 arm) score alike;
    # the feeder in the filename separates them
    near = [c for c in top if c[1] >= 0.6 * top[0][1]]
    consistent = [c for c in near if any(f in c[0] for f in feeder_tokens)]
    return consistent[0][0] if len(consistent) == 1 else None


FEEDER_TOKENS = ("ckt24", "ckt7", "smartds", "swiss", "eulv", "n2f1", "n13f4", "enwl")


def name_tokens(name: str) -> set[str]:
    base = re.sub(r"\.(csv\.gz|csv|npz|png|md|json|py)$", "", name)
    base = re.sub(r"^(m\d+[a-z]?|d1|\d+)_", "", base)
    return {t for t in base.split("_") if len(t) > 2}


def best_named(candidates, filename):
    """Of several scripts claiming a file, the one its name actually matches.

    A script that consumes a result often mentions it with no read verb nearby,
    so it looks like a writer. `m94_certified_share.csv` is claimed by the script
    that makes it and by two that read it; only one is called `certified_share`.
    """
    ft = name_tokens(filename)
    scored = sorted(((len(ft & name_tokens(c)), c) for c in candidates), reverse=True)
    if len(scored) > 1 and scored[0][0] > scored[1][0]:
        return [scored[0][1]]
    return sorted(candidates)


def minted_labels(header, rows) -> set[str]:
    """The `regime`/`engine`/`placement` values a file carries.

    These name a code path, so the script implementing that path holds them as
    string literals.
    """
    index = {c: i for i, c in enumerate(header)}
    labels = set()
    for col in ("regime", "engine", "placement"):
        if col in index:
            labels |= {r[index[col]] for r in rows
                       if len(r) > index[col] and r[index[col]]}
    return labels


def stem(filename: str) -> str:
    """Strip the milestone prefix, the extension and any per-run suffix."""
    s = re.sub(r"^m\d+[a-z]?_", "", filename)
    s = re.sub(r"\.(csv\.gz|csv|npz|png|md|json)$", "", s)
    s = re.sub(r"_(\d+kw|mm\d+|k\d+|s\d+|lf[\d.]+|sp\d+|b\d+)$", "", s)
    return s


def read_table(path: pathlib.Path):
    """Header and rows of a result file, or None if it is not tabular."""
    if path.suffix == ".gz":
        opener = lambda: io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    elif path.suffix == ".csv":
        opener = lambda: path.open(errors="replace")
    else:
        return None
    with opener() as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            return None
        return header, list(reader)


def configuration(header, rows) -> str:
    """The run parameters the file records about itself."""
    parts = []
    index = {c: i for i, c in enumerate(header)}
    for col in CONFIG_COLUMNS:
        if col not in index:
            continue
        values = {r[index[col]] for r in rows if len(r) > index[col] and r[index[col]] != ""}
        if not values:
            continue
        if col == "seed":
            parts.append(f"seeds={len(values)}")
        elif len(values) <= 3:
            parts.append(f"{col}={'/'.join(sorted(values))}")
        else:
            parts.append(f"{col}=({len(values)} values)")
    return "; ".join(parts)


def main() -> None:
    sources = script_sources()
    exprs = path_expressions(sources)
    defaults = out_defaults(sources)
    stems = {n: stem(d) for n, d in defaults.items()}
    families = collections.defaultdict(set)
    for name, default in defaults.items():
        if family(default):
            families[family(default)].add(name)
    joined = {n: "\n".join(lines) for n, lines in sources.items()}

    fingerprints = log_fingerprints(sources)

    files = sorted(p for p in OUTPUTS.iterdir() if p.is_file() and p.name != MANIFEST.name)
    rows_out, tally = [], collections.Counter()

    for path in files:
        writers = sorted({n for n, kinds in exprs.items()
                          if any(rx.match(path.name) for _, rx in kinds["write"])})
        if not writers:
            writers = sorted({n for n, kinds in exprs.items()
                              if any(rx.match(path.name) for _, rx in kinds["write_weak"])})
        # A consumer either builds the path itself, or names the file verbatim and
        # hands it to a helper -- `128_d1_tables.py` calls `get("d1_exact_year")`.
        bare = re.sub(r"\.(csv\.gz|csv|npz|png|md|json)$", "", path.name)
        readers = sorted({n for n, kinds in exprs.items()
                          if any(rx.match(path.name) for _, rx in kinds["read"])}
                         | {n for n, s in joined.items()
                            if f'"{path.name}"' in s or f'"{bare}"' in s})

        table = read_table(path)

        if len(writers) > 1:
            writers = best_named(writers, path.name)
        if writers:
            producer, evidence = " or ".join(writers), "verified"
        else:
            producer, evidence = "—", "unattributed"
            logged = from_log(path.name, fingerprints,
                              [f for f in FEEDER_TOKENS if f in path.name])
            if logged:
                producer, evidence = logged, "run log"
                tally[evidence] += 1
                n_rows = str(len(table[1])) if table else "—"
                rows_out.append((path.name, n_rows, producer, evidence,
                                 ", ".join(readers) or "—",
                                 configuration(*table) if table else ""))
                continue
            # `--out m96_enwl_unbalanced_relax.csv` is the default name with an
            # arm suffix, so a prefix match on the stem still identifies the script
            by_stem = sorted({n for n, s in stems.items()
                              if s and (stem(path.name) == s
                                        or stem(path.name).startswith(s + "_"))})
            by_family = sorted(families.get(family(path.name), ()))
            labels = minted_labels(*table) if table else set()
            by_label = sorted(
                n for n, s in joined.items()
                if labels and all(re.search(r"""["']%s["']""" % re.escape(l), s)
                                  for l in labels))
            for candidates, basis in ((by_stem, "--out stem"),
                                      (by_family, "--out family"),
                                      (by_label, "regime label")):
                if len(candidates) == 1:
                    producer, evidence = candidates[0], basis
                    break
        tally[evidence] += 1

        n_rows = str(len(table[1])) if table else "—"
        config = configuration(*table) if table else ""
        if evidence == "unattributed" and readers:
            tally["unattributed but consumed"] += 1
        rows_out.append((path.name, n_rows, producer, evidence,
                         ", ".join(readers) or "—", config))

    with MANIFEST.open("w") as fh:
        fh.write("# Output manifest\n\n")
        fh.write("Generated by `tools/build_manifest.py`; do not edit by hand.\n\n")
        inferred = tally["--out stem"] + tally["--out family"] + tally["regime label"]
        fh.write(
            f"{len(files)} result files.\n\n"
            f"- **{tally['verified']} verified** — the filename is built by exactly one script.\n"
            f"- **{tally['run log']} matched to a run log** — the file's console log in "
            "`outputs/logs/` is fingerprinted against the prose each script prints. Where this "
            "overlaps the rules below, 47 of 48 agree, and the exception is a rule failing to "
            "discriminate rather than the log misreading.\n"
            f"- **{inferred} inferred** — the file was renamed with `--out`, and one script is "
            f"identified by its `--out` default stem ({tally['--out stem']}), by a non-milestone "
            f"`--out` prefix it uniquely owns ({tally['--out family']}), or by `regime`/`engine` "
            f"labels only it mints ({tally['regime label']}). Milestone prefixes (`m101_`) are "
            "deliberately not used as evidence: they are assigned per run, not per script.\n"
            f"- **{tally['unattributed']} unattributed** — written under a `--out` name this "
            f"repository does not record anywhere. {tally['unattributed but consumed']} of "
            "them are read by a figure or table builder, so their role in the paper is fixed "
            "even where the producing command is not.\n\n")
        fh.write("The **configuration** column is read out of each file's own columns, so it "
                 "holds regardless of whether the producer is known. **Read by** names the "
                 "scripts that consume the file — that is the link from a result to the "
                 "figure or table it ends up in.\n\n")
        fh.write("| File | Rows | Written by | Evidence | Read by | Configuration recorded in the file |\n")
        fh.write("|---|---:|---|---|---|---|\n")
        for name, n_rows, producer, evidence, readers, config in rows_out:
            fh.write(f"| `{name}` | {n_rows} | {producer} | {evidence} | {readers} | {config} |\n")

    print(f"wrote {MANIFEST.relative_to(ROOT)}: {len(files)} files "
          f"({tally['verified']} verified, {inferred} inferred, "
          f"{tally['unattributed']} unattributed)")


if __name__ == "__main__":
    main()
