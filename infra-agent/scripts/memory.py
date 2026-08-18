from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class MemoryEvent:
    timestamp: str
    prompt: str
    module: str
    environment: str
    mode: str
    status: str
    detail: str


def _default_memory_path() -> Path:
    return Path(__file__).resolve().parent / ".agent-memory.json"


def load_memory(path: Path | None = None) -> list[dict]:
    memory_path = path or _default_memory_path()
    if not memory_path.exists():
        return []
    return json.loads(memory_path.read_text(encoding="utf-8"))


def save_event(event: MemoryEvent, path: Path | None = None) -> None:
    memory_path = path or _default_memory_path()
    history = load_memory(memory_path)
    history.append(asdict(event))
    memory_path.write_text(json.dumps(history, indent=2), encoding="utf-8")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

