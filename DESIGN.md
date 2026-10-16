# TextScope terminal design

## Purpose and visual direction

TextScope is a local book-analysis tool. Its interface should feel like a reading
desk in the engraved printing workshop from the project cover: warm, structured,
and focused on the text. The main controls sit at the center of the screen;
analysis uses an actual dashboard with panels and charts. Avoid web-style chrome,
an oversized ASCII logo, animation, and decoration that crowds the analysis.

## Palette and runtime ownership

The cover supplies ink #191716, parchment #F6E2B3, gold #D9AD62, burgundy #581C24,
and warm paper #FFF4D6. `terminal_ui.PALETTES` is the single runtime adapter:
library uses terminal colors 223/234/222/52 for text/background/accent/selection.
Paper reverses the reading surface, contrast uses white/black/yellow, and terminal
uses the user's own foreground/background. `TerminalApp.set_theme` owns all
color pairs and emphasis roles. Eight-color terminals receive basic fallbacks.
`NO_COLOR` and `--no-color` take precedence over theme selection.

The user's terminal owns font family and size. Hierarchy uses bold headings,
spacing, quiet borders, and numeric values beside every chart. Compact/roomy
spacing affects book lists. No application action changes terminal-wide config.

## Canonical interface owners

- `TerminalApp` owns screen state, keyboard routing, focus, status and navigation.
- `Canvas` owns virtual rows, panel borders, wrapping and side-by-side panels.
- `dashboard` owns Overview, Words, Letters and Compare content.
- `chart_rows` owns frequency bars, labels, counts and optional percentages.
- `picker_command` owns optional native file-dialog selection. A subprocess keeps
  GUI work separate from curses; `TerminalApp.browser` provides the terminal
  fallback. OS-owned file-dialog styling is intentional.
- `stats.analyze_book` remains the sole source for counts and vocabulary. The
  dashboard derives ratios only from those verified counts. Collection vocabulary
  is not summed or presented as a combined unique count.

No locale service, web component library, API, authentication, destructive action,
or persistent preference store is involved. Text is English; analyzed text can
contain Unicode. No network service is used to open or analyze a book.

## Screen and interaction contract

```text
                     TextScope
               Read / Count / Discover

                 > Pick your book
                 Open a file browser

                     Library
                 Browse existing books

                       Exit

                Status and key controls
```

Home has a centered content region at most 66 cells wide. Library and dashboard
share a centered region at most 110 cells wide. Footer controls are centered.
Minimum terminal size is 36 columns by 16 rows. Small windows retain exit and
resize guidance. Narrow dashboards stack metric panels/charts and wrap tab labels.
The viewport owns scrolling; all source text and ranked values remain reachable.

Pick your book opens Zenity, KDialog, or Tk. Success immediately analyzes that
file and adds it to the session library. Cancel restores Home. Unavailable or
failed graphical pickers open a terminal browser; folders open with Enter and
files start analysis. Hidden entries are omitted there. No input file is modified.
Library retains selections and search when returning from a dashboard. Space
selects; Enter analyzes the selection, or the focused book when none is selected.
A toggles all filtered entries. Search is immediate, Enter commits, and Escape
clears an active search. B/Escape navigates back; Q exits globally.

Background analysis keeps resize, cancel and exit available. Its success opens
Overview, failures return to the source screen with an error, and canceled results
never replace a newer screen. Native picker processes are terminated on cancel
or exit. Terminal settings are restored through `curses.wrapper`.

The dashboard exposes 1–4/Tab for views, Left/Right for books, and arrows,
Page Up/Down, Home/End for scrolling. Overview has six summary panels, paired
frequency charts, diversity and composition insights. Compare reports per-book
vocabulary and the sum of individually rounded reading-time estimates honestly.

## Accessibility and verification

Focus includes a `>` marker and emphasis. Library selection includes `[x]`; color
alone conveys no required information. Charts include exact counts. Plain mode
provides linear output without redraws, colors or interactive prompts for screen
readers and pipelines. There is no motion or mouse requirement. Wide glyphs are
measured by terminal cells, though shaping and right-to-left behavior depend on
the terminal. This is not a claim of exhaustive assistive-technology compatibility.

Verification uses standard-library unit tests plus pseudo-terminal interaction
checks. Render captured terminal cells to inspect Home, dashboard and comparison
at a wide size, and file browsing/dashboard at a narrow size. Exercise native
picker launch/cancel, missing-file recovery, themes, resizing, selection, filtering,
all dashboard tabs, book switching, back and terminal restoration. Screenshots
are evidence from the running curses app, not separate mockup implementations.
