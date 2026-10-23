"""A badge for a README that shows the result of a verification: ``sciproof badge``.

The badge is a static SVG written by the program itself, so it needs no service and no network, and
nothing from the manifest is put into it: only the verdict, whether the command was re-executed,
and the date of the verification. It says ``integrity`` or ``rerun`` because those are different
claims. A badge is a picture of one verification at one time; keep it honest by writing it in the CI
job that verifies (see docs/BADGE.md), and never by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime
from xml.sax.saxutils import escape

from sciproof.verify import Status

_COLOURS = {
    Status.PASS: "#2ea44f",
    Status.FAIL: "#cf222e",
    Status.INCONCLUSIVE: "#bf8700",
}
_LABEL = "sciproof"
_CHAR_WIDTH = 6.6  # approximate width of one character of 11 px Verdana
_PADDING = 10


def render_badge(status: Status, *, rerun: bool, when: datetime | None = None) -> str:
    """The SVG text of a badge for ``status`` (``PASS``, ``FAIL`` or ``INCONCLUSIVE``)."""
    day = (when or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d")
    message = f"{status.value} · {'rerun' if rerun else 'integrity'} · {day}"
    left = round(len(_LABEL) * _CHAR_WIDTH + _PADDING * 2)
    right = round(len(message) * _CHAR_WIDTH + _PADDING * 2)
    width = left + right
    colour = _COLOURS[status]
    description = escape(f"{_LABEL}: {message}")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" '
        f'aria-label="{description}">\n'
        f"  <title>{description}</title>\n"
        '  <linearGradient id="s" x2="0" y2="100%">'
        '<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        '<stop offset="1" stop-opacity=".1"/></linearGradient>\n'
        f'  <clipPath id="r"><rect width="{width}" height="20" rx="3" fill="#fff"/></clipPath>\n'
        '  <g clip-path="url(#r)">\n'
        f'    <rect width="{left}" height="20" fill="#555"/>\n'
        f'    <rect x="{left}" width="{right}" height="20" fill="{colour}"/>\n'
        f'    <rect width="{width}" height="20" fill="url(#s)"/>\n'
        "  </g>\n"
        '  <g fill="#fff" text-anchor="middle" '
        'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">\n'
        f'    <text x="{left / 2:.0f}" y="14">{_LABEL}</text>\n'
        f'    <text x="{left + right / 2:.0f}" y="14">{escape(message)}</text>\n'
        "  </g>\n"
        "</svg>\n"
    )
