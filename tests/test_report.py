"""Tests for the report layer.

``render_report_html`` is pure (no IO / no shell-out) and is tested directly.
``write_report``'s PDF path renders via headless Chromium (Playwright), so the
guard test forces its import to fail — tests never launch a real browser.
"""

from __future__ import annotations

import re
import sys

import pytest

from timetui import report
from timetui.models import Interval

# Two completed intervals (1h30m + 1h = 2h30m) and one active interval.
EARLIER = Interval.from_export(
    {"id": 3, "start": "20260309T210000Z", "end": "20260309T223000Z",
     "tags": ["LA", "paid"], "annotation": "crypto work"}
)
LATER = Interval.from_export(
    {"id": 2, "start": "20260311T120000Z", "end": "20260311T130000Z",
     "tags": ["LA"], "annotation": "ship <newsletter> & more"}
)
ACTIVE = Interval.from_export(
    {"id": 1, "start": "20260312T060000Z", "tags": ["b"], "annotation": "ongoing"}
)

GEN = "January 01, 2026"  # pinned so the header is deterministic

# A fully-populated brand for asserting that configured branding shows up. Tests
# that don't pass a brand exercise the neutral DEFAULT_BRAND ("YourCompany", no
# payment block, text logo) instead.
BRAND = report.BrandConfig(
    company="Acme Corp",
    tagline="We don't provide a service. We provide a result.",
    currency="USD",
    btc_address="bc1qexampleaddress00000000000000000000000",
    logo_svg='<svg id="brand-logo" class="cls-1"></svg>',
)


def test_render_is_pure_html_with_rows_and_total():
    html = report.render_report_html([EARLIER, LATER], generated=GEN)
    assert html.startswith("<!DOCTYPE html>")
    assert "crypto work" in html
    assert "<h1>Invoice</h1>" in html
    assert f"Invoice Date: {GEN}" in html
    # 1h30m + 1h = 2h30m of completed time
    assert "2h 30m" in html


def test_render_orders_oldest_first():
    # pass newest-first; output must list the earlier interval before the later one
    html = report.render_report_html([LATER, EARLIER], generated=GEN)
    assert html.index("crypto work") < html.index("newsletter")


def test_render_escapes_annotation():
    html = report.render_report_html([LATER], generated=GEN)
    assert "&lt;newsletter&gt; &amp; more" in html
    assert "<newsletter>" not in html


def test_render_open_interval_excluded_from_total():
    html = report.render_report_html([ACTIVE], generated=GEN)
    assert "Open" in html
    # an active-only report totals 0h
    assert "0h 00m" in html


def test_render_styles_differ():
    cyber = report.render_report_html([EARLIER], style="cyberpunk", generated=GEN)
    printer = report.render_report_html([EARLIER], style="printer", generated=GEN)
    assert "#00ffff" in cyber  # neon cyan
    assert "#005a9e" in printer  # blue
    assert cyber != printer


def test_invoice_heading_underlined_in_both_themes():
    # The "Invoice" heading has an underline rule in BOTH themes (color differs).
    cyber = report.render_report_html([EARLIER], style="cyberpunk", generated=GEN)
    printer = report.render_report_html([EARLIER], style="printer", generated=GEN)
    assert "border-bottom: 3px solid #00ffff" in cyber
    assert "border-bottom: 3px solid #2a7fc0" in printer


def test_header_and_total_rows_shaded_in_both_themes():
    # The header (th) and total rows carry a shaded background in BOTH themes so
    # the layouts match; only the shade color differs (dark vs light).
    cyber = report.render_report_html([EARLIER], style="cyberpunk", generated=GEN)
    printer = report.render_report_html([EARLIER], style="printer", generated=GEN)
    assert cyber.count("background-color: #0d0d0d") >= 2  # th + total-row
    assert printer.count("background-color: #e9eef6") >= 2  # th + total-row


