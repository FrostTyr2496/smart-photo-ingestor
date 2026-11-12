"""JSON-lines protocol v1 (fnd-6). See system-design §10."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_RED, EXIT_PAUSED = 0, 1, 2, 3, 4


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000
    return f"{n:.1f} TB"


class Emitter:
    """Writes events as JSON lines (`json_mode=True`) or as short human-readable lines."""

    def __init__(self, stream: TextIO = sys.stdout, json_mode: bool = True) -> None:
        self.stream = stream
        self.json_mode = json_mode

    def emit(self, event: str, **fields: Any) -> None:
        if self.json_mode:
            payload = {"v": 1, "event": event, "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), **fields}
            self.stream.write(json.dumps(payload, default=_default) + "\n")
        else:
            self.stream.write(self._human(event, fields))
        self.stream.flush()

    def _human(self, event: str, f: dict[str, Any]) -> str:
        if event == "progress":
            total = f.get("total")
            count = f"{f.get('current')}/{total}" if total else ""
            return f"[{f.get('phase', '')}] {count} {f.get('message', '')}".rstrip() + "\n"
        if event == "item":
            if f.get("state") in ("committed", "skipped_dup") and not f.get("verbose"):
                return ""
            err = f" ({f['error']})" if f.get("error") else ""
            return f"  {f.get('state')}: {f.get('src')} -> {f.get('dst')}{err}\n"
        if event in ("warning", "error"):
            return f"{event.upper()} [{f.get('code')}]: {f.get('message')}\n"
        if event == "result":
            lines = []
            for key, value in f.items():
                if isinstance(value, list):
                    lines.append(f"{key}:")
                    for row in value:
                        if isinstance(row, dict):
                            lines.append("  - " + ", ".join(f"{k}={v}" for k, v in row.items()))
                        else:
                            lines.append(f"  - {row}")
                elif isinstance(value, dict):
                    lines.append(f"{key}: " + ", ".join(f"{k}={v}" for k, v in value.items()))
                else:
                    lines.append(f"{key}: {value}")
            return "\n".join(lines) + "\n"
        return f"{event}: {f}\n"
