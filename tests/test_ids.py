import random

import pytest

from tarpit.ids import (
    BAIT_ID,
    ID_PATTERN,
    NEW_ID_PATTERN,
    generate_new_ids,
    is_valid_id,
)


def test_bait_id_is_root() -> None:
    assert BAIT_ID == "root"


def test_bait_id_is_valid() -> None:
    assert is_valid_id(BAIT_ID) is True


def test_generate_new_ids_determinism() -> None:
    result1 = generate_new_ids(random.Random("x"), 10)
    result2 = generate_new_ids(random.Random("x"), 10)
    assert result1 == result2


def test_generate_new_ids_matches_pattern() -> None:
    for seed in range(100):
        rng = random.Random(seed)
        ids = generate_new_ids(rng, 5)
        for id_ in ids:
            assert NEW_ID_PATTERN.match(id_), f"{id_!r} does not match NEW_ID_PATTERN"


def test_generate_new_ids_count() -> None:
    rng = random.Random(42)
    result = generate_new_ids(rng, 7)
    assert len(result) == 7


def test_is_valid_id_true_cases() -> None:
    assert is_valid_id("root") is True
    assert is_valid_id("a8f3") is True
    assert is_valid_id("." * 64) is True
    assert is_valid_id("AbCd123") is True
    assert is_valid_id("a-b_c.d~e") is True


def test_is_valid_id_false_empty() -> None:
    assert is_valid_id("") is False


def test_is_valid_id_false_too_long() -> None:
    assert is_valid_id("a" * 65) is False


def test_is_valid_id_false_slash() -> None:
    assert is_valid_id("abc/def") is False


def test_is_valid_id_false_angle_bracket() -> None:
    assert is_valid_id("<script>") is False


def test_is_valid_id_false_space() -> None:
    assert is_valid_id("hello world") is False


def test_is_valid_id_false_percent_encoded() -> None:
    assert is_valid_id("hello%20world") is False


def test_generate_specific_determinism() -> None:
    result = generate_new_ids(random.Random("x"), 10)
    assert result == generate_new_ids(random.Random("x"), 10)
    assert all(NEW_ID_PATTERN.match(id_) for id_ in result)
