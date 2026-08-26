# Citation Ledger

Locked at Phase 1. Every external claim used anywhere in this project — README, code comments, phase
PDFs, or handed to teammates for the Business Proposal and Pitch — must appear in **§1** below.

The ledger has three parts, and they are never blurred:

- **§1 — Verified external sources.** Read in full, citation confirmed, safe to cite as stated.
- **§2 — Numbers explicitly forbidden.** Real figures from real papers that would be *misleading* in
  our context. Each row says why. These are not banned because they are false; they are banned
  because repeating them here would misattribute them.
- **§3 — Our own numbers.** Everything this project produces, and how each must be qualified.

A claim that is in none of these three sections does not go in the project.

---

## §1 Verified external sources

### Bottleneck detection

**Roser, C., Nakano, M., & Tanaka, M. (2001/2002).** Active Period Method — a shifting-bottleneck
detection method developed at Toyota Central R&D. Winter Simulation Conference.
*Used for:* the core diagnostic. The bottleneck is the station active for the longest uninterrupted
period; a breakdown does **not** end an active period.

**Roser, C., Lorentzen, K., & Deuse, J. (2015).** Bottleneck Walk. *Logistics Research.*
*Used for:* the operator-facing explanation ("queue building faster than downstream can drain"), and
for the fact that the method is in production use at Bosch under that name.

**Ragazzini, L., Negri, E., Fumagalli, L., & Macchi, M. (2024).** "Digital Twin-based bottleneck
prediction for improved production control." *Computers & Industrial Engineering* 192, 110231.
DOI: 10.1016/j.cie.2024.110231. Open access, CC BY-NC-ND.
*Used for:* independent validation of the Active Period Method choice, on three stated grounds — it
is data-driven and cheap to implement, it works on synthetic simulation output as well as historical
data, and it is independent of the manufacturing system modelled.
*Also used for:* the roadmap claim that predicted-bottleneck-driven order release and dispatching is
the natural next step. Their result is statistically validated (paired *t*-test, Wilcoxon signed-rank,
CLES ≈ 0.90 for the strongest pair). **Roadmap only — we do not implement production control.**
*Caveat to carry:* validated in a laboratory setting (Politecnico di Milano Industry 4.0 Lab, 7
workstations), which the authors state plainly.

**Kumbhar, M., Ng, A. H. C., & Bandaru, S. (2023).** "A digital twin based framework for detection,
diagnosis, and improvement of throughput bottlenecks." *Journal of Manufacturing Systems* 66, 92–106.
DOI: 10.1016/j.jmsy.2022.11.016. Open access, CC BY.
*Used for:* the **statistical significance gate** — one-way ANOVA across stations followed by a
Tukey–Kramer post-hoc test for unequal sample sizes, so a bottleneck is named only when the difference
is significant. This is our direct, citable answer to the brief's false-alarm warning.
*Also used for:* the sole-vs-shifting bottleneck distinction.
*Caveat to carry:* the paper argues its Busy Ratio detector is preferable to the Active Period Method
because active-period identification "fluctuates more" week to week. This is a **single-site
qualitative comparison with no ground truth** — it never establishes which detector was correct. We
therefore cite it as evidence that active-period identification benefits from a significance test on
top, **not** as evidence that the Active Period Method is wrong. Their ~10 % throughput improvement is
a projected minimum from a what-if scenario, and must be described that way.

### Defect tracing and root cause

**Detzner, A., & Eigner, M. (2018).** "A Digital Twin for Root Cause Analysis and Product Quality
Monitoring." *Proceedings of DESIGN 2018*, 1547–1558. DOI: 10.21278/idc.2018.0418.
*Used for:* three design rules — (i) the as-planned → as-built → as-maintained chain keyed per unit;
(ii) **inverting the Bill of Processes** so each process is assigned to a part, making manufacturing
changes retraceable; (iii) keeping **zero, missing, and not-applicable as three distinct states**,
never collapsed into one null.
*Used for, most importantly:* a published warning that **spurious correlations are amplified by a low
failure base rate** — with rare positives, the chance that some attribute correlates with failure by
coincidence rises, and simply raising the significance threshold then discards real signal. This is
the citable justification for our calibration-and-baseline discipline.
*Caveat to carry:* conceptual paper, no experiment, no dataset, no validation. Its authors defer the
attribute-selection problem to future work. Cite for framing and data-model design, never for results.

