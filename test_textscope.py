import contextlib
import csv
import io
import json
import gzip
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from main import main, render_report, write_output
from stats import analyze_book, get_book_count, get_book_dict, chars_dict_sorted_list

ROOT = Path(__file__).resolve().parent


class TextScopeTests(unittest.TestCase):
    def test_gzip_books_are_analyzed_without_manual_extraction(self):
        compressed = self.root / "sample.txt.gz"
        with gzip.open(compressed, "wt", encoding="utf-8", newline="") as book:
            book.write("one two\nthree")
        result = analyze_book(compressed)
        self.assertEqual((result.word_count, result.line_count), (3, 2))

    def test_utf8_bom_is_not_part_of_first_excluded_word(self):
        stopwords = self.root / "bom-stopwords.txt"
        stopwords.write_bytes(b"\xef\xbb\xbfhello\n")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([str(self.book), "--exclude-file", str(stopwords), "--format", "json"]), 0)
        result = json.loads(output.getvalue())[0]
        self.assertNotIn("hello", [row["word"] for row in result["top_words"]])
        self.assertEqual(result["unique_words"], 3)

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

    def test_exclusions_ignore_accidental_surrounding_whitespace(self):
        self.assertNotIn("hello", [row["word"] for row in analyze_book(self.book, exclude=["  HELLO\t"]).top_words])

    def test_unicode_analysis(self):
        book = analyze_book(self.book)
        self.assertEqual(book.word_count, 7)
        self.assertEqual(book.line_count, 2)
        self.assertEqual(book.nonempty_line_count, 2)
        self.assertEqual(book.longest_line_length, 23)
        self.assertEqual(book.sentence_count, 3)
        self.assertEqual(book.unique_words, 3)
        self.assertEqual(book.hapax_words, 0)
        self.assertEqual(
            book.top_words,
            [
                {"word": "café", "count": 2},
                {"word": "don't", "count": 2},
                {"word": "hello", "count": 2},
            ],
        )
        self.assertEqual(book.vocabulary_percent, 50)
        self.assertEqual(book.average_word_length, 4.67)
        self.assertEqual(book.median_word_length, 5)
        self.assertEqual(book.character_count, len(self.book.read_text()))
        self.assertEqual(book.numeric_count, 3)
        self.assertEqual(book.punctuation_count, 5)

    def test_sentence_count_accepts_closing_quotes_and_unicode_endings(self):
        self.book.write_text(
            '“Hello!” “Really?” 你好。 Wait…\n', encoding='utf-8'
        )
        book = analyze_book(self.book)
        self.assertEqual(book.sentence_count, 4)

    def test_unicode_numbers_are_not_vocabulary(self):
        self.book.write_text("² ½ Ⅷ hello²world hello 123", encoding="utf-8")
        book = analyze_book(self.book)
        self.assertEqual(book.word_count, 6)
        self.assertEqual(book.unique_words, 2)
        self.assertEqual(book.top_words, [
            {"word": "hello", "count": 2},
            {"word": "world", "count": 1},
        ])
        self.assertEqual(book.vocabulary_percent, 66.67)

    def test_filters_do_not_change_totals(self):
        book = analyze_book(self.book, top=1, min_word_length=5, exclude=["HELLO"])
        self.assertEqual(book.top_words, [{"word": "don't", "count": 2}])
        self.assertEqual(book.word_count, 7)
        self.assertEqual(book.unique_words, 3)

    def test_vocabulary_can_be_sorted_alphabetically(self):
        book = analyze_book(self.book, word_sort="alphabetical")
        self.assertEqual([row["word"] for row in book.top_words], ["café", "don't", "hello"])
        with self.assertRaises(ValueError):
            analyze_book(self.book, word_sort="random")

    def test_alphabetical_vocabulary_can_be_descending(self):
        book = analyze_book(self.book, word_sort="alphabetical", word_order="descending")
        self.assertEqual([row["word"] for row in book.top_words], ["hello", "don't", "café"])
        with self.assertRaises(ValueError):
            analyze_book(self.book, word_order="random")

    def test_maximum_word_length_filters_rankings(self):
        book = analyze_book(self.book, max_word_length=4)
        self.assertEqual(book.top_words, [{"word": "café", "count": 2}])
        self.assertEqual(book.word_count, 7)
        with self.assertRaises(ValueError):
            analyze_book(self.book, min_word_length=5, max_word_length=4)

    def test_no_vocabulary_hides_rankings_without_changing_totals(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([str(self.book), "--no-vocabulary", "--format", "json"]), 0)
        result = json.loads(output.getvalue())[0]
        self.assertEqual(result["top_words"], [])
        self.assertEqual(result["word_count"], 7)
        self.assertEqual(result["unique_words"], 3)

    def test_empty_book(self):
        self.book.write_text("")
        book = analyze_book(self.book)
        self.assertEqual(book.word_count, 0)
        self.assertEqual(book.hapax_words, 0)
        self.assertEqual(book.vocabulary_percent, 0)
        self.assertEqual(book.average_word_length, 0)
        self.assertEqual(book.median_word_length, 0)
        self.assertEqual(book.reading_minutes, 0)
        self.assertEqual(book.nonempty_line_count, 0)
        self.assertIn("No words match", render_report([book], "text"))

    def test_require_words_rejects_empty_inputs(self):
        self.book.write_text("")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main([str(self.book), "--require-words"]), 1)
        self.assertIn(str(self.book), error.getvalue())

    def test_max_words_rejects_oversized_inputs(self):
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main([str(self.book), "--max-words", "6"]), 1)
        self.assertIn("Word limit exceeded", error.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.book), "--max-words", "7"]), 0)

    def test_min_words_rejects_undersized_inputs_without_writing(self):
        output = self.root / "report.json"
        output.write_text("existing", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(
                main([str(self.book), "--min-words", "8", "--format", "json", "--output", str(output)]),
                1,
            )
        self.assertIn("Minimum word count not met", error.getvalue())
        self.assertEqual(output.read_text(encoding="utf-8"), "existing")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.book), "--min-words", "7"]), 0)

    def test_exclusion_files_combine_with_inline_filters(self):
        stopwords = self.root / "stopwords.txt"
        stopwords.write_text("# Common greeting\nHELLO\n\n", encoding="utf-8")
        more = self.root / "more.txt"
        more.write_text("cafe\u0301\n", encoding="utf-8")
        output = self.root / "report.json"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.book), "--exclude-file", str(stopwords),
                                   "--exclude-file", str(more),
                                   "--exclude", "don't", "--format", "json",
                                   "--output", str(output)]), 0)
        result = json.loads(output.read_text())[0]
        self.assertEqual(result["top_words"], [])
        self.assertEqual(result["word_count"], 7)
        self.assertEqual(result["unique_words"], 3)

    def test_missing_exclusion_file_preserves_output(self):
        output = self.root / "report.txt"
        output.write_text("existing")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.book), "--exclude-file",
                                   str(self.root / "missing"), "--output", str(output)]), 1)
        self.assertEqual(output.read_text(), "existing")

    def test_exclusion_file_cannot_be_overwritten(self):
        stopwords = self.root / "stopwords.txt"
        stopwords.write_text("hello")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main([str(self.book), "--exclude-file", str(stopwords), "--output", str(stopwords)])
        self.assertEqual(stopwords.read_text(), "hello")

    def test_canonically_equivalent_vocabulary_and_exclusions(self):
        text = "café cafe\u0301"
        self.book.write_text(text, encoding="utf-8")
        book = analyze_book(self.book)
        self.assertEqual(book.unique_words, 1)
        self.assertEqual(book.top_words, [{"word": "café", "count": 2}])
        self.assertEqual(book.character_count, len(text))
        self.assertEqual(book.word_count, 2)
        self.assertEqual(analyze_book(self.book, exclude=["cafe\u0301"]).top_words, [])

    def test_nonempty_line_count_excludes_whitespace_only_lines(self):
        self.book.write_text("first\n   \nsecond\n", encoding="utf-8")
        book = analyze_book(self.book)
        self.assertEqual(book.line_count, 3)
        self.assertEqual(book.nonempty_line_count, 2)

    def test_paragraphs_are_separated_by_blank_lines(self):
        self.book.write_text("First line\ncontinues\n\n  \nSecond paragraph\n", encoding="utf-8")
        book = analyze_book(self.book)
        self.assertEqual(book.paragraph_count, 2)
        self.assertIn("Paragraphs: 2", render_report([book], "text"))

    def test_vocabulary_keeps_unicode_combining_marks_inside_words(self):
        self.book.write_text("हिन्दी हिन्दी", encoding="utf-8")
        self.assertEqual(
            analyze_book(self.book).top_words,
            [{"word": "हिन्दी", "count": 2}],
        )

    def test_minimum_word_length_counts_letters_not_combining_marks(self):
        self.book.write_text("हिन्दी cat", encoding="utf-8")
        self.assertEqual(
            analyze_book(self.book, min_word_length=4).top_words,
            [],
        )

    def test_canonically_equivalent_letters_share_counts(self):
        self.book.write_text("é e\u0301", encoding="utf-8")
        letters = {row["character"]: row["count"] for row in analyze_book(self.book).letters}
        self.assertEqual(letters, {"é": 2})

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
        self.assertEqual(book.longest_line_length, 3)

    def test_analysis_reports_the_source_file_size(self):
        self.book.write_text("é", encoding="utf-8")
        book = analyze_book(self.book)
        self.assertEqual(book.character_count, 1)
        self.assertEqual(book.source_bytes, 2)

    def test_analysis_fingerprints_exact_source_bytes(self):
        source = "café\n".encode("utf-8")
        self.book.write_bytes(source)
        book = analyze_book(self.book)
        self.assertEqual(book.source_sha256, hashlib.sha256(source).hexdigest())
        rendered = json.loads(render_report([book], "json"))[0]
        self.assertEqual(rendered["source_sha256"], book.source_sha256)
        csv_row = next(csv.DictReader(io.StringIO(render_report([book], "csv"))))
        self.assertEqual(csv_row["source_sha256"], book.source_sha256)

    def test_analysis_reads_source_once_for_counts_and_fingerprint(self):
        source = "one two café\n".encode("utf-8")
        self.book.write_bytes(source)
        with patch("stats.open", wraps=open) as opened:
            book = analyze_book(self.book)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(book.word_count, 3)
        self.assertEqual(book.source_bytes, len(source))
        self.assertEqual(book.source_sha256, hashlib.sha256(source).hexdigest())

    def test_compressed_source_metrics_use_consumed_archive_bytes(self):
        compressed = self.root / "measured.txt.gz"
        with gzip.open(compressed, "wt", encoding="utf-8") as book:
            book.write("one two three")
        source = compressed.read_bytes()
        result = analyze_book(compressed)
        self.assertEqual(result.word_count, 3)
        self.assertEqual(result.source_bytes, len(source))
        self.assertEqual(result.source_sha256, hashlib.sha256(source).hexdigest())

    def test_analysis_counts_unicode_whitespace(self):
        self.book.write_text("one\ttwo\u2003three\n", encoding="utf-8")
        book = analyze_book(self.book)
        self.assertEqual(book.whitespace_count, 3)
        self.assertEqual(json.loads(render_report([book], "json"))[0]["whitespace_count"], 3)

    def test_json_and_csv_multiple_books(self):
        books = [analyze_book(self.book), analyze_book(self.book)]
        parsed = json.loads(render_report(books, "json"))
        self.assertEqual(len(parsed), 2)
        rows = list(csv.DictReader(io.StringIO(render_report(books, "csv"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["word_count"], "7")
        self.assertEqual(rows[0]["hapax_words"], "0")
        self.assertEqual(rows[0]["average_word_length"], "4.67")
        self.assertIn("Comparison", render_report(books, "text"))

    def test_tsv_report_is_tab_delimited(self):
        rows = list(csv.reader(io.StringIO(render_report([analyze_book(self.book)], "tsv")), delimiter="\t"))
        self.assertEqual(rows[0][:2], ["path", "word_count"])
        self.assertEqual(rows[1][1], "7")

    def test_markdown_report_is_a_compact_summary_table(self):
        report = render_report([analyze_book(self.book)], "markdown")
        self.assertIn("| Book | Words | Unique | Sentences | Reading time |", report)
        self.assertIn("| sample.txt | 7 | 3 | 3 | 1 min |", report)

    def test_multi_book_reports_can_sort_by_word_count(self):
        shorter = self.root / "shorter.txt"
        shorter.write_text("one")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([str(shorter), str(self.book), "--sort-books", "words", "--format", "json"]), 0)
        paths = [row["path"] for row in json.loads(output.getvalue())]
        self.assertEqual(paths, [str(self.book), str(shorter)])

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

    def test_output_replacement_preserves_file_permissions(self):
        output = self.root / "report.txt"
        output.write_text("existing")
        output.chmod(0o640)
        write_output(output, "replacement")
        self.assertEqual(output.stat().st_mode & 0o777, 0o640)

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
