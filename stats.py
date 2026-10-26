"""Dependency-free counting and streaming book analysis."""

from collections import Counter
from dataclasses import asdict, dataclass
import math
import unicodedata


def vocabulary_words(text):
    """Return Unicode words, retaining combining marks and internal apostrophes."""
    words, current = [], []
    for char in text:
        category = unicodedata.category(char)
        if category.startswith("L") or (current and category.startswith("M")):
            current.append(char)
        elif char in "'’" and current and current[-1] not in "'’":
            current.append(char)
        else:
            if current and current[-1] in "'’":
                current.pop()
            if current:
                words.append("".join(current))
            current = []
    if current and current[-1] in "'’":
        current.pop()
    if current:
        words.append("".join(current))
    return words


def vocabulary_length(word):
    """Measure visible token characters without counting combining marks."""
    return sum(not unicodedata.category(char).startswith("M") for char in word)


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
    path, *, encoding="utf-8", top=10, min_word_length=1, max_word_length=None, exclude=(), reading_wpm=250, word_sort="frequency", word_order="ascending"
):
    """Read one line at a time; retain character counts and vocabulary frequencies.

    Word totals preserve whitespace semantics. Vocabulary rankings normalize
    Unicode letter tokens with NFC and casefold, treating curly and straight apostrophes
    equivalently. Exclusions affect rankings only, not totals or vocabulary size.
    """
    if top < 0 or min_word_length < 1 or reading_wpm < 1 or word_sort not in {"frequency", "alphabetical"} or word_order not in {"ascending", "descending"} or (max_word_length is not None and max_word_length < min_word_length):
        raise ValueError("top must be nonnegative; minimum word length and reading speed must be positive")
    characters, vocabulary = Counter(), Counter()
    word_count = line_count = character_count = 0
    with open(path, encoding=encoding, newline="") as book:
        for line in book:
            normalized_line = unicodedata.normalize("NFC", line)
            line_count += 1
            word_count += get_book_count(line)
            character_count += len(line)
            characters.update(normalized_line.lower())
            # Python's \d omits numeric characters such as ² and ½, while
            # \w includes them. Treat all Unicode number categories as separators.
            vocabulary_line = "".join(
                " " if unicodedata.category(char).startswith("N") else char
                for char in normalized_line
            )
            vocabulary.update(
                word.casefold().replace("’", "'")
                for word in vocabulary_words(vocabulary_line)
            )
    letter_counts = [
        (char, count)
        for char, count in chars_dict_sorted_list(characters)
        if char.isalpha()
    ]
    excluded = {
        unicodedata.normalize("NFC", word.strip()).casefold().replace("’", "'")
        for word in exclude
    }
    ranked = sorted(
        (
            (word, count)
            for word, count in vocabulary.items()
            if vocabulary_length(word) >= min_word_length
            and (max_word_length is None or vocabulary_length(word) <= max_word_length)
            and word not in excluded
        ),
        key=(lambda item: item[0]) if word_sort == "alphabetical" else (lambda item: (-item[1], item[0])),
        reverse=word_sort == "alphabetical" and word_order == "descending",
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
