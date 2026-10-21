import random


class MarkovChain:
    """Order-2 Markov chain. Immutable after construction."""

    _followers: dict[tuple[str, str], list[str]]
    _sentence_starts: list[tuple[str, str]]

    def __init__(
        self,
        followers: dict[tuple[str, str], list[str]],
        sentence_starts: list[tuple[str, str]],
    ) -> None:
        self._followers = followers
        self._sentence_starts = sentence_starts

    @classmethod
    def from_corpus_text(cls, text: str) -> "MarkovChain":
        """Tokenise `text` into words, build (w1, w2) -> [next_word] table."""
        words = text.split()
        if len(words) < 2:
            return cls({}, [])

        followers: dict[tuple[str, str], list[str]] = {}
        sentence_starts: list[tuple[str, str]] = []

        # The first pair is always a sentence start
        sentence_starts.append((words[0], words[1]))

        for i in range(len(words) - 2):
            pair = (words[i], words[i + 1])
            next_word = words[i + 2]
            if pair not in followers:
                followers[pair] = []
            followers[pair].append(next_word)

            # If words[i] ends a sentence, the pair (words[i+1], words[i+2]) starts a new one
            if words[i].endswith((".", "!", "?")):
                start_pair = (words[i + 1], words[i + 2])
                if start_pair not in sentence_starts:
                    sentence_starts.append(start_pair)

        # Sort sentence_starts for deterministic iteration order
        sentence_starts_sorted = sorted(set(sentence_starts))

        return cls(followers, sentence_starts_sorted)

    def generate(self, rng: random.Random, word_count: int) -> list[str]:
        """Produce `word_count` words.

        Starting pair is chosen via `rng.choice` from the set of pairs that
        begin a sentence (i.e. the word after a '.', '!' or '?' token).
        Each subsequent word is `rng.choice(followers[(w1, w2)])`.
        If a pair has no followers mid-walk, re-draw a start pair."""
        if not self._sentence_starts or word_count <= 0:
            return []

        starts = self._sentence_starts
        w1, w2 = rng.choice(starts)
        result = [w1, w2]

        while len(result) < word_count:
            pair = (result[-2], result[-1])
            if pair in self._followers and self._followers[pair]:
                next_word = rng.choice(self._followers[pair])
                result.append(next_word)
            else:
                # Dead end: re-draw a sentence-start pair
                w1, w2 = rng.choice(starts)
                result.extend([w1, w2])

        return result[:word_count]