def test_printer_forces_color_printing_for_shaded_headers():
    # Without print-color-adjust the new light header/total shading would drop out
    # of the printer PDF; the rule must be present in the printer styles.
    printer = report.render_report_html([EARLIER], style="printer", fmt="pdf", generated=GEN)
    assert "print-color-adjust: exact" in printer


def test_render_amount_due_when_rate_given():
    # EARLIER (1h30m) + LATER (1h) = 2.5h; at $100/h that is $250.00.
    for style in ("cyberpunk", "printer"):
        for fmt in ("html", "pdf"):
            html = report.render_report_html(
                [EARLIER, LATER], style=style, fmt=fmt, rate=100, generated=GEN
            )
            assert "Amount Due" in html, (style, fmt)
            assert "$250.00" in html, (style, fmt)
            assert "2.50h" in html and "$100.00/h" in html


def test_render_amount_uses_thousands_separator():
    html = report.render_report_html([EARLIER, LATER], rate=1000, generated=GEN)
    assert "$2,500.00" in html


def test_amount_has_currency_label():
    html = report.render_report_html([EARLIER, LATER], rate=100, generated=GEN)
    assert "Amount Due (USD)" in html
    assert "$250.00 USD" in html


def test_tagline_is_present_on_every_variant():
    for style in ("cyberpunk", "printer"):
        for fmt in ("html", "pdf"):
            html = report.render_report_html(
                [EARLIER], style=style, fmt=fmt, generated=GEN, brand=BRAND
            )
            assert BRAND.tagline in html, (style, fmt)
            assert 'class="tagline"' in html


def test_tagline_omitted_when_explicitly_empty():
    # An explicitly empty tagline drops the side <div> entirely (the dataclass
    # default ships a real placeholder, so the default brand DOES render one).
    no_tag = report.BrandConfig(tagline="")
    html = report.render_report_html([EARLIER], generated=GEN, brand=no_tag)
    assert '<div class="tagline">' not in html


def test_default_brand_has_a_visible_placeholder_tagline():
    # A fresh install ships with a real side tagline so the layout doesn't look
    # half-empty; users can clear it with `tagline = ""` in their config.
    assert report.DEFAULT_BRAND.tagline
    html = report.render_report_html([EARLIER], generated=GEN)
    assert f'<div class="tagline">{report.DEFAULT_BRAND.tagline}</div>' in html


# --------------------------------------------------------------------------- #
# Plain-text invoice (console output)
# --------------------------------------------------------------------------- #
def test_render_text_looks_like_an_invoice():
    text = report.render_report_text([EARLIER, LATER], generated=GEN, width=76, brand=BRAND)
    assert BRAND.company in text
    assert f"Invoice Date: {GEN}" in text
    assert "-" * 76 in text and "=" * 76 in text  # separator rules
    assert "crypto work" in text  # annotation
    assert "Total" in text and "2h 30m" in text
    assert BRAND.tagline in text
    assert BRAND.btc_address in text
    # every line is bounded to the requested width
    assert max(len(line) for line in text.splitlines()) <= 76


def test_render_text_default_brand_includes_placeholder_payment_and_tagline():
    # DEFAULT_BRAND ships with both a placeholder BTC address and a placeholder
    # tagline, so the text invoice's footer is non-empty out of the box.
    text = report.render_report_text([EARLIER], generated=GEN)
    assert "YourCompany" in text
    assert "Please use BTC address:" in text
    assert report.DEFAULT_BRAND.btc_address in text
    assert report.DEFAULT_BRAND.tagline in text


def test_render_text_explicit_empty_btc_drops_payment_line():
    # Setting btc_address="" still hides the payment line (users who don't want
    # to accept BTC can opt out in their config).
    no_btc = report.BrandConfig(btc_address="")
    text = report.render_report_text([EARLIER], generated=GEN, brand=no_btc)
    assert "Please use BTC address:" not in text


