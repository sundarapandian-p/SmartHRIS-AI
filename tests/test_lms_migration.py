"""Read-only checks for the LMS courses Alembic migration structure."""

from __future__ import annotations

import ast
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20261109_0006_lms_courses.py"
)


def _load_migration_source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_file_exists() -> None:
    assert MIGRATION_PATH.is_file()


def test_migration_revision_chain() -> None:
    namespace: dict[str, object] = {}
    exec(compile(_load_migration_source(), str(MIGRATION_PATH), "exec"), namespace)
    assert namespace["revision"] == "20261109_0006"
    assert namespace["down_revision"] == "20261109_0005"


def test_migration_creates_only_lms_tables() -> None:
    tree = ast.parse(_load_migration_source())
    created_tables: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name == "create_table" and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                created_tables.append(arg0.value)

    assert created_tables == ["courses", "course_enrolments"]

    upgrade_fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    upgrade_src = ast.unparse(upgrade_fn)
    assert "drop_table" not in upgrade_src
    assert "add_column" not in upgrade_src
    assert "alter_column" not in upgrade_src
    assert "rename_table" not in upgrade_src
    # FK references are allowed; structural alterations of existing tables are not.
    assert "op.add_column" not in upgrade_src
    assert 'create_table(\n        "employees"' not in upgrade_src
    assert 'create_table(\n        "import_jobs"' not in upgrade_src
    assert 'create_table(\n        "departments"' not in upgrade_src
    assert 'create_table(\n        "hr_documents"' not in upgrade_src
