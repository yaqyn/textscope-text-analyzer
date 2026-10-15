import contextlib
import csv
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from main import main, render_report, write_output
from stats import analyze_book, get_book_count, get_book_dict, chars_dict_sorted_list

ROOT = Path(__file__).resolve().parent


class TextScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book = self.root / "sample.txt"
        self.book.write_text(
            "Hello hello! Café café.\nDon't don’t 123.\n", encoding="utf-8"
        )

    def test_legacy_counts_and_stable_ties(self):
        self.assertEqual(get_book_count(" one\ttwo\nthree "), 3)
        self.assertEqual(get_book_dict("Aa!"), {"a": 2, "!": 1})
        self.assertEqual(chars_dict_sorted_list({"b": 2, "a": 2}), [("a", 2), ("b", 2)])

    def test_unicode_analysis(self):
        book = analyze_book(self.book)
        self.assertEqual(book.word_count, 7)
        self.assertEqual(book.line_count, 2)
        self.assertEqual(book.unique_words, 3)
        self.assertEqual(
            book.top_words,
            [
                {"word": "café", "count": 2},
                {"word": "don't", "count": 2},
                {"word": "hello", "count": 2},
            ],
        )
        self.assertEqual(book.vocabulary_percent, 50)
        self.assertEqual(book.character_count, len(self.book.read_text()))

    def test_filters_do_not_change_totals(self):
        book = analyze_book(self.book, top=1, min_word_length=5, exclude=["HELLO"])
        self.assertEqual(book.top_words, [{"word": "don't", "count": 2}])
        self.assertEqual(book.word_count, 7)
        self.assertEqual(book.unique_words, 3)

    def test_empty_book(self):
        self.book.write_text("")
        book = analyze_book(self.book)
        self.assertEqual(book.word_count, 0)
        self.assertEqual(book.vocabulary_percent, 0)
        self.assertEqual(book.reading_minutes, 0)
        self.assertIn("No words match", render_report([book], "text"))

    def test_canonically_equivalent_vocabulary_and_exclusions(self):
        text = "café cafe\u0301"
        self.book.write_text(text, encoding="utf-8")
        book = analyze_book(self.book)
        self.assertEqual(book.unique_words, 1)
        self.assertEqual(book.top_words, [{"word": "café", "count": 2}])
        self.assertEqual(book.character_count, len(text))
        self.assertEqual(book.word_count, 2)
        self.assertEqual(analyze_book(self.book, exclude=["cafe\u0301"]).top_words, [])

    def test_reading_time_rounds_up(self):
        self.assertEqual(analyze_book(self.book, reading_wpm=3).reading_minutes, 3)

    def test_casefold_vocabulary(self):
        self.book.write_text("Straße STRASSE مرحبا مرحبا")
        self.assertEqual(analyze_book(self.book).unique_words, 2)

    def test_newline_preservation_and_final_line(self):
        self.book.write_bytes(b"one\r\ntwo")
        book = analyze_book(self.book)
        self.assertEqual(book.character_count, 8)
        self.assertEqual(book.line_count, 2)
        self.assertEqual(book.word_count, 2)

    def test_json_and_csv_multiple_books(self):
        books = [analyze_book(self.book), analyze_book(self.book)]
        parsed = json.loads(render_report(books, "json"))
        self.assertEqual(len(parsed), 2)
        rows = list(csv.DictReader(io.StringIO(render_report(books, "csv"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["word_count"], "7")
        self.assertIn("Comparison", render_report(books, "text"))

    def test_missing_file_and_invalid_encoding(self):
        for args in [
            [str(self.root / "missing")],
            [str(self.book), "--encoding", "not-an-encoding"],
        ]:
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(main(args), 1)
                self.assertIn("Error reading", error.getvalue())

    def test_decode_error_reports_hint(self):
        self.book.write_bytes(b"\xff")
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main([str(self.book)]), 1)
            self.assertIn("decode", error.getvalue())

    def test_output_encoding_failure_returns_error(self):
        with (
            patch("main.sys.stdout.write", side_effect=UnicodeEncodeError(
                "ascii", "caf\u00e9", 3, 4, "ordinal not in range")),
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            self.assertEqual(main([str(self.book)]), 1)
        self.assertIn("Error writing report", error.getvalue())

    def test_input_cannot_be_overwritten(self):
        before = self.book.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main([str(self.book), "--output", str(self.book)])
        self.assertEqual(before, self.book.read_bytes())

    def test_export_to_nested_path(self):
        output = self.root / "reports/book.json"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main([str(self.book), "--format", "json", "--output", str(output)]), 0
            )
        self.assertEqual(json.loads(output.read_text())[0]["word_count"], 7)

    def test_atomic_write_failure_preserves_existing(self):
        output = self.root / "report.txt"
        output.write_text("existing")
        with (
            patch("main.os.replace", side_effect=OSError("blocked")),
            self.assertRaises(OSError),
        ):
            write_output(output, "replacement")
        self.assertEqual(output.read_text(), "existing")
        self.assertEqual(
            sorted(p.name for p in self.root.iterdir()), ["report.txt", "sample.txt"]
        )

    def test_import_has_no_side_effects(self):
        result = subprocess.run(
            ["python3", "-c", "import main"], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_launcher_from_another_directory(self):
        result = subprocess.run(
            [str(ROOT / "launch")], cwd=self.root, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Found 75767 total words", result.stdout)

    def test_bundled_books_preserve_original_totals(self):
        for path in (ROOT / "books").glob("*.txt"):
            with self.subTest(path=path.name):
                result = analyze_book(path)
                original = path.read_text()
                self.assertEqual(result.word_count, get_book_count(original))
                self.assertEqual(
                    {row["character"]: row["count"] for row in result.letters},
                    {
                        char: count
                        for char, count in get_book_dict(original).items()
                        if char.isalpha()
                    },
                )
