https://github.com/user-attachments/assets/64a79a1c-59c0-40bb-ab9d-f0a238592e4e

# timetui

A pretty, billing-focused TUI for [Time Warrior](https://timewarrior.net),
built with [Textual](https://textual.textualize.io).

I know if it was made in rust it would make me more leet. But oh well.

- Browse every interval in a fast table (ID · Start · Duration · Tags · Annotation);
  columns are toggleable with `C` (the ID column is hidden by default)
- **Real-time filtering** that matches how you think — space-separated terms are
  **AND**ed; a term that's a known tag filters by tag, anything else fuzzy-searches
  the annotation/date/time (so `LA new` = tagged **both** LA and new, while
  `mailerlite` searches annotations)
- **Live billing totals** of the filtered set — `Σ Hh Mm` **and decimal hours** — plus a
  **totals-by-tag-set** breakdown in the sidebar
- Dark, neon "cyberpunk" theme by default — switch to any built-in Textual theme
  (monokai, dracula, gruvbox, nord, …) via the command palette (`Ctrl+P` →
  "Change theme") and the table/sidebar colors follow it
- Edit straight from the TUI: annotate, tag/untag, modify start/end, delete,
  start/stop tracking, add historical intervals
- **Vim-style keys**, with `u` wired to `timew undo`

## Requirements

- [`timew`](https://timewarrior.net) (Time Warrior) on your `PATH`
- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/)

## Run

```sh
uv run timetui
```

Or install it as an isolated CLI on your `PATH`:

```sh
uv tool install .
timetui
```

## Time Warrior database location

By default timetui uses your normal Time Warrior database (`~/.timewarrior` or
`~/.local/share/timewarrior`). To point it at a different one — e.g. a separate
client/project database — specify the directory in any of these ways (highest
precedence first):

1. **CLI flag:** `timetui --timew-dir /path/to/timewarrior`
2. **Config file:** a `[timew]` table in `~/.config/timetui/config.toml`:

   ```toml
   [timew]
   db_path = "~/clients/acme/timewarrior"
   ```

3. **Environment:** a `TIMEWARRIORDB` already exported in your shell (Time
   Warrior's own variable) is honored when neither of the above is set.

timetui sets `TIMEWARRIORDB` for the `timew` subprocess from whichever it finds.
If the directory doesn't exist, timetui creates it on startup so Time Warrior
initializes a fresh database there (without its interactive "create new
database?" prompt, which would otherwise hang the TUI).

## Keybindings

| Key | Action |
| --- | --- |
| `/` | reveal & focus the fuzzy filter (hidden until then) |
| `esc` | in the filter: clear it, then (empty) hide it & return to the table |
| `j` / `k` | move down / up |
| `gg` / `G` | jump to top / bottom |
| `ctrl+d` / `ctrl+u` | half-page down / up |
| `h` / `l` | scroll the annotation left / right |
| `space` | toggle selection of the current row |
| `esc` | leave search / clear the selection |
| `a` | edit annotation |
| `t` / `T` | add / remove tag(s) |
| `m` | modify start/end times |
| `o` | add a new interval (`timew track`) |
| `dd` | delete the selected interval (confirm) |
| `s` / `S` | start / stop tracking |
| `c` | continue (resume the highlighted interval now) |
| `u` | undo the last Time Warrior change |
| `R` | generate an HTML/PDF report of the selection (or the filtered view) |
| `w` | wrap annotations (multi-line rows) |
| `f` | toggle the sidebar (full-width table) |
| `C` | show / hide table columns (ID hidden by default) |
| `r` | reload from Time Warrior |
| `?` | help |
| `q` | quit |

## Reading long annotations

The first four columns stay frozen while the annotation scrolls — use `h` / `l`
to move it left / right. The full annotation is also always shown, word-wrapped,
in the sidebar **Detail** pane for the selected row. Press `w` to wrap
annotations into multi-line rows (up to 4 lines) when scanning, and `f` to hide
the sidebar so the table takes the full width.

## Selecting multiple entries

Press `space` to toggle selection of the highlighted row (selected rows are
highlighted, like the cursor row, and the status bar shows the count). With a
selection active, `t` (tag), `T` (untag) and `dd` (delete) act on **all** selected
entries at once — issued as a single `timew` command — and the sidebar totals and
status `Σ` cover only the selected entries. Press `esc` to clear the selection.
A selection is also what `R` turns into a report (see below).

## Reports

Press `R` to generate a styled time report. A dialog asks for:

- **Style** — `cyberpunk` (dark, neon) or `printer` (clean, light) (`j`/`k` or arrows)
- **Format** — `html`, `pdf`, or `text` (a plain-ASCII, `timew summary`-style
  invoice previewed in-app and saved as `.txt`)
- **Hourly rate** — optional; when set, an **Amount Due** row (decimal hours ×
  rate) is added to the report. Leave blank for an hours-only report.
- **Output file** — defaults to `~/timetui-report.html`
- **Open when done** — launch the file in your default viewer afterwards

The report covers the **multi-selection** if one is active, otherwise the whole
**filtered view** — so you can narrow with a tag (e.g. `LA paid`), press `R`, and
get an invoice-style table (Date · Time · Description · Duration) with a total.

PDF output is rendered with headless Chromium via
[Playwright](https://playwright.dev/python/) (a Python dependency). The slim
Chromium headless-shell build is downloaded automatically the first time you
export a PDF (~90 MB, one-time); you can pre-fetch it with `uv run playwright
install chromium-headless-shell`, or set `TIMETUI_NO_BROWSER_DOWNLOAD=1` to
require that manual install instead of auto-downloading. HTML needs no extra tools.

## Branding / configuration

The report header, logo, tagline, currency, and payment (BTC) details are
configurable, so you can put your own brand on exported reports. With **no**
config you get neutral defaults: a generic `YourCompany` header, the bundled
open-source placeholder SVG mark, a placeholder tagline, and a placeholder BTC
address (so the layout reads as a complete invoice out of the box — see below
to opt any of these out).

Create `~/.config/timetui/config.toml` (or point `$TIMETUI_CONFIG` at any file;
`$XDG_CONFIG_HOME` is honored). Copy [`config.example.toml`](config.example.toml)
as a starting point:

```toml
[brand]
company = "Acme Consulting"
tagline = "We ship results."     # omit -> bundled placeholder; "" -> no side tagline
currency = "USD"
btc_address = "bc1q..."          # omit -> bundled placeholder; "" -> no payment block
logo_svg_path = "logo.svg"       # relative to this file; omit -> bundled placeholder
```

Every key is optional. `logo_svg_path` points to an SVG file — **relative paths
resolve next to the config file**, so dropping `config.toml` + your `logo.svg`
into `~/.config/timetui/` works out of the box; you can instead inline the markup
with `logo_svg = """<svg ...>"""`. Omit the key entirely to ship with the bundled
[placeholder logo](src/timetui/logo.svg); set `logo_svg = ""` to render the
company name as styled text instead.

Provide a single **white** logo and the report recolors it per theme. Tag your
shapes with the semantic CSS classes `primary` / `secondary` / `accent` (or the
Illustrator-export aliases `cls-1` / `cls-2` / `cls-3`, which map to the same
roles); each theme retints those roles to its palette — cyberpunk to white /
cyan / neon, printer to blue (`#005a9e`) / navy / light-blue — so one master
reads on both the dark and light sheets. Untagged shapes keep their own color,
and overlapping tagged shapes are best avoided so nothing vanishes once they
share a color (see the bundled [`src/timetui/logo.svg`](src/timetui/logo.svg)
for a working example).

Set `logo_recolor = false` to skip recoloring and show the logo with its **own**
colors on every theme — handy when your mark is already colored to taste and
reads on both backgrounds.

## Billing

Type a tag such as `paid` (or several, like `LA new`, which requires both) to
instantly narrow the view; the status bar shows the count and the total time of
that slice in both `Hh Mm` and decimal hours. The sidebar groups the total by the
**exact tag-set** of the matching rows (e.g. `LA + new`), so each interval is
counted once and the groups sum to `Σ`. Non-tag terms like `mailerlite` or a date
fragment fuzzy-search the annotation/date/time instead.

## Data safety

- Every change is issued through `timew`; destructive actions ask first.
- `u` runs `timew undo` (Time Warrior keeps its own undo log).
- Back up the database any time (it is tiny):

  ```sh
  cp -a ~/.local/share/timewarrior \
        ~/timewarrior-backups/timewarrior-$(date +%Y%m%d-%H%M%S)
  ```

## Development

```sh
uv run pytest                  # data-layer + UI snapshot tests
uv run pytest --snapshot-update   # refresh UI snapshots after intentional changes
uv run textual run --dev timetui.app:TimewApp   # Textual devtools + hot reload
```

The data layer (`timetui/timew.py`, `timetui/models.py`) is pure and fully
unit-tested; the `args_*` builders produce the exact `timew` argv and are tested
without ever executing `timew`.
