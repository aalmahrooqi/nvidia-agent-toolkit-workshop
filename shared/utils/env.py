"""Environment variable loading and API key validation."""

import os
from pathlib import Path

from colorama import Fore, Style
from dotenv import load_dotenv


def load_env() -> None:
    """Load environment variables from .env file at the repository root.

    Walks up from this file's location to find the directory containing
    pyproject.toml, then loads .env from that directory.
    """
    # Find repo root by walking up from this file
    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").is_file():
            env_file = parent / ".env"
            if env_file.is_file():
                load_dotenv(env_file)
            return

    # Fallback: try python-dotenv's default search
    load_dotenv()


def validate_keys(
    required: list[str],
    optional: list[str] | None = None,
) -> dict[str, str | None]:
    """Validate that required API keys are present in the environment.

    Args:
        required: Keys that must be set. Raises ValueError if any are missing.
        optional: Keys that are checked but only warned if missing.

    Returns:
        Dict mapping all checked key names to their values (None if missing).

    Raises:
        ValueError: If any required keys are missing from the environment.
    """
    optional = optional or []
    result: dict[str, str | None] = {}
    missing_required: list[str] = []

    for key in required:
        raw = os.environ.get(key)
        val = raw.strip() if raw is not None else None
        if val:
            result[key] = val
            print(f"  {Fore.GREEN}OK{Style.RESET_ALL} {key} configured")
        else:
            result[key] = None
            missing_required.append(key)
            print(f"  {Fore.RED}MISSING{Style.RESET_ALL} {key}")

    for key in optional:
        raw = os.environ.get(key)
        val = raw.strip() if raw is not None else None
        if val:
            result[key] = val
            print(f"  {Fore.GREEN}OK{Style.RESET_ALL} {key} configured")
        else:
            result[key] = None
            print(f"  {Fore.YELLOW}WARN{Style.RESET_ALL} {key} not set (optional)")

    if missing_required:
        raise ValueError(
            f"Missing required environment keys: {', '.join(missing_required)}. "
            "Set them in your .env file at the repo root."
        )

    return result
