import asyncio
import random
import time

import pytest

from council import aggregate
from council.errors import AllMembersFailed, MemberCallError, ProxyError
from council.types import AggregateResult, MemberAnswer


def _answers(*pairs):
    return [MemberAnswer(a, ok=ok, answer=ans, detail="ok" if ok else "x") for a, ok, ans in pairs]


async def _judge(alias, prompt, **_params):
    return "Merged best answer.\nDISAGREEMENTS: candidate differed on scope.\nCONFIDENCE: high"


def _run(ans, caller, judges=("chair",)):
    return asyncio.run(
        aggregate.aggregate(
            "q", ans, caller=caller, judge_aliases=list(judges), rng=random.Random(0)
        )
    )


def test_vote_on_closed_form_unanimous_is_high():
    r = _run(_answers(("m1", True, "Yes"), ("m2", True, "yes"), ("m3", True, "YES")), _judge)
    assert r.mode == "vote" and r.answer == "yes" and r.judge_used is None
    assert r.confidence == "high"


def test_vote_majority_is_medium():
    r = _run(_answers(("m1", True, "Yes"), ("m2", True, "yes"), ("m3", True, "No")), _judge)
    assert r.mode == "vote" and r.confidence == "medium"


def test_vote_plurality_is_low():
    r = _run(_answers(("m1", True, "A"), ("m2", True, "B"), ("m3", True, "C")), _judge)
    assert r.mode == "vote" and r.confidence == "low"


def test_judge_parses_answer_disagreements_and_confidence():
    ans = _answers(
        ("m1", True, "A long detailed explanation of the tradeoffs involved here."),
        ("m2", True, "Another multi sentence answer with different emphasis entirely."),
    )
    r = _run(ans, _judge)
    assert r.mode == "judge" and r.answer == "Merged best answer."
    assert "scope" in r.disagreements and "CONFIDENCE" not in r.disagreements
    assert r.judge_used == "chair" and r.confidence == "high"


def test_judge_prompt_uses_code_names_not_aliases_or_indices():
    captured = {}

    async def caller(alias, prompt, **_params):
        captured["prompt"] = prompt
        return "Merged.\nDISAGREEMENTS: none\nCONFIDENCE: medium"

    ans = _answers(
        ("council/cerebras-glm-4.7", True, "A long detailed answer about the tradeoffs here."),
        ("council/groq-gpt-oss-120b", True, "A different multi sentence answer entirely here."),
    )
    r = _run(ans, caller)
    assert r.confidence == "medium"
    assert "council/cerebras-glm-4.7" not in captured["prompt"]  # no model aliases
    assert "[1]" not in captured["prompt"] and "[2]" not in captured["prompt"]  # no indices
    from council.anonymize import CODE_NAMES
    assert any(f"{n}:" in captured["prompt"] for n in CODE_NAMES)  # code-names present


def test_judge_without_markers_yields_empty_disagreements_and_confidence():
    ans = _answers(
        ("m1", True, "A long detailed explanation of the tradeoffs involved here."),
        ("m2", True, "Another multi sentence answer with different emphasis entirely."),
    )

    async def caller(alias, prompt, **_params):
        return "Just a merged answer with several words here."

    r = _run(ans, caller)
    assert r.mode == "judge" and r.disagreements == "" and r.confidence == ""
    assert r.answer == "Just a merged answer with several words here."


def test_judge_falls_back_to_next_judge():
    ans = _answers(
        ("m1", True, "A long detailed explanation of the tradeoffs involved here."),
        ("m2", True, "Another multi sentence answer with different emphasis entirely."),
    )

    async def caller(alias, prompt, **_params):
        if alias == "chair":
            raise MemberCallError("chair", "429 rate-limited")
        return "Backup merge.\nDISAGREEMENTS: none\nCONFIDENCE: low"

    r = _run(ans, caller, judges=("chair", "backup"))
    assert r.mode == "judge" and r.judge_used == "backup" and r.answer == "Backup merge."
    assert r.confidence == "low"


def test_best_single_when_all_judges_fail():
    ans = _answers(
        ("m1", True, "short one"),
        ("m2", True, "A much longer and more substantive candidate answer here indeed."),
    )

    async def caller(alias, prompt, **_params):
        raise MemberCallError(alias, "429 rate-limited")

    r = _run(ans, caller, judges=("chair", "backup"))
    assert r.mode == "best-single" and r.judge_used is None and r.confidence == ""
    assert r.answer == "A much longer and more substantive candidate answer here indeed."


def test_all_failed_raises():
    ans = _answers(("m1", False, None), ("m2", False, None))
    with pytest.raises(AllMembersFailed):
        _run(ans, _judge)


