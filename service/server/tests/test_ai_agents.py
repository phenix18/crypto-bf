"""The council. Above all: the ways an agent must not be able to open a trade.

The tests that matter here are not the happy path. They are the ones proving
that an enthusiastic, malformed, or outright hostile model reply cannot produce
a position the deterministic engine did not already propose.
"""

import os
import sys
import unittest

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from ai_agents import (  # noqa: E402
    Briefing,
    CouncilConfig,
    Opinion,
    Role,
    Stance,
    briefing_for,
    build_briefing,
    consult,
    deliberate,
    extract_json,
    fallback_analyst,
    fallback_historian,
    fallback_macro,
    fallback_risk,
    parse_opinion,
)
from llm_client import NullClient, ScriptedClient  # noqa: E402


def proposal(
    direction="long",
    confluence=85.0,
    actionable=True,
    blockers=None,
    regime="trending_up",
):
    return {
        "symbol": "BTCUSDT",
        "interval": "5m",
        "direction": direction,
        "regime": regime,
        "confluence": confluence,
        "entry": 100.0,
        "stop_loss": 99.0,
        "take_profit": 103.0,
        "risk_reward": 3.0,
        "actionable": actionable,
        "blockers": list(blockers or []),
        "votes": [{"name": "rsi", "direction": "long", "strength": 0.8}],
        "readings": {"rsi": 32.0, "atr_percent": 0.4},
    }


def briefing(
    prop=None,
    risk=None,
    calibration=None,
    breadth=None,
    account=None,
):
    return Briefing(
        symbol="BTCUSDT",
        interval="5m",
        proposal=prop or proposal(),
        risk=risk if risk is not None else {"approved": True, "reasons": ["sized ok"]},
        calibration=calibration
        if calibration is not None
        else {"allowed": True, "basis": "calibrated", "probability": 62.0, "reasons": ["30 trades"]},
        breadth=breadth or {},
        account=account or {"equity": 1000.0},
    )


def reply(stance="confirm", confidence=0.8, rationale="looks fine"):
    return (
        '{"stance": "%s", "confidence": %s, "rationale": "%s"}'
        % (stance, confidence, rationale)
    )


