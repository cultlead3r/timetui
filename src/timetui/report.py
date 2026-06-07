"""Generate pretty HTML / PDF time-tracking reports from intervals.

Ported from the standalone ``timew-report`` script and split, like ``timew.py``,
into a **pure** part and an **impure** part:

* :func:`render_report_html` builds the report HTML from ``Interval`` objects and
  never shells out or touches the filesystem, so it is fully unit-testable.
* :func:`write_report` is the only function that performs IO: it writes the HTML
  file or, for PDF, renders the HTML with headless Chromium via Playwright. It is
  the analogue of ``timew.execute``.

Times are rendered in **local** time (matching the TUI); intervals are listed
oldest-first. Chromium (Playwright) is an optional dependency — when the package
or its browser is missing PDF generation raises :class:`ReportError` with a
friendly message.
"""

from __future__ import annotations

import html
import os
import textwrap
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Sequence

from .models import Interval


def _clean_svg(text: str) -> str:
    """Strip anything before the ``<svg`` tag (pure: unit-tested).

    Real-world ``.svg`` files often start with an ``<?xml ...?>`` prolog or a
    comment, which is invalid when the markup is embedded inline in the report's
    HTML. Dropping everything up to the first ``<svg`` keeps inline embedding
    clean; a string with no ``<svg`` (or an empty one) is returned unchanged.
    """
    i = text.find("<svg")
    return text[i:] if i != -1 else text


def _load_placeholder_logo() -> str:
    """Load the bundled placeholder ``logo.svg`` shipped inside the package.

    Used as the default logo when the user has no config / no logo configured,
    so a fresh install still shows a real SVG mark rather than the bare text
    fallback. Returns ``""`` if the resource cannot be read (which then falls
    through to the text-logo fallback in the renderer).
    """
    try:
        import importlib.resources

        return _clean_svg(
            importlib.resources.files(__package__)
            .joinpath("logo.svg")
            .read_text(encoding="utf-8")
        )
    except (OSError, FileNotFoundError, ModuleNotFoundError, AttributeError):
        return ""


# The open-source placeholder logo, bundled as a package resource. This is the
# default ``BrandConfig.logo_svg`` so an unconfigured install still shows a real
# logo on every report instead of the bare text fallback.
PLACEHOLDER_LOGO_SVG = _load_placeholder_logo()


@dataclass(frozen=True)
class BrandConfig:
    """User-supplied branding for the report header / invoice.

    Every field is optional and defaults to a neutral placeholder, so the
    project ships with **no** personal branding. The real values are loaded at
    runtime from a TOML config (see :func:`load_brand_config`) — your own company
    details live in ``~/.config/timetui/config.toml``, never in the source tree.

    * ``tagline`` empty -> the vertical side tagline is omitted. The dataclass
      default ships a small placeholder line so a fresh install isn't half-empty;
      pass ``tagline=""`` to drop the side text entirely.
    * ``btc_address`` empty -> the whole payment block (QR + copy script) is omitted.
      The dataclass default ships a placeholder address so the layout is complete
      out of the box; pass ``btc_address=""`` to opt out of the payment block.
    * ``logo_svg`` empty -> the company name is rendered as styled text, not an SVG.
      The dataclass default is :data:`PLACEHOLDER_LOGO_SVG` (the bundled
      open-source mark), so a fresh install with no config still shows a real
      logo; explicitly pass ``logo_svg=""`` to opt into the text fallback.
    * ``logo_svg_printer`` is an optional light-background logo for the printer
      theme. When set it is shown **with its own colors** (the printer recolor is
      skipped); when empty the printer theme falls back to ``logo_svg`` recolored
      to the printer accent. ``logo_svg`` is the dark-theme (cyberpunk) logo.
    """

    company: str = "YourCompany"
    tagline: str = "Meow btw. If you even care."
    currency: str = "USD"
    btc_address: str = "bc1meowbtwifyouevencare"
    logo_svg: str = PLACEHOLDER_LOGO_SVG
    logo_svg_printer: str = ""


# Neutral defaults used when the user has no config file (the open-source default).
DEFAULT_BRAND = BrandConfig()


