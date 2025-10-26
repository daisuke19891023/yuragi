"""Entry point for the yuragi interfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from yuragi.core.errors import ExposureConfigurationError
from yuragi.interfases.factory import resolve_exposure_from_environment

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any
    from collections.abc import Mapping


def main(argv: Sequence[str] | None = None) -> NoReturn:
    """Resolve the requested exposure and delegate execution to it."""
    try:
        exposure = resolve_exposure_from_environment()
    except ExposureConfigurationError as error:
        raise SystemExit(str(error)) from error

    config: Mapping[str, Any] | None = None
    if argv is not None:
        config = {"argv": list(argv)}

    exposure.serve(config=config)
    raise SystemExit(0)


if __name__ == "__main__":  # pragma: no cover - manual execution guard
    main()
