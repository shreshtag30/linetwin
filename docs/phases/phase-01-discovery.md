# Phase 1 — Discovery & Requirements Lock

**LineTwin** · Accenture Innovation Challenge 2026 · Round 2 · Problem Track 4 "DigitalTwin.ai"
Deliverable 2 of 3: Working Prototype

---

## Purpose

Map every requirement in the Round 2 brief to a feature before any engine code exists, and fix the
citation ledger so that no unverified number can enter the project later. Phase 1 produces no runnable
code by design — it produces the constraints every later phase is checked against.

---

## Starting position, stated honestly

The working directory contained reference material only: five journal PDFs, a patent, three internal
notes, and the Round 1 problem statement. `git status` reported no repository. **No project code
existed at the start of this phase.**

Toolchain verified before planning: `uv` 0.11.24, Node 24.16, git 2.39.2, 19 GB free disk. The system
Python is 3.13, so `uv` will provision the pinned 3.12 interpreter itself — the pin is binding because
xgboost 3.4.1 requires ≥3.12.

---

## What Round 2 asks for

| # | Deliverable | Owner |
|---|---|---|
| 1 | **Detailed Business Proposal** — problem framing, solution design, target users, business case and impact, phased roadmap, key risks with mitigations | Teammates |
| 2 | **Working Prototype** — a functional demonstration of the core mechanism, on illustrative or sample data | **This repository** |
| 3 | **Pitch Presentation** — presenting both proposal and prototype | Teammates |

The brief states the prototype "does not need to be production-grade or use real enterprise data; a
working proof-of-concept on illustrative or sample data is expected and encouraged."

Confirmed by reading both the Round 1 problem-statement PDF and the Round 2 brief in full: there is
**no evaluation rubric, no scoring weights, no official metric, no leaderboard, and no technology
mandate or ban**. Recorded in `REQUIREMENTS.md` §E so no self-imposed constraint is later mistaken for
an external one.

---

## Deliverables produced

| Artefact | What it fixes |
|---|---|
| `docs/REQUIREMENTS.md` | All 7 real-world complexities and 6 solutioning areas traced to a feature, a module, and the evidence that will prove it |
| `docs/CITATIONS.md` | Three-part ledger: verified sources · forbidden numbers · our own numbers with mandatory qualifications |
| `docs/DECISIONS.md` | Identity, architecture, absorbed changes, scope cuts, contingency ladder, open items |
| `docs/PRIOR_ART.md` | The Accenture patent: what it claims, what we adopt, how we differ, and mandatory citation discipline |
| `docs/tracker.html` | Live progress tracker, published |

---

## Findings that changed the build

Twelve sources were read in full during this phase. Four produced changes that were not in the
original build brief.

**1 · A significance gate before naming any bottleneck.**
Kumbhar, Ng & Bandaru (2023) validate bottleneck claims with one-way ANOVA across stations followed by
a Tukey–Kramer post-hoc test for unequal sample sizes. Adopted. Our detector now returns *"no
significant bottleneck"* rather than always naming a station. This is a published, citable answer to
the brief's warning that false alarms erode floor-level trust.

The same paper argues its Busy Ratio detector is preferable to the Active Period Method because
active-period identification fluctuates week to week. That comparison is single-site with no ground
truth and never establishes which detector was correct, so it does not overturn our method choice —
but the volatility observation is real, and the significance test is the right response to it.

**2 · Three-state value tagging.**
Detzner & Eigner (2018) require that zero, missing, and not-applicable remain three distinct states,
never collapsed. Combined with the OBSERVED / INFERRED / SIMULATED tagging from the internal
architecture note, every value in the twin now carries its provenance, a confidence, and a freshness.
This directly prevents the failure mode of treating an estimate as a measurement — which matters more
here than usual, because a third of our stations have no instrumentation at all.

The same paper supplies something more valuable: a published warning that **spurious correlations are
amplified by a low failure base rate**, and that raising the significance threshold to compensate
discards real signal. That is the citable justification for our calibration-and-baseline discipline,
and it comes from the literature rather than from us asserting it.

**3 · Transfer-delay timestamp realignment.**
US 12,353,197 B2 discloses computing contributing-factor timestamps as *(alert time − cumulative lag)*
so upstream evidence is compared on the correct time frame. Adopted directly, with attribution. See
below.

**4 · Per-station rather than global thresholds.**
Fani et al. (2026) find per-workstation thresholds outperform a single global threshold. Applied
wherever thresholds are used.

---

## Prior art: a patent held by the judging organisation

LineTwin's defect-genealogy feature falls within the field of **US 12,353,197 B2**, *System
contextualization of manufacturing plants*, assignee **Accenture Global Solutions Limited**, granted
8 July 2025.

