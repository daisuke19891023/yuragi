"""Entry point for the yuragi CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from yuragi.interfases.cli.app import main as cli_main

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> NoReturn:
    """Delegate to the CLI implementation and terminate with its exit code."""
    raise SystemExit(cli_main(argv))


if __name__ == "__main__":  # pragma: no cover - manual execution guard
    main()
