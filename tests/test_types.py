import dataclasses

import pytest

from council.errors import (
    AllMembersFailed,
    ConsiliumError,
    MemberCallError,
    NoEligibleMember,
    PrivacyRefusal,
)
from council.types import AskResult, CallResult, CouncilResult, Member, MemberAnswer


def test_member_is_frozen():
    m = Member(alias="a", privacy_tier="A", scores={"general": 3}, rpm=5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.rpm = 4


def test_member_scores_and_derived_props():
    m = Member(alias="a", privacy_tier="A", scores={"general": 3, "code": 4}, rpm=5)
    assert m.capabilities == ("general", "code")
    assert m.strength == 4
    assert m.provider_family == "" and m.rpd is None and m.tpd is None


def test_result_types_construct():
    ask = AskResult(answer="x", model_used="a", capability="code", note="n")
    council = CouncilResult(
        answer="x", per_member=[], disagreements="", judge_used=None, mode="vote"
    )
    ans = MemberAnswer(alias="a", ok=True, answer="x", detail="ok")
    assert ask.answer == "x" and council.mode == "vote" and ans.ok


def test_errors_subclass_base():
    for exc in (PrivacyRefusal, NoEligibleMember, AllMembersFailed, MemberCallError):
        assert issubclass(exc, ConsiliumError)


def test_member_call_error_carries_detail():
    e = MemberCallError("council/x", "429 rate-limited")
    assert e.alias == "council/x" and e.detail == "429 rate-limited"


def test_member_cost_per_1k_defaults_zero():
    m = Member(alias="a", privacy_tier="A", scores={"general": 3}, rpm=5)
    assert m.cost_per_1k == 0.0


def test_call_result_defaults():
    assert CallResult("x", "a").fallbacks == 0


def test_member_answer_new_defaults():
    a = MemberAnswer("a", True, "x", "ok")
    assert a.served_by is None and a.permanent is False
