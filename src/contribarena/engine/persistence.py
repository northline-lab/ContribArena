from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    sort_keys: bool = False,
    ensure_ascii: bool = True,
) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=sort_keys, ensure_ascii=ensure_ascii) + "\n",
    )