# Cyberpunk color scheme (dark, neon).
CYBERPUNK_STYLES = """
    * {
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
        color-adjust: exact !important;
    }
    html {
        background-color: #0a0a0a;
    }
    body {
        font-family: 'Courier New', monospace;
        max-width: 900px;
        margin: 40px auto;
        padding: 20px;
        background-color: #0a0a0a;
    }
    .invoice {
        background: #1a1a1a;
        background-color: #1a1a1a;
        padding: 40px;
    }
    h1 {
        color: #00ffff;
        margin-bottom: 10px;
        font-size: 28px;
        border-bottom: 3px solid #00ffff;
        padding-bottom: 10px;
        text-shadow: 0 0 10px #00ffff;
    }
    .date {
        color: #00cccc;
        margin-bottom: 30px;
        font-size: 14px;
    }
    table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 20px;
    }
    th {
        background-color: #0d0d0d;
        padding: 12px;
        text-align: left;
        border-bottom: 2px solid #00ffff;
        font-weight: 600;
        color: #00ffff;
        font-size: 13px;
        text-transform: uppercase;
        white-space: nowrap;
    }
    td {
        padding: 12px;
        border-bottom: 1px solid #333;
        color: #00cccc;
        font-family: 'Courier New', monospace;
        font-size: 14px;
        white-space: nowrap;
    }
    tr:hover {
        background-color: #252525;
        box-shadow: 0 0 10px rgba(0, 255, 255, 0.1);
    }
    .duration {
        text-align: right;
        font-weight: 500;
        color: #00ffff;
    }
    .total-row {
        font-weight: bold;
        background-color: #0d0d0d;
    }
    .total-row td {
        border-top: 2px solid #00ffff;
        border-bottom: 2px solid #00ffff;
        padding: 15px 12px;
        color: #00ffff;
    }
    .annotation {
        max-width: 400px;
        white-space: normal;
        word-wrap: break-word;
    }
    .logo {
        margin-bottom: 20px;
        text-align: center;
    }
    /* No glow on .logo-text: a cyan-on-cyan blur on this inline span
       rasterizes as a solid filled box in Chromium's PDF pipeline (the
       h1 glow is on a block element and works). Bold + letter-spacing
       is enough to make the text fallback read. */
    .logo-text {
        font-size: 28px;
        font-weight: 700;
        letter-spacing: 2px;
        color: #00ffff;
    }
    .payment {
        margin-top: 40px;
        padding-top: 20px;
        border-top: 1px solid #333;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 20px;
    }
    .payment-qr svg {
        width: 100px;
        height: 100px;
    }
    .payment-qr svg path {
        fill: #00ffff;
    }
    .payment-text {
        color: #00cccc;
        font-size: 12px;
    }
    .payment-text .btc-label {
        color: #00ffff;
        margin-bottom: 5px;
    }
    .payment-text .btc-address {
        font-family: 'Courier New', monospace;
        word-break: break-all;
        cursor: pointer;
        transition: color 0.2s;
    }
    .payment-text .btc-address:hover {
        color: #fff;
    }
    .payment-text .btc-address.copied::after {
        content: ' (copied!)';
        color: #00ff00;
    }
"""