def _members(*pairs):
    # pairs: (alias, answer)
    return [MemberAnswer(a, ok=True, answer=ans, detail="ok") for a, ans in pairs]


def test_peer_rank_picks_mean_ordinal_winner():
    ans = _members(
        ("m1", "Answer ONE is long and detailed and substantive."),
        ("m2", "Answer TWO is long and detailed and substantive."),
        ("m3", "Answer THREE is long and detailed and substantive."),
    )
    # With rng=Random(0), 3 answers get code-names Aardvark/Basilisk/Cheetah in some order.
    # Each ranker returns a fixed ranking; controller-independent because we assert on the
    # winning TEXT, resolved through the code-name mapping.
    async def caller(alias, prompt, **_params):
        # every ranker ranks the SAME code-name order (best->worst) as printed in `prompt`;
        # pick the first code-name that appears in the block as the unanimous winner
        import re as _re
        names = _re.findall(r"^([A-Z][a-z]+):$", prompt, _re.MULTILINE)
        return "RANKING: " + ", ".join(names)  # everyone agrees on block order

    r = asyncio.run(aggregate.aggregate(
        "q", ans, caller=caller, judge_aliases=["chair"], rng=random.Random(0), mode="peer-rank"))
    assert r.mode == "peer-rank"
    # unanimous first choice -> high confidence, and the winner is a real member answer
    assert r.confidence == "high"
    assert r.answer in [a.answer for a in ans]


def test_peer_rank_excludes_self_vote():
    ans = _members(
        ("m1", "Answer from m1, quite long and detailed here indeed."),
        ("m2", "Answer from m2, quite long and detailed here indeed."),
        ("m3", "Answer from m3, quite long and detailed here indeed."),
        ("m4", "Answer from m4, quite long and detailed here indeed."),
    )
    from council.anonymize import anonymize_pairs
    _, mapping = anonymize_pairs([(a.alias, a.answer) for a in ans], rng=random.Random(0))
    cn = {owner: c for c, owner in mapping}

    async def caller(alias, prompt, **_params):
        # m1 and m2 both rank m1 first; m3 and m4 both rank m2 first.
        # Counting self-votes, m1 and m2 tie (2 first-place each). Excluding m1's and m2's
        # self-votes, m2 wins outright (m3 & m4 back it, m1 does not) — so only correct
        # self-vote exclusion reliably picks m2.
        if alias in ("m1", "m2"):
            order = [cn["m1"], cn["m2"], cn["m3"], cn["m4"]]
        else:
            order = [cn["m2"], cn["m1"], cn["m3"], cn["m4"]]
        return "RANKING: " + ", ".join(order)

    r = asyncio.run(aggregate.aggregate(
        "q", ans, caller=caller, judge_aliases=["chair"], rng=random.Random(0), mode="peer-rank"))
    assert r.answer == "Answer from m2, quite long and detailed here indeed."


def test_peer_rank_falls_back_to_judge_when_too_few_rankers():
    ans = _members(("m1", "A long detailed answer about the tradeoffs here."),
                   ("m2", "Another long detailed answer with different emphasis."))

    async def caller(alias, prompt, **_params):
        if "RANKING" in prompt or "Rank them" in prompt:  # ranker prompt -> unparseable
            return "I cannot rank these."
        return "Judged merge.\nDISAGREEMENTS: none\nCONFIDENCE: medium"

    r = asyncio.run(aggregate.aggregate(
        "q", ans, caller=caller, judge_aliases=["chair"], rng=random.Random(0), mode="peer-rank"))
    assert r.mode == "judge" and r.answer == "Judged merge."


def test_mode_forces_path_and_unknown_raises():
    ans = _members(("m1", "yes"), ("m2", "yes"), ("m3", "no"))
    # closed-form, but mode='judge' forces the judge path
    async def judge(alias, prompt, **_params):
        return "Merged.\nDISAGREEMENTS: none\nCONFIDENCE: low"
    rj = asyncio.run(aggregate.aggregate(
        "q", ans, caller=judge, judge_aliases=["chair"], rng=random.Random(0), mode="judge"))
    assert rj.mode == "judge"
    # mode='vote' forces vote
    rv = asyncio.run(aggregate.aggregate(
        "q", ans, caller=judge, judge_aliases=["chair"], mode="vote"))
    assert rv.mode == "vote" and rv.answer == "yes"
    # unknown mode
    with pytest.raises(ValueError):
        asyncio.run(
            aggregate.aggregate("q", ans, caller=judge, judge_aliases=["chair"], mode="bogus"))


def test_parse_ranking_full_order():
    valid = ["Aardvark", "Basilisk", "Cheetah"]
    assert aggregate._parse_ranking("RANKING: Cheetah, Aardvark, Basilisk", valid) == [
        "Cheetah", "Aardvark", "Basilisk"]


