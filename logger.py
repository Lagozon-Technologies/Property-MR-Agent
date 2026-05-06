# logger.py
# Global logging setup — one shared rotating log file per run.

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_LOGS_DIR  = Path("logs")
_ROOT_NAME = "oracle_bot"


def _bootstrap() -> logging.Logger:
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = _LOGS_DIR / f"log_{timestamp}.log"

    root = logging.getLogger(_ROOT_NAME)
    if root.handlers:
        return root

    root.setLevel(logging.DEBUG)
    root.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    root.addHandler(fh)
    root.addHandler(ch)
    root.info(f"[Logger] Bootstrapped → {log_file}")
    return root


_root_logger = _bootstrap()


def get_logger(name: str = _ROOT_NAME) -> logging.Logger:
    if name == _ROOT_NAME:
        return _root_logger
    child = logging.getLogger(f"{_ROOT_NAME}.{name}")
    child.setLevel(logging.DEBUG)
    return child


# Convenience alias kept for compatibility with old modules
def setup_logger(name: str) -> logging.Logger:
    return get_logger(name)


logger = get_logger()
