"""Bounded atomic replacement for short-lived Windows reader/indexer locks."""

import os
import time
from pathlib import Path


def replace_file(source: Path, target: Path) -> None:
    for attempt in range(4):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 3:
                raise
            time.sleep(0.1 * (2 ** attempt))