# Printer-friendly color scheme (clean, light).
PRINTER_STYLES = """
    * {
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
        color-adjust: exact !important;
    }
    body {
        font-family: Arial, Helvetica, sans-serif;
        margin: 0;
        padding: 0;
        background-color: white;
    }
    .invoice {
        background: white;
        padding: 0;
    }
    h1 {
        color: #005a9e;
        margin-bottom: 10px;
        font-size: 28px;
        border-bottom: 3px solid #2a7fc0;
        padding-bottom: 10px;
    }
    .date {
        color: #555;
        margin-bottom: 30px;
        font-size: 14px;
    }
    table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 20px;
    }
    th {
        background-color: #e9eef6;
        padding: 12px;
        text-align: left;
        border-bottom: 2px solid #005a9e;
        font-weight: 600;
        color: #005a9e;
        font-size: 13px;
        text-transform: uppercase;
        white-space: nowrap;
    }
    td {
        padding: 12px;
        border-bottom: 1px solid #ddd;
        color: #333;
        white-space: nowrap;
        font-size: 14px;
    }
    .duration {
        text-align: right;
        font-weight: 500;
        color: #005a9e;
    }
    .total-row {
        font-weight: bold;
        background-color: #e9eef6;
    }
    .total-row td {
        padding: 15px 12px;
        border-top: 3px solid #005a9e;
        border-bottom: 2px solid #005a9e;
        color: #005a9e;
    }
    .annotation {
        max-width: 400px;
        white-space: normal;
        word-wrap: break-word;
    }
    .logo {
        margin-bottom: 20px;
        text-align: center;
    }
    .logo-text {
        font-size: 28px;
        font-weight: 700;
        letter-spacing: 2px;
        color: #005a9e;
    }
    /* A single (dark-theme) logo is recolored to the printer accent so it stays
       legible on white. A dedicated `logo_svg_printer` keeps its own colors and
       is rendered without the `recolor` class. */
    .logo.recolor svg .cls-1,
    .logo.recolor svg .cls-2 {
        fill: #005a9e;
    }
    .payment {
        margin-top: 40px;
        padding-top: 20px;
        border-top: 1px solid #ddd;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 20px;
    }
    .payment-qr svg {
        width: 100px;
        height: 100px;
    }
    .payment-qr svg path {
        fill: #333;
    }
    .payment-text {
        color: #333;
        font-size: 12px;
    }
    .payment-text .btc-label {
        color: #005a9e;
        margin-bottom: 5px;
    }
    .payment-text .btc-address {
        font-family: 'Courier New', monospace;
        word-break: break-all;
        cursor: pointer;
        transition: color 0.2s;
    }
    .payment-text .btc-address:hover {
        color: #000;
    }
    .payment-text .btc-address.copied::after {
        content: ' (copied!)';
        color: #005a9e;
    }
"""

STYLES = ("cyberpunk", "printer")
# All output formats offered in the report dialog. "text" is a plain-ASCII
# invoice (style-agnostic); "html"/"pdf" are the styled documents.
FORMATS = ("html", "pdf", "text")
HTML_FORMATS = ("html", "pdf")


class ReportError(RuntimeError):
    """Raised when report generation fails (e.g. Chromium/Playwright missing or errored)."""


def _hours_minutes(seconds: float) -> str:
    """Format a duration in seconds as ``"Hh Mm"`` (invoice style, e.g. ``2h 05m``)."""
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    return f"{hours}h {minutes:02d}m"


def _btc_qr_svg(address: str) -> str:
    """Build an inline SVG QR code for ``address`` (lazy-imports ``qrcode``).

    Returns ``""`` for an empty ``address`` or when ``qrcode`` is not installed,
    so report generation degrades to an address-only (or no) payment block
    instead of failing outright.
    """
    import io

    if not address:
        return ""

    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ""

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(address)
    qr.make(fit=True)
    buffer = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    return buffer.getvalue().decode("utf-8")


def _build_styles(style: str, fmt: str) -> str:
    """Assemble the full CSS for the chosen ``style`` / ``fmt`` (mirrors timew-report)."""
    base = CYBERPUNK_STYLES if style == "cyberpunk" else PRINTER_STYLES

    if fmt == "pdf":
        styles = (
            """
    @page {
        size: letter;
        margin: 0;
    }
"""
            + base
        )
        if style == "cyberpunk":
            styles += """
    /* Force colors and backgrounds for PDF rendering */
    html {
        margin: 0;
        padding: 0;
        background-color: #0a0a0a !important;
    }
    body {
        margin: 0;
        padding: 20px;
        max-width: 100%;
        background-color: #0a0a0a !important;
    }
    .invoice {
        background-color: #1a1a1a !important;
    }
    th {
        color: #00ffff !important;
        background-color: #0d0d0d !important;
    }
    td {
        color: #00cccc !important;
        font-family: 'Courier New', monospace !important;
    }
    tr {
        background-color: #1a1a1a !important;
    }
    .duration {
        color: #00ffff !important;
    }
    /* Scope to td: an unqualified `.annotation` (class) beats
       `th { color: #00ffff !important }` (type) on specificity, so the
       Description header inherits the duller body cyan instead of the
       bright neon used by the other column headers. */
    td.annotation {
        color: #00cccc !important;
    }
    .total-row {
        background-color: #0d0d0d !important;
    }
    .total-row td {
        color: #00ffff !important;
        background-color: #0d0d0d !important;
    }
    h1 {
        color: #00ffff !important;
    }
    .date {
        color: #00cccc !important;
    }
"""
    else:
        styles = base
    # Appended last so it wins the cascade: identical geometry for every
    # style/format (US Letter width, centered, 0.5in content inset) plus the
    # side tagline, then on-screen-only chrome.
    styles += _layout_and_chrome(style, fmt)
    return styles


