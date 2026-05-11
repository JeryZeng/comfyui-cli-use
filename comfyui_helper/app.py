from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import websockets
from rich.markup import escape
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Static

from comfyui_helper.client import ComfyClient
from comfyui_helper.config import AppConfig, load_config
from comfyui_helper.state import RecentItem, RuntimeState, parse_queue_items
from comfyui_helper.workflow import (
    ConfigField,
    WorkflowInfo,
    apply_field_values,
    collect_execution_nodes,
    scan_workflows,
)

MIN_WIDTH = 78
MIN_HEIGHT = 24
SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


@dataclass(frozen=True)
class RandomSeedValue:
    pass


@dataclass(frozen=True)
class LastSubmission:
    workflow_name: str
    values: dict[tuple[str, str], Any]
    count: int


class ComfyHelperApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #root {
        height: 100%;
    }

    #top {
        height: 1fr;
        border: round $accent;
        padding: 0 1;
    }

    #top_content {
        height: 1fr;
    }

    #param_input {
        dock: bottom;
    }

    #bottom {
        height: 9;
        border: round $primary;
        padding: 0 1;
    }

    .hidden {
        display: none;
    }
    """

    BINDINGS = [
        ("q", "request_quit", "Quit"),
        ("r", "reload_workflows", "Reload workflows"),
        ("s", "refresh_status", "Refresh status"),
        ("i", "interrupt", "Interrupt"),
        ("c", "clear_pending", "Clear pending"),
        ("d", "delete_pending", "Delete pending"),
        ("b", "start_batch", "Batch"),
        ("u", "repeat_last_submission", "Repeat last"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config: AppConfig = load_config()
        self.client = ComfyClient()
        self.client_id = str(uuid.uuid4())
        self.runtime = RuntimeState()
        self.workflows: list[WorkflowInfo] = []
        self.workflow_index = 0
        self.pending_index = 0
        self.focus_area = "top"
        self.mode = "browse"
        self.input_error: str | None = None
        self.active_workflow: WorkflowInfo | None = None
        self.active_field_index = 0
        self.active_values: dict[tuple[str, str], Any] = {}
        self.batch_after_input = False
        self.batch_workflow: WorkflowInfo | None = None
        self.batch_values: dict[tuple[str, str], Any] = {}
        self.confirm_message: str | None = None
        self.confirm_callback: Callable[[], Awaitable[None]] | None = None
        self.session_tasks: dict[str, str] = {}
        self.session_total_nodes: dict[str, int] = {}
        self.session_executed_nodes: dict[str, set[str]] = {}
        self.session_finished: set[str] = set()
        self.last_submission: LastSubmission | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self.spinner_index = 0
        self.completion_matches: list[str] = []
        self.completion_prefix: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="root"):
            with Vertical(id="top"):
                yield Static(id="top_content")
                yield Input(id="param_input")
            yield Static(id="bottom")

    async def on_mount(self) -> None:
        self.query_one("#param_input", Input).add_class("hidden")
        for message in self.config.messages:
            self.add_message(message)
        self.reload_workflows()
        self.render_all()
        self._tasks.append(asyncio.create_task(self.poll_loop()))
        self._tasks.append(asyncio.create_task(self.websocket_loop()))
        self._tasks.append(asyncio.create_task(self.spinner_loop()))
        logging.info("Comfy Helper started with client_id=%s", self.client_id)

    async def on_unmount(self) -> None:
        for task in self._tasks:
            task.cancel()
        await self.client.close()
        logging.info("Comfy Helper stopped")

    async def on_key(self, event: events.Key) -> None:
        if self.size.width < MIN_WIDTH or self.size.height < MIN_HEIGHT:
            if event.key == "q":
                self.exit()
            return

        if self.confirm_message:
            await self.handle_confirm_key(event)
            return

        if self.mode == "input":
            if event.key == "escape":
                self.cancel_input()
                event.stop()
            elif event.key == "tab":
                self.complete_path()
                event.stop()
            elif is_batch_key(event.key):
                await self.start_batch_from_input()
                event.stop()
            return

        if self.mode == "batch_count":
            if event.key == "escape":
                self.cancel_batch_count()
                event.stop()
            return

        key = event.key
        logging.debug("key=%s event trigger", key)
        if key == "q":
            await self.request_quit()
        elif key == "r":
            self.reload_workflows()
            self.render_all()
        elif key == "s":
            await self.refresh_status()
            self.render_all()
        elif key == "i":
            await self.action_interrupt()
        elif key == "c":
            await self.action_clear_pending()
        elif key == "d":
            await self.action_delete_pending()
        elif key == "b":
            await self.start_selected_workflow(batch=True)
        elif key == "u":
            await self.action_repeat_last_submission()
        if key == "tab":
            self.focus_area = "bottom" if self.focus_area == "top" else "top"
            self.render_all()
        elif key == "enter" and self.focus_area == "top":
            await self.start_selected_workflow(batch=False)
        elif is_batch_key(key) and self.focus_area == "top":
            await self.start_selected_workflow(batch=True)
        elif key == "up":
            self.move_selection(-1)
        elif key == "down":
            self.move_selection(1)
        event.stop()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.mode in {"input", "batch_count"} or self.confirm_message:
            return False
        return True

    async def action_request_quit(self) -> None:
        await self.request_quit()

    def action_reload_workflows(self) -> None:
        self.reload_workflows()
        self.render_all()

    async def action_refresh_status(self) -> None:
        await self.refresh_status()
        self.render_all()

    async def action_interrupt(self) -> None:
        if not self.runtime.running:
            self.add_message("Nothing to interrupt.")
            self.render_all()
            return
        await self.confirm("Interrupt current running task?", self.interrupt_running)

    async def action_start_batch(self) -> None:
        if self.focus_area == "top":
            await self.start_selected_workflow(batch=True)

    async def action_repeat_last_submission(self) -> None:
        if self.mode != "browse" or self.confirm_message:
            return
        if self.runtime.online is not True:
            self.add_message("ComfyUI is offline; cannot repeat last submission.")
            self.render_all()
            return
        last = self.last_submission
        if last is None:
            self.add_message("No previous submission to repeat.")
            self.render_all()
            return
        workflow = self.find_workflow_by_name(last.workflow_name)
        if workflow is None or not workflow.valid:
            self.add_message(f"Previous workflow is unavailable: {last.workflow_name}")
            self.render_all()
            return
        logging.info(f"repeat submit {last.count} tasks of workflow {last.workflow_name}")
        await self.submit_workflow(workflow, dict(last.values), count=last.count)

    async def action_clear_pending(self) -> None:
        if not self.runtime.pending:
            self.add_message("No pending tasks to clear.")
            self.render_all()
            return
        await self.confirm("Clear all pending tasks?", self.clear_pending)

    async def action_delete_pending(self) -> None:
        if self.focus_area == "bottom":
            await self.confirm_delete_pending()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.mode == "input":
            await self.accept_field_value(event.value)
        elif self.mode == "batch_count":
            await self.accept_batch_count(event.value)

    def reload_workflows(self) -> None:
        previous_name = self.selected_workflow.name if self.selected_workflow else None
        workflows, messages = scan_workflows(self.config.workflow_dir)
        self.workflows = workflows
        if previous_name:
            self.workflow_index = next(
                (index for index, workflow in enumerate(workflows) if workflow.name == previous_name),
                0,
            )
        else:
            self.workflow_index = 0
        for message in messages:
            self.add_message(message)
        self.add_message(f"Loaded {len(workflows)} workflow(s).")
        logging.info("Scanned %s workflow(s) from %s", len(workflows), self.config.workflow_dir)

    def find_workflow_by_name(self, name: str) -> WorkflowInfo | None:
        return next((workflow for workflow in self.workflows if workflow.name == name), None)

    @property
    def selected_workflow(self) -> WorkflowInfo | None:
        if not self.workflows:
            return None
        self.workflow_index = max(0, min(self.workflow_index, len(self.workflows) - 1))
        return self.workflows[self.workflow_index]

    def move_selection(self, delta: int) -> None:
        if self.focus_area == "top":
            if self.workflows:
                self.workflow_index = max(0, min(self.workflow_index + delta, len(self.workflows) - 1))
        else:
            if self.runtime.pending:
                self.pending_index = max(0, min(self.pending_index + delta, len(self.runtime.pending) - 1))
        self.render_all()

    async def start_selected_workflow(self, batch: bool = False) -> None:
        workflow = self.selected_workflow
        if workflow is None:
            self.add_message("No workflow selected.")
            self.render_all()
            return
        if not workflow.valid:
            self.add_message(f"Cannot run invalid workflow: {workflow.name}")
            self.render_all()
            return
        if not self.runtime.online:
            self.add_message("ComfyUI is offline; cannot submit workflow.")
            self.render_all()
            return
        if not workflow.fields:
            if batch:
                self.show_batch_count_input(workflow, {})
            else:
                await self.submit_workflow(workflow, {})
            return

        self.mode = "input"
        self.active_workflow = workflow
        self.active_field_index = 0
        self.active_values = {}
        self.batch_after_input = batch
        self.input_error = None
        self.clear_completion()
        self.show_input_for_current_field()

    def show_input_for_current_field(self) -> None:
        workflow = self.active_workflow
        if workflow is None:
            self.cancel_input()
            return
        while self.active_field_index < len(workflow.fields):
            field = workflow.fields[self.active_field_index]
            if field.supported:
                param_input = self.query_one("#param_input", Input)
                param_input.value = ""
                param_input.cursor_position = 0
                param_input.placeholder = "Enter keeps current, :run submits now, Esc cancels"
                param_input.remove_class("hidden")
                param_input.focus()
                self.render_all()
                return
            self.add_message(f"Unsupported field skipped: {field.display_name}")
            self.active_field_index += 1
        asyncio.create_task(self.finish_guided_input())

    async def accept_field_value(self, raw_value: str) -> None:
        workflow = self.active_workflow
        if workflow is None:
            return
        value_text = raw_value.strip()
        if value_text == ":run":
            await self.finish_guided_input()
            return
        if value_text == ":batch":
            await self.start_batch_from_input()
            return
        if not await self.capture_current_field_value(value_text):
            return
        self.active_field_index += 1
        self.show_input_for_current_field()

    async def capture_current_field_value(self, value_text: str) -> bool:
        workflow = self.active_workflow
        if workflow is None:
            return False
        field = workflow.fields[self.active_field_index]
        if value_text:
            try:
                value = await self.parse_field_value(field, value_text)
            except Exception as exc:
                self.input_error = str(exc)
                self.render_all()
                return False
            self.active_values[(field.node_id, field.name)] = value
        self.input_error = None
        self.clear_completion()
        return True

    async def start_batch_from_input(self) -> None:
        workflow = self.active_workflow
        if workflow is None:
            return
        param_input = self.query_one("#param_input", Input)
        value_text = param_input.value.strip()
        if value_text == ":run":
            value_text = ""
        if not await self.capture_current_field_value(value_text):
            return
        values = dict(self.active_values)
        self.cancel_input(render=False)
        self.show_batch_count_input(workflow, values)

    async def parse_field_value(self, field: ConfigField, value_text: str) -> Any:
        if field.is_load_image:
            path = Path(value_text).expanduser()
            if not path.exists():
                raise ValueError(f"File does not exist: {path}")
            if not path.is_file():
                raise ValueError(f"Expected a file path, not a directory: {path}")
            return await self.client.upload_image(path)

        current = field.value
        if isinstance(current, bool):
            normalized = value_text.lower()
            if normalized in {"true", "yes", "y", "1", "on"}:
                return True
            if normalized in {"false", "no", "n", "0", "off"}:
                return False
            raise ValueError("Expected a boolean value.")
        if isinstance(current, int) and not isinstance(current, bool):
            if value_text == ":seed":
                return RandomSeedValue()
            try:
                return int(value_text)
            except ValueError as exc:
                raise ValueError("Expected an integer value.") from exc
        if isinstance(current, float):
            try:
                return float(value_text)
            except ValueError as exc:
                raise ValueError("Expected a float value.") from exc
        if isinstance(current, str):
            return value_text
        raise ValueError("Unsupported field type.")

    async def finish_guided_input(self) -> None:
        workflow = self.active_workflow
        values = dict(self.active_values)
        batch = self.batch_after_input
        self.cancel_input(render=False)
        if workflow is not None:
            if batch:
                self.show_batch_count_input(workflow, values)
            else:
                await self.submit_workflow(workflow, values)

    def show_batch_count_input(self, workflow: WorkflowInfo, values: dict[tuple[str, str], Any]) -> None:
        self.mode = "batch_count"
        self.batch_workflow = workflow
        self.batch_values = values
        self.input_error = None
        self.clear_completion()
        param_input = self.query_one("#param_input", Input)
        param_input.value = ""
        param_input.cursor_position = 0
        param_input.placeholder = "Batch count, positive integer"
        param_input.remove_class("hidden")
        param_input.focus()
        self.render_all()

    async def accept_batch_count(self, raw_value: str) -> None:
        value_text = raw_value.strip()
        if not value_text:
            self.input_error = "Batch count is required."
            self.render_all()
            return
        try:
            count = int(value_text)
        except ValueError:
            self.input_error = "Batch count must be an integer."
            self.render_all()
            return
        if count <= 0:
            self.input_error = "Batch count must be greater than 0."
            self.render_all()
            return
        workflow = self.batch_workflow
        values = dict(self.batch_values)
        self.cancel_batch_count(render=False)
        if workflow is not None:
            await self.submit_workflow(workflow, values, count=count)

    def cancel_input(self, render: bool = True) -> None:
        self.mode = "browse"
        self.active_workflow = None
        self.active_field_index = 0
        self.active_values = {}
        self.batch_after_input = False
        self.input_error = None
        self.clear_completion()
        self.hide_param_input()
        if render:
            self.add_message("Cancelled workflow run.")
            self.render_all()

    def cancel_batch_count(self, render: bool = True) -> None:
        self.mode = "browse"
        self.batch_workflow = None
        self.batch_values = {}
        self.input_error = None
        self.clear_completion()
        self.hide_param_input()
        if render:
            self.add_message("Cancelled batch submit.")
            self.render_all()

    def hide_param_input(self) -> None:
        param_input = self.query_one("#param_input", Input)
        param_input.value = ""
        param_input.add_class("hidden")

    async def submit_workflow(
            self,
            workflow: WorkflowInfo,
            values: dict[tuple[str, str], Any],
            count: int = 1,
    ) -> None:
        if workflow.data is None:
            self.add_message("Workflow data is not available.")
            self.render_all()
            return
        submitted: list[str] = []
        for index in range(count):
            prompt_id = str(uuid.uuid4())
            try:
                prompt = apply_field_values(workflow.data, self.resolve_submission_values(values))
                await self.client.submit(prompt, self.client_id, prompt_id)
            except Exception as exc:
                logging.exception("Failed to submit workflow %s", workflow.name)
                self.add_message(f"Submit failed for {workflow.name}: {short_error(exc)}")
                if submitted:
                    self.add_message(f"Submitted {len(submitted)}/{count} before failure.")
                await self.refresh_status()
                self.render_all()
                return
            self.session_tasks[prompt_id] = workflow.name
            self.session_total_nodes[prompt_id] = len(workflow.data)
            self.session_executed_nodes[prompt_id] = set()
            self.session_finished.discard(prompt_id)
            submitted.append(prompt_id)
            logging.info(
                "Submitted workflow %s prompt_id=%s batch_index=%s/%s",
                workflow.name,
                prompt_id,
                index + 1,
                count,
            )
        if count == 1:
            self.add_message(f"Submitted {workflow.name}, prompt_id {submitted[0]}")
        else:
            self.add_message(
                f"Submitted {workflow.name} {count} times, first prompt_id {submitted[0]}, last {submitted[-1]}"
            )
        self.last_submission = LastSubmission(workflow_name=workflow.name, values=dict(values), count=count)
        await self.refresh_status()
        self.render_all()

    def complete_path(self) -> None:
        workflow = self.active_workflow
        if workflow is None:
            return
        field = workflow.fields[self.active_field_index]
        if not field.is_load_image:
            return
        param_input = self.query_one("#param_input", Input)
        raw = param_input.value.strip()
        if not raw:
            self.clear_completion()
            return
        expanded = str(Path(raw).expanduser())
        matches = sorted(glob.glob(expanded + "*"))
        if not matches:
            self.input_error = "No path completion matches."
            self.clear_completion()
            self.render_all()
            return
        common = _common_prefix(matches)
        if len(matches) == 1:
            common = _format_completion(matches[0])
            self.clear_completion()
        else:
            if len(common) > len(expanded):
                common = _format_completion(common)
            else:
                common = _format_completion(expanded)
            self.completion_prefix = raw
            self.completion_matches = [_format_completion(match) for match in matches[:20]]
        param_input.value = common
        param_input.cursor_position = len(param_input.value)
        self.input_error = None
        self.render_all()

    def resolve_submission_values(self, values: dict[tuple[str, str], Any]) -> dict[tuple[str, str], Any]:
        resolved: dict[tuple[str, str], Any] = {}
        for key, value in values.items():
            if isinstance(value, RandomSeedValue):
                resolved[key] = secrets.randbits(63)
            else:
                resolved[key] = value
        return resolved

    async def poll_loop(self) -> None:
        while True:
            try:
                await self.refresh_status()
                self.render_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Queue poll failed")
            await asyncio.sleep(max(self.config.refresh_interval, 0.1))

    async def spinner_loop(self) -> None:
        while True:
            try:
                if self.runtime.running:
                    self.spinner_index = (self.spinner_index + 1) % len(SPINNER_FRAMES)
                    self.render_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Spinner refresh failed")
            await asyncio.sleep(0.12)

    async def refresh_status(self) -> None:
        try:
            queue = await self.client.queue()
        except Exception as exc:
            if self.runtime.online:
                self.add_message("ComfyUI connection lost.")
            self.runtime.online = False
            self.runtime.last_error = short_error(exc)
            return

        if not self.runtime.online:
            self.add_message("ComfyUI connection restored.")
        self.runtime.online = True
        self.runtime.last_error = None
        self.runtime.running = parse_queue_items(queue.get("queue_running", []), self.session_tasks)
        self.runtime.pending = parse_queue_items(queue.get("queue_pending", []), self.session_tasks)
        self.sync_queue_totals(self.runtime.running + self.runtime.pending)
        if self.pending_index >= len(self.runtime.pending):
            self.pending_index = max(0, len(self.runtime.pending) - 1)

    async def websocket_loop(self) -> None:
        url = f"{self.client.ws_url}?clientId={self.client_id}"
        while True:
            try:
                async with websockets.connect(url) as websocket:
                    async for message in websocket:
                        if isinstance(message, bytes):
                            continue
                        await self.handle_ws_message(json.loads(message))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(max(self.config.refresh_interval, 0.5))

    async def handle_ws_message(self, message: dict[str, Any]) -> None:
        event_type = message.get("type")
        data = message.get("data", {})
        if not isinstance(data, dict):
            return
        prompt_id = data.get("prompt_id")
        if event_type == "progress" and isinstance(prompt_id, str):
            value = data.get("value")
            maximum = data.get("max")
            node = data.get("node")
            self.runtime.progress[prompt_id] = f"{value}/{maximum}" if maximum is not None else str(value)
            if isinstance(node, str):
                self.runtime.current_node[prompt_id] = node
                self.session_finished.discard(prompt_id)
        elif event_type == "executing" and isinstance(prompt_id, str):
            node = data.get("node")
            if node is None:
                self.session_finished.add(prompt_id)
                self.mark_recent(prompt_id, "completed")
            elif isinstance(node, str):
                self.runtime.current_node[prompt_id] = node
                self.session_finished.discard(prompt_id)
        elif event_type == "executed" and isinstance(prompt_id, str):
            node = data.get("node")
            if isinstance(node, str):
                self.session_executed_nodes.setdefault(prompt_id, set()).add(node)
        elif event_type == "execution_cached" and isinstance(prompt_id, str):
            cached_nodes = data.get("nodes", [])
            if isinstance(cached_nodes, list):
                for node in cached_nodes:
                    if isinstance(node, str):
                        self.session_executed_nodes.setdefault(prompt_id, set()).add(node)
        elif event_type == "execution_error" and isinstance(prompt_id, str):
            self.mark_recent(prompt_id, "failed")
            message_text = data.get("exception_message") or data.get("exception_type") or "Execution failed."
            self.add_message(str(message_text))
            self.session_finished.discard(prompt_id)
        elif event_type == "status":
            await self.refresh_status()
        self.render_all()

    def mark_recent(self, prompt_id: str, status: str) -> None:
        workflow_name = self.session_tasks.get(prompt_id)
        if not workflow_name:
            return
        existing = [item for item in self.runtime.recent if item.prompt_id == prompt_id and item.status == status]
        if existing:
            return
        self.runtime.recent.appendleft(
            RecentItem(
                time_text=datetime.now().strftime("%H:%M:%S"),
                workflow_name=workflow_name,
                status=status,
                prompt_id=prompt_id,
            )
        )
        self.add_message(f"{workflow_name} {status}: {prompt_id}")

    async def confirm(self, message: str, callback: Callable[[], Awaitable[None]]) -> None:
        self.confirm_message = message
        self.confirm_callback = callback
        self.render_all()

    async def handle_confirm_key(self, event: events.Key) -> None:
        if event.key in {"escape", "n"}:
            self.confirm_message = None
            self.confirm_callback = None
            self.add_message("Operation cancelled.")
            self.render_all()
            event.stop()
            return
        if event.key in {"enter", "y"}:
            callback = self.confirm_callback
            self.confirm_message = None
            self.confirm_callback = None
            if callback:
                await callback()
            self.render_all()
            event.stop()

    async def confirm_delete_pending(self) -> None:
        if not self.runtime.pending:
            self.add_message("No pending task selected.")
            self.render_all()
            return
        item = self.runtime.pending[self.pending_index]
        await self.confirm(f"Delete pending task {item.prompt_id}?", lambda: self.delete_pending(item.prompt_id))

    async def delete_pending(self, prompt_id: str) -> None:
        try:
            await self.client.delete_pending(prompt_id)
            self.add_message(f"Deleted pending task {prompt_id}.")
        except Exception as exc:
            logging.exception("Failed to delete pending task %s", prompt_id)
            self.add_message(f"Delete failed: {short_error(exc)}")
        await self.refresh_status()

    async def clear_pending(self) -> None:
        if not self.runtime.pending:
            self.add_message("No pending tasks to clear.")
            return
        try:
            await self.client.clear_pending()
            self.add_message("Cleared pending queue.")
        except Exception as exc:
            logging.exception("Failed to clear pending queue")
            self.add_message(f"Clear failed: {short_error(exc)}")
        await self.refresh_status()

    async def interrupt_running(self) -> None:
        if not self.runtime.running:
            self.add_message("Nothing to interrupt.")
            return
        try:
            await self.client.interrupt()
            self.add_message("Interrupt requested.")
        except Exception as exc:
            logging.exception("Failed to interrupt running task")
            self.add_message(f"Interrupt failed: {short_error(exc)}")
        await self.refresh_status()

    async def request_quit(self) -> None:
        active_ids = {item.prompt_id for item in self.runtime.running + self.runtime.pending}
        session_active = bool(active_ids.intersection(self.session_tasks))
        if session_active:
            await self.confirm(
                "This session has active tasks. Quit TUI and leave them running?",
                self.quit_app,
            )
            return
        await self.quit_app()

    async def quit_app(self) -> None:
        self.exit()

    def add_message(self, message: str) -> None:
        self.runtime.messages.appendleft(message)

    def render_all(self) -> None:
        if not self.is_mounted:
            return
        if self.size.width < MIN_WIDTH or self.size.height < MIN_HEIGHT:
            self.query_one("#top_content", Static).update("Terminal too small, please resize.")
            self.query_one("#bottom", Static).update("")
            return
        self.query_one("#top_content", Static).update(self.render_top())
        self.query_one("#bottom", Static).update(self.render_bottom())

    def render_top(self) -> str:
        border = "[bold cyan]" if self.focus_area == "top" else "[bold]"
        if self.confirm_message:
            return (
                f"{border}Operation[/]\n\n"
                f"[yellow]{escape(self.confirm_message)}[/]\n\n"
                "Enter/y confirm | Esc/n cancel"
            )
        if self.mode == "input":
            return self.render_input()
        if self.mode == "batch_count":
            return self.render_batch_count()
        return self.render_browser()

    def render_browser(self) -> str:
        lines = ["[bold cyan]Workflow Runner[/]" if self.focus_area == "top" else "[bold]Workflow Runner[/]"]
        lines.append("")
        if not self.workflows:
            lines.append(f"No workflows found in {escape(str(self.config.workflow_dir))}")
        else:
            height_budget = max(4, self.size.height // 4)
            start = max(0, min(self.workflow_index - height_budget // 2, len(self.workflows) - height_budget))
            end = min(len(self.workflows), start + height_budget)
            for index, workflow in enumerate(self.workflows[start:end], start=start):
                marker = ">" if index == self.workflow_index else " "
                status = " [red]invalid[/]" if not workflow.valid else ""
                row = f"{marker} {escape(workflow.name)}{status}"
                if index == self.workflow_index:
                    row = f"[reverse bold]{row}[/]"
                lines.append(row)
        lines.append("")
        workflow = self.selected_workflow
        if workflow:
            status = "[green]valid[/]" if workflow.valid else "[red]invalid[/]"
            modified = datetime.fromtimestamp(workflow.modified).strftime("%Y-%m-%d %H:%M:%S")
            lines.extend(
                [
                    "[bold]Selected[/]",
                    f"name: {escape(workflow.name)}",
                    f"path: {escape(str(workflow.path))}",
                    f"modified: {modified}",
                    f"status: {status}",
                    f"configurable fields: {len(workflow.fields)}",
                    f"unsupported fields: {workflow.unsupported_count}",
                ]
            )
            if workflow.error:
                lines.append(f"error: [red]{escape(workflow.error)}[/]")
        return "\n".join(lines)

    def render_input(self) -> str:
        workflow = self.active_workflow
        if workflow is None:
            return ""
        field = workflow.fields[self.active_field_index]
        current = self.active_values.get((field.node_id, field.name), field.value)
        lines = [
            "[bold cyan]Guided Run[/]",
            f"Workflow: {escape(workflow.name)}",
            "",
            f"Step {self.active_field_index + 1}/{len(workflow.fields)}: {escape(field.display_name)}",
            f"Current value: {escape(format_field_value(current))}",
            "",
            "Input new value, Enter keeps current, :run submits now, Esc cancels:",
        ]
        if isinstance(field.value, int) and not isinstance(field.value, bool):
            lines.append("Use :seed for a new random integer on every submission.")
        if field.is_load_image:
            lines.append("LoadImage.image expects a local file path. Tab completes paths.")
        if self.input_error:
            lines.append(f"[red]{escape(self.input_error)}[/]")
        if self.completion_matches:
            lines.append("")
            lines.append("[bold]Path matches[/]")
            lines.extend(f"  {escape(match)}" for match in self.completion_matches[:10])
        return "\n".join(lines)

    def render_batch_count(self) -> str:
        workflow = self.batch_workflow
        if workflow is None:
            return ""
        lines = [
            "[bold cyan]Batch Submit[/]",
            f"Workflow: {escape(workflow.name)}",
            "",
            "Enter the number of times to submit this workflow.",
            "Each submission gets a separate prompt_id and appears in the ComfyUI queue.",
            "",
            "Input a positive integer, Enter submits, Esc cancels:",
        ]
        if self.input_error:
            lines.append(f"[red]{escape(self.input_error)}[/]")
        return "\n".join(lines)

    def render_bottom(self) -> str:
        status = "online" if self.runtime.online else "offline"
        focus = "[bold cyan]Status[/]" if self.focus_area == "bottom" else "[bold]Status[/]"
        error_count = 1 if self.runtime.last_error else 0
        lines = [
            f"{focus} ComfyUI {status} | running {color_count(len(self.runtime.running), 'yellow')} | "
            f"pending {color_count(len(self.runtime.pending), 'cyan')} | "
            f"recent {color_count(len(self.runtime.recent), 'green')} | "
            f"error {color_count(error_count, 'red')}",
        ]
        if self.runtime.running:
            item = self.runtime.running[0]
            node = self.runtime.current_node.get(item.prompt_id, "-")
            sampler = self.runtime.progress.get(item.prompt_id, "-")
            percent = self.node_percent(item.prompt_id)
            spinner = SPINNER_FRAMES[self.spinner_index]
            if item.workflow_name == "unknown":
                progress_text = "progress unknown"
            else:
                progress_text = f"{percent}% node={escape(node)} sampler={escape(sampler)}"
            lines.append(
                f"[reverse bold yellow] Running {spinner} {escape(item.workflow_name)} {short_id(item.prompt_id)} {progress_text} [/]"
            )
        else:
            lines.append("Running: none")

        if self.runtime.pending:
            height_budget = 2
            start = max(0, min(self.pending_index - height_budget // 2, len(self.runtime.pending) - height_budget))
            end = min(len(self.runtime.pending), start + height_budget)
            for index, item in enumerate(self.runtime.pending[start:end], start=start):
                marker = ">" if self.focus_area == "bottom" and index == self.pending_index else " "
                lines.append(
                    f"Pending: {marker} {escape(item.number):>6} {escape(item.workflow_name)} {short_id(item.prompt_id)}"
                )
        else:
            lines.append("Pending: none")

        if self.runtime.recent:
            item = self.runtime.recent[0]
            style = "green" if item.status == "completed" else "red"
            lines.append(
                f"Recent: {item.time_text} {escape(item.workflow_name)} [{style}]{escape(item.status)}[/] {short_id(item.prompt_id)}"
            )
        else:
            lines.append("Recent: none")

        if self.runtime.messages:
            lines.append(f"Msg: {escape(self.runtime.messages[0])}")
        else:
            lines.append("Msg: none")
        while len(lines) < 6:
            lines.append("")
        lines.append(self.help_text())
        return "\n".join(lines)

    def node_percent(self, prompt_id: str) -> int:
        total = self.session_total_nodes.get(prompt_id)
        if not total:
            return 0
        if prompt_id in self.session_finished:
            return 100

        done = len(self.session_executed_nodes.get(prompt_id, set()))
        fraction = self.node_progress_fraction(prompt_id)
        return max(0, min(100, int(((done + fraction) / total) * 100)))

    def node_progress_fraction(self, prompt_id: str) -> float:
        current = self.runtime.current_node.get(prompt_id)
        if not current:
            return 0.0
        progress = self.runtime.progress.get(prompt_id)
        if not progress or "/" not in progress:
            return 0.0
        try:
            value_text, max_text = progress.split("/", 1)
            value = float(value_text)
            maximum = float(max_text)
        except ValueError:
            return 0.0
        if maximum <= 0:
            return 0.0
        return max(0.0, min(0.999, value / maximum))

    def sync_queue_totals(self, items: list[Any]) -> None:
        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) < 5:
                continue
            prompt_id = item[1]
            prompt = item[2]
            outputs_to_execute = item[4]
            if not isinstance(prompt_id, str) or not isinstance(prompt, dict) or not isinstance(outputs_to_execute,
                                                                                                list):
                continue
            nodes = collect_execution_nodes(prompt, [node for node in outputs_to_execute if isinstance(node, str)])
            if nodes:
                self.session_total_nodes[prompt_id] = len(nodes)

    def help_text(self) -> str:
        if self.confirm_message:
            return "Enter/y confirm | Esc/n cancel"
        if self.mode == "input":
            return "Enter next | b/:batch batch | :seed random | :run submit | Tab complete | Esc cancel"
        if self.mode == "batch_count":
            return "Enter submit batch | positive integer only | Esc cancel"
        return "↑↓ sel | Enter run | b batch | u repeat | Tab | r/s refresh | i/d/c queue | q quit"

    def clear_completion(self) -> None:
        self.completion_matches = []
        self.completion_prefix = None


def short_error(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) <= 160 else text[:157] + "..."


def short_id(prompt_id: str) -> str:
    return escape(prompt_id[:8] if prompt_id and prompt_id != "?" else prompt_id)


def color_count(value: int, color: str) -> str:
    style = color if value > 0 else "bright_black"
    return f"[{style}]{value}[/]"


def format_field_value(value: Any) -> str:
    if isinstance(value, RandomSeedValue):
        return ":seed (random each submit)"
    return str(value)


def is_batch_key(key: str) -> bool:
    return key in {"shift+enter", "shift_enter", "ctrl+enter", "ctrl+j"}


def _common_prefix(paths: list[str]) -> str:
    if not paths:
        return ""
    prefix = paths[0]
    for path in paths[1:]:
        while not path.startswith(prefix) and prefix:
            prefix = prefix[:-1]
    return prefix


def _format_completion(path: str) -> str:
    suffix = "/" if os.path.isdir(path) and not path.endswith("/") else ""
    return Path(path).as_posix() + suffix
