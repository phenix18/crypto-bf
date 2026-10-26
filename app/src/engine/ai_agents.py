"""A council of agents, each with one job, deciding together.

The user asked for several AIs with defined roles that work together to trade.
This is that, with one structural constraint that shapes everything else:

**No agent can open a position.** The council runs only on a proposal the
deterministic engine has already produced, it is never asked for a direction,
and its arithmetic can only lower conviction, never raise it. A hallucinating
model can therefore refuse a good trade — annoying — but cannot invent a bad
one, which is the failure that costs money.

The roles:

- **analyst** reads the indicator picture and says whether the setup is clean
  or whether something about it is off.
- **risk** holds the account's side: exposure, drawdown, what a loss here costs.
- **historian** speaks for the realised record of setups like this one.
- **macro** brings market-wide context (breadth, dominance, the 24h tape).
- **arbiter** is not a model. It is the aggregation at the bottom of this file,
  deliberately arithmetic, because the final word on whether money moves should
  be auditable line by line.

Every agent has a deterministic fallback drawn from the same layers that
produced the briefing, so the council reaches the same verdict with no API key,
no network, and no quota. A model makes the reasoning richer; it is never load
bearing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence

from llm_client import LlmClient, NullClient

MAX_RATIONALE = 400


class Role(str, Enum):
    ANALYST = "analyst"
    RISK = "risk"
    HISTORIAN = "historian"
    MACRO = "macro"


class Stance(str, Enum):
    CONFIRM = "confirm"
    ABSTAIN = "abstain"
    VETO = "veto"


# A veto from these two ends it. They speak for the account and for the record;
# neither is a matter of opinion. The analyst and the macro agent get a say, not
# a verdict.
HARD_VETO_ROLES = frozenset({Role.RISK, Role.HISTORIAN})


@dataclass(frozen=True)
class Opinion:
    """One agent's view. Note what is absent: a direction, a size, a price.

    Agents judge a proposal; they do not author one. Keeping direction out of
    this type is what makes that impossible to get wrong later.
    """

    role: Role
    stance: Stance
    confidence: float
    rationale: str
    source: str  # "fallback", or the model that answered

    def to_dict(self) -> dict:
        return {
            "role": self.role.value,
            "stance": self.stance.value,
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale,
            "source": self.source,
        }


@dataclass(frozen=True)
class Briefing:
    """Everything the council is allowed to see, and nothing else.

    Assembled from the deterministic layers: the strategy proposal, the risk
    decision, the calibration verdict, optional market breadth. One flat
    structure so a run can be replayed exactly from the audit trail.
    """

    symbol: str
    interval: str
    proposal: dict
    risk: dict = field(default_factory=dict)
    calibration: dict = field(default_factory=dict)
    breadth: dict = field(default_factory=dict)
    account: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "proposal": self.proposal,
            "risk": self.risk,
            "calibration": self.calibration,
            "breadth": self.breadth,
            "account": self.account,
        }


# ------------------------------------------------------------------- prompts --

_SHARED_RULES = """
You are one member of a trading council. A deterministic engine has already
chosen the direction and the levels; you are reviewing its work.

You cannot open, size, or reverse a trade. Do not suggest a different
direction, a different entry, or a different stop — those are not yours.

Answer with JSON only, no prose around it, exactly:
{"stance": "confirm" | "abstain" | "veto", "confidence": 0.0-1.0,
 "rationale": "one or two sentences"}

confirm = the setup holds up from where you are looking.
abstain  = nothing in your remit speaks either way.
veto     = something in your remit makes this trade a bad idea, and say what.
"""

ROLE_PROMPTS = {
    Role.ANALYST: _SHARED_RULES + """
You are the technical analyst. You look at the indicator readings, the regime,
and how the individual checks voted. Veto when the picture is internally
inconsistent: momentum against the trade, a target the structure sits on top
of, volatility that makes the stop noise. A merely ordinary setup is not a
veto — the engine's own thresholds already handle thin ones.
""",
    Role.RISK: _SHARED_RULES + """
You are the risk officer. You care about what a loss here costs the account:
open exposure, distance from peak equity, the day's realised loss, how close
the sizing sits to its caps. Veto when taking this trade would leave the
account badly placed even if the trade is sound. You are not judging the chart.
""",
    Role.HISTORIAN: _SHARED_RULES + """
You are the historian. You speak only for the realised record of trades taken
in conditions like these: sample size, confident hit rate, expectancy in R.
Veto when the record argues against this bucket. When the sample is too small
to say anything, abstain and say so — do not borrow conviction from the chart.
""",
    Role.MACRO: _SHARED_RULES + """
