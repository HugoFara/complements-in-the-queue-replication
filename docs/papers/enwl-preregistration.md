# Pre-registration: out-of-sample test of the taxonomy on fresh ENWL feeders

Written **2026-08-02, before the runs were started**, and committed before the
results existed. The point is to give Section 5's taxonomy an out-of-sample leg
and to answer one specific question a referee raised: is ENWL n2f1 — the
corpus's one counterexample, a feeder that keeps a certified irreducible
residual without co-location — rare or common?

## Feeder selection, by rule rather than by inspection

`Feeder_1` of the first four ENWL LVNS networks not already in the corpus, in
network order: **network_1, network_4, network_18, network_21**. The corpus
uses network_13/Feeder_4 (n13f4) and network_2/Feeder_1 (n2f1), which are
excluded. No feeder was solved, inspected or screened before this list was
fixed.

## Arm, identical to the n2f1 cell it is compared against

Phase-resolved OpenDSS on the shipped three-phase model; `relax` treatment
(limits clamped to the feeder's own unbalanced baseline); random placement;
K = 24 (12 generation + 12 load) at 50 kW; 20 draws; line thermal dropped
because the release specifies no `NormAmps`; substation transformer enforced.
That is `experiments/122_enwl_unbalanced.py --treatment relax --placement
random --size-kw 50 --k 24`, the command that produced `m119_n2f1_rand_50.csv`.

## Prediction

From Section 5.6's account — small UK LV feeders are electrically tight, so
every request is close to every other, the binding constraint is shared, and
alternating re-study should reach the joint optimum:

1. **Median $R_{\ast} = 0$ on at least 3 of the 4 feeders.**
2. Single-pass residuals $R_1$ positive on most of them, since these feeders
   are PV-scarce and a one-pass study leaves capacity behind even when
   re-study recovers it.
3. Where an irreducible residual does appear, it is a minority of draws rather
   than the median.

## What would falsify it

**Two or more of the four carrying a positive median $R_{\ast}$.** That would
make n2f1 typical rather than exceptional, and the paper's framing — "electrical
extent organizes the corpus, and one feeder refuses it" — would have to be
replaced by something that predicts the exception rather than reporting it.

A single positive-median feeder out of four is consistent with the account and
would be reported as such: one exception in a corpus of six LV feeders.

## What this cannot settle

Four feeders from one distribution operator's release, at one request size and
one placement rule. It tests whether the exception rate is near zero or near
one half; it does not estimate it.