class ExtractJsonTests(unittest.TestCase):
    def test_a_bare_object(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_a_fenced_object(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_prose_around_it(self):
        text = 'Sure! Here is my view:\n{"a": 1}\nHope that helps.'
        self.assertEqual(extract_json(text), {"a": 1})

    def test_nested_objects_are_kept_whole(self):
        self.assertEqual(extract_json('x {"a": {"b": 2}} y'), {"a": {"b": 2}})

    def test_braces_inside_strings_do_not_confuse_it(self):
        self.assertEqual(extract_json('{"a": "} not the end {"}'), {"a": "} not the end {"})

    def test_escaped_quotes_do_not_confuse_it(self):
        self.assertEqual(extract_json('{"a": "say \\"hi\\""}'), {"a": 'say "hi"'})

    def test_a_broken_object_followed_by_a_good_one(self):
        self.assertEqual(extract_json('{not json} then {"a": 1}'), {"a": 1})

    def test_no_object_at_all(self):
        self.assertIsNone(extract_json("I would rather not say."))

    def test_an_empty_reply(self):
        self.assertIsNone(extract_json(""))

    def test_a_bare_array_is_not_an_opinion(self):
        self.assertIsNone(extract_json("[1, 2, 3]"))


class ParseOpinionTests(unittest.TestCase):
    def test_a_well_formed_reply(self):
        opinion = parse_opinion(Role.ANALYST, reply("veto", 0.9, "momentum opposes"), "m")
        self.assertEqual(opinion.stance, Stance.VETO)
        self.assertAlmostEqual(opinion.confidence, 0.9)
        self.assertEqual(opinion.rationale, "momentum opposes")
        self.assertEqual(opinion.source, "model:m")

    def test_an_unknown_stance_is_rejected(self):
        # "strong buy" is not a stance this council has. Reading it as a
        # confirmation is how a model talks itself into a position.
        self.assertIsNone(parse_opinion(Role.ANALYST, reply("strong buy"), "m"))

    def test_a_missing_stance_is_rejected(self):
        self.assertIsNone(parse_opinion(Role.ANALYST, '{"confidence": 1.0}', "m"))

    def test_a_non_numeric_confidence_is_rejected(self):
        self.assertIsNone(
            parse_opinion(Role.ANALYST, '{"stance": "confirm", "confidence": "high"}', "m")
        )

    def test_a_nan_confidence_is_rejected(self):
        self.assertIsNone(
            parse_opinion(Role.ANALYST, '{"stance": "confirm", "confidence": NaN}', "m")
        )

    def test_an_oversized_confidence_is_clamped(self):
        opinion = parse_opinion(Role.ANALYST, reply("confirm", 9.9), "m")
        self.assertEqual(opinion.confidence, 1.0)

    def test_a_negative_confidence_is_clamped(self):
        opinion = parse_opinion(Role.ANALYST, reply("confirm", -3), "m")
        self.assertEqual(opinion.confidence, 0.0)

    def test_an_essay_rationale_is_truncated(self):
        long = parse_opinion(Role.ANALYST, reply("confirm", 0.5, "x" * 2000), "m")
        self.assertEqual(len(long.rationale), 400)

    def test_a_missing_rationale_still_parses(self):
        opinion = parse_opinion(Role.ANALYST, '{"stance": "abstain", "confidence": 0.1}', "m")
        self.assertEqual(opinion.rationale, "(no rationale given)")

    def test_junk_is_rejected(self):
        self.assertIsNone(parse_opinion(Role.ANALYST, "I think you should buy!", "m"))

    def test_an_opinion_carries_no_direction_or_size(self):
        # The type itself is the guarantee: there is nowhere to put one.
        fields = Opinion.__dataclass_fields__.keys()
        self.assertNotIn("direction", fields)
        self.assertNotIn("quantity", fields)
        self.assertNotIn("entry", fields)


class FallbackTests(unittest.TestCase):
    def test_the_analyst_vetoes_on_engine_blockers(self):
        opinion = fallback_analyst(briefing(proposal(blockers=["counter-trend"])))
        self.assertEqual(opinion.stance, Stance.VETO)
        self.assertIn("counter-trend", opinion.rationale)

    def test_the_analyst_confirms_strong_confluence(self):
        self.assertEqual(fallback_analyst(briefing(proposal(confluence=88))).stance, Stance.CONFIRM)

    def test_the_analyst_abstains_in_the_middle(self):
        self.assertEqual(fallback_analyst(briefing(proposal(confluence=55))).stance, Stance.ABSTAIN)

    def test_the_analyst_vetoes_weak_confluence(self):
        self.assertEqual(fallback_analyst(briefing(proposal(confluence=20))).stance, Stance.VETO)

    def test_the_risk_officer_vetoes_a_refused_sizing(self):
        opinion = fallback_risk(briefing(risk={"approved": False, "reasons": ["daily loss"]}))
        self.assertEqual(opinion.stance, Stance.VETO)
        self.assertEqual(opinion.confidence, 1.0)

    def test_a_missing_risk_decision_is_a_veto_not_a_shrug(self):
        opinion = fallback_risk(briefing(risk={}))
        self.assertEqual(opinion.stance, Stance.VETO)

    def test_the_risk_officer_confirms_an_approved_sizing(self):
        self.assertEqual(fallback_risk(briefing()).stance, Stance.CONFIRM)

    def test_the_historian_vetoes_a_closed_gate(self):
        opinion = fallback_historian(
            briefing(calibration={"allowed": False, "basis": "calibrated", "reasons": ["42% rate"]})
        )
        self.assertEqual(opinion.stance, Stance.VETO)

    def test_a_missing_calibration_verdict_is_a_veto(self):
        self.assertEqual(fallback_historian(briefing(calibration={})).stance, Stance.VETO)

    def test_the_historian_abstains_while_the_sample_is_thin(self):
        # Allowed on confluence is not a track record, and the historian must
        # not lend the chart's conviction to the vote.
        opinion = fallback_historian(
            briefing(calibration={"allowed": True, "basis": "confluence", "reasons": ["4 trades"]})
        )
        self.assertEqual(opinion.stance, Stance.ABSTAIN)

    def test_the_historian_confirms_a_measured_record(self):
        self.assertEqual(fallback_historian(briefing()).stance, Stance.CONFIRM)

    def test_macro_abstains_without_data(self):
        self.assertEqual(fallback_macro(briefing()).stance, Stance.ABSTAIN)

    def test_macro_abstains_on_unusable_data(self):
        opinion = fallback_macro(briefing(breadth={"percent_change_24h": "n/a"}))
        self.assertEqual(opinion.stance, Stance.ABSTAIN)

    def test_macro_vetoes_a_long_into_a_falling_market(self):
        opinion = fallback_macro(briefing(breadth={"percent_change_24h": -8.0}))
        self.assertEqual(opinion.stance, Stance.VETO)

    def test_macro_vetoes_a_short_into_a_ripping_market(self):
        opinion = fallback_macro(
            briefing(prop=proposal(direction="short"), breadth={"percent_change_24h": 9.0})
        )
        self.assertEqual(opinion.stance, Stance.VETO)

    def test_macro_tolerates_a_mild_drift_against_the_trade(self):
        # Vetoing on a -1% tape would block nearly every scalp.
        opinion = fallback_macro(briefing(breadth={"percent_change_24h": -1.0}))
        self.assertEqual(opinion.stance, Stance.ABSTAIN)

    def test_macro_confirms_an_aligned_tape(self):
        opinion = fallback_macro(briefing(breadth={"percent_change_24h": 3.0}))
        self.assertEqual(opinion.stance, Stance.CONFIRM)


class BriefingSliceTests(unittest.TestCase):
    def test_the_analyst_sees_the_chart_not_the_account(self):
        text = briefing_for(Role.ANALYST, briefing())
        self.assertIn("readings", text)
        self.assertNotIn("account", text)

    def test_the_risk_officer_sees_the_account_not_the_readings(self):
        text = briefing_for(Role.RISK, briefing())
        self.assertIn("risk_decision", text)
        self.assertNotIn("readings", text)

    def test_the_historian_sees_only_the_record(self):
        text = briefing_for(Role.HISTORIAN, briefing())
        self.assertIn("calibration", text)
        self.assertNotIn("votes", text)

    def test_every_role_sees_the_proposal_core(self):
        for role in Role:
            with self.subTest(role=role):
                text = briefing_for(role, briefing())
                self.assertIn("stop_loss", text)
                self.assertIn("direction", text)


class ConsultTests(unittest.TestCase):
    def test_without_a_model_the_agent_uses_its_own_reasoning(self):
        opinion = consult(Role.RISK, briefing(), NullClient())
        self.assertEqual(opinion.source, "fallback")

    def test_an_unavailable_client_is_never_called(self):
        client = ScriptedClient(replies=[reply()], available=False)
        consult(Role.RISK, briefing(), client)
        self.assertEqual(client.calls, [])

    def test_a_usable_reply_is_adopted(self):
        client = ScriptedClient(replies=[reply("veto", 0.7, "thin volume")])
        opinion = consult(Role.ANALYST, briefing(), client)
        self.assertEqual(opinion.stance, Stance.VETO)
        self.assertTrue(opinion.source.startswith("model:"))

    def test_a_garbled_reply_sends_the_agent_back_to_its_fallback(self):
        client = ScriptedClient(replies=["buy buy buy"])
        opinion = consult(Role.RISK, briefing(), client)
        self.assertEqual(opinion.source, "fallback")
        self.assertEqual(opinion.stance, Stance.CONFIRM)

    def test_no_reply_sends_the_agent_back_to_its_fallback(self):
        client = ScriptedClient(replies=[None])
        self.assertEqual(consult(Role.RISK, briefing(), client).source, "fallback")

    def test_a_transport_that_raises_does_not_take_the_council_down(self):
        client = ScriptedClient(replies=[RuntimeError("socket closed")])
        self.assertEqual(consult(Role.RISK, briefing(), client).source, "fallback")

    def test_the_default_client_is_no_client(self):
        self.assertEqual(consult(Role.MACRO, briefing()).source, "fallback")


class DeliberateTests(unittest.TestCase):
    def test_a_non_actionable_proposal_is_refused_without_consulting_anyone(self):
        # The structural half of "no agent can open a position": with nothing
        # to review, no sequence of replies can produce a trade.
        client = ScriptedClient(replies=[reply("confirm", 1.0)] * 4)
        verdict = deliberate(
            briefing=briefing(proposal(actionable=False, blockers=["counter-trend"])),
            client=client,
        )
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.opinions, [])
        self.assertEqual(client.calls, [])
        self.assertIn("counter-trend", verdict.reasons[0])

    def test_a_flat_proposal_is_refused(self):
        verdict = deliberate(briefing=briefing(proposal(direction="flat", actionable=False)))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.conviction, 0.0)

    def test_a_clean_setup_is_approved(self):
        verdict = deliberate(briefing=briefing(proposal(confluence=92)))
        self.assertTrue(verdict.approved)
        self.assertEqual(verdict.direction, "long")
        self.assertGreater(verdict.conviction, 0.6)

    def test_the_risk_officers_veto_ends_it(self):
        verdict = deliberate(
            briefing=briefing(
                proposal(confluence=99), risk={"approved": False, "reasons": ["drawdown"]}
            )
        )
        self.assertFalse(verdict.approved)
        self.assertTrue(any("risk vetoed" in r for r in verdict.reasons))

    def test_the_historians_veto_ends_it(self):
        verdict = deliberate(
            briefing=briefing(
                proposal(confluence=99),
                calibration={"allowed": False, "basis": "calibrated", "reasons": ["38% rate"]},
            )
        )
        self.assertFalse(verdict.approved)

    def test_one_advisory_veto_is_noted_but_not_fatal_on_its_own(self):
        config = CouncilConfig(min_conviction=0.1)
        verdict = deliberate(
            briefing=briefing(proposal(confluence=95), breadth={"percent_change_24h": -9.0}),
            config=config,
        )
        self.assertTrue(verdict.approved)
        self.assertTrue(any("advisory veto" in r for r in verdict.reasons))

    def test_two_advisory_vetoes_are_as_good_as_one_hard_veto(self):
        config = CouncilConfig(min_conviction=0.0)
        verdict = deliberate(
            briefing=briefing(
                proposal(confluence=95, blockers=[]), breadth={"percent_change_24h": -9.0}
            ),
            # analyst vetoes by model, macro vetoes on the tape
            client=ScriptedClient(replies=[reply("veto", 0.8, "divergence")]),
            config=config,
        )
        self.assertFalse(verdict.approved)
        self.assertTrue(any("advisory vetoes" in r for r in verdict.reasons))

    def test_a_council_that_all_abstains_backs_nothing(self):
        client = ScriptedClient(replies=[reply("abstain", 0.5)] * 4)
        verdict = deliberate(briefing=briefing(proposal(confluence=99)), client=client)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.conviction, 0.0)
        self.assertTrue(any("abstained" in r for r in verdict.reasons))

    def test_conviction_can_never_exceed_the_engines_own_score(self):
        # Four models shouting at maximum confidence must not turn a 55%
        # confluence into a high-conviction trade.
        client = ScriptedClient(replies=[reply("confirm", 1.0, "certain")] * 4)
        verdict = deliberate(briefing=briefing(proposal(confluence=55)), client=client)
        self.assertLessEqual(verdict.conviction, 0.55 + 1e-9)
        self.assertFalse(verdict.approved)

    def test_disagreement_scales_conviction_down(self):
        agreed = deliberate(briefing=briefing(proposal(confluence=90)))
        split = deliberate(
            briefing=briefing(proposal(confluence=90)),
            client=ScriptedClient(replies=[reply("veto", 0.9, "divergence")]),
        )
        self.assertLess(split.conviction, agreed.conviction)

    def test_the_conviction_floor_is_configurable(self):
        low = deliberate(
            briefing=briefing(proposal(confluence=62)), config=CouncilConfig(min_conviction=0.95)
        )
        self.assertFalse(low.approved)

    def test_every_opinion_is_recorded_for_the_audit_trail(self):
        verdict = deliberate(briefing=briefing())
        self.assertEqual(
            [o.role for o in verdict.opinions],
            [Role.ANALYST, Role.RISK, Role.HISTORIAN, Role.MACRO],
        )
        payload = verdict.to_dict()
        self.assertEqual(len(payload["opinions"]), 4)
        self.assertTrue(all("rationale" in o for o in payload["opinions"]))

    def test_the_roles_consulted_are_configurable(self):
        verdict = deliberate(
            briefing=briefing(), config=CouncilConfig(roles=(Role.RISK, Role.HISTORIAN))
        )
        self.assertEqual(len(verdict.opinions), 2)

    def test_the_verdict_direction_mirrors_the_engine(self):
        verdict = deliberate(briefing=briefing(proposal(direction="short", regime="trending_down")))
        self.assertEqual(verdict.direction, "short")

    def test_a_model_cannot_redirect_the_trade(self):
        # The reply asks for the opposite side. There is nowhere for that to
        # land, so the verdict still mirrors the engine.
        client = ScriptedClient(
            replies=[
                '{"stance": "confirm", "confidence": 1.0, "rationale": "go short instead",'
                ' "direction": "short", "entry": 1.0}'
            ]
            * 4
        )
        verdict = deliberate(briefing=briefing(proposal(direction="long")), client=client)
        self.assertEqual(verdict.direction, "long")


class BuildBriefingTests(unittest.TestCase):
    class Fake:
        def __init__(self, payload):
            self.payload = payload

        def to_dict(self):
            return self.payload

    def test_objects_are_converted(self):
        assembled = build_briefing(
            symbol="ETHUSDT",
            interval="15m",
            proposal=self.Fake(proposal()),
            risk=self.Fake({"approved": True}),
            calibration=self.Fake({"allowed": True}),
        )
        self.assertEqual(assembled.symbol, "ETHUSDT")
        self.assertTrue(assembled.risk["approved"])
        self.assertTrue(assembled.calibration["allowed"])

    def test_dicts_pass_straight_through(self):
        assembled = build_briefing(
            symbol="BTCUSDT", interval="5m", proposal=proposal(), risk={"approved": False}
        )
        self.assertFalse(assembled.risk["approved"])

    def test_omitted_layers_are_empty_not_absent(self):
        assembled = build_briefing(symbol="BTCUSDT", interval="5m", proposal=proposal())
        self.assertEqual(assembled.risk, {})
        self.assertEqual(assembled.calibration, {})
        # And an empty risk layer is a veto, not a pass.
        self.assertFalse(deliberate(briefing=assembled).approved)


if __name__ == "__main__":
    unittest.main()