def test_parse_ranking_partial_keeps_only_listed():
    valid = ["Aardvark", "Basilisk", "Cheetah"]
    # missing names are NOT appended by the parser (the scorer treats them as tied-last)
    assert aggregate._parse_ranking("RANKING: Basilisk, Aardvark", valid) == [
        "Basilisk", "Aardvark"]


def test_parse_ranking_absent_or_malformed_returns_empty():
    valid = ["Aardvark", "Basilisk"]
    assert aggregate._parse_ranking("I cannot rank these.", valid) == []
    assert aggregate._parse_ranking("", valid) == []


def test_parse_ranking_dedupes_and_drops_unknown():
    valid = ["Aardvark", "Basilisk"]
    # duplicate Aardvark collapses; unknown 'Zebra' dropped
    assert aggregate._parse_ranking(
        "RANKING: Aardvark, Aardvark, Zebra, Basilisk", valid) == ["Aardvark", "Basilisk"]


def test_parse_ranking_case_insensitive_marker_and_names():
    valid = ["Aardvark", "Basilisk"]
    assert aggregate._parse_ranking("ranking: basilisk, AARDVARK", valid) == [
        "Basilisk", "Aardvark"]


def test_jaccard_identical_and_disjoint():
    assert aggregate._jaccard("the quick brown fox", "the quick brown fox") == 1.0
    assert aggregate._jaccard("alpha beta", "gamma delta") == 0.0


def test_jaccard_partial_and_empty():
    assert aggregate._jaccard("a b c", "b c d") == 0.5  # |∩|=2 |∪|=4
    assert aggregate._jaccard("", "") == 1.0
    assert aggregate._jaccard("x", "") == 0.0


def test_mean_pairwise_jaccard():
    assert aggregate._mean_pairwise_jaccard(["solo"]) == 1.0
    assert aggregate._mean_pairwise_jaccard(["a b", "a b", "a b"]) == 1.0
    assert aggregate._mean_pairwise_jaccard(["a", "b", "c"]) == 0.0


def test_parse_revision():
    assert (
        aggregate._parse_revision("Critique.\nREVISED: my best answer now")
        == "my best answer now"
    )
    assert aggregate._parse_revision("no marker here") is None
    assert aggregate._parse_revision("revised: lowercase marker works") == "lowercase marker works"
    assert aggregate._parse_revision("REVISED:    ") is None


def test_debate_assigns_round_robin_stances():
    ans = _members(("m1", "alpha one"), ("m2", "bravo two"), ("m3", "charlie three"))
    stances = []

    async def caller(alias, prompt, **_params):
        if "debating" in prompt:
            for s in ("for", "against", "neutral"):
                if f"stance: {s}" in prompt:
                    stances.append(s)
            return "REVISED: converged shared answer text for all members here now"
        return "Merged.\nDISAGREEMENTS: none\nCONFIDENCE: high"

    asyncio.run(aggregate.aggregate(
        "q", ans, caller=caller, judge_aliases=["chair"], rng=random.Random(0), mode="debate"))
    assert set(stances) == {"for", "against", "neutral"}


def test_debate_converges_early_and_stops():
    ans = _members(("m1", "initial one"), ("m2", "initial two"), ("m3", "initial three"))
    calls = {"challenge": 0}

    async def caller(alias, prompt, **_params):
        if "debating" in prompt:
            calls["challenge"] += 1
            return "Critique.\nREVISED: consensus answer shared by all members here now"
        return "Merged consensus.\nDISAGREEMENTS: none\nCONFIDENCE: high"

    r = asyncio.run(aggregate.aggregate(
        "q", ans, caller=caller, judge_aliases=["chair"], rng=random.Random(0), mode="debate"))
    assert r.mode == "debate" and r.answer == "Merged consensus."
    assert calls["challenge"] == 3  # all identical -> converged after 1 round (not 2)
    assert r.confidence == "high"


def test_debate_runs_full_rounds_when_divergent():
    ans = _members(("m1", "alpha"), ("m2", "bravo"), ("m3", "charlie"))
    calls = {"challenge": 0}

    async def caller(alias, prompt, **_params):
        if "debating" in prompt:
            calls["challenge"] += 1
            return f"REVISED: wordone{calls['challenge']} wordtwo{calls['challenge']}"
        return "Merged.\nDISAGREEMENTS: they differ\nCONFIDENCE: medium"

    r = asyncio.run(aggregate.aggregate(
        "q", ans, caller=caller, judge_aliases=["chair"], rng=random.Random(0), mode="debate"))
    assert r.mode == "debate"
    assert calls["challenge"] == 6  # never converges -> 2 full rounds x 3 members
    assert r.confidence == "low"  # pairwise-disjoint revisions