**Benjwal, N., Shrivastava, P. K., Dhananjaya, R. S., Hariharan, S., & Rangan, T.**
*System contextualization of manufacturing plants.* **US Patent 12,353,197 B2**, assignee **Accenture
Global Solutions Limited**, filed 30 Aug 2022, granted 8 Jul 2025.
*Used for:* prior art on graph-traversal root-cause attribution with **transfer-delay timestamp
realignment** — computing contributing-factor times as (alert time − cumulative lag) so upstream
evidence is compared on the correct time frame. We adopt the realignment idea and cite its source.
*How we differ:* the patent chains fixed thresholds (alert fires, upstream node tested against a
second threshold). We carry a calibrated probability instead, so the trace reports confidence rather
than a binary attribution.
**Citation discipline — mandatory:** a granted patent means a claim set was allowed over prior art. It
is **not** evidence the method works. Always write *discloses*, *claims*, or *describes an embodiment
in which*. **Never** *shows*, *demonstrates*, *achieves*, or *validates*. It contains no dataset, no
evaluation, and no reported performance of any kind. Format as a patent — no DOI, no journal.

### Sensor gaps and inference

**Zhu, X., Ghahramani, Z., & Lafferty, J. (2003).** "Semi-supervised learning using Gaussian fields
and harmonic functions." ICML.
*Used for:* the harmonic-extension solve over uninstrumented stations. λ = 0 recovers their
formulation exactly; λ > 0 adds the station's own cycle-time deviation and makes the system strictly
diagonally dominant, hence provably nonsingular.

**Krause, A., Singh, A., & Guestrin, C. (2008).** Near-optimal sensor placements in Gaussian
processes. *JMLR.*
*Used for:* the (1 − 1/e) greedy approximation guarantee, cited as the principled basis for the
sensor-placement value ranking.

### Digital twin positioning

**Kritzinger, W., Karner, M., Traar, G., Henjes, J., & Sihn, W. (2018).** Digital Twin in
manufacturing: a categorical literature review and classification. *IFAC-PapersOnLine.*
*Used for:* the digital model / digital shadow / digital twin distinction. Under this taxonomy
LineTwin is a **Digital Shadow** — automated one-way data flow, no write-back.
**Discipline:** paraphrase only. Every publisher route to the PDF was blocked; the three-level
definitions are corroborated across secondary sources but not verbatim. **Never use quotation marks**
until someone has read the actual open-access IFAC-PapersOnLine PDF.

**Grieves, M., & Vickers, J. (2017).** Digital Twin: Mitigating Unpredictable, Undesirable Emergent
Behavior in Complex Systems.
*Used for:* the **Digital Twin Prototype** category — a virtual construct existing before any physical
instance, which is exactly what this brief asked for. Stated alongside the Kritzinger classification
rather than instead of it.

**Villegas, L. F., Macchi, M., & Polenghi, A. (2025).** "Digital twins in manufacturing: A unified
conceptual framework." *Annual Reviews in Control* 60, 101031. DOI: 10.1016/j.arcontrol.2025.101031.
*Used for:* the maturity ladder — Model → Connected → **Predictive** → Prescriptive → Autonomous.
LineTwin is positioned honestly at **Predictive**. Also for their finding that most real deployments
sit at mid-maturity and that Prescriptive/Autonomous twins remain rare.
*Caveat:* a review of review articles (76 included from 433 records). Contains **no algorithms and no
performance metrics**. Its counts are bibliometric — never present 76/433 as evidence about digital
twin performance. Appears twice in the source folder; cite once.

**Palotai, B., Bárkányi, Á., Kis, G., & Abonyi, J. (2026).** "Moving towards digital twins: Overview
of key challenges in the process industry." *Heliyon* 12, e45350. DOI: 10.1016/j.heliyon.2026.e45350.
*Used for:* ISO 23247 reference-architecture vocabulary, the VV&A (verification, validation,
accreditation) discipline, and their conclusion that digital twin adoption is realistically a **phased**
process. Their explicit flag that unvalidated predictive claims create "false trust" supports our own
framing.
*Caveat:* process industry (refinery), not discrete manufacturing. Their proof-of-concept ran **one
week**, open-loop and human-in-the-loop, and they state results are directional rather than a
comprehensive assessment.

**Mendi, A. F. (2022).** "A Digital Twin Case Study on Automotive Production Line." *Sensors* 22(18),
6963. DOI: 10.3390/s22186963. **Single author — cite as "Mendi", never "Mendi et al."**
*Used for:* precedent that a real automotive plant deployed a sensor → broker → stream-processing →
visualisation twin, and for its human-in-the-loop, decision-support-only stance.
*Caveat — important:* this paper contains **no machine learning, no bottleneck detection algorithm,
and no sensor-gap inference**. Its analysis is threshold/trend-based across 3 sensor types and 4
predetermined scenarios. Do not imply it covers any method it does not.

