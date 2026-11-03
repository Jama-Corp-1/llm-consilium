import pytest

from council.heuristics import CONFIDENCE_THRESHOLD, classify_local
from council.router import CAPABILITIES


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("```python\ndef f(x):\n    return x + 1\n```\nwhy is this slow?", "code"),
        ("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b", "code"),
        ('Traceback (most recent call last):\n  File "a.py", line 1', "code"),
        ("Refactor council/compose.py to drop the diversity pass", "code"),
        ("Why does a vendor-diverse council beat a single strong model?", "reasoning"),
        ("Compare the trade-offs of judge synthesis versus majority vote", "reasoning"),
        ("What is the capital of Portugal?", "fast"),
        ("ping", "fast"),
    ],
)
def test_classify_local_confident_cases(prompt, expected):
    capability, confidence = classify_local(prompt)
    assert capability == expected
    assert confidence >= CONFIDENCE_THRESHOLD


def test_code_block_about_architecture_is_code():
    # Review Focus 2: the code signal wins over the reasoning signal, deterministically.
    prompt = "```python\nclass Router: ...\n```\nWhat are the trade-offs of this design?"
    assert classify_local(prompt)[0] == "code"


def test_short_reasoning_keyword_is_not_fast():
    # Review Focus 3: brevity must not override an explicit reasoning ask.
    capability, confidence = classify_local("Why?")
    assert capability == "reasoning"
    assert confidence >= CONFIDENCE_THRESHOLD


def test_russian_prompt_defers_to_the_llm():
    # Review Focus 4: non-English prompts are not guessed at.
    capability, confidence = classify_local(
        "Объясни, как устроена маршрутизация запросов в этом проекте и почему именно так."
    )
    assert confidence < CONFIDENCE_THRESHOLD


def test_unmatched_prompt_defers_to_the_llm():
    capability, confidence = classify_local("Tell me about the thing we discussed.")
    assert capability == "general"
    assert confidence < CONFIDENCE_THRESHOLD


def test_classify_local_never_returns_an_unknown_capability():
    for prompt in ("", " ", "x" * 5000, "SELECT 1;", "почему?"):
        assert classify_local(prompt)[0] in CAPABILITIES


def test_keyword_cluster_is_code_at_the_lower_confidence():
    capability, confidence = classify_local(
        "In this module I import the helper and then return early from the loop body."
    )
    assert capability == "code"
    assert confidence == pytest.approx(0.8)


def test_single_code_keyword_is_not_a_cluster():
    assert classify_local("Please import the latest figures into the deck.")[0] != "code"


def test_long_multi_question_prompt_is_reasoning():
    prompt = "A " * 700 + "? and then what ?"
    capability, confidence = classify_local(prompt)
    assert capability == "reasoning"
    assert confidence >= CONFIDENCE_THRESHOLD


def test_blank_prompt_is_not_fast():
    assert classify_local("   \n ") == ("general", pytest.approx(0.3))