def test_render_text_amount_due_with_currency():
    text = report.render_report_text([EARLIER, LATER], rate=100, generated=GEN)
    assert "Amount Due (USD)" in text
    assert "$250.00 USD" in text


def test_render_text_no_amount_without_rate():
    assert "Amount Due" not in report.render_report_text([EARLIER, LATER], generated=GEN)


def test_render_text_open_interval():
    text = report.render_report_text([ACTIVE], generated=GEN)
    assert "Open" in text


def test_write_report_text(tmp_path):
    out = report.write_report(
        [EARLIER, LATER], fmt="text", rate=100, path=tmp_path / "r.txt", brand=BRAND
    )
    assert out.exists()
    body = out.read_text()
    assert "Acme Corp" in body
    assert "$250.00 USD" in body


def test_payment_block_present_only_with_btc_address():
    # With a configured BTC address the payment block + copy script appear...
    with_btc = report.render_report_html([EARLIER], generated=GEN, brand=BRAND)
    assert 'class="payment"' in with_btc
    assert "copyBtcAddress" in with_btc
    assert BRAND.btc_address in with_btc
    # ...the dataclass default ships a placeholder BTC, so the default render
    # ALSO has the block (so fresh installs aren't half-styled)...
    default = report.render_report_html([EARLIER], generated=GEN)
    assert 'class="payment"' in default
    assert report.DEFAULT_BRAND.btc_address in default
    # ...and explicitly clearing it drops the whole block (and its script).
    cleared = report.render_report_html(
        [EARLIER], generated=GEN, brand=report.BrandConfig(btc_address="")
    )
    assert 'class="payment"' not in cleared
    assert "copyBtcAddress" not in cleared


def test_logo_svg_used_when_configured_else_text_fallback():
    # A configured SVG is embedded verbatim (no text-logo span element)...
    with_logo = report.render_report_html([EARLIER], generated=GEN, brand=BRAND)
    assert 'id="brand-logo"' in with_logo
    assert '<span class="logo-text">' not in with_logo
    # ...and an explicitly empty logo falls back to the styled company text.
    no_logo = report.BrandConfig(logo_svg="")
    fallback = report.render_report_html([EARLIER], generated=GEN, brand=no_logo)
    assert '<span class="logo-text">YourCompany</span>' in fallback


def test_default_brand_uses_bundled_placeholder_logo():
    # An unconfigured install ships with a real SVG mark (the bundled
    # placeholder), not the bare text fallback — so even a vanilla report has
    # a logo on the page.
    assert report.PLACEHOLDER_LOGO_SVG.startswith("<svg")
    assert report.DEFAULT_BRAND.logo_svg == report.PLACEHOLDER_LOGO_SVG
    html = report.render_report_html([EARLIER], generated=GEN)
    assert report.PLACEHOLDER_LOGO_SVG in html
    assert '<span class="logo-text">' not in html


def test_cyberpunk_pdf_description_header_matches_other_columns():
    # Regression: the cyberpunk PDF override `.annotation { color: #00cccc }`
    # used to win over `th { color: #00ffff }` on specificity (class > type),
    # so the Description header rendered duller than DATE/TIME/DURATION. The
    # rule must be scoped to td so it doesn't reach the th.
    import re

    css = report.render_report_html([EARLIER], style="cyberpunk", fmt="pdf", generated=GEN)
    assert "td.annotation {" in css
    # An unqualified `.annotation { color: ... }` rule would re-introduce the
    # bug (its class selector beats the th type selector on specificity).
    assert not re.search(r"(^|[^a-z])\.annotation\s*\{[^}]*color", css)


def test_logo_text_fallback_has_no_text_shadow_for_pdf_safety():
    # A cyan-on-cyan text-shadow on this inline span rasterizes as a solid filled
    # box in Chromium's PDF pipeline (regression: company name was unreadable in
    # the cyberpunk PDF default). The h1 glow lives on a block and is unaffected.
    no_logo = report.BrandConfig(logo_svg="")
    css = report.render_report_html([EARLIER], style="cyberpunk", fmt="pdf",
                                    generated=GEN, brand=no_logo)
    logo_text_rule = css[css.index(".logo-text {"):css.index(".payment {")]
    assert "text-shadow" not in logo_text_rule