**Fani, V., Bucci, I., Rossi, M., & Bandinelli, R. (2026).** "Building operational resilience: A
digital twin approach in mixed-model assembly line." *Journal of Industrial Information Integration*
52, 101122. DOI: 10.1016/j.jii.2026.101122.
*Used for:* the finding that **per-workstation thresholds outperform a single global threshold**;
human-in-the-loop alerting to the operator who decides; and their qualitative finding that adoption
depends on operator training and that operators may resist digitally-suggested actions — which
supports the brief's trust requirement.
*Caveat:* single company, single department, simulated, no confidence intervals or replication counts
reported. Its −60 % lead-time figures must never be generalised.

**Skoogh, A., et al. (2023).** Names data-driven throughput bottleneck analysis as an open research
direction and states that existing literature lacks real-world validation of these methods.
Co-authored by Roser. *Used for:* honest positioning of where this work sits.

### Simulation practice

**ProdSim** — a published, peer-reviewed, open-source SimPy production simulator for generating
synthetic data for quality prediction.
*Used for, unprompted:* "our station construction matches the published ProdSim package." Volunteering
this is stronger than waiting to be asked whether the pattern is sound.

### Datasets

**AI4I 2020 Predictive Maintenance Dataset.** UCI ML Repository id 601, 10,000 rows, CC BY 4.0.
*Used for:* the offline benchmark model (Model A). Label column is `Machine failure` — 339 positives,
3.39 %. Note the known inconsistency: the individual failure-mode columns sum to 433, not 339.

**Bosch Production Line Performance.** Kaggle, 2016.
*Used for:* **one published statistic only** — the ~0.58 % defect prevalence, as the calibration target
for our synthetic defect base rate. Source for the prevalence figure: arXiv:2101.11715.
**Never downloaded, never trained on.** Its features are anonymised (`L3_S36_F3939`) with no released
semantics, so no mapping to our features exists even in principle.

**Future Factories V2.** University of South Carolina. arXiv:2502.05020 / arXiv:2401.15544. Real
industrial-grade assembly pipeline, 292 assembly cycles, 21 distinct cycle states.
*Used for:* grounding cycle-time and state-duration parameters. Published summary statistics only.

**PyScrew.** Zenodo DOI 10.5281/zenodo.14729547. Real industrial screw-driving time series.
*Used for:* grounding fastening-station variability, relevant to automotive torque stations.

---

## §2 Numbers explicitly forbidden

Each of these is a real figure from a real source. Each is banned **in this project** for the stated
reason. This section exists so that a number cannot quietly re-enter later via a slide or a README edit.

