"""ThermoFlow development server entry point."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from .settings import Settings


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")
    settings = Settings.from_env(project_root)
    uvicorn.run(
        "thermoflow.api:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
