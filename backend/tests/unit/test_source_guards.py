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


def test_the_demo_seed_writes_no_fixed_dates():
    """The demo must be dated from today, never from a written-down day.

    Seeded as "31 August 2026", the demo drifted into the past a day at a time:
    a month that ended long ago, no income expected, no days remaining, nothing
    to project. The data looked broken when only the calendar had moved. The fix
    is only durable if a future edit cannot quietly reintroduce a literal.

    ``date(...)`` calls whose arguments are all literals are the ones that
    freeze; ``date(index // 12, index % 12 + 1, 1)`` is derived and fine.
    """
    seed = BACKEND_ROOT / "scripts" / "seed_demo.py"
    tree = ast.parse(seed.read_text())

    frozen = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "date"
        and node.args
        and all(isinstance(a, ast.Constant) for a in node.args)
    ]
    assert frozen == [], f"fixed dates would drift out of date: {frozen}"
