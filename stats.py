"""Dependency-free counting and streaming book analysis."""

from collections import Counter
from dataclasses import asdict, dataclass
import math
import re
import unicodedata

# Vocabulary tokens contain Unicode letters and optional internal apostrophes.
WORD_PATTERN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


def get_book_count(file_contents):
    """Keep the course definition: words are whitespace-separated tokens."""
    return len(file_contents.split())


def get_book_dict(file_contents):
    return dict(Counter(file_contents.lower()))


def sort_on(item):
    return item[1]


def chars_dict_sorted_list(letters_dict):
    return sorted(letters_dict.items(), key=lambda item: (-item[1], item[0]))


@dataclass
class BookAnalysis:
    path: str
    word_count: int
    character_count: int
    alphabetic_count: int
    line_count: int
    unique_words: int
    vocabulary_percent: float
    reading_minutes: int
    letters: list[dict]
    top_words: list[dict]

    def to_dict(self):
        return asdict(self)


def analyze_book(
    path, *, encoding="utf-8", top=10, min_word_length=1, exclude=(), reading_wpm=250
):
    """Read one line at a time; retain character counts and vocabulary frequencies.

    Word totals preserve whitespace semantics. Vocabulary rankings normalize
    Unicode letter tokens with NFC and casefold, treating curly and straight apostrophes
    equivalently. Exclusions affect rankings only, not totals or vocabulary size.
    """
    if top < 1 or min_word_length < 1 or reading_wpm < 1:
        raise ValueError("top, minimum word length, and reading speed must be positive")
    characters, vocabulary = Counter(), Counter()
    word_count = line_count = character_count = 0
    with open(path, encoding=encoding, newline="") as book:
        for line in book:
            normalized_line = unicodedata.normalize("NFC", line)
            line_count += 1
            word_count += get_book_count(line)
            character_count += len(line)
            characters.update(normalized_line.lower())
            vocabulary.update(
                word.casefold().replace("’", "'")
                for word in WORD_PATTERN.findall(normalized_line)
            )
    letter_counts = [
        (char, count)
        for char, count in chars_dict_sorted_list(characters)
        if char.isalpha()
    ]
    excluded = {
        unicodedata.normalize("NFC", word).casefold().replace("’", "'")
        for word in exclude
    }
    ranked = sorted(
        (
            (word, count)
            for word, count in vocabulary.items()
            if len(word) >= min_word_length and word not in excluded
        ),
        key=lambda item: (-item[1], item[0]),
    )[:top]
    token_count = sum(vocabulary.values())
    return BookAnalysis(
        path=str(path),
        word_count=word_count,
        character_count=character_count,
        alphabetic_count=sum(count for _, count in letter_counts),
        line_count=line_count,
        unique_words=len(vocabulary),
        vocabulary_percent=round(100 * len(vocabulary) / token_count, 2)
        if token_count
        else 0,
        reading_minutes=math.ceil(word_count / reading_wpm),
        letters=[{"character": char, "count": count} for char, count in letter_counts],
        top_words=[{"word": word, "count": count} for word, count in ranked],
    )
