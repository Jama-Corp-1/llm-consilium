import time

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


def test_russian_reasoning_prompt_is_classified_locally():
    # Review Focus 4 (amended): Russian is a first-class traffic language here.
    capability, confidence = classify_local(
        "Объясни, как устроена маршрутизация запросов в этом проекте и почему именно так."
    )
    assert capability == "reasoning"
    assert confidence >= CONFIDENCE_THRESHOLD


@pytest.mark.parametrize(
    "prompt",
    [
        "Почему совет из разных моделей лучше одной сильной модели?",
        "Сравните judge-синтез и голосование большинством.",
        "Стоит ли выносить классификатор в локальный код?",
        "Обоснуйте выбор архитектуры этого прокси.",
    ],
)
def test_russian_markers_match_with_inflection(prompt):
    capability, confidence = classify_local(prompt)
    assert capability == "reasoning"
    assert confidence >= CONFIDENCE_THRESHOLD


def test_russian_prose_without_a_marker_defers_to_the_llm():
    capability, confidence = classify_local(
        "Напиши письмо коллеге о переносе встречи на вторник."
    )
    assert capability == "general"
    assert confidence < CONFIDENCE_THRESHOLD


def test_russian_prompt_with_a_code_block_is_code():
    capability, confidence = classify_local(
        "Почему это медленно?\n```python\ndef f(x):\n    return x + 1\n```"
    )
    assert capability == "code"
    assert confidence >= CONFIDENCE_THRESHOLD


def test_unmatched_prompt_defers_to_the_llm():
    capability, confidence = classify_local("Tell me about the thing we discussed.")
    assert capability == "general"
    assert confidence < CONFIDENCE_THRESHOLD


def test_classify_local_never_returns_an_unknown_capability():
    for prompt in ("", " ", "x" * 5000, "SELECT 1;", "почему?"):
        assert classify_local(prompt)[0] in CAPABILITIES


def test_keyword_cluster_is_code_at_the_lower_confidence():
    capability, confidence = classify_local("import os and return cleanup(x) when the loop ends")
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


# --- Fix round 1: misroutes found in review -------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "Which of these two schemas scales better under 10k writes/s?",
        "Is 2**82589933-1 prime?",
    ],
)
def test_short_but_hard_questions_are_not_fast(prompt):
    # The brevity rule must not swallow a comparison or a computation.
    capability, confidence = classify_local(prompt)
    assert capability == "reasoning"
    assert confidence >= CONFIDENCE_THRESHOLD


def test_prose_keyword_cluster_without_code_punctuation_is_not_code():
    capability, _ = classify_local(
        "Compare the design trade-offs of class-based versus functional import strategies"
    )
    assert capability == "reasoning"


def test_prose_question_with_two_code_keywords_is_not_code():
    assert classify_local(
        "Should we return an error or retry when the const pool is empty?"
    )[0] != "code"


def test_numbered_label_is_not_a_source_path():
    assert classify_local("See rule 1.c of the contract and tell me if we are liable.")[0] != "code"


def test_real_paths_still_read_as_code():
    for prompt in (
        "Refactor council/compose.py to drop the diversity pass",
        "What changed in test2.py?",
        "Look at src/main.rs",
    ):
        assert classify_local(prompt) == ("code", pytest.approx(0.9))


def test_indented_block_counts_as_code_punctuation():
    capability, confidence = classify_local("my module:\n    import os\n    return 1\n")
    assert capability == "code"
    assert confidence == pytest.approx(0.8)


def test_pathological_inputs_stay_linear():
    # A greedy class in front of the path extension used to backtrack quadratically;
    # a pasted traceback or a wall of punctuation must not stall the hot path.
    for prompt in ("x" * 50_000, "a/b/c." * 5_000, "(" * 20_000, "src/mod_x.py " * 2_000):
        start = time.perf_counter()
        classify_local(prompt)
        assert time.perf_counter() - start < 1.0


# --- Fix round 2: the arithmetic signal must not fire on prose -------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "Please make this **bold** text sound nicer.",
        "Hi **bold**",
        "Our revenue was 1200000 rubles last quarter, summarise the trend.",
        "Use the ^ character to anchor the regex, not the $ one.",
    ],
)
def test_markdown_emphasis_and_long_numbers_are_not_reasoning(prompt):
    capability, confidence = classify_local(prompt)
    assert capability != "reasoning" or confidence < CONFIDENCE_THRESHOLD


@pytest.mark.parametrize("prompt", ["Is 2**82589933-1 prime?", "Simplify x**2 + 2^31"])
def test_power_between_operands_is_still_reasoning(prompt):
    capability, confidence = classify_local(prompt)
    assert capability == "reasoning"
    assert confidence >= CONFIDENCE_THRESHOLD


# --- Fix round 3: soft markers defer, Russian lookups route locally --------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "What should we have for dinner tonight, something quick?",
        "Can you make this sentence better: the meeting was moved.",
        "How do I make this page load faster?",
        "Make the font scales consistent across breakpoints",
    ],
)
def test_soft_markers_defer_instead_of_deciding(prompt):
    # A bare "should we"/"better"/"faster"/"scales" is a weak signal: report it, but let
    # the LLM classifier decide rather than paying reasoning effort for a trivial ask.
    capability, confidence = classify_local(prompt)
    assert confidence < CONFIDENCE_THRESHOLD
    assert capability == "reasoning"
    assert confidence == pytest.approx(0.6)


def test_strong_markers_still_decide_confidently():
    assert classify_local("Which of these two schemas scales better under 10k writes/s?") == (
        "reasoning",
        pytest.approx(0.8),
    )
    assert classify_local("Is 2**82589933-1 prime?") == ("reasoning", pytest.approx(0.8))


@pytest.mark.parametrize(
    "prompt",
    [
        "Что такое LiteLLM?",
        "какой командой посмотреть логи systemd?",
        "Как лучше назвать эту функцию?",
    ],
)
def test_short_russian_questions_are_fast(prompt):
    capability, confidence = classify_local(prompt)
    assert capability == "fast"
    assert confidence >= CONFIDENCE_THRESHOLD


@pytest.mark.parametrize("prompt", ["什么是 LiteLLM？", "ما هو LiteLLM؟", "LiteLLM מה זה"])
def test_uncovered_scripts_still_defer(prompt):
    # We carry no markers for these scripts, so brevity alone must not mean "fast".
    capability, confidence = classify_local(prompt)
    assert capability == "general"
    assert confidence < CONFIDENCE_THRESHOLD
