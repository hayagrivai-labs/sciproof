"""Single-file self-contained HTML verification report with visual tolerance meters."""

from __future__ import annotations

import html
import json
import math
from typing import TYPE_CHECKING, Any

from sciproof import __version__
from sciproof.textutil import safe_text

if TYPE_CHECKING:
    from sciproof.verify import Report

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SciProof Verification: {name}</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --pass: #10b981;
      --pass-bg: rgba(16, 185, 129, 0.15);
      --fail: #ef4444;
      --fail-bg: rgba(239, 68, 68, 0.15);
      --inc: #f59e0b;
      --inc-bg: rgba(245, 158, 11, 0.15);
      --accent: #3b82f6;
    }}
    @media (prefers-color-scheme: light) {{
      :root {{
        --bg: #f8fafc;
        --card-bg: #ffffff;
        --border: #e2e8f0;
        --text: #0f172a;
        --text-muted: #64748b;
        --pass: #059669;
        --pass-bg: #ecfdf5;
        --fail: #dc2626;
        --fail-bg: #fef2f2;
        --inc: #d97706;
        --inc-bg: #fffbeb;
        --accent: #2563eb;
      }}
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 2rem 1rem;
    }}
    .container {{ max-width: 960px; margin: 0 auto; }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 2rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1rem;
    }}
    .brand {{ font-size: 1.25rem; font-weight: 700; letter-spacing: -0.02em; }}
    .badge {{
      display: inline-block;
      padding: 0.35rem 0.85rem;
      border-radius: 9999px;
      font-weight: 700;
      font-size: 0.875rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .badge-pass {{ background: var(--pass-bg); color: var(--pass); border: 1px solid var(--pass); }}
    .badge-fail {{ background: var(--fail-bg); color: var(--fail); border: 1px solid var(--fail); }}
    .badge-inc {{ background: var(--inc-bg); color: var(--inc); border: 1px solid var(--inc); }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
      margin-top: 1rem;
    }}
    .meta-item {{ font-size: 0.875rem; }}
    .meta-label {{
      color: var(--text-muted);
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .meta-value {{ font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    h2 {{ font-size: 1.15rem; margin-bottom: 1rem; color: var(--text); }}
    table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 0.875rem; }}
    th, td {{ padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); }}
    th {{
      color: var(--text-muted);
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    tr:last-child td {{ border-bottom: none; }}
    .subject {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 500; }}
    .message {{ color: var(--text-muted); }}
    .scope-box {{
      padding: 0.75rem 1rem;
      border-radius: 0.5rem;
      background: rgba(59, 130, 246, 0.1);
      border: 1px solid rgba(59, 130, 246, 0.25);
      margin-top: 1rem;
      font-size: 0.875rem;
    }}
    .gauge {{
      width: 100px;
      height: 8px;
      background: var(--border);
      border-radius: 4px;
      overflow: hidden;
      display: inline-block;
      vertical-align: middle;
      margin-right: 0.5rem;
    }}
    .gauge-fill {{ height: 100%; background: var(--pass); transition: width 0.2s; }}
    .gauge-fill.exceeded {{ background: var(--fail); }}
    .slider-group {{
      display: flex;
      align-items: center;
      gap: 1rem;
      margin: 0.75rem 0;
      font-size: 0.875rem;
    }}
    .slider-group label {{ min-width: 120px; color: var(--text-muted); }}
    .slider-group input[type="range"] {{ flex: 1; accent-color: var(--accent); }}
    .slider-val {{ font-family: ui-monospace, monospace; min-width: 70px; font-weight: 600; }}
    ul.check-list {{ list-style-type: none; padding-left: 0; }}
    ul.check-list li {{ padding: 0.35rem 0; font-size: 0.875rem; color: var(--text-muted); }}
    ul.check-list li::before {{ content: "• "; color: var(--text-muted); }}
    footer {{
      margin-top: 3rem;
      text-align: center;
      font-size: 0.75rem;
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">SciProof <span>v{version}</span></div>
      <div class="badge {status_class}">{status}</div>
    </header>

    <div class="card">
      <h2>Experiment Verification: {name}</h2>
      <p style="color: var(--text-muted); font-size: 0.875rem;">{reason}</p>
      <div class="scope-box">{scope_note}</div>
      <div class="meta-grid">
        <div class="meta-item">
          <div class="meta-label">Total Checks</div>
          <div class="meta-value">{total_checks}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Passed</div>
          <div class="meta-value" style="color: var(--pass);">{passed_count}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Failed</div>
          <div class="meta-value" style="color: var(--fail);">{failed_count}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Inconclusive</div>
          <div class="meta-value" style="color: var(--inc);">{inc_count}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Verification Checks</h2>
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Subject</th>
            <th>Check Message</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>

    {numeric_tables}

    {warnings_section}

    {notes_section}

    {not_checked_section}

    <footer>
      Verified by
      <a href="https://github.com/hayagrivai-labs/sciproof" style="color: inherit;">SciProof</a>
      &bull; Zero-Dependency Scientific Reproducibility
    </footer>
  </div>
  {script_block}
</body>
</html>
"""


# Log10 slider range: the lowest stop means exactly 0, then 1e-12 .. 1.
_SLIDER_ZERO = -13


def _slider_value(tol: float) -> str:
    """Slider position (log10) for a recorded tolerance; 0 maps to the lowest stop."""
    if not (math.isfinite(tol) and tol > 0):
        return str(_SLIDER_ZERO)
    return f"{min(0.0, max(-12.0, math.log10(tol))):.2f}"


def _tolerance_label(tol: float) -> str:
    """Label matching the slider's JavaScript formatting, e.g. ``1.0e-9`` or ``0``."""
    if not (math.isfinite(tol) and tol > 0):
        return "0"
    mantissa, exponent = f"{tol:.1e}".split("e")
    return f"{mantissa}e{int(exponent)}"


def _render_numeric_section(report: Report) -> tuple[str, str]:
    """Render interactive tolerance tables and slider controls for numeric checks."""
    tables: list[str] = []
    numeric_datasets: list[dict[str, Any]] = []

    for index, check in enumerate(report.checks):
        evidence = check.evidence or {}
        details = evidence.get("difference_details")
        if evidence.get("kind") != "rerun_output_numeric" or not details:
            continue

        path = html.escape(str(evidence.get("path", "numeric output")))
        rtol = float(evidence.get("rtol", 1e-9))
        atol = float(evidence.get("atol", 1e-9))

        rows_html: list[str] = []
        clean_details = []
        for d in details:
            if d.get("kind") == "summary":
                continue
            loc = html.escape(str(d.get("location") or "-"))
            rec = d.get("recorded")
            fnd = d.get("found")
            raw_adiff = d.get("abs_diff")
            # No numeric distance (a changed string, NaN, inf vs finite): not comparable, so it
            # is never shown as within tolerance.
            comparable = raw_adiff is not None
            adiff = float(raw_adiff) if comparable else None
            rdiff = float(d.get("rel_diff", 0.0) or 0.0)
            allowed = float(d.get("allowed", 0.0) or 0.0)
            if comparable:
                ratio = (adiff / allowed * 100) if allowed > 0 else 100.0
                gauge_pct = min(100.0, max(0.0, ratio))
                is_exceeded = adiff > allowed
            else:
                gauge_pct = 100.0
                is_exceeded = True

            clean_details.append(
                {
                    "loc": loc,
                    "recorded": rec,
                    "found": fnd,
                    "abs_diff": adiff,
                    "rel_diff": rdiff,
                }
            )

            gauge_cls = "gauge-fill exceeded" if is_exceeded else "gauge-fill"
            rows_html.append(
                f'<tr id="row-{index}-{loc}">'
                f'<td class="subject">{loc}</td>'
                f"<td>{rec}</td><td>{fnd}</td>"
                f"<td>{f'{adiff:.4g}' if comparable else 'n/a'}</td>"
                f"<td>{f'{rdiff:.4g}' if comparable else 'n/a'}</td>"
                f'<td><div class="gauge"><div class="{gauge_cls}" '
                f'style="width: {gauge_pct:.1f}%"></div></div>'
                f'<span class="meta-value" style="font-size:0.75rem">'
                f"{f'{allowed:.4g}' if comparable else 'n/a'}</span></td>"
                f"</tr>"
            )

        numeric_datasets.append(
            {
                "index": index,
                "path": path,
                "rtol": rtol,
                "atol": atol,
                "items": clean_details,
            }
        )

        tables.append(
            f'<div class="card" id="numeric-card-{index}">'
            f"<h2>Numeric Comparison: {path}</h2>"
            f'<div class="slider-group">'
            f"<label>Tolerance rtol (10<sup>x</sup>):</label>"
            f'<input type="range" id="rtol-slider-{index}" '
            f'min="{_SLIDER_ZERO}" max="0" step="0.01" '
            f'value="{_slider_value(rtol)}" oninput="updateTolerance({index})">'
            f'<span class="slider-val" id="rtol-val-{index}">{_tolerance_label(rtol)}</span>'
            f"</div>"
            f'<div class="slider-group">'
            f"<label>Tolerance atol (10<sup>x</sup>):</label>"
            f'<input type="range" id="atol-slider-{index}" '
            f'min="{_SLIDER_ZERO}" max="0" step="0.01" '
            f'value="{_slider_value(atol)}" oninput="updateTolerance({index})">'
            f'<span class="slider-val" id="atol-val-{index}">{_tolerance_label(atol)}</span>'
            f"</div>"
            f"<table><thead><tr>"
            f"<th>Location</th><th>Recorded</th><th>Found</th><th>Abs. Diff</th>"
            f"<th>Rel. Diff</th><th>Tolerance Meter</th>"
            f"</tr></thead><tbody>" + "\n".join(rows_html) + "</tbody></table></div>"
        )

    if not numeric_datasets:
        return "", ""

    script = f"""<script>
    const datasets = {json.dumps(numeric_datasets)};
    function updateTolerance(idx) {{
      const ds = datasets.find(d => d.index === idx);
      if (!ds) return;
      const rtolExp = parseFloat(document.getElementById(`rtol-slider-${{idx}}`).value);
      const atolExp = parseFloat(document.getElementById(`atol-slider-${{idx}}`).value);
      // The lowest slider stop means exactly 0 (atol=0 is the default and must be representable).
      const rtol = rtolExp <= {_SLIDER_ZERO} ? 0 : Math.pow(10, rtolExp);
      const atol = atolExp <= {_SLIDER_ZERO} ? 0 : Math.pow(10, atolExp);
      document.getElementById(`rtol-val-${{idx}}`).innerText =
        rtol === 0 ? '0' : rtol.toExponential(1);
      document.getElementById(`atol-val-${{idx}}`).innerText =
        atol === 0 ? '0' : atol.toExponential(1);

      ds.items.forEach(item => {{
        const row = document.getElementById(`row-${{idx}}-${{item.loc}}`);
        if (!row) return;
        const maxVal = Math.max(Math.abs(item.recorded || 0), Math.abs(item.found || 0));
        const allowed = Math.max(rtol * maxVal, atol);
        const comparable = item.abs_diff !== null;
        const ratio = comparable && allowed > 0 ? (item.abs_diff / allowed) * 100 : 100;
        const pct = Math.min(100, Math.max(0, ratio));
        const exceeded = !comparable || item.abs_diff > allowed;
        const gauge = row.querySelector('.gauge-fill');
        if (gauge) {{
          gauge.style.width = pct.toFixed(1) + '%';
          gauge.className = 'gauge-fill' + (exceeded ? ' exceeded' : '');
        }}
        const valSpan = row.querySelector('.meta-value');
        if (valSpan) valSpan.innerText = comparable ? allowed.toExponential(2) : 'n/a';
      }});
    }}
  </script>"""
    return "\n".join(tables), script


def report_html(report: Report) -> str:
    """Generate self-contained HTML verification report."""
    status_str = report.status.value
    status_class = f"badge-{status_str.lower()[:4]}"
    if status_str == "INCONCLUSIVE":
        status_class = "badge-inc"

    passed_count = sum(1 for c in report.checks if c.status.value == "PASS")
    failed_count = sum(1 for c in report.checks if c.status.value == "FAIL")
    inc_count = sum(1 for c in report.checks if c.status.value == "INCONCLUSIVE")

    if report.rerun_executed:
        scope_note = (
            "<strong>Rerun scope:</strong> The command recorded in the manifest was "
            "re-executed with the current environment."
        )
    else:
        scope_note = (
            "<strong>Integrity scope:</strong> The recorded command was <em>NOT</em> executed. "
            "Only hash and structural integrity of declared files were verified."
        )

    rows = []
    for check in report.checks:
        c_status = check.status.value
        c_class = f"badge-{c_status.lower()[:4]}"
        if c_status == "INCONCLUSIVE":
            c_class = "badge-inc"
        subject = html.escape(safe_text(check.subject, 200))
        message = html.escape(safe_text(check.message, 1000))
        rows.append(
            f'<tr><td><span class="badge {c_class}">{c_status}</span></td>'
            f'<td class="subject">{subject}</td>'
            f'<td class="message">{message}</td></tr>'
        )

    warnings_section = ""
    if report.warnings:
        warn_items = "".join(f"<li>{html.escape(safe_text(w, 500))}</li>" for w in report.warnings)
        warnings_section = (
            f'<div class="card" style="border-left: 4px solid var(--inc);">'
            f'<h2>Warnings</h2><ul class="check-list">{warn_items}</ul></div>'
        )

    notes_section = ""
    notes = list(report.info)
    if report.rerun_outputs_dir:
        notes.append(f"files produced by the rerun were moved to {report.rerun_outputs_dir}/")
    if notes:
        note_items = "".join(f"<li>{html.escape(safe_text(n, 1000))}</li>" for n in notes)
        notes_section = (
            f'<div class="card" id="notes">'
            f'<h2>Notes</h2><ul class="check-list">{note_items}</ul></div>'
        )

    not_checked_section = ""
    if report.not_checked:
        not_checked_items = "".join(
            f"<li>{html.escape(safe_text(item, 1000))}</li>" for item in report.not_checked
        )
        not_checked_section = (
            f'<div class="card" id="not-checked">'
            f'<h2>Not checked</h2><ul class="check-list">{not_checked_items}</ul></div>'
        )

    numeric_tables, script_block = _render_numeric_section(report)

    return HTML_TEMPLATE.format(
        version=__version__,
        name=html.escape(safe_text(report.name, 100)),
        status=status_str,
        status_class=status_class,
        reason=html.escape(safe_text(report.reason, 300)),
        scope_note=scope_note,
        total_checks=len(report.checks),
        passed_count=passed_count,
        failed_count=failed_count,
        inc_count=inc_count,
        rows="\n".join(rows),
        numeric_tables=numeric_tables,
        warnings_section=warnings_section,
        notes_section=notes_section,
        not_checked_section=not_checked_section,
        script_block=script_block,
    )


def format_report_html(report: Report) -> str:
    return report_html(report) + "\n"