def test_recolor_retints_logo_per_theme():
    # A configured logo carries the `recolor` class (logo_recolor defaults to
    # True) and is retinted per theme from a single white master: printer -> blue
    # primary, cyberpunk -> white primary. Both themes emit the recolor hooks.
    printer = report.render_report_html([EARLIER], style="printer", generated=GEN, brand=BRAND)
    assert '<div class="logo recolor">' in printer
    assert ".logo.recolor svg .primary" in printer
    assert "fill: #005a9e !important" in printer  # printer primary
    cyber = report.render_report_html([EARLIER], style="cyberpunk", generated=GEN, brand=BRAND)
    assert '<div class="logo recolor">' in cyber
    assert ".logo.recolor svg .primary" in cyber
    assert "fill: #ffffff !important" in cyber  # cyberpunk primary


def test_recolor_supports_semantic_and_cls_aliases():
    # Both themes recolor the semantic roles AND the Illustrator cls-N aliases,
    # with !important so the rules beat inline `fill:` on the SVG shapes.
    for style in ("cyberpunk", "printer"):
        css = report.render_report_html([EARLIER], style=style, generated=GEN)
        for role in ("primary", "secondary", "accent"):
            assert f".logo.recolor svg .{role}" in css, (style, role)
        for cls in ("cls-1", "cls-2", "cls-3"):
            assert f".logo.recolor svg .{cls}" in css, (style, cls)
        expect = "#ffffff !important" if style == "cyberpunk" else "#005a9e !important"
        assert f"fill: {expect}" in css, style


def test_logo_recolor_toggle_controls_recolor_class():
    # logo_recolor=False shows the logo with its OWN colors (no `recolor` class)
    # on every theme; the default (True) tags it for per-theme recoloring.
    own = report.BrandConfig(
        company="Acme Corp", logo_svg='<svg id="my-logo"></svg>', logo_recolor=False
    )
    for style in ("cyberpunk", "printer"):
        html = report.render_report_html([EARLIER], style=style, generated=GEN, brand=own)
        assert 'id="my-logo"' in html
        assert '<div class="logo">' in html
        assert '<div class="logo recolor">' not in html
    default = report.BrandConfig(company="Acme Corp", logo_svg='<svg id="my-logo"></svg>')
    for style in ("cyberpunk", "printer"):
        html = report.render_report_html([EARLIER], style=style, generated=GEN, brand=default)
        assert '<div class="logo recolor">' in html


def test_render_no_amount_when_rate_zero_or_omitted():
    assert "Amount Due" not in report.render_report_html([EARLIER, LATER], generated=GEN)
    assert "Amount Due" not in report.render_report_html(
        [EARLIER, LATER], rate=0, generated=GEN
    )


def test_printer_uses_sans_serif_and_larger_fonts():
    printer = report.render_report_html([EARLIER], style="printer", generated=GEN)
    assert "font-family: Arial, Helvetica, sans-serif;" in printer
    assert "font-size: 13px;" in printer  # headers (th), matching cyberpunk
    assert "font-size: 14px;" in printer  # rows (td), bumped up for paper


def test_cyberpunk_table_fonts_match_printer():
    # Both themes use 13px headers (th) and 14px rows (td).
    cyber = report.render_report_html([EARLIER], style="cyberpunk", generated=GEN)
    assert "font-size: 13px;" in cyber  # th
    assert "font-size: 14px;" in cyber  # td


