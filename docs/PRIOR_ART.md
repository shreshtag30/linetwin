# Prior Art Note

## US 12,353,197 B2 — assignee: Accenture Global Solutions Limited

**Full citation.** Benjwal, N., Shrivastava, P. K., Dhananjaya, R. S., Hariharan, S., & Rangan, T.
*System contextualization of manufacturing plants.* US Patent 12,353,197 B2. Assignee: Accenture
Global Solutions Limited, Dublin, IE. Filed 30 August 2022; pre-grant publication US 2024/0069532 A1
(29 February 2024); **granted 8 July 2025**. CPC G05B 19/41865, G05B 19/4183. 17 claims, 4 drawing
sheets. Patents have no DOI and no journal — cite as a patent.

**Why this note exists.** LineTwin's defect-genealogy feature — walk upstream from a detected defect,
realign timestamps by cumulative transfer delay, attribute a likely origin — falls within the field of
a granted patent held by the organisation running this challenge. Discovering that during judging
would be considerably worse than declaring it now. Naming it converts a potential ambush into evidence
that we surveyed the field properly.

---

## What the patent discloses

A method for building a queryable digital twin of a plant by mapping heterogeneous, non-standardised
plant data onto a hierarchical template model conforming to a predefined taxonomy (ISO 14224 levels
L1–L9), producing a **Manufacturing Plant Analytics Graph (MPAG)** — a directed graph of assets,
processes, sub-processes and their relationships.

Elements relevant to our work:

- **Root cause by graph traversal (claim 1).** An alert fires at node A → identify node B connected to
  A by defined relationships → test B's metric against a second threshold → if crossed, label B's
  metric the root cause of A's alert → adjust operation accordingly. Dependent claims 3 and 4 cover B
  being directly connected, or connected through at least a third representation — i.e. multi-hop
  tracing.
- **Transfer-delay realignment.** Relationships carry a `transfer delay` property. Contributing-factor
  timestamps are computed as *(alert time − cumulative time lag)*, realigning all contributing factors
  onto the current process's time frame before analysis. The specification notes that doing this
  *before* thresholds are crossed identifies root causes more efficiently than time-adjusting
  afterwards.
- **Generic path / parent generic path.** Data lacking a unique identifier is tagged with a
  filesystem-like hierarchy string built by concatenating raw-data columns with the source filename.
  Query the graph for that path; if found, update the node; if only the *parent* path is found,
  instantiate a new twin node beneath it.
- **Edge device as single point of contact** per plant, performing pre-processing.

---

## What we adopt, and how we differ

| Aspect | The patent | LineTwin |
|---|---|---|
| Trace mechanism | Graph traversal upstream from an alert | Same idea, adopted and cited. We traverse the per-unit event log along the line topology |
| Time handling | Transfer-delay realignment: (alert time − cumulative lag) | **Adopted directly, with attribution.** This is the single most useful thing in the document for us, and it solves a real problem in our genealogy trace |
| Attribution logic | **Threshold chaining** — upstream node's metric tested against a second fixed threshold; attribution is binary | **Continuous confidence, not a binary label.** The trace reports a monotone confidence from the origin visit's cycle-time z-score, plus the affected unit range explicitly. **Correction:** this row previously said "produced by an isotonically-calibrated model" — it is not. It is a fixed logistic map `1/(1+e^(−z/2))` (`diagnostic/genealogy.py`), and that function's own docstring says it is "deliberately NOT claimed as calibrated against any real outcome", because on a synthetic line there is no ground truth for "was this really the origin". Continuous and monotone is still a genuine difference from binary threshold chaining; calibrated is a stronger word that was not earned |
| False-positive control | None disclosed. No reported false-positive rate | ANOVA + Tukey–Kramer significance **annotation** on the bottleneck side (an annotation, not a suppression gate — see `diagnostic/bottleneck.py`); on the risk side, the operating point published in full: precision, recall, flag rate, false alarms per true catch, and the confusion matrix at the model's own MCC-tuned threshold, beside a `cycle_time_z` baseline. (This row previously claimed "Brier score and reliability curve" — neither is computed for Model B; corrected rather than left standing) |
| Evidence of performance | **None.** No dataset, no evaluation, no baseline, no metric anywhere in the document | Metrics on a held-out line configuration, published beside a baseline, with the label-generating process documented before any metric was computed |
| Sensor gaps | Generic-path mechanism places unmapped legacy sources into the hierarchy | Harmonic extension actually *estimates state* at stations with no instrumentation, with an exact partition-of-unity attribution of how much of each estimate is sensor-derived |
| Scope | Whole-plant contextualisation across heterogeneous sources; taxonomy-driven | One line, deliberately. Depth over breadth |

**The honest summary of the difference:** the patent's contribution is *contextualisation* — getting
messy plant data into a coherent, queryable graph so that traversal becomes possible at all. Ours is
*inference and calibration* — what to do when the graph has holes in it, and how to earn trust in an
estimate. These are complementary, not competing. The MPAG is a plausible substrate that LineTwin's
inference and genealogy layers could run on top of.

---

## Citation discipline — mandatory

A granted patent establishes that a claim set was allowed over the cited art. **It is not evidence
that the method works, is accurate, or outperforms anything.**

**Always write:** *discloses*, *claims*, *describes an embodiment in which*, *is the subject of*.

**Never write:** *shows*, *demonstrates*, *achieves*, *reports*, *validates*, *proves*.

Additional rules:

- **Cite no number from it.** Every figure in the document is an illustrative example inside a
  hypothetical embodiment — a throughput threshold "190 to 220" with an observed "245", a motor at
  "34000 RPM", milk pasteurisation as a worked domain. None are measurements. None are benchmarks.
- **Do not attribute its examiner-cited references** (Mammoser, Hoppes, Höfig, Kymal, Altare, and
  others) to these inventors. That list is prior art cited *against* the application.
- Its boilerplate scope disclaimers ("should not be construed as limitations on the scope") are legal
  language, not scientific caveats — do not read them as a limitations section. The document has none.
- When contrasting our approach with the patent's threshold chaining, frame it as a **design contrast**,
  never a benchmark claim. We have not compared the two empirically and cannot claim to have.

---

## How to answer the question if it is asked

> *"Are you aware Accenture holds a patent covering this?"*

Yes — US 12,353,197 B2, granted July 2025, and it is documented in `docs/PRIOR_ART.md` along with
which of its ideas we adopted. We took its transfer-delay realignment directly and cite it. Where we
differ is attribution: the patent chains fixed thresholds and reports a binary result with no disclosed
false-positive rate; we carry a continuous confidence rather than a binary label, publish the risk
model's full operating point (precision, recall, flag rate, false alarms per true catch, confusion
matrix), and **annotate** bottleneck claims with a significance test — because the brief specifically
warns that false alarms erode floor-level trust. Two words in that sentence were previously stronger
than the code: the genealogy confidence is monotone but not calibrated against any outcome, there is
no calibration curve, and the significance test annotates rather than gates (a hard gate would delay
detection past the 2 s live-response requirement — `diagnostic/bottleneck.py` explains why). The patent solves getting plant data into a traversable graph. We are working
the layer above: what to do when that graph has holes in it.
