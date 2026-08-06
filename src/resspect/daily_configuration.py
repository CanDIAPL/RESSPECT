"""Configuration loading for the daily RESSPECT command."""

from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass(frozen=True)
class DailyConfiguration:
    """Configuration for one daily RESSPECT run."""

    fastdb_profile: str
    processing_version: str
    requester: str
    state_dir: Path
    fastdb_client_path: Optional[Path] = None
    bootstrap_features: Optional[Path] = None
    detected_in_last_days: int = 14
    batch_size: int = 5
    mjd_now: Optional[float] = None


def _path(value, config_dir):
    if value is None:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (config_dir / path).resolve()


def load_configuration(path):
    """Load the daily-run YAML configuration."""

    path = Path(path).expanduser().resolve()
    with path.open(encoding="utf-8") as stream:
        values = yaml.safe_load(stream) or {}

    required = {"fastdb_profile", "processing_version", "requester", "state_dir"}
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError(f"Missing required configuration values: {', '.join(missing)}")

    detected_in_last_days = int(values.get("detected_in_last_days", 14))
    batch_size = int(values.get("batch_size", 5))
    if detected_in_last_days <= 0 or batch_size <= 0:
        raise ValueError("detected_in_last_days and batch_size must be positive")

    return DailyConfiguration(
        fastdb_profile=str(values["fastdb_profile"]),
        fastdb_client_path=_path(values.get("fastdb_client_path"), path.parent),
        processing_version=str(values["processing_version"]),
        requester=str(values["requester"]),
        state_dir=_path(values["state_dir"], path.parent),
        bootstrap_features=_path(values.get("bootstrap_features"), path.parent),
        detected_in_last_days=detected_in_last_days,
        batch_size=batch_size,
        mjd_now=None if values.get("mjd_now") is None else float(values["mjd_now"]),
    )


def connect_to_fastdb(config):
    """Create a FASTDB client using the configured client directory and profile."""

    if config.fastdb_client_path is not None:
        if not config.fastdb_client_path.is_dir():
            raise ValueError(f"FASTDB client directory does not exist: {config.fastdb_client_path}")
        sys.path.insert(0, str(config.fastdb_client_path))

    try:
        fastdb_client = importlib.import_module("fastdb_client")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Could not import fastdb_client. Configure fastdb_client_path or add the client to PYTHONPATH."
        ) from error
    return fastdb_client.FASTDBClient(config.fastdb_profile)


def configure_logging(config, run_id):
    """Configure terminal and persistent file logging."""

    log_dir = config.state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"resspect-daily-{run_id}.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    for handler in (logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")):
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    return log_path
