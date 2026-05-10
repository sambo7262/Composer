"""Phase 6 D-33: sklearn allowlist test (extends Phase 5 ``test_no_sklearn_import``).

Phase 5 forbade sklearn in ``taste_profile_service.py`` specifically. Phase 6
broadens the rule:

    sklearn imports are allowed ONLY in files matching one of:
      - ``vibe_clusterer.py``
      - ``clustering*.py``
      - ``clusterer*.py``

This test scans the whole ``app/services/`` tree and fails if sklearn appears
outside the allowlist. Plan 02 will create ``vibe_clusterer.py`` with sklearn
imports — this test must already exist and pass before that import is added;
it is the gate that prevents accidental sklearn leakage into other service
files (taste_profile_service, event_handlers, anthropic_client, etc.).

Today (Plan 01), no service file imports sklearn at all. This test passes
trivially. Add a sklearn import to any non-allowlist file and the test trips
red — that's the whole point.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SERVICES_DIR = Path(__file__).parent.parent / "app" / "services"
ALLOWLIST_RE = re.compile(r"^(vibe_clusterer\.py|clustering.*\.py|clusterer.*\.py)$")


def test_sklearn_only_in_clusterer_module():
    """Every app/services/*.py file is sklearn-free unless it's on the allowlist."""
    violations: list[str] = []
    for py_file in SERVICES_DIR.glob("*.py"):
        if ALLOWLIST_RE.match(py_file.name):
            continue
        source = py_file.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "sklearn" in alias.name:
                        violations.append(f"{py_file.name}: import {alias.name}")
            if isinstance(node, ast.ImportFrom):
                if node.module and "sklearn" in node.module:
                    violations.append(f"{py_file.name}: from {node.module}")
    assert violations == [], (
        "sklearn imports outside vibe_clusterer.py allowlist:\n"
        + "\n".join(violations)
    )
