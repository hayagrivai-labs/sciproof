"""JUnit XML for the reports, so that CI systems that show test results can show these checks.

Each check of a verification is a test case: ``PASS`` passes, ``FAIL`` is a failure, and
``INCONCLUSIVE`` is reported as skipped *with its reason*, because the check could not be carried
out (it is never a pass; the exit status of ``sciproof`` is still 2). Text that comes from a
manifest is escaped by the XML library, and characters XML 1.0 cannot carry are replaced.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405  (only used to write XML, never to parse it)
from typing import TYPE_CHECKING

from sciproof.textutil import safe_text

if TYPE_CHECKING:
    from sciproof.ledger import Entry
    from sciproof.verify import Report

_INVALID_XML = re.compile("[^\x09\x0a\x0d\x20-퟿-�\U00010000-\U0010ffff]")
MAX_TEXT = 2000


def _clean(text: object, limit: int = MAX_TEXT) -> str:
    return _INVALID_XML.sub("?", safe_text(str(text), limit))


def _render(suite: ET.Element) -> str:
    root = ET.Element("testsuites")
    root.append(suite)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def report_junit(report: Report) -> str:
    """One test suite for a verification report."""
    failures = sum(1 for c in report.checks if c.status.value == "FAIL")
    skipped = sum(1 for c in report.checks if c.status.value == "INCONCLUSIVE")
    suite = ET.Element(
        "testsuite",
        name=f"sciproof:{_clean(report.name, 100)}",
        tests=str(len(report.checks)),
        failures=str(failures),
        errors="0",
        skipped=str(skipped),
        time="0",
    )
    properties = ET.SubElement(suite, "properties")
    for key, value in (("result", report.status.value), ("reason", report.reason)):
        ET.SubElement(properties, "property", name=key, value=_clean(value))
    for check in report.checks:
        case = ET.SubElement(
            suite, "testcase", classname="sciproof", name=_clean(check.subject, 300), time="0"
        )
        if check.status.value == "FAIL":
            failure = ET.SubElement(
                case, "failure", message=_clean(check.message, 500), type="FAIL"
            )
            failure.text = _clean(check.message)
        elif check.status.value == "INCONCLUSIVE":
            ET.SubElement(case, "skipped", message="INCONCLUSIVE: " + _clean(check.message, 500))
    out = ET.SubElement(suite, "system-out")
    out.text = "\n".join(
        [f"RESULT: {report.status.value}", f"basis: {_clean(report.reason)}"]
        + [f"warning: {_clean(w)}" for w in report.warnings]
        + [f"not checked: {_clean(item)}" for item in report.not_checked]
    )
    return _render(suite)


def list_junit(entries: list[Entry]) -> str:
    """One test suite for ``sciproof list --check``: a test case per manifest."""
    failures = sum(1 for e in entries if e.error or e.verdict == "FAIL")
    skipped = sum(1 for e in entries if not e.error and e.verdict == "INCONCLUSIVE")
    suite = ET.Element(
        "testsuite",
        name="sciproof:list",
        tests=str(len(entries)),
        failures=str(failures),
        errors="0",
        skipped=str(skipped),
        time="0",
    )
    for entry in entries:
        case = ET.SubElement(
            suite, "testcase", classname="sciproof.manifest", name=_clean(entry.file, 300), time="0"
        )
        if entry.error:
            ET.SubElement(
                case,
                "failure",
                message="invalid manifest: " + _clean(entry.error, 500),
                type="INVALID",
            )
        elif entry.verdict == "FAIL":
            ET.SubElement(case, "failure", message="verification failed", type="FAIL")
        elif entry.verdict == "INCONCLUSIVE":
            ET.SubElement(
                case, "skipped", message="INCONCLUSIVE: some evidence could not be checked"
            )
    return _render(suite)
