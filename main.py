"""Analyze one or more plain-text books with readable or structured reports."""

import argparse
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile

from stats import analyze_book, get_book_count, get_book_dict, chars_dict_sorted_list


def get_book_text(path, encoding="utf-8"):
    with open(path, encoding=encoding) as book:
        return book.read()


def print_report(book_path, wc, letters_sort):
    """Retain the original course report helper."""
    print(
        f"============ TEXTSCOPE ============\nAnalyzing book found at {book_path}..."
    )
    print(f"----------- Word Count ----------\nFound {wc} total words")
    print("--------- Character Count -------")
    for letter, count in letters_sort:
        if letter.isalpha():
            print(f"{letter}: {count}")
    print("============= END ===============")


def positive_int(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def render_text(books):
    reports = []
    for book in books:
        lines = [
            "============ TEXTSCOPE ============",
            f"Analyzing book found at {book.path}...",
            "----------- Word Count ----------",
            f"Found {book.word_count} total words",
            "--------- Character Count -------",
        ]
        lines.extend(
            f"{entry['character']}: {entry['count']}" for entry in book.letters
        )
        lines.extend(
            [
                "------------ Insights -----------",
                f"Characters: {book.character_count:,} | Lines: {book.line_count:,}",
                f"Unique vocabulary: {book.unique_words:,} ({book.vocabulary_percent:.2f}% of letter tokens)",
                f"Estimated reading time: {book.reading_minutes:,} minutes",
                "------------ Top Words ----------",
            ]
        )
        maximum = book.top_words[0]["count"] if book.top_words else 1
        for entry in book.top_words:
            bar = "#" * max(1, round(24 * entry["count"] / maximum))
            lines.append(f"{entry['word']:<20} {entry['count']:>7,}  {bar}")
        if not book.top_words:
            lines.append("No words match the ranking filters.")
        lines.append("============= END ===============")
        reports.append("\n".join(lines))
    if len(books) > 1:
        lines = [
            "----------- Comparison ----------",
            f"{'Book':<32} {'Words':>10} {'Unique':>10} {'Minutes':>10}",
        ]
        for book in books:
            lines.append(
                f"{Path(book.path).name:<32} {book.word_count:>10,} {book.unique_words:>10,} {book.reading_minutes:>10,}"
            )
        reports.append("\n".join(lines))
    return "\n\n".join(reports) + "\n"


def render_report(books, format):
    if format == "text":
        return render_text(books)
    if format == "json":
        return (
            json.dumps([book.to_dict() for book in books], indent=2, ensure_ascii=False)
            + "\n"
        )
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "path",
            "word_count",
            "character_count",
            "alphabetic_count",
            "line_count",
            "unique_words",
            "vocabulary_percent",
            "reading_minutes",
        ]
    )
    for book in books:
        writer.writerow(
            [
                book.path,
                book.word_count,
                book.character_count,
                book.alphabetic_count,
                book.line_count,
                book.unique_words,
                book.vocabulary_percent,
                book.reading_minutes,
            ]
        )
    return output.getvalue()


def write_output(path, content):
    """Atomically replace a report, preserving existing output on write errors."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as report:
            temporary = report.name
            report.write(content)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Count, rank, and compare plain-text books"
    )
    parser.add_argument(
        "books", nargs="*", type=Path, help="One or more plain-text files"
    )
    parser.add_argument("--format", choices=["text", "json", "csv"], default="text")
    parser.add_argument(
        "--output", type=Path, help="Write a report instead of printing it"
    )
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument("--top", type=positive_int, default=10)
    parser.add_argument("--min-word-length", type=positive_int, default=1)
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude a word from rankings; repeatable",
    )
    parser.add_argument("--reading-wpm", type=positive_int, default=250)
    parser.add_argument(
        "--exclude-file",
        action="append",
        default=[],
        type=Path,
        help="UTF-8 file of words to exclude, one per line; repeatable",
    )
    parser.add_argument(
        "--interactive", action="store_true", help="Open the keyboard-driven library"
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Print a linear report; suitable for screen readers",
    )
    parser.add_argument(
        "--theme",
        choices=["library", "paper", "contrast", "terminal"],
        default="library",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Use terminal defaults without color pairs",
    )
    parser.add_argument("--spacing", choices=["compact", "roomy"], default="compact")
    args = parser.parse_args(argv)
    try:
        for path in args.exclude_file:
            args.exclude.extend(
                word.strip()
                for word in path.read_text(encoding="utf-8").splitlines()
                if word.strip()
            )
    except (OSError, UnicodeError) as error:
        print(f"Error reading exclusions: {error}", file=sys.stderr)
        return 1
    if args.interactive and (args.plain or args.output or args.format != "text"):
        parser.error(
            "--interactive cannot be combined with --plain, --output, or structured formats"
        )
    interactive = args.interactive or (
        not args.books
        and not args.plain
        and not args.output
        and args.format == "text"
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and os.environ.get("TERM", "dumb") != "dumb"
    )
    if interactive:
        if (
            not sys.stdin.isatty()
            or not sys.stdout.isatty()
            or os.environ.get("TERM", "dumb") == "dumb"
        ):
            parser.error(
                "Interactive mode needs a terminal. Use --plain for a linear report."
            )
        from terminal_ui import run_interactive

        paths = args.books or sorted(
            (Path(__file__).resolve().parent / "books").glob("*.txt")
        )
        try:
            return run_interactive(paths, args)
        except (ImportError, RuntimeError) as error:
            print(
                f"Interactive mode unavailable: {error}. Use --plain.", file=sys.stderr
            )
            return 1
    if not args.books:
        args.books = [Path(__file__).resolve().parent / "books/frankenstein.txt"]
    if args.output and any(
        args.output.resolve() == path.resolve()
        for path in [*args.books, *args.exclude_file]
    ):
        parser.error("Output must differ from every input book or exclusion file")
    books = []
    for path in args.books:
        try:
            books.append(
                analyze_book(
                    path,
                    encoding=args.encoding,
                    top=args.top,
                    min_word_length=args.min_word_length,
                    exclude=args.exclude,
                    reading_wpm=args.reading_wpm,
                )
            )
        except (OSError, UnicodeError, LookupError) as error:
            print(f"Error reading {path}: {error}", file=sys.stderr)
            return 1
    try:
        report = render_report(books, args.format)
        if args.output:
            write_output(args.output, report)
            print(
                f"Saved {len(books)} book report(s) to {args.output}", file=sys.stderr
            )
        else:
            sys.stdout.write(report)
        return 0
    except BrokenPipeError:
        return 0
    except (OSError, UnicodeError) as error:
        print(f"Error writing report: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
