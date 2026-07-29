from __future__ import annotations

import asyncio
import random
import re
from collections import Counter

from council.anonymize import anonymize, anonymize_pairs
from council.errors import AllMembersFailed, MemberCallError
from council.types import AggregateResult, AsyncCaller, MemberAnswer

# Fusion-style synthesis: reason and rewrite a standalone answer, don't average.
# (Design idea: FreeLLMAPI Fusion, MIT.) Candidates are code-name anonymized.
_JUDGE_PROMPT = (
    "You are the chair of a council. {n} members answered the question independently; "
    "their answers are labelled by code-name below. You do not know which member wrote "
    "which — weigh them equally.\n\n"
    "Synthesize the single best STANDALONE answer to the question. Reason about which "
    "claims are correct; do NOT average or split the difference, and NEVER refer to a "
    "member by code-name or number in your answer.\n\n"
    "After the answer, add two lines exactly:\n"
    "DISAGREEMENTS: <where members differed, or 'none'>\n"
    "CONFIDENCE: <high|medium|low>\n\n"
    "Question:\n{prompt}\n\nAnswers:\n{candidates}"
)

# Peer-rank: each member ranks the anonymized answers; mean-ordinal with self-vote
# exclusion picks the winner verbatim. (Design idea: karpathy/llm-council; reimplemented,
# self-vote exclusion added.)
_RANK_PROMPT = (
    "Below are {n} anonymized candidate answers to a question, labelled by code-name. "
    "Rank them from best to worst by correctness and completeness; do not favour any "
    "particular one.\n\nEnd with exactly one line:\n"
    "RANKING: <Name>, <Name>, ... (list every code-name, best first)\n\n"
    "Question:\n{prompt}\n\nAnswers:\n{candidates}"
)

# Stance-steered debate: members critique the anonymized answers from an assigned stance,
# then revise; rounds repeat until word-set convergence. (Design ideas: PAL stance-steering
# Apache-2.0 + DUH debate/convergence AGPL — both reimplemented; the math is standard.)
_STANCES = ("for", "against", "neutral")
_DEBATE_PROMPT = (
    "You are a member of a council debating a question. Your assigned stance: {stance}.\n"
    "- for: argue in favour of the strongest position among the answers.\n"
    "- against: probe the answers for errors, gaps, and weak reasoning.\n"
    "- neutral: weigh the positions impartially.\n"
    "Your stance is NOT a license to lie — flag only genuine errors, never invent them.\n\n"
    "Below are the current anonymized candidate answers. Critique them from your stance, "
    "then give your OWN best revised answer to the question.\n\n"
    "End with exactly one line:\nREVISED: <your single best answer>\n\n"
    "Question:\n{prompt}\n\nCurrent answers:\n{candidates}"
)

_CONF_RE = re.compile(r"(high|medium|low)", re.IGNORECASE)
_RANKING_RE = re.compile(r"RANKING:\s*(.+)", re.IGNORECASE)


def _looks_closed_form(answers: list[str]) -> bool:
    return all(len(a.strip().lower().rstrip(".!").split()) <= 3 for a in answers)


def _majority(answers: list[str]) -> str:
    norm = [a.strip().lower().rstrip(".!") for a in answers]
    return Counter(norm).most_common(1)[0][0]


def _vote_confidence(answers: list[str]) -> str:
    norm = [a.strip().lower().rstrip(".!") for a in answers]
    counts = Counter(norm)
    if len(counts) == 1:
        return "high"
    if counts.most_common(1)[0][1] * 2 > len(norm):
        return "medium"
    return "low"


def _split_marker(text: str, marker: str) -> tuple[str, str]:
    m = re.search(marker, text, re.IGNORECASE)
    if not m:
        return text, ""
    return text[: m.start()], text[m.end() :]


def _parse_judge(reply: str) -> tuple[str, str, str]:
    body, conf_tail = _split_marker(reply, r"CONFIDENCE:")
    answer, dis = _split_marker(body, r"DISAGREEMENTS:")
    conf_match = _CONF_RE.search(conf_tail)
    confidence = conf_match.group(1).lower() if conf_match else ""
    return answer.strip(), dis.strip(), confidence


def _parse_ranking(reply: str, valid: list[str]) -> list[str]:
    m = _RANKING_RE.search(reply)
    if not m:
        return []
    canon = {v.lower(): v for v in valid}
    order: list[str] = []
    for tok in re.split(r"[,\s]+", m.group(1).strip()):
        name = canon.get(tok.strip(" .").lower())
        if name is not None and name not in order:
            order.append(name)
    return order


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _jaccard(a: str, b: str) -> float:
    wa, wb = set(_words(a)), set(_words(b))
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _mean_pairwise_jaccard(answers: list[str]) -> float:
    if len(answers) < 2:
        return 1.0
    pairs = [(i, j) for i in range(len(answers)) for j in range(i + 1, len(answers))]
    return sum(_jaccard(answers[i], answers[j]) for i, j in pairs) / len(pairs)


def _parse_revision(reply: str) -> str | None:
    m = re.search(r"REVISED:", reply, re.IGNORECASE)
    if not m:
        return None
    revised = reply[m.end() :].strip()
    return revised or None


def _vote(ok: list[str]) -> AggregateResult:
    return AggregateResult(_majority(ok), "vote", "", None, _vote_confidence(ok))


async def _judge(
    prompt: str, ok: list[str], *, caller: AsyncCaller, judge_aliases: list[str],
    rng: random.Random | None,
) -> AggregateResult:
    candidates, _ = anonymize(ok, rng=rng)
    for judge_alias in judge_aliases:
        try:
            reply = await caller(
                judge_alias,
                _JUDGE_PROMPT.format(n=len(ok), prompt=prompt, candidates=candidates),
            )
        except MemberCallError:
            continue
        answer, disagreements, confidence = _parse_judge(reply)
        return AggregateResult(answer, "judge", disagreements, judge_alias, confidence)
    return AggregateResult(max(ok, key=len), "best-single", "", None, "")


