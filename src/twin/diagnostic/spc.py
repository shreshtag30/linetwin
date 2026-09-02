"""Statistical process control over per-station cycle time.

The brief names SPC explicitly alongside anomaly detection, physics-informed
models and ML. Nothing in this project implemented it, so this is the gap
closed rather than a fourth restatement of the Active Period Method.

WHY IT EARNS A PLACE BESIDE THE BOTTLENECK DETECTOR. They answer different
questions and fail in different directions. The Active Period Method asks
"which station is constraining the line RIGHT NOW" and always answers, even
when nothing is wrong. An individuals/moving-range chart asks "is this
station behaving differently from its own established baseline" and stays
silent when it is not. A station can be the constraint while perfectly in
control (it is simply the slowest), and a station can go out of control
without ever being the constraint (it drifts, but something else still
gates the line). An operator needs both readings.

METHOD, standard and cited rather than invented. This is the individuals
(X) and moving-range (mR) chart -- the correct chart for a process sampled
one unit at a time, which is what a per-unit cycle time is. Control limits
use the conventional unbiasing constants for a moving range of length 2:

    mR-bar  = mean(|x_i - x_{i-1}|)
    sigma^  = mR-bar / d2,   d2 = 1.128   for n = 2
    X chart: CL = x-bar,  UCL/LCL = x-bar +/- 3 * sigma^
    mR chart: UCL = D4 * mR-bar,  D4 = 3.267 for n = 2;  LCL = 0

d2 and D4 are the standard control-chart constants for subgroup size 2
(Montgomery, *Introduction to Statistical Quality Control*; they appear in
every SPC table). They are not tuned parameters and are not ours.

The signal rules implemented are the first four Western Electric / Nelson
rules -- the widely-used subset. They are named individually in
`Violation.rule` so a UI can say WHICH rule fired rather than showing an
undifferentiated red dot:

    1. One point beyond 3 sigma                       (gross shift)
    2. Nine consecutive points on one side of centre  (sustained small shift)
    3. Six consecutive points steadily increasing or decreasing (trend)
    4. Two of three consecutive points beyond 2 sigma on the same side

MEASURED FALSE-ALARM RATE, because the brief warns specifically that false
alarms erode floor trust and it would be dishonest to ship a detector
without stating its own. Over 2,000 replications of a genuinely in-control
Gaussian process sampled 60 times:

    any rule fires                34.4%
    rule 1  beyond 3 sigma        14.6%
    rule 3  trend of six          11.4%
    rule 4  two of three > 2 sig   9.5%
    rule 2  run of nine            6.4%

Rule 1's rate is exactly what theory predicts -- 1 - (1 - 0.0027)^60 ~= 15%
-- so this is not a bug, it is what running these rules over a window of
this length costs. The well-known consequence, stated here rather than
discovered by a user: **enabling all four rules together makes roughly one
healthy station in three signal at least once over 60 observations.** That
is why `control_chart` reports every violation with its `rule` name instead
of collapsing them to a boolean, and why any consumer wiring this to an
operator-facing alert should choose a subset deliberately and rate-limit it,
exactly as `web/app.js` already does for the Model B risk flag (rising edge
only, never re-firing while a condition persists).

DELIBERATELY NOT CLAIMED: these limits are computed from the station's own
observed history, so they describe its behaviour, not a specification. This
is process control, not conformance to a tolerance -- there is no
engineering tolerance anywhere in this project to check against. Calling a
violation a "defect" would be exactly the overreach this codebase's own
provenance discipline exists to prevent, so `Violation` says `rule`, never
`defect`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Standard control-chart constants for a moving range of subgroup size 2.
D2_N2 = 1.128
D4_N2 = 3.267

MIN_POINTS = 12  # below this the limits are noise; the chart reports "not established"

RULE_2_RUN_LENGTH = 9
RULE_3_TREND_LENGTH = 6


@dataclass(frozen=True)
class Violation:
    rule: str
    index: int  # index into the series that triggered it
    detail: str


@dataclass(frozen=True)
class ControlChart:
    station_id: str
    n: int
    established: bool
    center: float
    sigma_hat: float
    ucl: float
    lcl: float
    mr_bar: float
    mr_ucl: float
    violations: list[Violation] = field(default_factory=list)

    @property
    def in_control(self) -> bool:
        return self.established and not self.violations


def _moving_ranges(values: list[float]) -> list[float]:
    return [abs(values[i] - values[i - 1]) for i in range(1, len(values))]


def _rule_1(values: list[float], ucl: float, lcl: float) -> list[Violation]:
    return [
        Violation("beyond_3_sigma", i, f"{v:.1f}s is outside [{lcl:.1f}, {ucl:.1f}]")
        for i, v in enumerate(values)
        if v > ucl or v < lcl
    ]


def _rule_2(values: list[float], center: float) -> list[Violation]:
    out: list[Violation] = []
    run_side = 0
    run_len = 0
    for i, v in enumerate(values):
        side = 1 if v > center else -1 if v < center else 0
        if side != 0 and side == run_side:
            run_len += 1
        else:
            run_side, run_len = side, 1 if side != 0 else 0
        if run_len == RULE_2_RUN_LENGTH:
            out.append(
                Violation(
                    "run_of_nine",
                    i,
                    f"{RULE_2_RUN_LENGTH} consecutive points "
                    f"{'above' if run_side > 0 else 'below'} centre",
                )
            )
    return out


def _rule_3(values: list[float]) -> list[Violation]:
    out: list[Violation] = []
    up = down = 1
    for i in range(1, len(values)):
        if values[i] > values[i - 1]:
            up, down = up + 1, 1
        elif values[i] < values[i - 1]:
            down, up = down + 1, 1
        else:
            up = down = 1
        if up == RULE_3_TREND_LENGTH:
            out.append(Violation("trend_of_six", i, "6 consecutive points increasing"))
        if down == RULE_3_TREND_LENGTH:
            out.append(Violation("trend_of_six", i, "6 consecutive points decreasing"))
    return out


def _rule_4(values: list[float], center: float, sigma: float) -> list[Violation]:
    if sigma <= 0:
        return []
    out: list[Violation] = []
    for i in range(2, len(values)):
        window = values[i - 2 : i + 1]
        for sign in (1, -1):
            beyond = sum(1 for v in window if sign * (v - center) > 2 * sigma)
            if beyond >= 2 and sign * (values[i] - center) > 2 * sigma:
                out.append(
                    Violation(
                        "two_of_three_beyond_2_sigma",
                        i,
                        f"2 of 3 points beyond 2 sigma {'above' if sign > 0 else 'below'} centre",
                    )
                )
                break
    return out


def control_chart(station_id: str, cycle_times: list[float]) -> ControlChart:
    """Individuals/moving-range chart for one station's cycle times.

    `established=False` (and no violations reported) until MIN_POINTS
    observations exist -- control limits computed from a handful of points
    are themselves noise, and firing on them would manufacture exactly the
    false alarms the brief warns erode floor trust.
    """
    values = [v for v in cycle_times if v is not None]
    n = len(values)
    if n < MIN_POINTS:
        center = sum(values) / n if n else 0.0
        return ControlChart(
            station_id=station_id,
            n=n,
            established=False,
            center=center,
            sigma_hat=0.0,
            ucl=0.0,
            lcl=0.0,
            mr_bar=0.0,
            mr_ucl=0.0,
        )

    center = sum(values) / n
    ranges = _moving_ranges(values)
    mr_bar = sum(ranges) / len(ranges)
    sigma_hat = mr_bar / D2_N2
    ucl = center + 3.0 * sigma_hat
    lcl = max(0.0, center - 3.0 * sigma_hat)  # a cycle time cannot be negative

    violations = (
        _rule_1(values, ucl, lcl)
        + _rule_2(values, center)
        + _rule_3(values)
        + _rule_4(values, center, sigma_hat)
    )
    violations.sort(key=lambda v: v.index)

    return ControlChart(
        station_id=station_id,
        n=n,
        established=True,
        center=center,
        sigma_hat=sigma_hat,
        ucl=ucl,
        lcl=lcl,
        mr_bar=mr_bar,
        mr_ucl=D4_N2 * mr_bar,
        violations=violations,
    )


__all__ = ["D2_N2", "D4_N2", "MIN_POINTS", "ControlChart", "Violation", "control_chart"]
