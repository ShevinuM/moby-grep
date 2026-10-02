"""The package is installed, typed, and declares its entry points."""

import importlib
from importlib import metadata, resources

AREAS = ("shared", "ingestion", "detection", "search", "inference", "admin")
SHARED_PACKAGES = ("db", "db.migrations", "queue", "storage")
SCRIPTS = {
    "mobygrep-ingestor": "mobygrep.ingestion.main:main",
    "mobygrep-worker": "mobygrep.detection.main:main",
    "mobygrep-api": "mobygrep.search.main:main",
    "mobygrep-admin": "mobygrep.admin.cli:main",
}


def _import_package(name: str) -> None:
    """Import `name` and require it to be a regular package.

    A directory without `__init__.py` still imports, as a namespace package,
    and a namespace package has no `__file__`.
    """
    module = importlib.import_module(name)
    assert module.__file__ is not None, f"{name} has no __init__.py"


def test_area_packages_import() -> None:
    """`import mobygrep` works; every area and every shared sub-package imports."""
    _import_package("mobygrep")
    for area in AREAS:
        _import_package(f"mobygrep.{area}")
    for package in SHARED_PACKAGES:
        _import_package(f"mobygrep.shared.{package}")


def test_package_ships_py_typed() -> None:
    """`py.typed` is a file inside the installed `mobygrep` package."""
    assert resources.files("mobygrep").joinpath("py.typed").is_file()


def test_console_scripts_declared() -> None:
    """The installed package declares exactly SCRIPTS, each with its target.

    The entry points are read from metadata and not loaded: their target
    modules do not exist yet.
    """
    entry_points = metadata.distribution("mobygrep").entry_points
    declared = {
        entry_point.name: entry_point.value
        for entry_point in entry_points.select(group="console_scripts")
    }
    assert declared == SCRIPTS


def test_version_readable_from_metadata() -> None:
    """`importlib.metadata.version("mobygrep")` returns a non-empty string.

    The build-info metric will read the version the same way.
    """
    assert metadata.version("mobygrep")
