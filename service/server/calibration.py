"""Turn realised outcomes into a hit rate the engine can gate on.

The confluence score in `strategy_engine.py` measures agreement between
indicators. It is not a probability, and treating it as one is how a bot ends
up "90% confident" about a setup that has never once worked. This module keeps
the other half of the story: what actually happened, bucketed by the conditions
the trade was taken under, so the gate can eventually run on evidence.

Two things matter more than the arithmetic here.

**Small samples lie.** Three wins out of three is not a 100% strategy, it is
three coin flips. Every rate is reported as a Wilson score lower bound, which
answers "given what I have seen, what is the worst plausible true rate?" and
converges on the raw rate only as the sample grows. A bucket with too few
trades reports no probability at all rather than a flattering one.

**A hit rate is not an edge.** Eight wins of 0.3R and two losses of 1R is an
80% strategy that loses money. Expectancy in R is tracked alongside, and the
gate consults both.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Sequence


class Outcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    # Closed by something other than the stop or target — a manual exit, a
    # shutdown, an expiry. Counted in the sample but not as a directional
    # verdict, so it cannot flatter or damage the hit rate.
    SCRATCH = "scratch"


@dataclass(frozen=True)
class TradeRecord:
    """One closed trade, reduced to what calibration needs.

    ``r_multiple`` is profit measured in units of the risk taken: -1 means the
    stop was hit as planned, +2 means twice the risked amount was made. Using R
    rather than currency makes trades of different sizes comparable.
    """

    symbol: str
    interval: str
    regime: str
    direction: str
    confluence: float
    outcome: Outcome
    r_multiple: float

    @property
    def bucket(self) -> "Bucket":
        return Bucket(
            regime=self.regime,
            direction=self.direction,
            confluence_band=confluence_band(self.confluence),
        )


@dataclass(frozen=True)
class Bucket:
    """The conditions a trade was taken under.

    Deliberately coarse. Bucketing by symbol and interval too would split the
    history so finely that no bucket ever reaches a usable sample size — the
    classic way a calibration layer ends up never calibrating anything.
    """

    regime: str
    direction: str
    confluence_band: str

    def key(self) -> str:
        return f"{self.regime}|{self.direction}|{self.confluence_band}"


# Confluence bands. Anything under 50 is not a setup this engine would take, so
# the low end is one bucket rather than several empty ones.
CONFLUENCE_BANDS = ((0.0, 50.0), (50.0, 65.0), (65.0, 80.0), (80.0, 101.0))


def confluence_band(confluence: float) -> str:
    for low, high in CONFLUENCE_BANDS:
        if low <= confluence < high:
            return f"{low:.0f}-{high if high <= 100 else 100:.0f}"
    return "80-100" if confluence >= 100 else "0-50"


def wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float:
    """Worst plausible true rate at ~95% confidence, given what has been seen.

    Chosen over the raw rate because the raw rate is most wrong exactly when it
    is most tempting: early, on a tiny sample, after a lucky streak.
    """
    if total <= 0:
        return 0.0
    phat = wins / total
    denominator = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


@dataclass(frozen=True)
class BucketStats:
    """What the history says about one set of conditions."""

    bucket: Bucket
    total: int
    wins: int
    losses: int
    scratches: int
    raw_hit_rate: float
    confident_hit_rate: float
    expectancy_r: float
    sample_is_usable: bool

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket.key(),
            "regime": self.bucket.regime,
            "direction": self.bucket.direction,
            "confluence_band": self.bucket.confluence_band,
            "total": self.total,
            "wins": self.wins,
            "losses": self.losses,
            "scratches": self.scratches,
            "raw_hit_rate": round(self.raw_hit_rate * 100, 2),
            "confident_hit_rate": round(self.confident_hit_rate * 100, 2),
            "expectancy_r": round(self.expectancy_r, 4),
            "sample_is_usable": self.sample_is_usable,
        }


@dataclass(frozen=True)
class CalibrationConfig:
    """How much evidence is enough, and what counts as good enough."""

    # Decided trades (wins + losses) needed before a bucket's rate is used.
    min_sample: int = 30
    # Confident hit rate a bucket must clear once it has a usable sample.
    min_hit_rate: float = 0.55
    # Expectancy floor in R. A high hit rate with negative expectancy is a
    # losing strategy wearing a good-looking number.
    min_expectancy_r: float = 0.0
    # Confluence a proposal must clear while the bucket is still learning.
    confluence_gate: float = 90.0


def summarise(records: Sequence[TradeRecord], bucket: Bucket) -> BucketStats:
    """Fold the history for one bucket into its statistics."""
    relevant = [r for r in records if r.bucket == bucket]
    wins = sum(1 for r in relevant if r.outcome is Outcome.WIN)
    losses = sum(1 for r in relevant if r.outcome is Outcome.LOSS)
    scratches = sum(1 for r in relevant if r.outcome is Outcome.SCRATCH)
    decided = wins + losses

    raw = wins / decided if decided else 0.0
    confident = wilson_lower_bound(wins, decided) if decided else 0.0
    expectancy = sum(r.r_multiple for r in relevant) / len(relevant) if relevant else 0.0

    return BucketStats(
        bucket=bucket,
        total=len(relevant),
        wins=wins,
        losses=losses,
        scratches=scratches,
        raw_hit_rate=raw,
        confident_hit_rate=confident,
        expectancy_r=expectancy,
        sample_is_usable=decided >= CalibrationConfig().min_sample,
    )


def summarise_all(records: Sequence[TradeRecord]) -> list[BucketStats]:
    """Statistics for every bucket present in the history, busiest first."""
    buckets = {r.bucket for r in records}
    stats = [summarise(records, bucket) for bucket in buckets]
    return sorted(stats, key=lambda s: (-s.total, s.bucket.key()))


@dataclass(frozen=True)
class Verdict:
    """Whether to take the trade, and on what basis."""

    allowed: bool
    basis: str          # "calibrated" once there is evidence, else "confluence"
    probability: Optional[float]   # the confident hit rate, when there is one
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "basis": self.basis,
            "probability": None if self.probability is None else round(self.probability * 100, 2),
            "reasons": list(self.reasons),
        }


def gate(
    *,
    confluence: float,
    regime: str,
    direction: str,
    history: Sequence[TradeRecord],
    config: Optional[CalibrationConfig] = None,
) -> Verdict:
    """Decide whether this setup has earned a trade.

    Confluence rules while a bucket is still learning; the measured rate takes
    over once there is enough of it to mean something. This is the handover the
    user asked for — confluence first, calibration once it is real.
    """
    config = config or CalibrationConfig()
    bucket = Bucket(
        regime=regime,
        direction=direction,
        confluence_band=confluence_band(confluence),
    )
    stats = summarise(history, bucket)
    decided = stats.wins + stats.losses

    if decided < config.min_sample:
        allowed = confluence >= config.confluence_gate
        reason = (
            f"{decided} decided trades in {bucket.key()}, need {config.min_sample} "
            f"before the measured rate means anything; gating on confluence "
            f"{confluence:.1f} against {config.confluence_gate}"
        )
        return Verdict(
            allowed=allowed,
            basis="confluence",
            probability=None,
            reasons=[reason],
        )

    reasons = [
        f"{decided} decided trades in {bucket.key()}: "
        f"{stats.wins}W/{stats.losses}L, confident rate "
        f"{stats.confident_hit_rate * 100:.1f}% (raw {stats.raw_hit_rate * 100:.1f}%), "
        f"expectancy {stats.expectancy_r:+.2f}R"
    ]
    allowed = True
    if stats.confident_hit_rate < config.min_hit_rate:
        allowed = False
        reasons.append(
            f"confident rate below the {config.min_hit_rate * 100:.0f}% floor"
        )
    if stats.expectancy_r <= config.min_expectancy_r:
        allowed = False
        reasons.append(
            f"expectancy {stats.expectancy_r:+.2f}R at or below the "
            f"{config.min_expectancy_r:+.2f}R floor — a hit rate is not an edge"
        )

    return Verdict(
        allowed=allowed,
        basis="calibrated",
        probability=stats.confident_hit_rate,
        reasons=reasons,
    )


def classify(entry: float, exit_price: float, stop_loss: float, direction: str) -> tuple[Outcome, float]:
    """Reduce a closed trade to an outcome and an R multiple.

    R is signed profit over the risk that was taken, so a trade stopped out
    exactly at its stop scores -1.0 regardless of instrument or size.
    """
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return Outcome.SCRATCH, 0.0

    if direction == "long":
        profit = exit_price - entry
    elif direction == "short":
        profit = entry - exit_price
    else:
        return Outcome.SCRATCH, 0.0

    r_multiple = profit / risk
    if profit > 0:
        return Outcome.WIN, r_multiple
    if profit < 0:
        return Outcome.LOSS, r_multiple
    return Outcome.SCRATCH, 0.0
