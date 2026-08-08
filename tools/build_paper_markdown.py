"""Regenerate `docs/papers/complements-in-the-queue.md` from the LaTeX submission copy.

The Markdown copy is a *reading view* of the manuscript, not a second manuscript.
It exists so the paper is legible in a browser without a PDF viewer; the LaTeX in
`docs/papers/latex/` is the only edited source. Keeping the two in step by hand
did not work -- the Markdown drifted a full reframing behind, down to carrying a
title the paper had already moved past -- so it is now derived, and this script is
the derivation.

What the conversion does beyond `pandoc`:

  headings      shifted one level down (the file's H1 is the paper title) and
                numbered to match the LaTeX numbering, so the cross-references
                pandoc has already resolved ("Section 5.6") point somewhere real.
                `\\section*` stays unnumbered; sections after `\\appendix` become
                "Appendix A", "Appendix B", ...
  references    `\\ref`/`\\eqref` come out of pandoc as HTML anchors into a
                numbering scheme this file does not have. The anchor is dropped
                and the resolved number kept.
  figures       the manuscript plots vector PDFs from `latex/figures/`, which no
                browser renders inline. The archive carries a PNG of each in
                `docs/papers/figures/` under the same stem; the Markdown copy is
                pointed at those.
  frontmatter   elsarticle's abstract and keywords live in a `frontmatter`
                environment that pandoc drops. Both are converted separately and
                put back.

Citations are resolved against `complements-in-the-queue.bib` by `--citeproc`, so
the reference list at the foot is generated too.

Requires pandoc (tested on 3.7).

Run:  uv run python tools/build_paper_markdown.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "docs" / "papers"
TEX = PAPERS / "latex" / "complements-in-the-queue.tex"
BIB = PAPERS / "latex" / "complements-in-the-queue.bib"
FIGURES = PAPERS / "figures"
OUT = PAPERS / "complements-in-the-queue.md"

TO = "gfm+tex_math_dollars"

TITLE = ("Complements in the Queue: What Re-Studying Recovers, and What Only a "
         "Joint Interconnection Study Can")

HEADER = f"""# {TITLE}

**Hugo Farajallah** — Independent Research — <research@hugofara.net>
ORCID [0009-0001-0769-4434](https://orcid.org/0009-0001-0769-4434)

> **This file is generated.** It is a reading copy of the submission manuscript,
> converted from [`latex/complements-in-the-queue.tex`](latex/complements-in-the-queue.tex)
> by `tools/build_paper_markdown.py`. Edit the LaTeX, not this file. The typeset
> PDF is [`latex/complements-in-the-queue.pdf`](latex/complements-in-the-queue.pdf);
> where the two disagree, the PDF is authoritative.
>
> Unrefereed preprint. Several power-flow-modelling correctness claims are
> load-bearing and are flagged in Section 7 as not independently verified by the
> author. The per-feeder laboratory notebooks are in [`../logs/`](../logs/); the
> internal hardening record — retractions, dead ends, the reviewer-style
> back-and-forth this draft was built against — is not part of this archive.
> How to run any of the code is in [`../../REPLICATION.md`](../../REPLICATION.md).
"""

# `\section*{...}` and, separately, everything after `\appendix`. Titles are
# matched to pandoc's headings on normalised text, not position, so reordering
# the manuscript does not silently renumber this file.
SECTION_STAR = re.compile(r"\\section\*\{(.+?)\}", re.DOTALL)
SECTION = re.compile(r"\\section\{(.+?)\}", re.DOTALL)
ANCHOR = re.compile(r'<a href="#[^"]*"[^>]*>(.*?)</a>', re.DOTALL)
EMBED = re.compile(r'<embed src="([^"]+?)\.pdf"\s*/>')
DIV = re.compile(r"^\s*</?div\b[^>]*>\s*$")
ENVIRONMENT = "abstract"

# `--citeproc` emits the reference list with no heading of its own. Giving it one
# means declaring it unnumbered too, or it lands as a section of the manuscript.
REFERENCES = "References"


def need_pandoc() -> str:
    exe = shutil.which("pandoc")
    if exe is None:
        sys.exit("pandoc not found on PATH; install it to regenerate the Markdown copy")
    return exe


def pandoc(args: list[str], stdin: str | None = None) -> str:
    proc = subprocess.run(
        [need_pandoc(), *args],
        input=stdin, capture_output=True, text=True, cwd=TEX.parent,
    )
    if proc.returncode != 0:
        sys.exit(f"pandoc failed:\n{proc.stderr.strip()}")
    if proc.stderr.strip():
        print(f"  pandoc: {proc.stderr.strip()}", file=sys.stderr)
    return proc.stdout


def convert_document() -> str:
    body = pandoc([
        TEX.name, "-f", "latex", "-t", TO, "--wrap=none",
        "--citeproc", f"--bibliography={BIB.name}",
        "--metadata", f"reference-section-title={REFERENCES}",
    ])
    # Everything before the first heading is elsarticle's `frontmatter`, which
    # pandoc renders as a bare run of keywords. Title, abstract and keywords are
    # reassembled from the source in main(); drop the remnant so they appear once.
    first_heading = re.search(r"^# ", body, re.MULTILINE)
    return body[first_heading.start():] if first_heading else body


def convert_fragment(latex: str) -> str:
    return pandoc(["-f", "latex", "-t", TO, "--wrap=none",
                   "--citeproc", f"--bibliography={BIB.name}"], stdin=latex).strip()


def norm(title: str) -> str:
    """Comparison key: lowercase words only, so LaTeX escapes and line breaks in
    the source do not have to match pandoc's rendering character for character."""
    title = re.sub(r"\\[a-zA-Z]+\s*", " ", title)
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))