This is declared rather than discovered. The patent claims graph-traversal root-cause attribution with
transfer-delay realignment. We adopt the realignment and cite it. We differ in attribution: the patent
chains fixed thresholds and produces a binary result with no disclosed false-positive rate; we carry a
calibrated probability, publish the calibration curve, and gate bottleneck claims on a significance
test.

Its contribution is *contextualisation* — getting messy plant data into a queryable graph. Ours is
*inference and calibration* — what to do when that graph has holes. Complementary, not competing.

Citation discipline is mandatory and recorded in `PRIOR_ART.md`: a granted patent means a claim set
was allowed over prior art, **not** that the method works. Always *discloses* or *claims*; never
*demonstrates* or *validates*. The document contains no dataset, no evaluation, and no performance
figure of any kind, so no number from it may be cited.

---

## Data: simulated is not fabricated

*Fabricating* means inventing numbers and presenting them as measured. *Simulating* means a
discrete-event queueing model computing telemetry from stated first principles — seed-reproducible,
derived, and labelled as model output. The brief explicitly encourages the second.

A genuine search was run for real automotive assembly-line data rather than assuming none existed. The
finding: **per-station working/starved/blocked state across a connected line topology is essentially
not public**, because it is proprietary MES and PLC data. What exists nearby:

| Dataset | Verdict |
|---|---|
| Bosch Production Line Performance | Real automotive, 51 stations — but features are anonymised with no released semantics, so no mapping to our features exists even in principle. **Used for one published statistic only** (~0.58 % defect prevalence, as our calibration target). Never downloaded, never trained on |
| Future Factories V2 (Univ. of South Carolina) | Real industrial assembly pipeline, 292 cycles, 21 cycle states. **Closest real analogue** — grounds cycle-time and state-duration parameters |
| PyScrew | Real industrial screw-driving series — grounds fastening-station variability, relevant to automotive torque stations |
| AI4I 2020 (UCI, CC BY 4.0) | Real, public, 10k rows — the offline benchmark model, proving the modelling capability on real data with real metrics |
| CarDA | Real car-door assembly, but RGB-D video and motion capture — wrong modality for a discrete-event twin |

**Consequence for the build:** simulation parameters are calibrated against cited sources rather than
chosen from thin air. Every value in `scenarios/line6.yaml` carries a `source:` field, or is stamped
`synthetic — uncalibrated` with a note naming what would calibrate it.

---

## Sources rejected

**`deep-research-report (3).md`** — a generic data-science-competition playbook assuming a leaderboard,
an official numeric metric, GPU-scale deep learning, a 3–6 person team and a 12–24 week schedule. None
of those exist in this challenge. Following it would have optimised for the wrong objective entirely.

**Uncited performance thresholds** in an internal predictive-techniques note (precision ≥ 98 %, false
alarm rate ≤ 0.5 %, latency < 50 ms, 500-cycle trial). These have no provenance or benchmark in the
source. Adopting them as achieved *or even as targets* would fabricate a standard. Excluded entirely;
the staged-validation-gate narrative around them survives as a single roadmap sentence.

**Thirteen individual figures** from otherwise-good papers are named and banned in `CITATIONS.md` §2 —
each real, each misleading in our context. The most dangerous are Mendi's 87.56 % (a *downtime*
reduction, trivially misread as accuracy) and Palotai's ~$1 M/year (self-caveated by its authors as an
unverified extrapolation).

---

## Exit criteria

| Criterion | Status |
|---|---|
| Every brief complexity and solutioning area traced to a feature and its evidence | Met — `REQUIREMENTS.md` §A, §B |
| Citation ledger locked, forbidden figures named individually | Met — `CITATIONS.md` §2, 13 figures + 5 claim types |
| Identity, licence, station-count framing and positioning decided | Met — `DECISIONS.md` §1 |
| Scope cuts recorded so later phases cannot quietly re-add them | Met — `DECISIONS.md` §4 |
| Prior-art position documented with mandatory citation discipline | Met — `PRIOR_ART.md` |
| Tracker published | Met |

---

## Open items carried forward

None block Phase 2. Defaults are in force until overridden.

1. **Repo visibility** — local only; no remote push without explicit sign-off.
2. **Hosted deployment** — needs an account; deferred to Phase 10, falls back to a documented
   limitations entry.
3. **`CITATION.cff` authorship** — placeholder until teammate names are supplied.
4. **Fresh-machine clean-clone test** — unassigned. Should be run by someone other than the author,
   ideally on a machine without Python installed; any friction they hit is friction a judge hits.

---

## Next

**Phase 2 — Scaffold.** Repository, pinned dependencies with ML and dev groups isolated from the server
import path, CI on ubuntu and windows, and the frozen data contract that every later phase codes
against. Exit gate: 60 valid fixture frames conforming to the frozen contract, CI green on both
operating systems.
