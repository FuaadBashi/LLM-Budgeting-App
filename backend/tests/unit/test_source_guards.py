"""Static policy locks for rules that must remain single-source."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"


def python_files(*folders: str):
    for folder in folders:
        yield from sorted((APP_ROOT / folder).rglob("*.py"))


def containing_function(tree: ast.AST, target: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if target in ast.walk(node):
                return node.name
    return None


def test_X8_planned_contribution_clamp_has_one_implementation():
    matches = []
    for path in python_files("domain"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "max":
                continue
            if any(
                isinstance(child, ast.Attribute)
                and child.attr == "planned_contribution"
                for child in ast.walk(node)
            ):
                matches.append((path, containing_function(tree, node)))

    assert matches == [
        (APP_ROOT / "domain" / "disposable.py", "planned_contributions_split")
    ]


def test_X9_reporting_day_never_uses_the_server_local_date():
    violations = []
    for path in python_files("domain", "api"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            is_date_today = (
                isinstance(owner, ast.Name)
                and owner.id == "date"
                and node.func.attr == "today"
            )
            is_datetime_now = (
                isinstance(owner, ast.Name)
                and owner.id == "datetime"
                and node.func.attr == "now"
            )
            clock_source = path == APP_ROOT / "domain" / "clock.py"
            if is_date_today or (is_datetime_now and not clock_source):
                violations.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno}")

    assert violations == []
