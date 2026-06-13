from __future__ import annotations

import json
import os
from pathlib import Path


class FileEventSink:
    """Minimal append-only NDJSON event sink (§3.1). The bounded-queue background
    writer + fsync-on-pause is Spike 4's charter; this is the seam."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.write_text("")
        os.chmod(self.path, 0o600)

    def __call__(self, envelope: dict) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(envelope) + "\n")