async def _peer_rank(
    prompt: str, ok_members: list[MemberAnswer], *, caller: AsyncCaller,
    judge_aliases: list[str], rng: random.Random | None, timeout: float = 30.0,
) -> AggregateResult:
    pairs = [(a.alias, a.answer or "") for a in ok_members]
    block, mapping = anonymize_pairs(pairs, rng=rng)
    codenames = [cn for cn, _ in mapping]
    owner_of = dict(mapping)
    answer_of_alias = {a.alias: a.answer or "" for a in ok_members}
    n = len(codenames)

    async def _rank(alias: str) -> tuple[str, list[str] | None]:
        try:
            reply = await asyncio.wait_for(
                caller(alias, _RANK_PROMPT.format(n=n, prompt=prompt, candidates=block)),
                timeout,
            )
        except Exception:  # noqa: BLE001 - a bad ranker is dropped, never crashes the vote
            return alias, None
        return alias, _parse_ranking(reply, codenames)

    results = await asyncio.gather(*[_rank(a.alias) for a in ok_members])
    rankings = {alias: order for alias, order in results if order}
    if len(rankings) < 2:
        ok = [a.answer for a in ok_members if a.answer is not None]
        return await _judge(prompt, ok, caller=caller, judge_aliases=judge_aliases, rng=rng)

    scores: dict[str, float] = {}
    first_votes: dict[str, tuple[int, int]] = {}  # codename -> (firsts, non-self voters)
    for cn in codenames:
        owner = owner_of[cn]
        ranks: list[int] = []
        firsts = 0
        for ranker, order in rankings.items():
            if ranker == owner:
                continue  # self-vote excluded
            ranks.append(order.index(cn) if cn in order else n)  # missing => tied last
            if order and order[0] == cn:
                firsts += 1
        scores[cn] = sum(ranks) / len(ranks) if ranks else float(n)
        first_votes[cn] = (firsts, len(ranks))

    winner = min(codenames, key=lambda cn: (scores[cn], -first_votes[cn][0], codenames.index(cn)))
    firsts, voters = first_votes[winner]
    unique_best = [scores[cn] for cn in codenames].count(scores[winner]) == 1
    if voters and firsts * 2 > voters:
        confidence = "high"
    elif unique_best and voters:
        confidence = "medium"
    else:
        confidence = "low"
    return AggregateResult(answer_of_alias[owner_of[winner]], "peer-rank", "", None, confidence)


async def _debate(
    prompt: str, ok_members: list[MemberAnswer], *, caller: AsyncCaller,
    judge_aliases: list[str], rng: random.Random | None,
    max_rounds: int = 2, threshold: float = 0.7, timeout: float = 30.0,
) -> AggregateResult:
    aliases = [a.alias for a in ok_members]
    current = {a.alias: (a.answer or "") for a in ok_members}
    if len(aliases) < 2:
        return await _judge(
            prompt, list(current.values()), caller=caller,
            judge_aliases=judge_aliases, rng=rng,
        )

    async def _challenge(index: int, alias: str, block: str) -> tuple[str, str | None]:
        stance = _STANCES[index % len(_STANCES)]
        try:
            reply = await asyncio.wait_for(
                caller(
                    alias,
                    _DEBATE_PROMPT.format(stance=stance, prompt=prompt, candidates=block),
                ),
                timeout,
            )
        except Exception:  # noqa: BLE001 - a failed debater keeps its prior answer
            return alias, None
        return alias, _parse_revision(reply)

    conv = 0.0
    for _round in range(max_rounds):
        block, _ = anonymize_pairs([(a, current[a]) for a in aliases], rng=rng)
        results = await asyncio.gather(
            *[_challenge(i, a, block) for i, a in enumerate(aliases)]
        )
        for alias, revised in results:
            if revised:
                current[alias] = revised
        conv = _mean_pairwise_jaccard(list(current.values()))
        if conv >= threshold:
            break

    confidence = "high" if conv >= 0.7 else "medium" if conv >= 0.4 else "low"
    judged = await _judge(
        prompt, list(current.values()), caller=caller,
        judge_aliases=judge_aliases, rng=rng,
    )
    return AggregateResult(
        judged.answer, "debate", judged.disagreements, judged.judge_used, confidence
    )


async def aggregate(
    prompt: str,
    answers: list[MemberAnswer],
    *,
    caller: AsyncCaller,
    judge_aliases: list[str],
    rng: random.Random | None = None,
    mode: str | None = None,
    timeout: float = 30.0,
) -> AggregateResult:
    ok_members = [a for a in answers if a.ok and a.answer is not None]
    ok = [a.answer for a in ok_members if a.answer is not None]
    if not ok:
        raise AllMembersFailed("every member abstained")
    if mode is None:
        if _looks_closed_form(ok):
            return _vote(ok)
        return await _judge(prompt, ok, caller=caller, judge_aliases=judge_aliases, rng=rng)
    if mode == "vote":
        return _vote(ok)
    if mode == "judge":
        return await _judge(prompt, ok, caller=caller, judge_aliases=judge_aliases, rng=rng)
    if mode == "peer-rank":
        return await _peer_rank(
            prompt, ok_members, caller=caller, judge_aliases=judge_aliases,
            rng=rng, timeout=timeout,
        )
    if mode == "debate":
        return await _debate(
            prompt, ok_members, caller=caller, judge_aliases=judge_aliases,
            rng=rng, timeout=timeout,
        )
    raise ValueError(f"unknown aggregation mode: {mode}")
