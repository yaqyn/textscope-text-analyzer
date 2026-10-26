"""Cover-inspired terminal library, file selection, and analysis dashboards."""

import os
import shutil
import subprocess
import sys
import textwrap
import threading
import unicodedata
from concurrent.futures import Future
from pathlib import Path

from stats import analyze_book

THEMES = ("library", "paper", "contrast", "terminal")
# The single runtime palette adapter; see DESIGN.md for the cover colors.
PALETTES = {
    "library": (223, 234, 222, 52),
    "paper": (234, 230, 52, 223),
    "contrast": (231, 16, 226, 20),
    "terminal": (-1, -1, -1, -1),
}


def safe_text(value):
    """Never execute control characters from filenames or book contents."""
    return "".join(
        c if not unicodedata.category(c).startswith("C") else "?" for c in str(value)
    )


def cell_width(text):
    return sum(
        0
        if unicodedata.combining(c)
        else 2
        if unicodedata.east_asian_width(c) in "WF"
        else 1
        for c in text
    )


def fit(text, width):
    result, used = "", 0
    for char in safe_text(text):
        size = cell_width(char)
        if used + size > width:
            break
        result += char
        used += size
    return result + " " * max(0, width - used)


class Canvas:
    """Virtual rows shared by all screens; viewport scrolling never loses data."""

    def __init__(self, width):
        self.width = width
        self.rows = []

    def line(self, text="", role="normal", center=False):
        text = safe_text(text)
        if center:
            text = " " * max(0, (self.width - cell_width(text)) // 2) + text
        self.rows.append([(text, role)])

    def wrapped(self, text, role="normal"):
        # Account for wide glyphs as well as long words and paths.
        for paragraph in str(text).splitlines() or [""]:
            remaining = safe_text(paragraph)
            while cell_width(remaining) > self.width:
                piece = fit(remaining, self.width).rstrip()
                self.line(piece, role)
                remaining = remaining[len(piece) :]
            self.line(remaining, role)

    def panel(self, title, body):
        inner = self.width - 4
        self.line(
            "┌─ "
            + title
            + " "
            + "─" * max(0, self.width - cell_width(title) - 5)
            + "┐",
            "accent",
        )
        for row in body:
            if isinstance(row, tuple):
                value, role = row
            else:
                value, role = row, "normal"
            chunks = []
            remaining = safe_text(value)
            while cell_width(remaining) > inner:
                piece = fit(remaining, inner).rstrip()
                chunks.append(piece)
                remaining = remaining[len(piece) :]
            chunks.append(remaining)
            for chunk in chunks:
                self.rows.append(
                    [("│ ", "accent"), (fit(chunk, inner), role), (" │", "accent")]
                )
        self.line("└" + "─" * (self.width - 2) + "┘", "accent")

    def columns(self, left, right):
        for index in range(max(len(left.rows), len(right.rows))):
            row = []
            for canvas in (left, right):
                segments = canvas.rows[index] if index < len(canvas.rows) else []
                used = sum(cell_width(text) for text, _ in segments)
                row.extend(segments)
                row.append((" " * max(0, canvas.width - used), "normal"))
                if canvas is left:
                    row.append(("  ", "normal"))
            self.rows.append(row)


def chart_rows(entries, label_key, width, total=None):
    if not entries:
        return ["No words match the ranking filters."]
    maximum = max(row["count"] for row in entries) or 1
    label_width = min(16, max(5, width // 4))
    suffix_width = 8 if total else 0
    bar_width = max(2, width - label_width - 12 - suffix_width)
    rows = []
    for entry in entries:
        label, count = safe_text(entry[label_key]), entry["count"]
        if cell_width(label) > label_width:
            rows.append(label)
        bar = "█" * max(1, round(bar_width * count / maximum))
        suffix = f" {100 * count / total:.1f}%" if total else ""
        rows.append((f"{fit(label, label_width)} {count:>9,}  {bar}{suffix}", "accent"))
    return rows


def dashboard(books, current, tab, width, args):
    """Build the complete dashboard, then let the viewport own scrolling."""
    canvas = Canvas(width)
    book = books[current]
    canvas.line(Path(book.path).name, "heading", center=True)
    canvas.line(
        f"Book {current + 1} of {len(books)}  |  {args.reading_wpm:,} words/min",
        center=True,
    )
    canvas.line()
    labels = ("Overview", "Words", "Letters", "Compare")
    tabs = [
        f"[{i + 1} {label}]" if i == tab else f" {i + 1} {label} "
        for i, label in enumerate(labels)
    ]
    for start in range(0, 4, 2 if width < 64 else 4):
        canvas.line(
            "  ".join(tabs[start : start + (2 if width < 64 else 4)]),
            "accent",
            center=True,
        )
    canvas.line()
    if tab == 0:
        metrics = [
            ("Words", f"{book.word_count:,}"),
            ("Unique vocabulary", f"{book.unique_words:,}"),
            ("Reading time", f"{book.reading_minutes:,} min"),
            ("Characters", f"{book.character_count:,}"),
            ("Lines", f"{book.line_count:,}"),
            ("Alphabetic", f"{book.alphabetic_count:,}"),
        ]
        columns = 3 if width >= 78 else 2 if width >= 48 else 1
        for start in range(0, len(metrics), columns):
            cell = (width - (columns - 1) * 2) // columns
            panels = []
            for title, value in metrics[start : start + columns]:
                panel = Canvas(cell)
                panel.panel(title, [(value, "heading")])
                panels.append(panel)
            for row_index in range(3):
                canvas.rows.append(
                    [
                        segment
                        for i, panel in enumerate(panels)
                        for segment in (
                            ([("  ", "normal")] if i else []) + panel.rows[row_index]
                        )
                    ]
                )
        canvas.line()
        left = Canvas((width - 2) // 2 if width >= 78 else width)
        right = Canvas(width - left.width - 2 if width >= 78 else width)
        left.panel(
            "Most frequent words",
            chart_rows(book.top_words[:5], "word", left.width - 4),
        )
        right.panel(
            "Most frequent letters",
            chart_rows(
                book.letters[:5], "character", right.width - 4, book.alphabetic_count
            ),
        )
        if width >= 78:
            canvas.columns(left, right)
        else:
            canvas.rows.extend(left.rows + right.rows)
        canvas.line()
        diversity = book.vocabulary_percent
        alpha = (
            100 * book.alphabetic_count / book.character_count
            if book.character_count
            else 0
        )
        canvas.panel(
            "Inside the text",
            [
                (f"Vocabulary diversity   {diversity:.2f}%", "heading"),
                "█" * round((width - 6) * diversity / 100),
                "Unique vocabulary / normalized letter tokens",
                "",
                f"Alphabetic characters  {alpha:.2f}% of all characters",
                f"Words per line         {book.word_count / book.line_count if book.line_count else 0:.1f}",
            ],
        )
        canvas.line()
        source = Path(book.path)
        try:
            source = source.relative_to(Path(__file__).resolve().parent)
        except ValueError:
            pass
        canvas.wrapped(f"Source: {source}")
        canvas.wrapped(
            "Open Words or Letters for the full rankings. Chart lengths are relative to the most frequent entry; letter percentages use all alphabetic characters."
        )
    elif tab in (1, 2):
        letters = tab == 2
        entries = book.letters if letters else book.top_words
        canvas.panel(
            "Letter distribution" if letters else "Vocabulary ranking",
            chart_rows(
                entries,
                "character" if letters else "word",
                width - 4,
                book.alphabetic_count if letters else None,
            ),
        )
        canvas.line()
        canvas.wrapped(
            "Letters are lowercase alphabetic characters, ordered by frequency."
            if letters
            else f"Top {args.top} normalized words; minimum length {args.min_word_length}. Exclusions: {', '.join(args.exclude) or 'none'}. Filters change rankings only."
        )
    else:
        canvas.panel(
            "Collection summary",
            [
                (f"{len(books)} books analyzed", "heading"),
                f"Total words: {sum(b.word_count for b in books):,}",
                f"Total characters: {sum(b.character_count for b in books):,}",
                f"Reading time: {sum(b.reading_minutes for b in books):,} min (sum of book estimates)",
            ],
        )
        maximum = max((b.word_count for b in books), default=0) or 1
        for item in books:
            canvas.line()
            canvas.wrapped(Path(item.path).name, "heading")
            canvas.line("█" * round((width - 2) * item.word_count / maximum), "accent")
            canvas.wrapped(
                f"{item.word_count:,} words | {item.unique_words:,} unique | {item.vocabulary_percent:.2f}% diversity | {item.reading_minutes:,} min"
            )
        canvas.line()
        canvas.wrapped(
            "Vocabulary is measured per book, not combined across the collection. Reading time is an estimate, not measured reading speed."
        )
    return canvas


def picker_command():
    """Choose a native file dialog without a shell or required GUI dependency."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    if shutil.which("zenity"):
        return [
            "zenity",
            "--file-selection",
            "--title=TextScope — Pick your book",
            "--file-filter=Plain text | *.txt *.text",
            "--file-filter=All files | *",
            f"--filename={Path.home()}/",
        ]
    if shutil.which("kdialog"):
        return [
            "kdialog",
            "--title",
            "TextScope — Pick your book",
            "--getopenfilename",
            str(Path.home()),
            "*.txt *.text|Plain text\n*|All files",
        ]
    # A child process keeps Tk's GUI loop separate from curses and cancellable.
    return [
        sys.executable,
        "-c",
        "import tkinter as t; from tkinter.filedialog import askopenfilename; r=t.Tk(); r.withdraw(); p=askopenfilename(title='TextScope — Pick your book', filetypes=[('Plain text', '*.txt'), ('All files', '*')]); print(p); r.destroy()",
    ]


def run_interactive(paths, args):
    import curses

    try:
        return curses.wrapper(
            lambda screen: TerminalApp(screen, curses, paths, args).run()
        )
    except KeyboardInterrupt:
        return 130
    except curses.error as error:
        raise RuntimeError(
            f"terminal initialization or drawing failed: {error}"
        ) from error


class TerminalApp:
    def __init__(self, screen, curses, paths, args):
        self.screen, self.curses, self.args = screen, curses, args
        self.paths = list(dict.fromkeys(Path(p) for p in paths))
        self.theme = (
            "terminal" if args.no_color or "NO_COLOR" in os.environ else args.theme
        )
        self.spacing = args.spacing
        self.view, self.origin = "home", "library"
        self.index = self.menu = self.offset = self.current = self.tab = 0
        self.selected, self.books = set(), []
        self.query, self.message = "", "Choose a book. Discover what is inside."
        self.searching = False
        self.directory = Path.home()
        self.browser_entries = []
        self.dialog = self.future = None
        self.screen.keypad(True)
        self.screen.timeout(100)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.set_theme()

    def set_theme(self):
        c = self.curses
        self.styles = {
            "normal": 0,
            "accent": c.A_BOLD,
            "heading": c.A_BOLD,
            "focus": c.A_REVERSE | c.A_BOLD,
        }
        if self.theme != "terminal" and c.has_colors():
            c.start_color()
            fg, bg, gold, red = (
                PALETTES[self.theme]
                if c.COLORS >= 256
                else ((0, 7, 0, 7) if self.theme == "paper" else (7, 0, 3, 4))
            )
            for i, foreground, background in ((1, fg, bg), (2, gold, bg), (3, fg, red)):
                c.init_pair(i, foreground, background)
            self.styles.update(
                normal=c.color_pair(1),
                accent=c.color_pair(2) | c.A_BOLD,
                heading=c.color_pair(2) | c.A_BOLD,
                focus=c.color_pair(3) | c.A_REVERSE | c.A_BOLD,
            )
        self.screen.bkgd(" ", self.styles["normal"])

    def write(self, y, x, text, role="normal", limit=None):
        height, width = self.screen.getmaxyx()
        room = max(0, width - x - 1)
        if limit is not None:
            room = min(room, limit)
        if 0 <= y < height and x >= 0 and room:
            try:
                self.screen.addstr(y, x, fit(text, room), self.styles[role])
            except self.curses.error:
                pass  # A resize can invalidate a cell between measurement and draw.

    def centered(self, y, text, role="normal"):
        width = self.screen.getmaxyx()[1]
        self.write(
            y,
            max(1, (width - cell_width(safe_text(text))) // 2),
            text,
            role,
            cell_width(safe_text(text)),
        )

    def browser(self):
        self.view, self.index, self.query = "browser", 0, ""
        try:
            children = [
                p for p in self.directory.iterdir() if not p.name.startswith(".")
            ]
            self.browser_entries = [self.directory.parent] + sorted(
                children, key=lambda p: (not p.is_dir(), p.name.casefold())
            )
            self.message = "Enter opens a folder or analyzes a file. B returns home."
        except OSError as error:
            self.browser_entries = [self.directory.parent]
            self.message = f"Cannot open folder: {error}. Use .. to go back."

    def pick(self):
        command = picker_command()
        if command is None:
            self.browser()
            return
        try:
            self.dialog = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
            self.view = "picker"
            self.message = "Choose your file in the file browser. Esc cancels."
        except OSError:
            self.browser()
            self.message = "Desktop picker unavailable. Select a file here instead."

    def analyze(self, chosen, origin):
        self.origin, self.view = origin, "loading"
        self.message = f"Analyzing {len(chosen)} book(s). Esc returns without waiting."
        args = self.args

        def work():
            return [
                analyze_book(
                    p,
                    encoding=args.encoding,
                    top=args.top,
                    min_word_length=args.min_word_length,
                    max_word_length=args.max_word_length,
                    word_sort=args.word_sort,
                    word_order=getattr(args, "word_order", "ascending"),
                    exclude=args.exclude,
                    reading_wpm=args.reading_wpm,
                )
                for p in chosen
            ]

        future = Future()
        self.future = future

        def complete():
            try:
                future.set_result(work())
            except Exception as error:  # noqa: BLE001 - transfer worker failures to the UI thread
                future.set_exception(error)

        threading.Thread(target=complete, daemon=True).start()

    def poll(self):
        if self.dialog and self.dialog.poll() is not None:
            output, _ = self.dialog.communicate()
            code, self.dialog = self.dialog.returncode, None
            if code == 0 and output.rstrip("\n"):
                path = Path(output.rstrip("\n"))
                if path not in self.paths:
                    self.paths.append(path)
                self.analyze([path], "home")
            elif code in (0, 1):
                self.view, self.message = (
                    "home",
                    "Selection canceled. Pick a book whenever you are ready.",
                )
            else:
                self.browser()
                self.message = "Desktop picker unavailable. Select a file here instead."
        if self.view == "loading" and self.future and self.future.done():
            try:
                self.books = self.future.result()
                self.view, self.current, self.tab, self.offset = "dashboard", 0, 0, 0
                self.message = (
                    "Analysis complete. Explore the tabs for detailed rankings."
                )
            except (OSError, UnicodeError, LookupError) as error:
                self.view = self.origin
                self.message = (
                    f"Cannot read book: {error}. Check the file or use --encoding."
                )
            self.future = None

    def content(self, width):
        canvas = Canvas(width)
        if self.view == "home":
            short = self.screen.getmaxyx()[0] < 25
            if not short:
                canvas.line(
                    "Your next reading, seen differently.", "heading", center=True
                )
                for line in textwrap.wrap(
                    "Explore the words, letters, and patterns in your books.", width
                ):
                    canvas.line(line, center=True)
                canvas.line()
            items = [
                ("Pick your book", "Open a file browser and select a text file"),
                ("Library", f"Browse {len(self.paths)} available books"),
                ("Exit", "Return to your terminal"),
            ]
            for i, (title, description) in enumerate(items):
                canvas.line(
                    ("> " if i == self.menu else "  ") + title,
                    "focus" if i == self.menu else "heading",
                    center=True,
                )
                if not short:
                    canvas.line(description, center=True)
                canvas.line()
            return canvas
        if self.view in ("picker", "loading"):
            canvas.panel(
                "Pick your book" if self.view == "picker" else "Reading your book",
                [
                    (
                        "File browser is open"
                        if self.view == "picker"
                        else "Counting words and mapping vocabulary...",
                        "heading",
                    ),
                    "Your source files are read only.",
                    "",
                    "Esc  Cancel and return",
                ],
            )
            return canvas
        if self.view == "dashboard":
            return dashboard(self.books, self.current, self.tab, width, self.args)
        browser = self.view == "browser"
        items = self.browser_entries if browser else self.paths
        self.visible = [p for p in items if self.query.casefold() in str(p).casefold()]
        self.index = max(0, min(self.index, len(self.visible) - 1))
        canvas.line(
            "Pick your book / File browser" if browser else "Library",
            "heading",
            center=True,
        )
        if browser:
            canvas.wrapped(str(self.directory))
        else:
            canvas.line(
                f"{len(self.selected)} selected   /   {len(self.visible)} books shown",
                center=True,
            )
        canvas.line()
        self.list_start = len(canvas.rows)
        if not self.visible:
            canvas.panel(
                "No matching books",
                [
                    "Press / to edit your search.",
                    "Esc while editing clears the filter.",
                ],
            )
        for i, path in enumerate(self.visible):
            if browser:
                label = (
                    ".. / Parent folder"
                    if i == 0 and path == self.directory.parent
                    else ("[Folder] " if path.is_dir() else "[File] ") + path.name
                )
            else:
                label = f"[{'x' if path in self.selected else ' '}] {path.name}"
                if sum(p.name == path.name for p in self.paths) > 1:
                    label += f" ({path.parent})"
            canvas.line(
                ("> " if i == self.index else "  ") + label,
                "focus" if i == self.index else "normal",
            )
            if self.spacing == "roomy":
                canvas.line()
        if self.visible:
            canvas.line()
            canvas.wrapped("Focused file: " + str(self.visible[self.index]))
        return canvas

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 16 or width < 36:
            self.write(0, 1, "TextScope | Enlarge terminal", "heading")
            self.write(2, 1, "Minimum: 36 columns, 16 rows.")
            self.write(4, 1, "Q exits. Resize to continue.")
            self.screen.refresh()
            return
        self.centered(1, "T E X T S C O P E", "heading")
        self.centered(2, "Read / Count / Discover", "accent")
        self.centered(3, "─" * min(56, width - 6), "accent")
        content_width = min(66 if self.view == "home" else 110, width - 6)
        left = (width - content_width) // 2
        body_height = height - 11
        canvas = self.content(content_width)
        if self.view in ("library", "browser") and self.visible:
            row = self.list_start + self.index * (2 if self.spacing == "roomy" else 1)
            if row < self.offset:
                self.offset = row
            elif row >= self.offset + body_height:
                self.offset = row - body_height + 1
        if self.view == "home" and height < 25:
            self.offset = max(0, self.menu * 2 - body_height + 1)
        self.offset = max(0, min(self.offset, max(0, len(canvas.rows) - body_height)))
        top = 5
        if self.view in ("home", "picker", "loading"):
            top += max(0, (body_height - len(canvas.rows)) // 2)
        for row, segments in enumerate(
            canvas.rows[self.offset : self.offset + body_height]
        ):
            x = left
            for text, role in segments:
                self.write(top + row, x, text, role, max(0, left + content_width - x))
                x += cell_width(text)
        if len(canvas.rows) > body_height:
            self.centered(
                height - 6,
                f"{self.offset + 1}-{min(self.offset + body_height, len(canvas.rows))} / {len(canvas.rows)}  |  PgUp/PgDn",
                "accent",
            )
        status = (
            "Filter: " + self.query + "_  (Enter apply / Esc clear)"
            if self.searching
            else self.message
        )
        self.centered(height - 5, fit(status, width - 6).rstrip(), "accent")
        controls = {
            "home": (
                "↑ / ↓  Choose     Enter  Open     P  Pick     L  Library",
                "T  Theme     S  Spacing     Q  Exit",
            ),
            "library": (
                "↑ / ↓  Move   Space  Select   Enter  Analyze   /  Filter",
                "A  Select all   B  Home   T  Theme   S  Spacing   Q  Exit",
            ),
            "browser": (
                "↑ / ↓  Move   Enter  Open   /  Filter   B  Home",
                "T  Theme   S  Spacing   Q  Exit",
            ),
            "dashboard": (
                "1–4 / Tab  Views   ← / →  Books   ↑ / ↓  Scroll",
                "Home / End   PgUp / PgDn   B  Back   T  Theme   Q  Exit",
            ),
        }
        first, second = controls.get(self.view, ("Esc  Cancel", "Q  Exit"))
        if width < 64:
            first, second = {
                "home": ("↑↓ Choose  Enter Open", "P Pick  L Library  T Theme  Q Exit"),
                "library": (
                    "↑↓ Move  Space Select  Enter Read",
                    "/ Filter  A All  B Home  Q Exit",
                ),
                "browser": ("↑↓ Move  Enter Open", "/ Filter  B Home  Q Exit"),
                "dashboard": (
                    "1–4 Views  ←→ Books  ↑↓ Scroll",
                    "PgUp/PgDn Page  B Back  Q Exit",
                ),
            }.get(self.view, (first, second))
        self.centered(height - 3, first)
        self.centered(height - 2, second)
        self.screen.refresh()

    def cancel_dialog(self):
        if self.dialog:
            self.dialog.terminate()
            try:
                self.dialog.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                self.dialog.kill()
                self.dialog.communicate()
            self.dialog = None

    def handle(self, key):
        c = self.curses
        if self.searching:
            if key in (10, 13, c.KEY_ENTER):
                self.searching = False
            elif key == 27:
                self.query, self.searching = "", False
            elif key in (c.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
            elif 32 <= key <= 0x10FFFF and key < c.KEY_MIN:
                self.query += chr(key)
            self.index, self.offset = 0, 0
            return True
        if key in (ord("q"), ord("Q")):
            return False
        if self.view in ("picker", "loading"):
            if key == 27:
                self.cancel_dialog()
                self.view, self.message, self.offset = (
                    "home",
                    "Canceled. Choose another book when ready.",
                    0,
                )
            return True
        if key == ord("t") and not self.args.no_color and "NO_COLOR" not in os.environ:
            self.theme = THEMES[(THEMES.index(self.theme) + 1) % len(THEMES)]
            self.set_theme()
            self.message = f"Theme: {self.theme}"
        elif key == ord("s"):
            self.spacing = "roomy" if self.spacing == "compact" else "compact"
            self.message = f"Spacing: {self.spacing}"
        elif self.view == "home":
            if key in (c.KEY_DOWN, ord("j"), 9):
                self.menu = (self.menu + 1) % 3
            elif key in (c.KEY_UP, ord("k"), c.KEY_BTAB):
                self.menu = (self.menu - 1) % 3
            elif key in (ord("p"), ord("l"), 10, 13, c.KEY_ENTER):
                choice = 0 if key == ord("p") else 1 if key == ord("l") else self.menu
                if choice == 0:
                    self.pick()
                elif choice == 1:
                    self.view, self.offset = "library", 0
                    self.message = "Select books with Space, then Enter to analyze."
                else:
                    return False
            elif key == 27:
                return False
        elif self.view == "dashboard":
            if key in (ord("b"), 27, c.KEY_BACKSPACE):
                self.view, self.offset = self.origin, 0
                self.message = (
                    "Your selection is preserved. Choose a book or return home."
                )
            elif ord("1") <= key <= ord("4"):
                self.tab, self.offset = key - ord("1"), 0
            elif key in (9, c.KEY_BTAB):
                self.tab, self.offset = (self.tab + (1 if key == 9 else -1)) % 4, 0
            elif key in (c.KEY_LEFT, c.KEY_RIGHT):
                self.current = (self.current + (1 if key == c.KEY_RIGHT else -1)) % len(
                    self.books
                )
                self.offset = 0
            else:
                self.scroll(key)
        else:
            if key in (ord("b"), 27):
                self.view, self.offset = "home", 0
                self.message = "Choose a book. Discover what is inside."
            elif key in (c.KEY_DOWN, ord("j")):
                self.index = min(self.index + 1, len(self.visible) - 1)
            elif key in (c.KEY_UP, ord("k")):
                self.index = max(0, self.index - 1)
            elif key == c.KEY_HOME:
                self.index = 0
            elif key == c.KEY_END:
                self.index = max(0, len(self.visible) - 1)
            elif key in (c.KEY_NPAGE, c.KEY_PPAGE):
                self.index += (1 if key == c.KEY_NPAGE else -1) * max(
                    1, self.screen.getmaxyx()[0] - 14
                )
            elif key == ord("/"):
                self.searching = True
            elif key == ord("a") and self.view == "library":
                if self.visible and all(p in self.selected for p in self.visible):
                    self.selected.difference_update(self.visible)
                else:
                    self.selected.update(self.visible)
            elif key == ord(" ") and self.visible and self.view == "library":
                self.selected.symmetric_difference_update([self.visible[self.index]])
            elif key in (10, 13, c.KEY_ENTER) and (self.visible or self.selected):
                if self.view == "browser":
                    if not self.visible:
                        return True
                    path = self.visible[self.index]
                    if path.is_dir():
                        self.directory = path
                        self.browser()
                        self.offset = 0
                    else:
                        if path not in self.paths:
                            self.paths.append(path)
                        self.analyze([path], "browser")
                else:
                    chosen = (
                        [p for p in self.paths if p in self.selected]
                        if self.selected
                        else [self.visible[self.index]]
                    )
                    self.analyze(chosen, "library")
        return True

    def scroll(self, key):
        c = self.curses
        if key in (c.KEY_DOWN, ord("j")):
            self.offset += 1
        elif key in (c.KEY_UP, ord("k")):
            self.offset -= 1
        elif key in (c.KEY_NPAGE, ord(" ")):
            self.offset += max(1, self.screen.getmaxyx()[0] - 11)
        elif key == c.KEY_PPAGE:
            self.offset -= max(1, self.screen.getmaxyx()[0] - 11)
        elif key == c.KEY_HOME:
            self.offset = 0
        elif key == c.KEY_END:
            self.offset = 10**9

    def run(self):
        try:
            while True:
                self.poll()
                self.draw()
                try:
                    key = self.screen.get_wch()
                except self.curses.error:
                    continue  # Input timeout lets background analysis update the UI.
                # get_wch returns Unicode text or a distinct integer function key.
                if isinstance(key, str):
                    printable = key.isprintable()
                    key = ord(key)
                    if self.searching and printable:
                        self.query += chr(key)
                        self.index, self.offset = 0, 0
                        continue
                if self.screen.getmaxyx()[0] < 16 or self.screen.getmaxyx()[1] < 36:
                    if key in (ord("q"), ord("Q"), 27):
                        return 0
                    continue
                if not self.handle(key):
                    return 0
        finally:
            self.cancel_dialog()
