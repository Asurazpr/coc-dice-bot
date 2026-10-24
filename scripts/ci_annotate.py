"""Summarize a pytest JUnit XML report as GitHub Actions annotations.

Emits one notice with the pass/fail/skip counts and an error annotation per failing test.

Used by CI so test failures are visible in the Checks summary (and via the
check-runs API) without opening the job log.

    python scripts/ci_annotate.py pytest-report.xml
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

MAX_ANNOTATIONS = 10        # GitHub shows at most 10 error annotations per step
MAX_CHARS = 3000


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(path: str) -> None:
    root = ET.parse(path).getroot()
    failures = []
    for case in root.iter("testcase"):
        for tag in ("failure", "error"):
            node = case.find(tag)
            if node is not None:
                name = f"{case.get('classname', '')}.{case.get('name', '')}"
                detail = (node.get("message") or "") + "\n" + (node.text or "")
                failures.append((name, detail.strip()))
    cases = list(root.iter("testcase"))
    skipped = sum(1 for c in cases if c.find("skipped") is not None)
    passed = len(cases) - skipped - len(failures)
    print(f"::notice title=pytest summary::{passed} passed, {len(failures)} failed, {skipped} skipped "
          f"({len(cases)} tests)")
    for name, detail in failures[:MAX_ANNOTATIONS]:
        tail = detail[-MAX_CHARS:]          # the end of a traceback is the useful part
        print(f"::error title={_escape(name)}::{_escape(tail)}")
    if len(failures) > MAX_ANNOTATIONS:
        rest = ", ".join(n for n, _ in failures[MAX_ANNOTATIONS:])
        print(f"::error title=More failing tests::{_escape(rest[:MAX_CHARS])}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pytest-report.xml")