| Figure | True source | Why it is forbidden here |
|---|---|---|
| **87.56 %** downtime reduction | Mendi 2022, own result | It is a *downtime* reduction over two 6-month windows, **not accuracy**, not defect detection, not model performance. Trivially misread as "87.56 % accurate". |
| **6.01 %** efficiency gain | Mendi 2022, own result | Same class of error. Only usable with the full before/after framing, which is more words than it is worth. |
| Unilever 90 % false-alert reduction; KINEXON 5 % faster line; "Digital Twin Genie" 54 % / 37 %; Citic >30 % / $2 M / 3.5 M kWh; Yoo Ho Son 96.83 % | Third-party studies **surveyed inside** Mendi 2022 §2 | These are not Mendi's results. Citing them via Mendi misattributes them. If ever needed, go to the primary source. |
| **~$1 million/year** | Palotai et al. 2026, own estimate | The authors explicitly caveat it as a short-test extrapolation to an assumed 8,000 operating hours, not a verified benefit. Cannot be presented as proven savings. |
| **US $9.5 B → $72.65 B by 2032, 22.6 % CAGR**; 40 % North America share; >15 % automotive share | Future Market Insights, quoted in *ISACA Journal* 2023 | Market-forecast press-release figures quoted in a trade magazine. Not findings, not peer-reviewed, two removes from source. |
| **"Tesla uses digital twin technology in every vehicle it produces"** | Vendor blog (Thinkwik), quoted in *ISACA Journal* 2023 | Vendor marketing, unverifiable as stated. |
| **−60.4 % / −62.9 %** lead-time reduction | Fani et al. 2026, own result | One company, one simulated department, no confidence intervals or replication count. Must never be generalised into an expected outcome of digital twin deployment. |
| **~10 %** throughput improvement | Kumbhar et al. 2023, own result | It is a *projected minimum from a what-if scenario* in their dashboard, not a verified realised shop-floor gain. Only usable with "projected/estimated" attached. |
| **"Two-thirds of bottlenecks differ from what managers believe"** | Roser et al., quoted **inside** Kumbhar et al. 2023 | Not Kumbhar's finding. Attribute to the primary source or drop. |
| **10²⁷ product variants**; **€500 bn** cybertronic investment by 2020 | Zagel 2006 and Eigner et al. 2015, quoted inside Detzner & Eigner 2018 | Third-party, two decades old / a stale 2015 forecast about 2020. Not the citing paper's findings. |
| **76 of 433 records**; Scopus hit counts (29,105 etc.) | Villegas et al. 2025; Palotai et al. 2026 | Bibliometric corpus sizes. Presenting them near performance discussion implies they measure something. They do not. |
| **Precision ≥ 98 %, FAR ≤ 0.5 %, MTBFA, latency < 50 ms, 500-cycle trial** | `Focus on Predictive techniques….md` (internal note) | **Uncited in the source note.** No provenance, no benchmark. Adopting them as achieved *or as targets* would fabricate a standard. Excluded entirely. |
| **"Honest ceiling MCC 0.227"** | Misreading of Mangal & Kumar (arXiv:1701.00705) | Contradicted by the same paper: 0.227 is a 3-fold-CV *training* number, their leaderboard score was 0.215, and the same authors reached 0.407 with time-based features. The phrase "honest ceiling" is deleted. |
| **`mindate_id_diff` leak attributed to Mangal & Kumar** | Actually a Kaggle forum finding | Not in that paper. Attribute to the forum with a URL, or drop. |
| **"30.1 % of working time was a real bottleneck"** | Unverified | Replaced by a verifiable statement: in the seven-machine branched example, M3 (89 %) and M5 (94 %) utilisation had overlapping 95 % CIs — utilisation could not identify the primary bottleneck — while the Active Period Method gave M5 82.4 % bottleneck probability against M3's 30.5 %. |

### Claims never to make

- Millisecond real-time fidelity. The loop is wall-clock paced at 8 Hz; *simulated-time* ordering is
  exact. Say both.
- "ISO 23247 compliant." We use its vocabulary; we have not been assessed against it.
- Verbatim Kritzinger quotations (see §1).
- Causal isolation of root causes. Drivers are **associative**. The brief itself notes these causes are
  hard to isolate from data alone; agreeing with it is stronger than overclaiming.
- Any suggestion that the Accenture patent demonstrates, validates, or benchmarks anything.

---

## §3 Our own numbers

Everything below is produced by this project. Each must carry the stated qualification wherever it
appears — including in slides we hand to teammates.

| What | Qualification that must travel with it |
|---|---|
| Cycle times, coefficients of variation, buffer capacities | Either `source:`-tagged to a real dataset, or labelled **synthetic — self-consistent, uncalibrated**, naming what would calibrate it (per-station cycle logs joined to maintenance work orders on vehicle ID) |
| Defect base rate | *Calibrated to* Bosch's published prevalence — **not trained on it** |
| Every Model B metric | Always carries `"evaluated_on": "config E (UNSEEN)"` |
| PR-AUC headline | Published beside the single-feature `cycle_time_z` logistic baseline, whatever the lift. If lift is small, the honest reframe is published instead |
| MCC | **Always printed with its threshold.** Note: MCC for a constant classifier is undefined (0/0); scikit-learn returns 0.0 with a warning. State this explicitly — a statistician will check it |
| Accuracy | Appears exactly **once** in the whole repository: in the sentence explaining why it is never reported |
| Brier score + reliability curve | Reported alongside discrimination metrics, because calibration is the part that governs false-alarm trust |
| Ablation / inference errors | Measured against **our own** `oracle_risk`, and said so |
| Units-at-risk, dollars saved | Output of the QC-latency model with `qc_lag_units` visible as a config parameter — presented as checkable arithmetic, not an oracle |
| Sensor-derived share ("Inferred — N % sensor-derived") | An exact partition of unity from the influence operator, with **zero tuning parameters** — not a heuristic decay |
| Live values (`tick`, `sim_time_s`, `real_time_factor`, per-station `state`, `queue_depth`, `throughput_uph`) | Measured from the running simulation |
| Computed values (`defect_risk`, `drivers`, `sensor_share`) | Computed by a model *from* those measurements. This distinction is volunteered, never blurred |