def _layout_and_chrome(style: str, fmt: str) -> str:
    """Shared page/box geometry + side tagline for all four variants, and the
    screen-only chrome (backdrop, vertical breathing room, card shadow, hover).

    Unifying this keeps the HTML and PDF, cyberpunk and printer outputs the same
    width and proportions — only colors and fonts differ between themes.
    """
    accent = "#00ffff" if style == "cyberpunk" else "#005a9e"
    # Shared geometry: one US Letter sheet (8.5in wide), centered, with an extra
    # right gutter that the vertical tagline lives in (so it never collides with
    # the table). Appended last so it wins the cascade for every style/format.
    out = f"""
    @page {{
        size: letter;
        margin: 0;
    }}
    body {{
        max-width: 8.5in;
        margin: 0 auto;
        padding: 0;
        box-sizing: border-box;
    }}
    .invoice {{
        padding: 0.5in;
        box-sizing: border-box;
        position: relative;
    }}
    .page-frame {{
        display: none;
    }}
    .logo svg {{
        height: 40px;
        width: auto;
    }}
    .tagline {{
        top: 0;
        bottom: 0;
        left: 8.27in;
        right: 0.05in;
        box-sizing: border-box;
        writing-mode: vertical-rl;
        text-align: center;
        text-transform: uppercase;
        font-size: 10px;
        font-weight: bold;
        letter-spacing: 3px;
        white-space: nowrap;
        color: {accent};
        opacity: 0.8;
        pointer-events: none;
    }}
"""
    if fmt == "pdf":
        # The side tagline is page-relative; `fixed` pins it to the right gutter.
        out += """
    .tagline {
        position: fixed;
    }
"""
        if style == "cyberpunk":
            # Headless Chromium renders the neon frame as the invoice's *own*
            # border (an in-flow box), not a fixed overlay — a fixed frame gets
            # painted over by the invoice background in Chromium's print path. The
            # invoice fills the sheet (min-height) so the bottom border is inset
            # symmetrically with the top, and the tagline is pulled just inside the
            # right border.
            out += """
    html, body { background-color: #1a1a1a !important; }
    .page-frame { display: none; }
    .invoice {
        margin: 0.1in;
        min-height: 10.78in;
        box-sizing: border-box;
        background-color: #1a1a1a !important;
        border: 1px solid #00ffff;
        box-shadow: 0 0 24px rgba(0, 255, 255, 0.45),
                    inset 0 0 26px rgba(0, 255, 255, 0.12);
    }
    .tagline {
        left: 8.19in;
        right: 0.13in;
    }
"""
        return out

    # ---- HTML: tagline rides the sheet; all decoration is screen-only so that
    # printing the page from a browser stays clean (no drop shadows on paper).
    out += """
    .tagline {
        position: absolute;
    }
    @media screen {
        body {
            margin-top: 28px;
            margin-bottom: 28px;
        }
        .invoice {
            min-height: 11in;
        }
    }
"""
    if style == "cyberpunk":
        out += """
    @media screen {
        .invoice {
            border: 1px solid #00ffff;
            border-radius: 8px;
            box-shadow: 0 0 22px rgba(0, 255, 255, 0.28);
        }
    }
"""
    else:
        out += """
    @media screen {
        html {
            background-color: #e9e9ec;
        }
        .invoice {
            box-shadow: 0 2px 14px rgba(0, 0, 0, 0.22);
        }
        tr:hover {
            background-color: #eef3fb;
            box-shadow: 0 0 8px rgba(0, 90, 158, 0.15);
        }
    }
"""
    return out