def test_default_generated_date_is_year_month_day():
    # No explicit `generated` -> defaults to "YYYY Month DD" (e.g. "2026 June 07").
    html = report.render_report_html([EARLIER])
    match = re.search(r"Invoice Date: (\d{4}) ([A-Z][a-z]+) (\d{2})", html)
    assert match is not None, "default date not in 'YYYY Month DD' format"


def test_all_variants_share_uniform_letter_geometry():
    # Every style/format is bounded to the same US Letter sheet (8.5in, 0.5in
    # content inset, full-bleed @page) so HTML and PDF, cyberpunk and printer match.
    for style in ("cyberpunk", "printer"):
        for fmt in ("html", "pdf"):
            css = report.render_report_html([EARLIER], style=style, fmt=fmt, generated=GEN)
            assert "max-width: 8.5in" in css, (style, fmt)
            assert "padding: 0.5in" in css, (style, fmt)
            assert "@page" in css and "margin: 0;" in css, (style, fmt)


def test_cyberpunk_pdf_frame_is_in_flow_not_fixed():
    # The neon frame is the invoice's own border (Chromium prints in-flow boxes
    # reliably; a fixed overlay gets painted over). The invoice fills the sheet and
    # the side tagline is pulled just inside the right border.
    css = report.render_report_html([EARLIER], style="cyberpunk", fmt="pdf", generated=GEN)
    assert "min-height: 10.78in;" in css
    assert "left: 8.19in;" in css and "right: 0.13in;" in css
    assert "0.08in" not in css  # no leftover fixed-overlay frame inset


def test_printer_pdf_needs_no_chromium_tweaks():
    # Printer PDF has no neon frame and keeps the default tagline position.
    css = report.render_report_html([EARLIER], style="printer", fmt="pdf", generated=GEN)
    assert "min-height: 10.78in;" not in css
    assert "left: 8.19in;" not in css


def test_printer_html_has_hover_effect():
    html = report.render_report_html([EARLIER], style="printer", fmt="html", generated=GEN)
    assert "tr:hover" in html


def test_render_rejects_unknown_style_and_format():
    with pytest.raises(ValueError):
        report.render_report_html([EARLIER], style="neon")
    with pytest.raises(ValueError):
        report.render_report_html([EARLIER], fmt="docx")


def test_write_report_html(tmp_path):
    out = report.write_report([EARLIER], style="printer", fmt="html", path=tmp_path / "r.html")
    assert out.exists()
    text = out.read_text()
    assert text.startswith("<!DOCTYPE html>")
    assert "crypto work" in text


def test_write_report_html_includes_amount(tmp_path):
    out = report.write_report(
        [EARLIER, LATER], fmt="html", rate=50, path=tmp_path / "r.html"
    )
    text = out.read_text()
    assert "Amount Due" in text
    assert "$125.00" in text  # 2.5h * $50


def test_write_report_expands_user(tmp_path, monkeypatch):
    # '~' should be expanded; point HOME at a temp dir so nothing real is touched.
    monkeypatch.setenv("HOME", str(tmp_path))
    out = report.write_report([EARLIER], fmt="html", path="~/report.html")
    assert out == tmp_path / "report.html"
    assert out.exists()


def test_chromium_install_argv_targets_headless_shell():
    # Pure: the one-time browser-download command, built without running anything.
    # We fetch the slim headless shell, not the full headful chromium.
    argv = report._chromium_install_argv()
    assert argv[0]  # the current interpreter
    assert argv[1:] == ["-m", "playwright", "install", "chromium-headless-shell"]


def test_parse_download_progress_extracts_percent_and_ignores_small_files():
    # Pure parsing of Playwright's "N% of X MiB" install lines (no subprocess).
    out = report._parse_download_progress("|\u25a0\u25a0\u25a0| 70% of 92.4 MiB", 0.0)
    assert out is not None
    percent, file_mib, label = out
    assert percent == 70.0 and file_mib == 92.4 and "70%" in label
    # The tiny trailing ffmpeg (1 MiB, smaller than the 92.4 MiB shell) must not
    # drive the main bar backwards -> ignored.
    assert report._parse_download_progress("| 50% of 1 MiB", 92.4) is None
    # Non-progress lines -> None.
    assert report._parse_download_progress("Downloading Chrome Headless Shell ...", 0.0) is None