def tex_sections(source: str) -> tuple[set[str], set[str]]:
    unnumbered = {norm(m.group(1)) for m in SECTION_STAR.finditer(source)}
    unnumbered.add(norm(REFERENCES))
    head, _, tail = source.partition("\n\\appendix")
    appendix = {norm(m.group(1)) for m in SECTION.finditer(tail)} if tail else set()
    return unnumbered, appendix


def extract_environment(source: str, name: str) -> str:
    m = re.search(rf"\\begin\{{{name}\}}(.*?)\\end\{{{name}\}}", source, re.DOTALL)
    return m.group(1).strip() if m else ""


def repoint_figures(text: str) -> tuple[str, list[str]]:
    """Swap each vector PDF for its PNG twin. Pandoc emits `<embed>` for a PDF,
    which GitHub's sanitiser strips, so this becomes an `<img>` as well. A missing
    twin is reported rather than silently left as a broken image."""
    missing: list[str] = []

    def swap(m: re.Match[str]) -> str:
        stem = Path(m.group(1)).name
        png = FIGURES / f"{stem}.png"
        if not png.is_file():
            missing.append(f"{stem}.png")
        return f'<img src="figures/{stem}.png" alt="{stem}" width="640" />'

    return EMBED.sub(swap, text), missing


def renumber(text: str, unnumbered: set[str], appendix: set[str]) -> str:
    """Shift every heading one level down and restore the LaTeX section numbers."""
    letters = iter("ABCDEFGHIJ")
    out: list[str] = []
    section = 0
    subsection = 0
    label: str | None = None  # current top-level number, or "Appendix A"

    for line in text.split("\n"):
        m = re.match(r"^(#{1,6}) (.+)$", line)
        if m is None:
            out.append(line)
            continue
        depth, title = len(m.group(1)), m.group(2).strip()
        if depth == 1:
            key = norm(title)
            subsection = 0
            if key in appendix:
                label = f"Appendix {next(letters)}"
                out.append(f"## {label}. {title}")
            elif key in unnumbered:
                label = None
                out.append(f"## {title}")
            else:
                section += 1
                label = str(section)
                out.append(f"## {label}. {title}")
        elif depth == 2 and label is not None:
            subsection += 1
            out.append(f"### {label}.{subsection} {title}")
        else:
            out.append("#" + line)
    return "\n".join(out)


def tidy(text: str) -> str:
    text = ANCHOR.sub(r"\1", text)          # keep the resolved number, drop the link
    text = text.replace("<!-- -->", "")     # pandoc's block separator
    text = "\n".join(l for l in text.split("\n") if not DIV.match(l))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def main() -> None:
    source = TEX.read_text(encoding="utf-8")
    unnumbered, appendix = tex_sections(source)

    body = convert_document()
    body, missing = repoint_figures(body)
    body = renumber(tidy(body), unnumbered, appendix)

    abstract = tidy(convert_fragment(extract_environment(source, ENVIRONMENT)))
    keywords = extract_environment(source, "keyword")
    keywords = " · ".join(k.strip() for k in re.split(r"\\sep", keywords) if k.strip())
    keywords = " ".join(keywords.split())

    parts = [HEADER.strip(), f"**Keywords:** {keywords}", "## Abstract", abstract, body]
    OUT.write_text("\n\n".join(parts) + "\n", encoding="utf-8")

    if missing:
        print(f"  warning: no PNG for {', '.join(sorted(set(missing)))}", file=sys.stderr)
    words = len(OUT.read_text(encoding="utf-8").split())
    print(f"wrote {OUT.relative_to(ROOT)}: {words} words, "
          f"{len(unnumbered)} unnumbered sections, {len(appendix)} appendix")


if __name__ == "__main__":
    main()