def render_report_text(
    intervals: Sequence[Interval],
    *,
    generated: str = "",
    rate: float = 0.0,
    brand: BrandConfig = DEFAULT_BRAND,
    width: int = 76,
) -> str:
    """Render a plain-text (monospace) invoice — the console / ``timew summary``
    style output. Pure: no IO, no shell-out, so it is unit-tested directly.

    Columns are fixed-width and long descriptions wrap within their column;
    separator lines use ``-`` / ``=``. ``generated`` defaults to today's local
    date when empty (a parameter so tests can pin it).
    """
    if not generated:
        from datetime import datetime

        generated = datetime.now().strftime("%Y %B %d")

    bar = "=" * width
    rule = "-" * width
    date_w, time_w, dur_w = 10, 13, 8
    desc_w = width - date_w - time_w - dur_w - 6  # 3 two-space gaps

    def row(d: str, t: str, desc: str, dur: str) -> str:
        return f"{d:<{date_w}}  {t:<{time_w}}  {desc:<{desc_w}}  {dur:>{dur_w}}"

    out: list[str] = [bar, brand.company.center(width), bar]
    out.append(f"Invoice Date: {generated}")
    out.append("")
    out.append(row("Date", "Time", "Description", "Duration"))
    out.append(rule)

    total = timedelta()
    for iv in sorted(intervals, key=lambda i: i.start):
        start = iv.start_local
        start_date = start.strftime("%Y-%m-%d")
        start_time = start.strftime("%H:%M")
        end = iv.end_local
        if end is not None:
            time_range = f"{start_time} - {end.strftime('%H:%M')}"
            dur = iv.duration()
            total += dur
            duration_str = _hours_minutes(dur.total_seconds())
        else:
            time_range = f"{start_time} - Open"
            duration_str = "Open"
        desc_lines = textwrap.wrap(iv.annotation, desc_w) or [""]
        for index, desc_line in enumerate(desc_lines):
            if index == 0:
                out.append(row(start_date, time_range, desc_line, duration_str))
            else:
                out.append(row("", "", desc_line, ""))

    out.append(rule)
    out.append(row("", "", "Total", _hours_minutes(total.total_seconds())))

    if rate and rate > 0:
        hours = total.total_seconds() / 3600.0
        amount = hours * rate
        label = f"Amount Due ({brand.currency}):  {hours:.2f}h \u00d7 ${rate:,.2f}/h"
        value = f"${amount:,.2f} {brand.currency}"
        gap = width - len(label) - len(value)
        out.append("")
        if gap >= 1:
            out.append(label + " " * gap + value)
        else:
            out.append(label)
            out.append(f"{value:>{width}}")

    out.append(bar)
    footer: list[str] = []
    if brand.btc_address:
        footer.append("")
        footer.append("")
        footer.append(f"Please use BTC address: {brand.btc_address}")
        footer.append("")
    if brand.tagline:
        if footer:
            footer.append("")
        footer.append(brand.tagline.center(width))
    if footer:
        out.extend(footer)
        out.append(bar)
    return "\n".join(out)


