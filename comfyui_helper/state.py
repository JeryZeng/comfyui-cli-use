from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque


@dataclass
class QueueItem:
    prompt_id: str
    number: str
    workflow_name: str
    raw: Any


@dataclass
class RecentItem:
    time_text: str
    workflow_name: str
    status: str
    prompt_id: str


@dataclass
class RuntimeState:
    online: bool = False
    last_error: str | None = None
    running: list[QueueItem] = field(default_factory=list)
    pending: list[QueueItem] = field(default_factory=list)
    messages: Deque[str] = field(default_factory=lambda: deque(maxlen=5))
    recent: Deque[RecentItem] = field(default_factory=lambda: deque(maxlen=20))
    progress: dict[str, str] = field(default_factory=dict)
    current_node: dict[str, str] = field(default_factory=dict)


def parse_queue_items(items: list[Any], session_tasks: dict[str, str]) -> list[QueueItem]:
    parsed: list[QueueItem] = []
    for item in items:
        number = "?"
        prompt_id = "?"
        if isinstance(item, (list, tuple)):
            if len(item) > 0:
                number = str(item[0])
            if len(item) > 1:
                prompt_id = str(item[1])
        elif isinstance(item, dict):
            number = str(item.get("number", "?"))
            prompt_id = str(item.get("prompt_id", item.get("id", "?")))
        parsed.append(
            QueueItem(
                prompt_id=prompt_id,
                number=number,
                workflow_name=session_tasks.get(prompt_id, "unknown"),
                raw=item,
            )
        )
    return parsed
