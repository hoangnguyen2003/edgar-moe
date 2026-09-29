"""Keep the public API process detached from the private forecast registry."""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "src/edgar_moe/api/app.py"
_PRIVATE_REGISTRY_SYMBOLS = {
    "ForwardRegistry",
    "ForwardRegistryDependency",
    "RegistryDatabase",
    "SQLAlchemyError",
    "api_registry_database_url",
    "forward_database",
    "forward_registry",
    "get_forward_registry",
}


def test_public_api_has_no_private_registry_import_or_dependency() -> None:
    tree = ast.parse(_APP.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name if isinstance(node, ast.Import) else (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    imported_symbols = {
        alias.name.rsplit(".", 1)[-1]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    referenced_symbols = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert not (_PRIVATE_REGISTRY_SYMBOLS & imported_symbols)
    assert not (_PRIVATE_REGISTRY_SYMBOLS & referenced_symbols)
    assert not any(module.startswith("edgar_moe.forward") for module in imported_modules)