def render_report_html(
    intervals: Sequence[Interval],
    *,
    style: str = "cyberpunk",
    fmt: str = "html",
    generated: str = "",
    rate: float = 0.0,
    brand: BrandConfig = DEFAULT_BRAND,
) -> str:
    """Render the report HTML for ``intervals`` (pure: no IO, no shell-out).

    ``style`` is ``"cyberpunk"`` or ``"printer"``; ``fmt`` (``"html"``/``"pdf"``)
    only affects the embedded CSS. Times are shown in local time and rows are
    listed oldest-first. ``generated`` is the human date shown in the header; when
    empty it defaults to today's local date (kept as a parameter so callers/tests
    can pin it for determinism). When ``rate`` is greater than 0 an "Amount Due"
    row (decimal hours times the hourly ``rate``) is appended to every style.
    """
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r} (expected one of {STYLES})")
    if fmt not in HTML_FORMATS:
        raise ValueError(f"unknown format {fmt!r} (expected one of {HTML_FORMATS})")

    if not generated:
        from datetime import datetime

        generated = datetime.now().strftime("%Y %B %d")

    styles = _build_styles(style, fmt)

    total = timedelta()
    rows = ""
    for iv in sorted(intervals, key=lambda i: i.start):
        start = iv.start_local
        start_date = start.strftime("%Y-%m-%d")
        start_time = start.strftime("%H:%M")
        end = iv.end_local
        if end is not None:
            end_date = end.strftime("%Y-%m-%d")
            end_time = end.strftime("%H:%M")
            if start_date != end_date:
                date_str = f"{start_date} - {end_date}"
            else:
                date_str = start_date
            time_range = f"{start_time} - {end_time}"
            dur = iv.duration()
            total += dur
            duration_str = _hours_minutes(dur.total_seconds())
        else:
            date_str = start_date
            time_range = f"{start_time} - Open"
            duration_str = "Open"

        annotation = html.escape(iv.annotation)
        rows += f"""            <tr>
                <td>{date_str}</td>
                <td>{time_range}</td>
                <td class="annotation">{annotation}</td>
                <td class="duration">{duration_str}</td>
            </tr>
"""

    total_str = _hours_minutes(total.total_seconds())
    total_hours = total.total_seconds() / 3600.0

    amount_rows = ""
    if rate and rate > 0:
        amount = total_hours * rate
        amount_rows = (
            '            <tr class="total-row">\n'
            f'                <td colspan="3">Amount Due ({brand.currency}) '
            f'\u2014 {total_hours:.2f}h \u00d7 ${rate:,.2f}/h</td>\n'
            f'                <td class="duration">${amount:,.2f} {brand.currency}</td>\n'
            "            </tr>\n"
        )

    tagline_html = (
        f'<div class="tagline">{html.escape(brand.tagline, quote=False)}</div>'
        if brand.tagline
        else ""
    )
    # Logo selection. The printer theme prefers a dedicated light-background logo
    # (shown with its own colors); a lone dark-theme logo gets the `recolor` class
    # so the printer CSS retints it to the accent. No logo -> styled company text.
    if style == "printer" and brand.logo_svg_printer:
        logo_html = brand.logo_svg_printer
        logo_class = "logo"
    elif brand.logo_svg:
        logo_html = brand.logo_svg
        logo_class = "logo recolor"
    else:
        logo_html = f'<span class="logo-text">{html.escape(brand.company, quote=False)}</span>'
        logo_class = "logo"

    # The payment block, QR code and copy-to-clipboard script only exist when a
    # BTC address is configured (the open-source default ships without one).
    payment_html = ""
    script_html = ""
    if brand.btc_address:
        btc_qr_svg = _btc_qr_svg(brand.btc_address)
        addr = html.escape(brand.btc_address, quote=False)
        payment_html = (
            '        <div class="payment">\n'
            f'            <div class="payment-qr">{btc_qr_svg}</div>\n'
            '            <div class="payment-text">\n'
            '                <div class="btc-label">Please use BTC address:</div>\n'
            f'                <div class="btc-address" id="btc-address" onclick="copyBtcAddress()">{addr}</div>\n'
            "            </div>\n"
            "        </div>\n"
        )
        script_html = (
            "    <script>\n"
            "    function copyBtcAddress() {\n"
            f"        navigator.clipboard.writeText('{brand.btc_address}').then(function() {{\n"
            "            var el = document.getElementById('btc-address');\n"
            "            el.classList.add('copied');\n"
            "            setTimeout(function() {\n"
            "                el.classList.remove('copied');\n"
            "            }, 2000);\n"
            "        });\n"
            "    }\n"
            "    </script>\n"
        )

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
{styles}
    </style>
</head>
<body>
    <div class="page-frame"></div>
    <div class="invoice">
        {tagline_html}
        <div class="{logo_class}">{logo_html}</div>
        <h1>Invoice</h1>
        <div class="date">Invoice Date: {generated}</div>
        <table>
            <tr>
                <th>Date</th>
                <th>Time</th>
                <th class="annotation">Description</th>
                <th>Duration</th>
            </tr>
{rows}            <tr class="total-row">
                <td colspan="3">Total</td>
                <td class="duration">{total_str}</td>
            </tr>
{amount_rows}        </table>
{payment_html}    </div>
{script_html}</body>
</html>"""


def _chromium_install_argv() -> list[str]:
    """Argv that downloads the Chromium build Playwright needs (pure: unit-tested).

    We fetch ``chromium-headless-shell`` — the slim headless build that ``page.pdf``
    uses — not the full headful ``chromium`` (roughly a third of the size).
    """
    import sys

    return [sys.executable, "-m", "playwright", "install", "chromium-headless-shell"]


def _is_missing_browser_error(exc: BaseException) -> bool:
    """True when a launch failure just means the browser hasn't been downloaded yet."""
    msg = str(exc)
    return "Executable doesn't exist" in msg or "playwright install" in msg


def _parse_download_progress(line: str, main_mib: float) -> tuple[float, float, str] | None:
    """Parse a Playwright ``N% of X MiB`` install line (pure: unit-tested).

    Returns ``(percent, file_mib, label)`` for the main browser download, or
    ``None`` for non-progress lines and for any file smaller than one already seen
    (the tiny trailing ffmpeg) so a progress bar driven by it stays monotonic.
    """
    import re

    m = re.search(r"(\d+)%\s+of\s+([\d.]+)\s*MiB", line)
    if not m:
        return None
    percent, file_mib = float(m.group(1)), float(m.group(2))
    if file_mib < main_mib:
        return None
    return percent, file_mib, f"Downloading Chromium\u2026 {percent:.0f}%  ({file_mib:.0f} MiB)"


