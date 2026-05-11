from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_TYPES = (str, int, float, bool)


@dataclass(frozen=True)
class ConfigField:
    node_id: str
    class_type: str
    title: str | None
    name: str
    value: Any
    supported: bool
    is_load_image: bool

    @property
    def display_name(self) -> str:
        node_label = self.title or self.class_type
        if self.title:
            return f"[{self.node_id}] {node_label} / {self.class_type}.{self.name}"
        return f"[{self.node_id}] {self.class_type}.{self.name}"


@dataclass(frozen=True)
class WorkflowInfo:
    name: str
    path: Path
    modified: float
    valid: bool
    error: str | None
    fields: list[ConfigField]
    unsupported_count: int
    data: dict[str, Any] | None


def scan_workflows(workflow_dir: Path) -> tuple[list[WorkflowInfo], list[str]]:
    messages: list[str] = []
    workflows: list[WorkflowInfo] = []

    if not workflow_dir.exists():
        return [], [f"Workflow directory does not exist: {workflow_dir}"]

    for path in workflow_dir.rglob("*.json"):
        try:
            stat = path.stat()
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            logging.exception("Failed to read workflow file %s", path)
            messages.append("Some workflow files could not be read.")
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logging.exception("Invalid JSON workflow file %s", path)
            messages.append("Some workflow files contain invalid JSON.")
            continue

        name = path.relative_to(workflow_dir).with_suffix("").as_posix()
        info = validate_workflow(name, path, stat.st_mtime, data)
        workflows.append(info)

    workflows.sort(key=lambda item: (-item.modified, item.name))
    return workflows, _dedupe(messages)


def validate_workflow(
        name: str, path: Path, modified: float, data: Any
) -> WorkflowInfo:
    if not isinstance(data, dict):
        return _invalid(name, path, modified, "Top-level workflow JSON must be an object.")

    fields: list[ConfigField] = []
    unsupported_count = 0

    for node_id, node in data.items():
        if not isinstance(node, dict):
            return _invalid(name, path, modified, f'Node "{node_id}" must be an object.')
        if "class_type" not in node:
            return _invalid(name, path, modified, f'Node "{node_id}" is missing class_type.')
        if "inputs" not in node:
            return _invalid(name, path, modified, f'Node "{node_id}" is missing inputs.')
        if not isinstance(node["inputs"], dict):
            return _invalid(name, path, modified, f'Node "{node_id}".inputs must be an object.')

        class_type = node["class_type"]
        if not isinstance(class_type, str):
            return _invalid(name, path, modified, f'Node "{node_id}".class_type must be a string.')

        meta = node.get("_meta", {})
        if meta is None:
            meta = {}
        if not isinstance(meta, dict):
            return _invalid(name, path, modified, f'Node "{node_id}"._meta must be an object.')

        title = meta.get("title")
        if title is not None and not isinstance(title, str):
            title = str(title)

        configurable = meta.get("configurable", [])
        if configurable is None:
            configurable = []
        if not isinstance(configurable, list):
            return _invalid(
                name,
                path,
                modified,
                f'Node "{node_id}"._meta.configurable must be an array.',
            )

        seen: set[str] = set()
        for field_name in configurable:
            if not isinstance(field_name, str):
                return _invalid(
                    name,
                    path,
                    modified,
                    f'Node "{node_id}"._meta.configurable entries must be strings.',
                )
            if field_name in seen:
                logging.warning("Duplicate configurable field ignored: %s %s", node_id, field_name)
                continue
            seen.add(field_name)
            if field_name not in node["inputs"]:
                return _invalid(
                    name,
                    path,
                    modified,
                    f'Configurable field "{field_name}" not found in node "{node_id}".inputs.',
                )
            value = node["inputs"][field_name]
            is_load_image = class_type == "LoadImage" and field_name == "image"
            supported = isinstance(value, SUPPORTED_TYPES) or is_load_image
            if not supported:
                unsupported_count += 1
            fields.append(
                ConfigField(
                    node_id=str(node_id),
                    class_type=class_type,
                    title=title,
                    name=field_name,
                    value=value,
                    supported=supported,
                    is_load_image=is_load_image,
                )
            )

    return WorkflowInfo(
        name=name,
        path=path,
        modified=modified,
        valid=True,
        error=None,
        fields=fields,
        unsupported_count=unsupported_count,
        data=data,
    )


def apply_field_values(workflow: dict[str, Any], values: dict[tuple[str, str], Any]) -> dict[str, Any]:
    updated = copy.deepcopy(workflow)
    for (node_id, field_name), value in values.items():
        updated[node_id]["inputs"][field_name] = value
    return updated


def collect_execution_nodes(prompt: dict[str, Any], outputs_to_execute: list[str]) -> set[str]:
    reachable: set[str] = set()
    stack = [node_id for node_id in outputs_to_execute if isinstance(node_id, str)]
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        node = prompt.get(node_id)
        if not isinstance(node, dict):
            continue
        reachable.add(node_id)
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                upstream = value[0]
                if isinstance(upstream, str) and upstream in prompt:
                    stack.append(upstream)
    return reachable


def _invalid(name: str, path: Path, modified: float, error: str) -> WorkflowInfo:
    return WorkflowInfo(
        name=name,
        path=path,
        modified=modified,
        valid=False,
        error=error,
        fields=[],
        unsupported_count=0,
        data=None,
    )


def _dedupe(messages: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if message not in seen:
            seen.add(message)
            result.append(message)
    return result
