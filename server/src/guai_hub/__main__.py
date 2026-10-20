"""Entry point for the ``server`` / ``guai-hub`` console scripts and ``python -m guai_hub``."""

from __future__ import annotations

import uvicorn

from guai_hub.config import get_settings
from guai_hub.main import create_app


def main() -> None:
    settings = get_settings()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=8000, workers=1)


if __name__ == "__main__":
    main()