def install_chromium(progress=None) -> None:
    """Download the headless-shell browser once, streaming progress.

    ``progress`` (optional) is called as ``progress(percent: float, label: str)``
    as the download advances, so a UI can show a progress bar. Raises
    :class:`ReportError` (with the installer's last output) on failure. Impure
    counterpart of the pure :func:`_chromium_install_argv` / :func:`_parse_download_progress`.
    """
    import re
    import subprocess

    proc = subprocess.Popen(
        _chromium_install_argv(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    main_mib = 0.0
    tail: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = ansi.sub("", raw).rstrip()
        if line:
            tail.append(line)
            del tail[:-12]  # keep only the last few lines for error context
        parsed = _parse_download_progress(line, main_mib)
        if parsed is not None and progress is not None:
            percent, main_mib, label = parsed
            progress(percent, label)
    if proc.wait() != 0:
        detail = "\n".join(tail).strip()
        raise ReportError(
            "Failed to download Chromium for PDF export.\n"
            "Run `uv run playwright install chromium-headless-shell` manually, "
            "or choose HTML format." + (f"\n\n{detail}" if detail else "")
        )


def _install_chromium() -> None:
    """Silent one-time download (lazy fallback for non-UI callers)."""
    install_chromium()


def _launch_chromium(pw):
    """Launch the headless-shell Chromium, auto-downloading it once if it is missing.

    The browser binary cannot ship as a Python dependency, so the first PDF export
    fetches the slim ``chromium-headless-shell`` build on demand (a one-time
    ~90 MB download). Set ``TIMETUI_NO_BROWSER_DOWNLOAD`` to disable that and
    require a manual install.
    """
    import os

    def _launch():
        return pw.chromium.launch(channel="chromium-headless-shell")

    try:
        return _launch()
    except Exception as exc:  # noqa: BLE001 - classify the launch failure
        if not _is_missing_browser_error(exc):
            raise ReportError(f"Chromium failed to launch: {exc}") from exc
        if os.environ.get("TIMETUI_NO_BROWSER_DOWNLOAD"):
            raise ReportError(
                "Chromium for Playwright is not installed.\n"
                "Run `uv run playwright install chromium-headless-shell`, "
                "or choose HTML format."
            ) from exc
        _install_chromium()  # one-time download (raises ReportError on failure)
        return _launch()  # retry once


def _playwright_browsers_dir() -> Path:
    """Directory where Playwright stores downloaded browsers (mirrors its own layout)."""
    import os
    import sys

    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def chromium_available() -> bool:
    """Whether the headless-shell browser is already downloaded (no download triggered).

    Lets the UI warn the user before the first PDF export kicks off the one-time
    fetch. Checks for Playwright's own ``INSTALLATION_COMPLETE`` marker so it never
    has to launch (or download) a browser.
    """
    return any(
        _playwright_browsers_dir().glob("chromium_headless_shell-*/INSTALLATION_COMPLETE")
    )


def _html_to_pdf(html_text: str, path: Path) -> None:
    """Render ``html_text`` to a PDF at ``path`` with headless Chromium (Playwright).

    Chromium honors the report's print CSS (``@page``, ``print-color-adjust`` and
    background colors) so the PDF matches the on-screen styling. The ``playwright``
    package is a hard dependency; the browser binary is fetched on first use (see
    :func:`_launch_chromium`). A missing package or a failed download raises
    :class:`ReportError` (the HTML format never needs extras).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ReportError(
            "Playwright is required for PDF output but is not installed.\n"
            "Install it (`uv add playwright`) or choose HTML format."
        ) from exc

    try:
        with sync_playwright() as pw:
            browser = _launch_chromium(pw)
            try:
                page = browser.new_page()
                page.set_content(html_text, wait_until="load")
                page.pdf(
                    path=str(path),
                    prefer_css_page_size=True,  # honor @page { size: letter }
                    print_background=True,  # render dark backgrounds / neon
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
            finally:
                browser.close()
    except ReportError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any Chromium/Playwright failure
        raise ReportError(f"Chromium PDF generation failed: {exc}") from exc


def _config_path() -> Path:
    """Resolve the user's TOML config path (pure: just reads env vars).

    Honors ``$TIMETUI_CONFIG`` (an explicit file path), then ``$XDG_CONFIG_HOME``,
    falling back to ``~/.config/timetui/config.toml``.
    """
    explicit = os.environ.get("TIMETUI_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "timetui" / "config.toml"


def load_brand_config(path: str | Path | None = None) -> BrandConfig:
    """Load branding from the user's TOML config, else return :data:`DEFAULT_BRAND`.

    Impure: reads the config file and the logo SVG it points at. A missing file,
    malformed TOML, or unreadable logo silently yields the neutral defaults, so
    report generation never crashes on a bad config. ``path`` overrides the
    default location (used by tests); otherwise see :func:`_config_path`.

    The ``[brand]`` table maps onto :class:`BrandConfig`. ``logo_svg_path`` /
    ``logo_svg_path_printer`` are read into ``logo_svg`` / ``logo_svg_printer``
    (relative paths resolve next to the config file); an inline ``logo_svg`` /
    ``logo_svg_printer`` is used only when no readable path is given.
    """
    import tomllib

    cfg = Path(path).expanduser() if path is not None else _config_path()
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_BRAND
    brand = data.get("brand")
    if not isinstance(brand, dict):
        return DEFAULT_BRAND

    def _logo(inline_key: str, path_key: str) -> str:
        svg = str(brand.get(inline_key, "") or "")
        rel = brand.get(path_key)
        if rel:
            svg_file = Path(str(rel)).expanduser()
            if not svg_file.is_absolute():
                svg_file = cfg.parent / svg_file
            try:
                svg = svg_file.read_text(encoding="utf-8")
            except OSError:
                pass  # fall back to the inline value / the text logo
        return _clean_svg(svg)

    # Empty/unset main logo -> use the bundled placeholder, mirroring the no-config
    # path so configured-but-logo-less installs aren't worse off than uncustomized ones.
    return BrandConfig(
        company=str(brand.get("company", DEFAULT_BRAND.company)),
        tagline=str(brand.get("tagline", DEFAULT_BRAND.tagline)),
        currency=str(brand.get("currency", DEFAULT_BRAND.currency)),
        btc_address=str(brand.get("btc_address", DEFAULT_BRAND.btc_address)),
        logo_svg=_logo("logo_svg", "logo_svg_path") or PLACEHOLDER_LOGO_SVG,
        logo_svg_printer=_logo("logo_svg_printer", "logo_svg_path_printer"),
    )


def load_timew_db(path: str | Path | None = None) -> str | None:
    """Read ``[timew] db_path`` from the timetui config; ``None`` if unset.

    Reuses the same config file as :func:`load_brand_config` (see
    :func:`_config_path`). A missing file, malformed TOML, a missing ``[timew]``
    table, or an empty value all yield ``None`` so startup never crashes on a bad
    config — the caller then leaves ``TIMEWARRIORDB`` to the environment / default.
    ``path`` overrides the default location (used by tests).
    """
    import tomllib

    cfg = Path(path).expanduser() if path is not None else _config_path()
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    table = data.get("timew")
    if not isinstance(table, dict):
        return None
    value = table.get("db_path")
    return str(value) if value else None


def write_report(
    intervals: Sequence[Interval],
    *,
    style: str = "cyberpunk",
    fmt: str = "html",
    path: str | Path,
    rate: float = 0.0,
    brand: BrandConfig | None = None,
) -> Path:
    """Render and write the report to ``path``; returns the written path.

    This is the only impure entry point: it touches the filesystem and, for PDF,
    renders via headless Chromium (Playwright). When ``brand`` is ``None`` the
    user's branding is loaded from disk (:func:`load_brand_config`). Raises
    :class:`ReportError` on failure.
    """
    if brand is None:
        brand = load_brand_config()
    out = Path(path).expanduser()
    if fmt == "text":
        out.write_text(render_report_text(intervals, rate=rate, brand=brand), encoding="utf-8")
        return out
    html_text = render_report_html(intervals, style=style, fmt=fmt, rate=rate, brand=brand)
    if fmt == "pdf":
        _html_to_pdf(html_text, out)
    else:
        out.write_text(html_text, encoding="utf-8")
    return out
