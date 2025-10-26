"""Nox configuration for the project."""

import pathlib
import platform
import sys
from pathlib import Path

import nox
from nox.sessions import Session

# nox usage example
# @nox.session(python=["3.13"], venv_backend="uv", tags=["example"])
# def example(session: Session) -> None:
#     session.install("-c", constraints(session).as_posix(), ".[AAA]")  # noqa: ERA001
#     session.run("EXAMPLE_COMMAND")    # noqa: ERA001

nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True

# Coverage threshold
COVER_MIN = 60


def has_test_targets() -> bool:
    """Check if there are any Python files in the src directory to test.

    Returns:
        bool: True if test target files exist, False otherwise.

    """
    src_path = pathlib.Path("src")
    if not src_path.exists():
        return False

    # Return True if any .py files exist in src directory (recursive search)
    return any(src_path.glob("**/*.py"))


def constraints(session: Session) -> Path:
    """Generate constraints file path for the session."""
    # Automatically create constraints file name
    filename = f"python{session.python}-{sys.platform}-{platform.machine()}.txt"
    return Path("constraints", filename)


@nox.session(python=["3.13"], venv_backend="uv")
def lock(session: Session) -> None:
    """Lock dependencies."""
    filename = constraints(session)
    filename.parent.mkdir(exist_ok=True)
    session.run(
        "uv",
        "pip",
        "compile",
        "pyproject.toml",
        "--upgrade",
        "--quiet",
        "--all-extras",
        f"--output-file={filename}",
    )


@nox.session(python=["3.13"], tags=["lint"])
def lint(session: Session) -> None:
    """Run linting with Ruff."""
    session.install("-c", constraints(session).as_posix(), "ruff")
    session.run("ruff", "check", "--fix")


@nox.session(python=["3.13"], tags=["format"])
def format_code(session: Session) -> None:
    """Format code with Ruff."""
    session.install("-c", constraints(session).as_posix(), "ruff")
    session.run("ruff", "format")


@nox.session(python=["3.13"], tags=["sort"])
def sort(session: Session) -> None:
    """Sort imports with Ruff."""
    session.install("-c", constraints(session).as_posix(), "ruff")
    session.run("ruff", "check", "--select", "I", "--fix")


@nox.session(python=["3.13"], tags=["typing"])
def typing(session: Session) -> None:
    """Run type checking with Pyright."""
    session.install("-c", constraints(session).as_posix(), ".[dev]")
    session.run("pyright")


@nox.session(python=["3.13"], tags=["test"])
def test(session: Session) -> None:
    """Run pytest if test target files exist in src directory.

    Skip otherwise.
    """
    if not has_test_targets():
        session.skip("No test targets found in src directory")

    session.install("-c", constraints(session).as_posix(), ".[dev]")
    session.run("pytest", "--cov=src", f"--cov-fail-under={COVER_MIN}")


@nox.session(python=["3.13"], tags=["ci"])
def ci(session: Session) -> None:
    """Run all CI checks: lint, format, typing, test, security."""
    session.notify("lint")
    session.notify("sort")
    session.notify("format_code")
    session.notify("typing")
    session.notify("test")


@nox.session(python=["3.13"], tags=["all"])
def all_checks(session: Session) -> None:
    """Run all quality checks.

    ci, docs.
    """
    session.notify("ci")