def test_debate_single_member_falls_back_to_judge():
    ans = _members(("m1", "only answer here that is reasonably long indeed"))

    async def caller(alias, prompt, **_params):
        return "Judged.\nDISAGREEMENTS: none\nCONFIDENCE: high"

    r = asyncio.run(aggregate.aggregate(
        "q", ans, caller=caller, judge_aliases=["chair"], rng=random.Random(0), mode="debate"))
    assert r.mode == "judge"


def test_debate_failed_challenger_keeps_prior_answer():
    ans = _members(("m1", "prioralpha keeps"), ("m2", "priorbravo keeps"),
                   ("m3", "priorcharlie keeps"))
    captured = {}

    async def caller(alias, prompt, **_params):
        if "debating" in prompt:
            if alias == "m1":
                raise MemberCallError("m1", "boom")
            return "REVISED: revised shared text for the survivors here today"
        captured["judge"] = prompt
        return "Merged.\nDISAGREEMENTS: none\nCONFIDENCE: low"

    asyncio.run(aggregate.aggregate(
        "q", ans, caller=caller, judge_aliases=["chair"], rng=random.Random(0), mode="debate"))
    assert "prioralpha" in captured["judge"]  # m1's prior answer survived into synthesis


def test_aggregate_forwards_timeout_to_debate(monkeypatch):
    captured = {}

    async def fake_debate(prompt, ok_members, *, caller, judge_aliases, rng,
                          max_rounds=2, threshold=0.7, timeout=30.0):
        captured["timeout"] = timeout
        return AggregateResult("x", "debate", "", None, "high")

    monkeypatch.setattr(aggregate, "_debate", fake_debate)
    ans = _answers(("m1", True, "one long enough answer"), ("m2", True, "two long enough answer"))
    asyncio.run(
        aggregate.aggregate(
            "q", ans, caller=_judge, judge_aliases=["chair"], mode="debate", timeout=7.5
        )
    )
    assert captured["timeout"] == 7.5


def test_aggregate_forwards_timeout_to_peer_rank(monkeypatch):
    captured = {}

    async def fake_peer_rank(prompt, ok_members, *, caller, judge_aliases, rng, timeout=30.0):
        captured["timeout"] = timeout
        return AggregateResult("x", "peer-rank", "", None, "high")

    monkeypatch.setattr(aggregate, "_peer_rank", fake_peer_rank)
    ans = _answers(("m1", True, "one long enough answer"), ("m2", True, "two long enough answer"))
    asyncio.run(
        aggregate.aggregate(
            "q", ans, caller=_judge, judge_aliases=["chair"], mode="peer-rank", timeout=3.0
        )
    )
    assert captured["timeout"] == 3.0


def test_peer_rank_timeout_cancels_slow_ranker():
    import asyncio as _asyncio
    finished = []

    async def slow(alias, prompt, **_params):
        if "RANKING:" in prompt:
            await _asyncio.sleep(0.15)
            finished.append(alias)  # only reached if the call was NOT cancelled
            return "RANKING: whatever"
        return "Merged.\nDISAGREEMENTS: none\nCONFIDENCE: low"  # judge fallback

    ans = _answers(("m1", True, "first distinct answer here"),
                   ("m2", True, "second distinct answer here"))
    asyncio.run(
        aggregate.aggregate(
            "q", ans, caller=slow, judge_aliases=["chair"], mode="peer-rank", timeout=0.01
        )
    )
    assert finished == []  # both slow rankers cancelled by the tight timeout before finishing


def test_judge_timeout_falls_through_to_next_judge():
    async def caller(alias, prompt, **_params):
        if alias == "slow-chair":
            await asyncio.sleep(1.0)
        return "Fast synthesis.\nDISAGREEMENTS: none\nCONFIDENCE: high"

    ans = _answers(("m1", True, "a long free-form answer"), ("m2", True, "another long answer"))
    start = time.monotonic()
    r = asyncio.run(
        aggregate.aggregate(
            "q", ans, caller=caller, judge_aliases=["slow-chair", "fast-chair"],
            rng=random.Random(0), mode="judge", timeout=0.1,
        )
    )
    elapsed = time.monotonic() - start
    assert r.mode == "judge" and r.judge_used == "fast-chair"
    assert elapsed < 0.5


def test_judge_proxy_error_propagates():
    async def caller(alias, prompt, **_params):
        raise ProxyError("proxy rejected the request: HTTP 401")

    ans = _answers(("m1", True, "a long free-form answer"), ("m2", True, "another long answer"))
    with pytest.raises(ProxyError):
        _run(ans, caller, judges=("chair", "chair2"))
