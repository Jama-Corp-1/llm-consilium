from council.compose import adaptive_k, compose_council
from council.types import Member


def _m(alias, fam, score, cap="general", rpm=10):
    return Member(alias, "A", {cap: score}, rpm, fam)


def test_adaptive_k_by_capability():
    assert adaptive_k("fast") == 3
    assert adaptive_k("code") == 4
    assert adaptive_k("general") == 4
    assert adaptive_k("reasoning") == 5
    assert adaptive_k("unknown") == 4


def test_compose_prefers_distinct_families_first():
    members = [
        _m("a1", "alpha", 5), _m("a2", "alpha", 4),
        _m("b1", "beta", 3), _m("c1", "gamma", 2),
    ]
    picked = [m.alias for m in compose_council(members, k=3, capability="general")]
    # one per family before a second from alpha
    assert picked == ["a1", "b1", "c1"]


def test_compose_fills_remaining_by_score_after_families_exhausted():
    members = [_m("a1", "alpha", 5), _m("a2", "alpha", 4), _m("b1", "beta", 3)]
    picked = [m.alias for m in compose_council(members, k=3, capability="general")]
    assert picked[:2] == ["a1", "b1"]  # diversity first
    assert picked[2] == "a2"           # then next best regardless of family


def test_compose_ranks_by_requested_capability():
    coder = Member("coder", "A", {"code": 5, "general": 2}, 10, "x")
    gen = Member("gen", "A", {"code": 2, "general": 5}, 10, "y")
    assert compose_council([gen, coder], k=1, capability="code")[0].alias == "coder"


def test_compose_clamps_to_available():
    members = [_m("a1", "alpha", 5), _m("b1", "beta", 4)]
    assert len(compose_council(members, k=5, capability="general")) == 2


def test_compose_empty_returns_empty():
    assert compose_council([], k=3, capability="general") == []


def test_compose_missing_capability_scores_zero_not_best_unrelated():
    sprinter = Member("sprinter", "A", {"fast": 5}, 30, "x")
    reasoner = Member("reasoner", "A", {"reasoning": 4}, 10, "y")
    picked = [m.alias for m in compose_council([sprinter, reasoner], k=1, capability="reasoning")]
    assert picked == ["reasoner"]


def test_compose_diversity_pass_skips_incapable_families():
    # Two capable members share a family; an incapable member is a distinct family.
    r1 = Member("r1", "A", {"reasoning": 4}, 30, "same")
    r2 = Member("r2", "A", {"reasoning": 4}, 10, "same")
    other = Member("other", "A", {"fast": 5}, 10, "distinct")
    picked = [m.alias for m in compose_council([other, r1, r2], k=2, capability="reasoning")]
    assert picked == ["r1", "r2"]  # capable duplicates beat an incapable "diverse" member


def test_compose_falls_back_to_strength_when_nobody_has_capability():
    a = Member("a", "A", {"fast": 3}, 10, "x")
    b = Member("b", "A", {"code": 5}, 10, "y")
    picked = [m.alias for m in compose_council([a, b], k=1, capability="reasoning")]
    assert picked == ["b"]
