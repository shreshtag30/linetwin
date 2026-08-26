"""Machine-readable classification of what LineTwin is.

This module exists so the honest positioning stated in the README is also a
runtime fact, not just prose -- `describe()` returns the same classification an
API consumer or a test can assert against.

Three independent taxonomies, none contradicting the others:

- Kritzinger, Karner, Traar, Henjes & Sihn (2018): Digital Model / Digital Shadow /
  Digital Twin, distinguished by direction of automated data flow.
- Grieves & Vickers (2017): Digital Twin Prototype / Instance / Aggregate, distinguished
  by whether a physical instance exists yet.
- Villegas, Macchi & Polenghi (2025): Model / Connected / Predictive / Prescriptive /
  Autonomous maturity levels.

Paraphrased only -- see docs/CITATIONS.md. Every publisher route to the Kritzinger
IFAC-PapersOnLine PDF was blocked at the time of writing; the three-level definition
is corroborated across secondary sources but not verbatim, so no quotation marks are
used anywhere in this module until someone has read the original.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class KritzingerClass(StrEnum):
    """Distinguished by automated data flow direction."""

    DIGITAL_MODEL = "digital_model"  # manual data flow both ways, or none
    DIGITAL_SHADOW = "digital_shadow"  # automated physical -> digital, manual back
    DIGITAL_TWIN = "digital_twin"  # automated data flow both ways


class GrievesClass(StrEnum):
    """Distinguished by whether a physical instance exists."""

    PROTOTYPE = "digital_twin_prototype"  # exists before any physical instance
    INSTANCE = "digital_twin_instance"  # paired to one specific physical instance
    AGGREGATE = "digital_twin_aggregate"  # a fleet of instances


class MaturityLevel(StrEnum):
    """Villegas, Macchi & Polenghi (2025)."""

    MODEL = "model"
    CONNECTED = "connected"
    PREDICTIVE = "predictive"
    PRESCRIPTIVE = "prescriptive"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class Classification:
    kritzinger: KritzingerClass
    grieves: GrievesClass
    maturity: MaturityLevel
    rationale: str


def describe() -> Classification:
    """LineTwin's own classification, stated once, asserted in tests.

    Digital Shadow, not Digital Twin: telemetry flows automatically from the
    simulated line to the dashboard, but control (the perturbation slider) is a
    human decision re-entered by a human -- there is no automated write-back to
    a physical or simulated control system. See sources.py: TelemetrySource is
    read-only by construction, and no route in api/ writes to a station's control
    logic.

    Digital Twin Prototype, not Instance: LineTwin models an illustrative line
    that exists before (and independently of) any specific physical factory --
    exactly the category the brief asks for ("a working proof-of-concept on
    illustrative or sample data").

    Predictive, not Prescriptive: the twin forecasts a future bottleneck and
    scores defect risk, and a human interprets that forecast to act. It does not
    yet rank or recommend interventions on its own -- the leadership ROI panel
    surfaces a number a human decides whether to act on, which is short of
    Prescriptive. Reaching Prescriptive honestly would mean the system itself
    ranks candidate interventions, which is out of scope here.
    """
    return Classification(
        kritzinger=KritzingerClass.DIGITAL_SHADOW,
        grieves=GrievesClass.PROTOTYPE,
        maturity=MaturityLevel.PREDICTIVE,
        rationale=(
            "Automated physical-to-digital telemetry with no automated write-back "
            "(Digital Shadow); models an illustrative line with no paired physical "
            "instance (Digital Twin Prototype); forecasts and scores risk but does "
            "not itself rank interventions (Predictive, not yet Prescriptive)."
        ),
    )


__all__ = ["Classification", "GrievesClass", "KritzingerClass", "MaturityLevel", "describe"]