You are the macro observer. You hold market-wide context: total market cap
direction, dominance, the 24h move in the majors, whether the tape is broadly
risk-on or risk-off. Veto when the wider market is moving hard against this
trade. With no breadth data in the briefing, abstain.
""",
}


# ------------------------------------------------------------------- parsing --


def extract_json(text: str) -> Optional[dict]:
    """Find the JSON object in a model's reply.

    Models fence it, preface it, apologise after it. Scanning for the first
    balanced object is less brittle than demanding a clean payload, and a reply
    we cannot parse returns ``None`` — which sends the agent to its fallback
    rather than guessing at an intent.
    """
    if not text:
        return None
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    start = -1
                    continue
                return parsed if isinstance(parsed, dict) else None
            if depth < 0:
                depth = 0
    return None


def parse_opinion(role: Role, text: str, model: str) -> Optional[Opinion]:
    """Turn a reply into an opinion, or nothing at all.

    Anything unparseable, any stance that is not one of the three, a confidence
    that is not a number — all of it returns ``None``. Guessing that a
    malformed reply "probably meant confirm" is exactly the kind of leniency
    that ends with a position nobody authorised.
    """
    payload = extract_json(text)
    if payload is None:
        return None

    raw_stance = str(payload.get("stance", "")).strip().lower()
    try:
        stance = Stance(raw_stance)
    except ValueError:
        return None

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if confidence != confidence or confidence in (float("inf"), float("-inf")):
        return None
    confidence = max(0.0, min(1.0, confidence))

    rationale = str(payload.get("rationale", "")).strip()[:MAX_RATIONALE]
    return Opinion(
        role=role,
        stance=stance,
        confidence=confidence,
        rationale=rationale or "(no rationale given)",
        source=f"model:{model}",
    )


# -------------------------------------------------------------- the fallbacks --


def fallback_analyst(briefing: Briefing) -> Opinion:
    proposal = briefing.proposal
    confluence = float(proposal.get("confluence") or 0.0)
    blockers = proposal.get("blockers") or []
    if blockers:
        return Opinion(
            role=Role.ANALYST,
            stance=Stance.VETO,
            confidence=1.0,
            rationale=f"engine blockers: {'; '.join(blockers)}",
            source="fallback",
        )
    if confluence >= 80:
        stance, confidence = Stance.CONFIRM, 0.9
    elif confluence >= 65:
        stance, confidence = Stance.CONFIRM, 0.7
    elif confluence >= 50:
        stance, confidence = Stance.ABSTAIN, 0.5
    else:
        stance, confidence = Stance.VETO, 0.6
    return Opinion(
        role=Role.ANALYST,
        stance=stance,
        confidence=confidence,
        rationale=(
            f"confluence {confluence:.1f} in a {proposal.get('regime')} regime, "
            f"risk/reward {proposal.get('risk_reward')}"
        ),
        source="fallback",
    )


def fallback_risk(briefing: Briefing) -> Opinion:
    risk = briefing.risk
    if not risk:
        # No risk decision in the briefing is not "fine". The risk layer is
        # mandatory; its absence is a veto.
        return Opinion(
            role=Role.RISK,
            stance=Stance.VETO,
            confidence=1.0,
            rationale="no risk decision in the briefing",
            source="fallback",
        )
    reasons = "; ".join(risk.get("reasons") or []) or "no detail"
    if not risk.get("approved"):
        return Opinion(
            role=Role.RISK,
            stance=Stance.VETO,
            confidence=1.0,
            rationale=f"risk layer refused: {reasons}",
            source="fallback",
        )
    return Opinion(
        role=Role.RISK,
        stance=Stance.CONFIRM,
        confidence=0.9,
        rationale=f"sized within limits: {reasons}",
        source="fallback",
    )


def fallback_historian(briefing: Briefing) -> Opinion:
    calibration = briefing.calibration
    if not calibration:
        return Opinion(
            role=Role.HISTORIAN,
            stance=Stance.VETO,
            confidence=1.0,
            rationale="no calibration verdict in the briefing",
            source="fallback",
        )
    reasons = "; ".join(calibration.get("reasons") or []) or "no detail"
    if not calibration.get("allowed"):
        return Opinion(
            role=Role.HISTORIAN,
            stance=Stance.VETO,
            confidence=1.0,
            rationale=f"gate closed: {reasons}",
            source="fallback",
        )
    if calibration.get("basis") == "confluence":
        # Allowed, but on confluence rather than evidence. Saying "confirm"
        # here would let the chart's conviction masquerade as a track record.
        return Opinion(
            role=Role.HISTORIAN,
            stance=Stance.ABSTAIN,
            confidence=0.5,
            rationale=f"not enough closed trades to speak: {reasons}",
            source="fallback",
        )
    probability = calibration.get("probability")
    return Opinion(
        role=Role.HISTORIAN,
        stance=Stance.CONFIRM,
        confidence=0.9 if (probability or 0) >= 60 else 0.7,
        rationale=f"measured record supports it: {reasons}",
        source="fallback",
    )


def fallback_macro(briefing: Briefing) -> Opinion:
    breadth = briefing.breadth
    if not breadth:
        return Opinion(
            role=Role.MACRO,
            stance=Stance.ABSTAIN,
            confidence=0.3,
            rationale="no market breadth data available",
            source="fallback",
        )
    change = breadth.get("percent_change_24h")
    direction = (briefing.proposal.get("direction") or "flat").lower()
    try:
        change = float(change)
    except (TypeError, ValueError):
        return Opinion(
            role=Role.MACRO,
            stance=Stance.ABSTAIN,
            confidence=0.3,
            rationale="breadth data has no usable 24h change",
            source="fallback",
        )

    # Only a hard, opposing tape counts. A mild drift against the trade is
    # normal and vetoing on it would block most scalps.
    against = (direction == "long" and change <= -5.0) or (
        direction == "short" and change >= 5.0
    )
    if against:
        return Opinion(
            role=Role.MACRO,
            stance=Stance.VETO,
            confidence=0.7,
            rationale=f"market is {change:+.2f}% over 24h, against a {direction}",
            source="fallback",
        )
    aligned = (direction == "long" and change >= 1.0) or (
        direction == "short" and change <= -1.0
    )
    return Opinion(
        role=Role.MACRO,
        stance=Stance.CONFIRM if aligned else Stance.ABSTAIN,
        confidence=0.6 if aligned else 0.4,
        rationale=f"market {change:+.2f}% over 24h against a {direction}",
        source="fallback",
    )


FALLBACKS = {
    Role.ANALYST: fallback_analyst,
    Role.RISK: fallback_risk,
    Role.HISTORIAN: fallback_historian,
    Role.MACRO: fallback_macro,
}


# -------------------------------------------------------------------- agents --


def briefing_for(role: Role, briefing: Briefing) -> str:
    """What this role gets to read.

    Each agent sees its own slice plus the proposal. Handing every agent the
    whole picture sounds generous and is not: the risk officer reasoning about
    RSI, or the historian about the chart, is how four roles collapse into one
    opinion repeated four times.
    """
    proposal = briefing.proposal
    core = {
        "symbol": briefing.symbol,
        "interval": briefing.interval,
        "direction": proposal.get("direction"),
        "regime": proposal.get("regime"),
        "confluence": proposal.get("confluence"),
        "entry": proposal.get("entry"),
        "stop_loss": proposal.get("stop_loss"),
        "take_profit": proposal.get("take_profit"),
        "risk_reward": proposal.get("risk_reward"),
    }
    if role is Role.ANALYST:
        core["readings"] = proposal.get("readings")
        core["votes"] = proposal.get("votes")
        core["blockers"] = proposal.get("blockers")
    elif role is Role.RISK:
        core["risk_decision"] = briefing.risk
        core["account"] = briefing.account
    elif role is Role.HISTORIAN:
        core["calibration"] = briefing.calibration
    elif role is Role.MACRO:
        core["market_breadth"] = briefing.breadth
    return json.dumps(core, default=str, sort_keys=True)


def consult(role: Role, briefing: Briefing, client: Optional[LlmClient] = None) -> Opinion:
    """One agent's opinion: the model's if it gave a usable one, else its own."""
    client = client or NullClient()
    fallback = FALLBACKS[role]
    if not getattr(client, "available", False):
        return fallback(briefing)

    try:
        reply = client.complete(
            system=ROLE_PROMPTS[role],
            user=briefing_for(role, briefing),
            max_tokens=400,
        )
    except Exception:
        # A transport that raises instead of returning None must not take the
        # council down with it. The deterministic path is right here.
        return fallback(briefing)

    if reply is None:
        return fallback(briefing)
    opinion = parse_opinion(role, reply.text, reply.model)
    return opinion if opinion is not None else fallback(briefing)


# ------------------------------------------------------------------- arbiter --


@dataclass(frozen=True)
class CouncilConfig:
    min_conviction: float = 0.6
    # Two soft vetoes (analyst + macro) stand in for one hard one.
    soft_veto_limit: int = 2
    roles: tuple[Role, ...] = (Role.ANALYST, Role.RISK, Role.HISTORIAN, Role.MACRO)


@dataclass(frozen=True)
class CouncilVerdict:
    approved: bool
    direction: str
    conviction: float
    opinions: list[Opinion]
    reasons: list[str]

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "direction": self.direction,
            "conviction": round(self.conviction, 4),
            "opinions": [o.to_dict() for o in self.opinions],
            "reasons": list(self.reasons),
        }


def deliberate(
    *,
    briefing: Briefing,
    client: Optional[LlmClient] = None,
    config: Optional[CouncilConfig] = None,
) -> CouncilVerdict:
    """Run the council and aggregate it.

    The engine's confluence is the ceiling. Agreement among the agents scales
    it down; nothing scales it up. So the council is a brake, which is the only
    thing a language model should be in this position.
    """
    config = config or CouncilConfig()
    proposal = briefing.proposal
    direction = (proposal.get("direction") or "flat").lower()

    # Short-circuit before spending a single token: with no actionable proposal
    # there is nothing to review, and no sequence of model replies can conjure
    # one. This is the structural half of "an agent cannot open a position".
    if not proposal.get("actionable"):
        blockers = proposal.get("blockers") or []
        reason = "engine has no actionable proposal"
        if blockers:
            reason += f": {'; '.join(blockers)}"
        return CouncilVerdict(
            approved=False,
            direction=direction,
            conviction=0.0,
            opinions=[],
            reasons=[reason],
        )

    opinions = [consult(role, briefing, client) for role in config.roles]
    ceiling = max(0.0, min(100.0, float(proposal.get("confluence") or 0.0))) / 100.0

    support = sum(o.confidence for o in opinions if o.stance is Stance.CONFIRM)
    against = sum(o.confidence for o in opinions if o.stance is Stance.VETO)
    engaged = sum(o.confidence for o in opinions if o.stance is not Stance.ABSTAIN)
    agreement = (support - against) / engaged if engaged > 0 else 0.0
    conviction = ceiling * max(0.0, agreement)

    reasons: list[str] = []
    hard = [o for o in opinions if o.stance is Stance.VETO and o.role in HARD_VETO_ROLES]
    soft = [o for o in opinions if o.stance is Stance.VETO and o.role not in HARD_VETO_ROLES]

    approved = True
    if hard:
        approved = False
        for opinion in hard:
            reasons.append(f"{opinion.role.value} vetoed: {opinion.rationale}")
    if len(soft) >= config.soft_veto_limit:
        approved = False
        reasons.append(
            f"{len(soft)} advisory vetoes "
            f"({', '.join(o.role.value for o in soft)}) is as good as a hard one"
        )
    elif soft:
        reasons.append(
            f"advisory veto from {soft[0].role.value}, noted: {soft[0].rationale}"
        )
    if engaged == 0:
        approved = False
        reasons.append("every agent abstained; nobody is backing this trade")
    if conviction < config.min_conviction:
        approved = False
        reasons.append(
            f"conviction {conviction:.2f} below the {config.min_conviction:.2f} floor "
            f"(confluence ceiling {ceiling:.2f}, agreement {agreement:+.2f})"
        )

    if approved:
        reasons.append(
            f"council backs a {direction} at conviction {conviction:.2f} "
            f"({len([o for o in opinions if o.stance is Stance.CONFIRM])} confirming, "
            f"{len([o for o in opinions if o.stance is Stance.ABSTAIN])} abstaining)"
        )

    return CouncilVerdict(
        approved=approved,
        direction=direction,
        conviction=conviction,
        opinions=opinions,
        reasons=reasons,
    )


def build_briefing(
    *,
    symbol: str,
    interval: str,
    proposal,
    risk=None,
    calibration=None,
    breadth: Optional[dict] = None,
    account: Optional[dict] = None,
) -> Briefing:
    """Assemble a briefing from the deterministic layers' own objects."""
    return Briefing(
        symbol=symbol,
        interval=interval,
        proposal=proposal if isinstance(proposal, dict) else proposal.to_dict(),
        risk={} if risk is None else (risk if isinstance(risk, dict) else risk.to_dict()),
        calibration={}
        if calibration is None
        else (calibration if isinstance(calibration, dict) else calibration.to_dict()),
        breadth=dict(breadth or {}),
        account=dict(account or {}),
    )