def test_is_missing_browser_error_only_for_uninstalled_browser():
    assert report._is_missing_browser_error(
        Exception("Executable doesn't exist at /home/u/.cache/ms-playwright/...")
    )
    assert report._is_missing_browser_error(Exception("...please run `playwright install`"))
    # An unrelated launch failure must NOT trigger an auto-download.
    assert not report._is_missing_browser_error(Exception("Target page crashed"))


def test_chromium_available_detects_installed_headless_shell(tmp_path, monkeypatch):
    # Marker-based probe: never launches or downloads a browser. Empty dir -> False;
    # once Playwright's INSTALLATION_COMPLETE marker is present -> True.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert report.chromium_available() is False
    marker = tmp_path / "chromium_headless_shell-1234" / "INSTALLATION_COMPLETE"
    marker.parent.mkdir(parents=True)
    marker.touch()
    assert report.chromium_available() is True


def test_write_report_pdf_requires_playwright(tmp_path, monkeypatch):
    # Force the lazy `playwright.sync_api` import to fail (None in sys.modules) so
    # the PDF path raises a friendly error and never launches a real browser.
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    target = tmp_path / "r.pdf"
    with pytest.raises(report.ReportError) as exc:
        report.write_report([EARLIER], fmt="pdf", path=target)
    assert "Playwright" in str(exc.value)
    assert not target.exists()


# --------------------------------------------------------------------------- #
# Brand config loading (TOML + logo file)
# --------------------------------------------------------------------------- #
def test_load_brand_config_missing_file_returns_defaults():
    # The autouse conftest fixture points TIMETUI_CONFIG at a missing path.
    assert report.load_brand_config() == report.DEFAULT_BRAND


def test_load_brand_config_reads_toml_and_relative_logo(tmp_path, monkeypatch):
    (tmp_path / "logo.svg").write_text("<svg id='mine'></svg>", encoding="utf-8")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[brand]\n'
        'company = "Acme"\n'
        'tagline = "We ship."\n'
        'currency = "EUR"\n'
        'btc_address = "bc1qacme"\n'
        'logo_svg_path = "logo.svg"\n',  # relative -> resolves next to the config
        encoding="utf-8",
    )
    monkeypatch.setenv("TIMETUI_CONFIG", str(cfg))
    brand = report.load_brand_config()
    assert brand.company == "Acme"
    assert brand.tagline == "We ship."
    assert brand.currency == "EUR"
    assert brand.btc_address == "bc1qacme"
    assert brand.logo_svg == "<svg id='mine'></svg>"


def test_coerce_rate_parses_numbers_and_guards_bad_values():
    # Pure: ints/floats/numeric strings parse; junk, negatives and 0 -> unset.
    assert report._coerce_rate(200) == 200.0
    assert report._coerce_rate(200.5) == 200.5
    assert report._coerce_rate("150") == 150.0
    assert report._coerce_rate("lots") == 0.0
    assert report._coerce_rate(-50) == 0.0
    assert report._coerce_rate(None) == 0.0
    assert report._coerce_rate(0) == 0.0


def test_default_brand_has_no_rate():
    assert report.DEFAULT_BRAND.rate == 0.0


