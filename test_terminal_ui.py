"""Regression checks for accessible output and interactive routing."""

import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from main import main
from stats import analyze_book
from terminal_ui import safe_text


class TerminalTests(unittest.TestCase):
    def test_control_characters_are_visible_not_executed(self):
        self.assertEqual(safe_text("book\x1b[31m\n.txt"), "book?[31m?.txt")

    def test_plain_default_has_no_terminal_escape_sequences(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--plain"]), 0)
        self.assertNotIn("\x1b", output.getvalue())
        self.assertIn("Found 75767 total words", output.getvalue())

    def test_interactive_rejects_pipes(self):
        with (
            patch("sys.stdin.isatty", return_value=False),
            contextlib.redirect_stderr(io.StringIO()) as error,
            self.assertRaises(SystemExit) as result,
        ):
            main(["--interactive"])
        self.assertEqual(result.exception.code, 2)
        self.assertIn("--plain", error.getvalue())

    def test_interactive_exports_are_not_silently_ignored(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--interactive", "--format", "json"])

    def test_exclusion_file_is_loaded_before_opening_dashboard(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            exclusions = Path(directory) / "excluded words.txt"
            exclusions.write_text("the\ncafé\n", encoding="utf-8")
            with (
                patch("sys.stdin.isatty", return_value=True),
                patch("sys.stdout.isatty", return_value=True),
                patch.dict(os.environ, {"TERM": "xterm-256color"}),
                patch("terminal_ui.run_interactive", return_value=0) as run,
            ):
                self.assertEqual(main(["--exclude-file", str(exclusions)]), 0)
            self.assertEqual(run.call_args.args[1].exclude, ["the", "café"])

    def test_terminal_default_routes_to_library(self):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch.dict(os.environ, {"TERM": "xterm-256color"}),
            patch("terminal_ui.run_interactive", return_value=0) as run,
        ):
            self.assertEqual(main([]), 0)
        self.assertEqual(len(run.call_args.args[0]), 3)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace

        self.args = SimpleNamespace(
            no_color=True,
            theme="library",
            spacing="compact",
            reading_wpm=250,
            top=10,
            min_word_length=1,
            encoding="utf-8",
            exclude=[],
        )
        self.path = Path(__file__).parent / "books/frankenstein.txt"

    def app(self):
        import curses
        from unittest.mock import Mock

        from terminal_ui import TerminalApp

        screen = Mock()
        screen.getmaxyx.return_value = (32, 100)
        with patch("curses.curs_set"), patch("curses.has_colors", return_value=False):
            return TerminalApp(screen, curses, [self.path], self.args)

    @staticmethod
    def flatten(canvas):
        return "\n".join("".join(text for text, _ in row) for row in canvas.rows)

    def test_dashboard_has_metrics_charts_and_all_detail_tabs(self):
        from terminal_ui import dashboard

        books = [analyze_book(self.path)]
        overview = self.flatten(dashboard(books, 0, 0, 100, self.args))
        for value in (
            "75,767",
            "7,038",
            "304 min",
            "Most frequent words",
            "Most frequent letters",
            "Vocabulary diversity",
            "Source:",
        ):
            self.assertIn(value, overview)
        for tab, title in (
            (1, "Vocabulary ranking"),
            (2, "Letter distribution"),
            (3, "Collection summary"),
        ):
            self.assertIn(title, self.flatten(dashboard(books, 0, tab, 100, self.args)))

    def test_narrow_dashboard_cells_stay_inside_panels(self):
        from terminal_ui import cell_width, dashboard

        books = [analyze_book(self.path)]
        for tab in range(4):
            canvas = dashboard(books, 0, tab, 30, self.args)
            self.assertTrue(
                all(
                    sum(cell_width(text) for text, _ in row) <= 30
                    for row in canvas.rows
                )
            )
            self.assertIn("4 Compare", self.flatten(canvas))

    def test_home_library_and_back_preserve_selection(self):
        app = self.app()
        self.assertEqual(app.view, "home")
        app.handle(ord("l"))
        app.content(90)
        app.handle(ord(" "))
        app.handle(ord("b"))
        self.assertEqual(app.view, "home")
        app.handle(ord("l"))
        self.assertEqual(app.selected, {self.path})

    def test_analysis_success_and_failure_recover(self):
        app = self.app()
        app.analyze([self.path], "library")
        app.future.result(timeout=4)
        app.poll()
        self.assertEqual(app.view, "dashboard")
        app.analyze([self.path / "missing"], "library")
        with self.assertRaises(OSError):
            app.future.result(timeout=4)
        app.poll()
        self.assertEqual(app.view, "library")
        self.assertIn("Cannot read book", app.message)

    def test_native_picker_success_and_cancellation(self):
        from unittest.mock import Mock

        app = self.app()
        app.dialog = Mock()
        app.dialog.poll.return_value = 0
        app.dialog.returncode = 0
        app.dialog.communicate.return_value = (str(self.path) + "\n", None)
        with patch.object(app, "analyze") as analyze:
            app.poll()
        analyze.assert_called_once_with([self.path], "home")
        app.dialog = Mock()
        app.dialog.poll.return_value = 1
        app.dialog.returncode = 1
        app.dialog.communicate.return_value = ("", None)
        app.poll()
        self.assertEqual(app.view, "home")
        self.assertIn("canceled", app.message)

    def test_picker_falls_back_to_terminal_and_filters(self):
        app = self.app()
        with patch("terminal_ui.picker_command", return_value=None):
            app.pick()
        self.assertEqual(app.view, "browser")
        app.query = "a-name-that-does-not-exist-00000"
        self.assertIn("No matching books", self.flatten(app.content(70)))
        app.handle(ord("/"))
        app.handle(27)
        self.assertEqual(app.query, "")

    def test_picker_command_handles_no_desktop(self):
        from terminal_ui import picker_command

        with patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}):
            self.assertIsNone(picker_command())

    def test_wide_glyphs_fit_by_terminal_cells(self):
        from terminal_ui import cell_width, fit

        self.assertEqual(cell_width("界a"), 3)
        self.assertEqual(fit("界abc", 3), "界a")

    def test_empty_dashboard_has_honest_zero_values(self):
        from dataclasses import replace

        from terminal_ui import dashboard

        empty = replace(
            analyze_book(self.path),
            word_count=0,
            character_count=0,
            line_count=0,
            alphabetic_count=0,
            unique_words=0,
            vocabulary_percent=0,
            reading_minutes=0,
            top_words=[],
            letters=[],
        )
        for tab in range(4):
            rendered = self.flatten(dashboard([empty], 0, tab, 80, self.args))
            self.assertNotIn("nan", rendered.lower())
            self.assertNotIn("inf", rendered.lower())
        self.assertIn("0 min", self.flatten(dashboard([empty], 0, 0, 80, self.args)))

    def test_chart_retains_long_unicode_word_labels(self):
        from terminal_ui import Canvas, chart_rows

        word = "界" * 25
        canvas = Canvas(30)
        canvas.panel("Words", chart_rows([{"word": word, "count": 2}], "word", 26))
        # The full label wraps across rows before its abbreviated chart label.
        content = "".join(
            text.strip(" │") for row in canvas.rows[1:-1] for text, _ in row
        )
        self.assertIn(word, content)
