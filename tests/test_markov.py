import random

import pytest

from tarpit.markov import MarkovChain

# Toy corpus for testing — small, deterministic, predictable
TOY_CORPUS = (
    "The cat sat on the mat. The dog ran fast! "
    "A fox jumped high. The cat ran fast on the mat."
)

# Corpus designed to create a dead-end pair
DEAD_END_CORPUS = "hello world. goodbye world. unique end here."


def test_public_surface() -> None:
    chain = MarkovChain.from_corpus_text(TOY_CORPUS)
    assert hasattr(chain, "from_corpus_text")
    assert hasattr(chain, "generate")


def test_order_2_followers_are_2_tuples() -> None:
    chain = MarkovChain.from_corpus_text(TOY_CORPUS)
    for key in chain._followers:
        assert isinstance(key, tuple)
        assert len(key) == 2


def test_determinism_same_seed() -> None:
    chain = MarkovChain.from_corpus_text(TOY_CORPUS)
    result1 = chain.generate(random.Random(42), 50)
    result2 = chain.generate(random.Random(42), 50)
    assert result1 == result2


def test_reproducibility_across_rebuilds() -> None:
    result1 = MarkovChain.from_corpus_text(TOY_CORPUS).generate(random.Random(42), 50)
    result2 = MarkovChain.from_corpus_text(TOY_CORPUS).generate(random.Random(42), 50)
    assert result1 == result2


def test_generate_word_count() -> None:
    chain = MarkovChain.from_corpus_text(TOY_CORPUS)
    result = chain.generate(random.Random(1), 20)
    assert len(result) == 20


def test_generate_returns_list_of_strings() -> None:
    chain = MarkovChain.from_corpus_text(TOY_CORPUS)
    result = chain.generate(random.Random(7), 10)
    assert isinstance(result, list)
    assert all(isinstance(w, str) for w in result)


def test_dead_end_never_raises() -> None:
    # The dead-end corpus has pairs that lead to dead ends.
    # generate must re-draw rather than raise.
    chain = MarkovChain.from_corpus_text(DEAD_END_CORPUS)
    for seed in range(20):
        result = chain.generate(random.Random(seed), 30)
        assert len(result) == 30


def test_sentence_start_selection() -> None:
    # Sentence starters are pairs following '.', '!', '?'
    chain = MarkovChain.from_corpus_text(TOY_CORPUS)
    # sentence_starts should contain pairs that appear after punctuation
    assert len(chain._sentence_starts) >= 1
    # Every start pair is a 2-tuple
    for pair in chain._sentence_starts:
        assert isinstance(pair, tuple) and len(pair) == 2


def test_generate_empty_for_tiny_corpus() -> None:
    # Single word corpus: less than 2 words, should return empty
    chain = MarkovChain.from_corpus_text("hello")
    result = chain.generate(random.Random(0), 10)
    assert result == []