def test_load_brand_config_reads_rate(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[brand]\ncompany = "X"\nrate = 200\n', encoding="utf-8")
    monkeypatch.setenv("TIMETUI_CONFIG", str(cfg))
    assert report.load_brand_config().rate == 200.0


def test_load_brand_config_bad_rate_is_treated_as_unset(tmp_path, monkeypatch):
    # A non-numeric / negative rate must not crash the loader; it falls back to 0
    # (rate unset -> no live dollar amounts), like the rest of the bad-config path.
    cfg = tmp_path / "config.toml"
    cfg.write_text('[brand]\nrate = "expensive"\n', encoding="utf-8")
    monkeypatch.setenv("TIMETUI_CONFIG", str(cfg))
    assert report.load_brand_config().rate == 0.0


def test_clean_svg_strips_prolog_and_keeps_plain():
    assert report._clean_svg("<?xml version='1.0'?>\n<svg id='a'/>") == "<svg id='a'/>"
    assert report._clean_svg("<svg id='b'/>") == "<svg id='b'/>"
    assert report._clean_svg("") == ""
    assert report._clean_svg("no svg here") == "no svg here"


def test_load_brand_config_reads_logo_recolor_and_strips_prolog(tmp_path, monkeypatch):
    (tmp_path / "mark.svg").write_text(
        "<?xml version='1.0' encoding='UTF-8'?><svg id='p'></svg>", encoding="utf-8"
    )
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[brand]\n'
        'company = "X"\n'
        'logo_svg_path = "mark.svg"\n'
        'logo_recolor = false\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TIMETUI_CONFIG", str(cfg))
    brand = report.load_brand_config()
    assert brand.logo_svg == "<svg id='p'></svg>"  # XML prolog stripped
    assert brand.logo_recolor is False


def test_load_brand_config_malformed_toml_falls_back(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text("this is = not valid = toml", encoding="utf-8")
    monkeypatch.setenv("TIMETUI_CONFIG", str(cfg))
    assert report.load_brand_config() == report.DEFAULT_BRAND


def test_load_brand_config_partial_keeps_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[brand]\ncompany = "Solo"\n', encoding="utf-8")
    monkeypatch.setenv("TIMETUI_CONFIG", str(cfg))
    brand = report.load_brand_config()
    assert brand.company == "Solo"
    assert brand.currency == "USD"  # default kept
    assert brand.btc_address == report.DEFAULT_BRAND.btc_address  # placeholder kept
    # No logo configured -> the bundled placeholder logo is used (so a partial
    # config is no worse off than a fresh install with no config at all).
    assert brand.logo_svg == report.PLACEHOLDER_LOGO_SVG
    assert brand.logo_recolor is True  # default when unset


def test_shipped_example_config_loads(tmp_path, monkeypatch):
    # The committed config.example.toml parses cleanly. It doesn't set a
    # logo_svg_path by default (commented), so the bundled placeholder is used.
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    brand = report.load_brand_config(root / "config.example.toml")
    assert brand.company == "YourCompany"
    assert brand.logo_svg == report.PLACEHOLDER_LOGO_SVG


# --------------------------------------------------------------------------- #
# Time Warrior db dir config ([timew] db_path)
# --------------------------------------------------------------------------- #
def test_load_timew_db_missing_file_returns_none():
    # The autouse conftest fixture points TIMETUI_CONFIG at a missing path.
    assert report.load_timew_db() is None


def test_load_timew_db_reads_value(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[timew]\ndb_path = "/data/timewarrior"\n', encoding="utf-8")
    monkeypatch.setenv("TIMETUI_CONFIG", str(cfg))
    assert report.load_timew_db() == "/data/timewarrior"


def test_load_timew_db_no_table_or_empty_returns_none(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    # A config with branding but no [timew] table.
    cfg.write_text('[brand]\ncompany = "Solo"\n', encoding="utf-8")
    monkeypatch.setenv("TIMETUI_CONFIG", str(cfg))
    assert report.load_timew_db() is None
    # An empty value is treated as unset.
    cfg.write_text('[timew]\ndb_path = ""\n', encoding="utf-8")
    assert report.load_timew_db() is None


def test_load_timew_db_malformed_toml_returns_none(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text("this is = not valid = toml", encoding="utf-8")
    monkeypatch.setenv("TIMETUI_CONFIG", str(cfg))
    assert report.load_timew_db() is None
