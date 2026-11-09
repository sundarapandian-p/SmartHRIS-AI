"""Read-only checks for the HR documents Alembic migration structure."""

from __future__ import annotations

import ast
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20261109_0005_hr_documents.py"
)


def _load_migration_source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_file_exists() -> None:
    assert MIGRATION_PATH.is_file()


def test_migration_revision_chain() -> None:
    namespace: dict[str, object] = {}
    exec(compile(_load_migration_source(), str(MIGRATION_PATH), "exec"), namespace)
    assert namespace["revision"] == "20261109_0005"
    assert namespace["down_revision"] == "20261101_0004"


def test_migration_creates_only_hr_document_tables() -> None:
    tree = ast.parse(_load_migration_source())
    created_tables: list[str] = []
    altered_or_dropped: list[str] = []

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
        if name in {"drop_table", "add_column", "drop_column", "alter_column", "rename_table"}:
            if node.args and isinstance(node.args[0], ast.Constant):
                altered_or_dropped.append(f"{name}:{node.args[0].value}")

    assert created_tables == ["hr_documents", "hr_document_chunks"]
    # upgrade must not alter existing employee/import tables; drop_table only in downgrade
    upgrade_fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    upgrade_src = ast.unparse(upgrade_fn)
    assert "employees" not in upgrade_src
    assert "import_jobs" not in upgrade_src
    assert "import_errors" not in upgrade_src
    assert "departments" not in upgrade_src
    assert "drop_table" not in upgrade_src
    assert "add_column" not in upgrade_src
