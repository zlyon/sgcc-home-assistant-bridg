"""Compatibility helpers for the legacy scripts/ import layout."""

from pathlib import Path
import importlib
import sys


def _ensure_project_root() -> None:
    here = Path(__file__).resolve().parent
    for root in (here, here.parent):
        if (root / "sgcc_ha_bridge").is_dir() and str(root) not in sys.path:
            sys.path.insert(0, str(root))
            break


def load(module_name: str):
    _ensure_project_root()
    return importlib.import_module(f"sgcc_ha_bridge.{module_name}")


def alias(module_name: str, legacy_name: str) -> None:
    sys.modules[legacy_name] = load(module_name)


def run(module_name: str) -> None:
    load(module_name).main()
