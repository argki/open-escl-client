"""Initial smoke tests for package structure and entry point."""

from __future__ import annotations

import importlib

import open_escl_client
from open_escl_client.main import main


def test_version_defined() -> None:
    assert open_escl_client.__version__ == "0.1.0"


def test_subpackages_importable() -> None:
    subpackages = [
        "open_escl_client.core",
        "open_escl_client.escl",
        "open_escl_client.imaging",
        "open_escl_client.config",
        "open_escl_client.ui",
    ]
    for module_name in subpackages:
        module = importlib.import_module(module_name)
        assert module is not None


def test_cli_version_flag(capsys) -> None:
    exit_code = main(["--version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "0.1.0" in captured.out
