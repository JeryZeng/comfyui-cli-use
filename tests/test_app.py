from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from comfyui_helper.app import ComfyHelperApp, ImageBatch, LastSubmission, RandomSeedValue, format_field_value
from comfyui_helper.client import ComfyClient
from comfyui_helper.config import DEFAULT_COMFYUI_SERVER, load_config
from comfyui_helper.state import QueueItem, RecentItem
from comfyui_helper.workflow import ConfigField, WorkflowInfo, validate_workflow


class ComfyHelperAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    async def asyncTearDown(self) -> None:
        await asyncio.sleep(0)
        self._tmpdir.cleanup()

    def make_app(self, comfyui_dir: Path | None = None) -> ComfyHelperApp:
        app = ComfyHelperApp()
        if comfyui_dir is not None:
            app.config = replace(app.config, comfyui_dir=comfyui_dir)
        app.workflow_history_dir = self.root / "history"
        return app

    def make_workflow(self) -> WorkflowInfo:
        return WorkflowInfo(
            name="demo",
            path=self.root / "workflows" / "demo.json",
            modified=0.0,
            valid=True,
            error=None,
            fields=[
                ConfigField("1", "PrimitiveInt", None, "seed", 1, True, False),
                ConfigField("2", "LoadImage", None, "image", "", True, True),
                ConfigField("3", "PrimitiveString", None, "prompt", "hello", True, False),
            ],
            unsupported_count=0,
            data={},
        )

    async def immediate_to_thread(self, func, /, *args, **kwargs):
        return func(*args, **kwargs)

    async def test_history_round_trip_preserves_seed_image_batch_and_plain_values(self) -> None:
        comfyui_dir = self.root / "ComfyUI"
        (comfyui_dir / "input").mkdir(parents=True)
        source_dir = self.root / "batch_source"
        source_dir.mkdir()
        (source_dir / "a.png").write_bytes(b"1")
        (source_dir / "b.jpg").write_bytes(b"22")
        symlink_dir = self.root / "batch_link"
        symlink_dir.symlink_to(source_dir, target_is_directory=True)

        app = self.make_app(comfyui_dir=comfyui_dir)
        workflow = self.make_workflow()
        try:
            with patch("comfyui_helper.app.asyncio.to_thread", new=self.immediate_to_thread):
                batch_value = await app.prepare_load_image_value(str(symlink_dir))
            self.assertIsInstance(batch_value, ImageBatch)
            self.assertEqual(batch_value.source, str(symlink_dir.absolute()))
            self.assertEqual(len(batch_value.images), 2)
            self.assertFalse(batch_value.shuffle)

            values = {
                ("1", "seed"): RandomSeedValue(),
                ("2", "image"): batch_value,
                ("3", "prompt"): "hello",
            }
            serialized = app.serialize_history_values(values)
            self.assertEqual(serialized["1|seed"], {"type": "random_seed"})
            self.assertEqual(
                serialized["2|image"],
                {"type": "image_batch", "dir": str(symlink_dir.absolute()), "shuffle": False},
            )
            self.assertEqual(serialized["3|prompt"], "hello")

            app.save_workflow_history(workflow, serialized)

            reader = self.make_app(comfyui_dir=comfyui_dir)
            try:
                reader.load_workflow_history()
                with patch("comfyui_helper.app.asyncio.to_thread", new=self.immediate_to_thread):
                    restored = await reader.resolve_history_values(
                        workflow,
                        reader.workflow_history_raw[reader.workflow_history_key(workflow)],
                    )
                self.assertIsInstance(restored[("1", "seed")], RandomSeedValue)
                self.assertEqual(restored[("3", "prompt")], "hello")
                restored_batch = restored[("2", "image")]
                self.assertIsInstance(restored_batch, ImageBatch)
                self.assertEqual(restored_batch.source, str(symlink_dir.absolute()))
                self.assertEqual(len(restored_batch.images), 2)
                self.assertFalse(restored_batch.shuffle)
            finally:
                await reader.client.close()
        finally:
            await app.client.close()

    async def test_history_round_trip_preserves_template_strings(self) -> None:
        app = self.make_app()
        workflow = self.make_workflow()
        try:
            values = {
                ("1", "seed"): RandomSeedValue(),
                ("3", "prompt"): "prefix_${1.seed}_suffix",
            }
            serialized = app.serialize_history_values(values)
            self.assertEqual(serialized["3|prompt"], "prefix_${1.seed}_suffix")

            app.save_workflow_history(workflow, serialized)

            reader = self.make_app()
            try:
                reader.load_workflow_history()
                restored = await reader.resolve_history_values(
                    workflow,
                    reader.workflow_history_raw[reader.workflow_history_key(workflow)],
                )
                self.assertEqual(restored[("3", "prompt")], "prefix_${1.seed}_suffix")
            finally:
                await reader.client.close()
        finally:
            await app.client.close()

    async def test_prepare_load_image_value_keeps_final_comfyui_path_for_single_file(self) -> None:
        comfyui_dir = self.root / "ComfyUI"
        (comfyui_dir / "input").mkdir(parents=True)
        image_path = self.root / "source.png"
        image_path.write_bytes(b"abc")

        app = self.make_app(comfyui_dir=comfyui_dir)
        try:
            with patch("comfyui_helper.app.asyncio.to_thread", new=self.immediate_to_thread):
                value = await app.prepare_load_image_value(str(image_path))
            self.assertEqual(value, image_path.name)

            serialized = app.serialize_history_values({("2", "image"): value})
            self.assertEqual(serialized["2|image"], image_path.name)

            workflow = self.make_workflow()
            app.save_workflow_history(workflow, serialized)
            reader = self.make_app(comfyui_dir=comfyui_dir)
            try:
                reader.load_workflow_history()
                with patch("comfyui_helper.app.asyncio.to_thread", new=self.immediate_to_thread):
                    restored = await reader.resolve_history_values(
                        workflow,
                        reader.workflow_history_raw[reader.workflow_history_key(workflow)],
                    )
                self.assertEqual(restored[("2", "image")], image_path.name)
            finally:
                await reader.client.close()
        finally:
            await app.client.close()

    async def test_shuffle_image_batch_is_applied_per_submission(self) -> None:
        app = self.make_app()
        try:
            values = {
                ("2", "image"): ImageBatch(
                    source="/tmp/images",
                    images=["a.png", "b.png", "c.png"],
                    shuffle=True,
                )
            }
            with patch("comfyui_helper.app.random.shuffle", new=lambda items: items.reverse()):
                resolved = app.resolve_submission_values(values)
            self.assertEqual([item[("2", "image")] for item in resolved], ["c.png", "b.png", "a.png"])
        finally:
            await app.client.close()

    async def test_template_references_resolve_across_types_and_image_batch_branches(self) -> None:
        app = self.make_app()
        try:
            workflow = WorkflowInfo(
                name="refs",
                path=self.root / "workflows" / "refs.json",
                modified=0.0,
                valid=True,
                error=None,
                fields=[
                    ConfigField("1", "PrimitiveInt", None, "seed", 7, True, False),
                    ConfigField("2", "PrimitiveBool", None, "flag", True, True, False),
                    ConfigField("3", "LoadImage", None, "image", "", True, True),
                    ConfigField("5", "PrimitiveInt", None, "copy_seed", 0, True, False),
                    ConfigField("6", "PrimitiveBool", None, "copy_flag", False, True, False),
                    ConfigField("4", "PrimitiveString", None, "prompt", "", True, False),
                ],
                unsupported_count=0,
                data={},
            )
            values = {
                ("1", "seed"): 7,
                ("2", "flag"): True,
                ("3", "image"): ImageBatch(source="/tmp/images", images=["a.png", "b.png"], shuffle=False),
                ("5", "copy_seed"): "${1.seed}",
                ("6", "copy_flag"): "${2.flag}",
                ("4", "prompt"): "seed=${5.copy_seed},flag=${6.copy_flag},image=${3.image}",
            }

            resolved = await app.resolve_submission_values_for_workflow(workflow, values)

            self.assertEqual(len(resolved), 2)
            self.assertEqual([item[("3", "image")] for item in resolved], ["a.png", "b.png"])
            self.assertEqual([item[("5", "copy_seed")] for item in resolved], [7, 7])
            self.assertEqual([item[("6", "copy_flag")] for item in resolved], [True, True])
            self.assertEqual(
                [item[("4", "prompt")] for item in resolved],
                [
                    "seed=7,flag=True,image=a.png",
                    "seed=7,flag=True,image=b.png",
                ],
            )
        finally:
            await app.client.close()

    async def test_template_reference_escape_keeps_literal_reference_text(self) -> None:
        app = self.make_app()
        try:
            workflow = WorkflowInfo(
                name="refs",
                path=self.root / "workflows" / "refs.json",
                modified=0.0,
                valid=True,
                error=None,
                fields=[
                    ConfigField("1", "PrimitiveInt", None, "seed", 7, True, False),
                    ConfigField("2", "PrimitiveString", None, "prompt", "", True, False),
                ],
                unsupported_count=0,
                data={},
            )
            values = {
                ("1", "seed"): 7,
                ("2", "prompt"): r"literal=\${1.seed}, escaped_slash=\\${1.seed}",
            }

            resolved = await app.resolve_submission_values_for_workflow(workflow, values)

            self.assertEqual(
                resolved[0][("2", "prompt")],
                r"literal=${1.seed}, escaped_slash=\7",
            )
        finally:
            await app.client.close()

    def test_format_field_value_shows_image_batch_shuffle_state(self) -> None:
        value = ImageBatch(source="/tmp/images", images=["a.png", "b.png"], shuffle=True)
        self.assertEqual(format_field_value(value), "2 images from /tmp/images (shuffled)")

    async def test_shuffle_prompt_hides_input_before_waiting_for_confirmation(self) -> None:
        app = self.make_app()
        try:
            batch = ImageBatch(source="/tmp/images", images=["a.png", "b.png"], shuffle=False)
            with patch.object(app, "hide_param_input") as hide_param_input, patch.object(app, "render_all") as render_all:
                await app.prompt_image_batch_shuffle("LoadImage.image", ("2", "image"), batch, app.advance_after_current_field)
            hide_param_input.assert_called_once_with(render=False)
            render_all.assert_called_once()
            self.assertEqual(app.shuffle_prompt_message, "Shuffle file order for LoadImage.image?")
        finally:
            await app.client.close()

    async def test_f3_in_input_mode_returns_to_previous_supported_field(self) -> None:
        app = self.make_app()
        try:
            workflow = self.make_workflow()
            app.active_workflow = workflow
            app.mode = "input"
            app.active_field_index = 2
            app.active_values = {
                ("1", "seed"): 7,
                ("2", "image"): ImageBatch(source="/tmp/images", images=["a.png"], shuffle=True),
            }

            class FakeInput:
                def __init__(self) -> None:
                    self.value = ""
                    self.cursor_position = 0
                    self.placeholder = ""
                    self.can_focus = False

                def remove_class(self, _name: str) -> None:
                    pass

                def focus(self) -> None:
                    pass

            fake_input = FakeInput()
            with patch.object(app, "query_one", return_value=fake_input), patch.object(app, "render_all"):
                await app.go_to_previous_field()

            self.assertEqual(app.active_field_index, 1)
            self.assertEqual(fake_input.value, "/tmp/images")
            self.assertEqual(fake_input.cursor_position, len("/tmp/images"))
            self.assertEqual(
                fake_input.placeholder,
                "Enter keeps current, :run submits now, F2 fills current, F7 clears input, F3 previous, Esc cancels, Tab completes paths",
            )
        finally:
            await app.client.close()

    async def test_escape_in_input_mode_cancels_workflow_input(self) -> None:
        app = self.make_app()
        try:
            workflow = self.make_workflow()
            app.active_workflow = workflow
            app.mode = "input"
            app.active_field_index = 2
            app.active_values = {
                ("1", "seed"): 7,
                ("2", "image"): ImageBatch(source="/tmp/images", images=["a.png"], shuffle=True),
            }

            class FakeInput:
                def __init__(self) -> None:
                    self.value = "keep me"
                    self.cursor_position = 7
                    self.placeholder = ""
                    self.can_focus = True

                def add_class(self, _name: str) -> None:
                    pass

                def remove_class(self, _name: str) -> None:
                    pass

                def focus(self) -> None:
                    pass

            fake_input = FakeInput()
            with patch.object(app, "query_one", return_value=fake_input), patch.object(app, "render_all"), patch.object(app, "set_focus"):
                app.cancel_input(render=False)

            self.assertEqual(app.mode, "browse")
            self.assertIsNone(app.active_workflow)
            self.assertEqual(app.active_field_index, 0)
            self.assertEqual(app.active_values, {})
            self.assertEqual(fake_input.value, "")
            self.assertFalse(fake_input.can_focus)
        finally:
            await app.client.close()

    async def test_clear_current_field_input_empties_editor_content(self) -> None:
        app = self.make_app()
        try:
            class FakeInput:
                def __init__(self) -> None:
                    self.value = "hello"
                    self.cursor_position = 5

            fake_input = FakeInput()
            with patch.object(app, "query_one", return_value=fake_input), patch.object(app, "render_all"):
                app.clear_current_field_input()

            self.assertEqual(fake_input.value, "")
            self.assertEqual(fake_input.cursor_position, 0)
        finally:
            await app.client.close()

    async def test_accept_field_value_persists_latest_input(self) -> None:
        app = self.make_app()
        try:
            workflow = self.make_workflow()
            app.active_workflow = workflow
            app.mode = "input"
            app.active_field_index = 2
            app.active_values = {("1", "seed"): 7}

            with patch.object(app, "render_all"), patch.object(app, "show_input_for_current_field"):
                await app.accept_field_value("updated prompt")

            self.assertEqual(app.active_values[("3", "prompt")], "updated prompt")
            self.assertEqual(app.active_field_index, 3)
        finally:
            await app.client.close()

    async def test_render_workflow_history_panel_orders_fields_by_node_id(self) -> None:
        app = self.make_app()
        try:
            workflow = self.make_workflow()
            snapshot = LastSubmission(
                workflow_name=workflow.name,
                values={
                    "3|prompt": "third",
                    "1|seed": {"type": "random_seed"},
                    "2|image": {"type": "image_batch", "dir": "/tmp/images", "shuffle": True},
                },
                count=1,
            )
            app.workflow_last_submissions[app.workflow_history_key(workflow)] = snapshot
            app.workflows = [workflow]
            app.workflow_index = 0
            panel = app.render_workflow_history_panel()
            lines = panel.splitlines()
            seed_index = next(i for i, line in enumerate(lines) if line.startswith("1/seed"))
            image_index = next(i for i, line in enumerate(lines) if line.startswith("2/image"))
            prompt_index = next(i for i, line in enumerate(lines) if line.startswith("3/prompt"))
            self.assertLess(seed_index, image_index)
            self.assertLess(image_index, prompt_index)
            self.assertIn("image batch from /tmp/images (shuffled)", panel)
            self.assertIn("----------", panel)
        finally:
            await app.client.close()

    def test_render_workflow_history_panel_shows_empty_state_without_history(self) -> None:
        app = self.make_app()
        workflow = self.make_workflow()
        app.workflows = [workflow]
        panel = app.render_workflow_history_panel()
        self.assertIn("No submission history yet.", panel)

    def test_validate_workflow_orders_fields_by_topology_not_node_id(self) -> None:
        workflow = validate_workflow(
            "demo",
            self.root / "workflows" / "demo.json",
            0.0,
            {
                "20": {
                    "inputs": {
                        "prompt": "",
                        "image": ["10", 0],
                    },
                    "class_type": "TextEncodeQwenImageEditPlus",
                    "_meta": {"title": "Node 20", "configurable": ["prompt"]},
                },
                "10": {
                    "inputs": {
                        "image": "a.png",
                    },
                    "class_type": "LoadImage",
                    "_meta": {"title": "Node 10", "configurable": ["image"]},
                },
            },
        )
        self.assertTrue(workflow.valid)
        self.assertEqual([field.node_id for field in workflow.fields], ["10", "20"])

    def test_validate_workflow_orders_independent_sources_by_output_branch_order(self) -> None:
        workflow = validate_workflow(
            "demo",
            self.root / "workflows" / "demo.json",
            0.0,
            {
                "30": {
                    "inputs": {
                        "prompt": "",
                        "image": ["10", 0],
                    },
                    "class_type": "TextEncodeQwenImageEditPlus",
                    "_meta": {"title": "Node 30", "configurable": ["prompt"]},
                },
                "20": {
                    "inputs": {
                        "prompt": "",
                        "image": ["11", 0],
                    },
                    "class_type": "TextEncodeQwenImageEditPlus",
                    "_meta": {"title": "Node 20", "configurable": ["prompt"]},
                },
                "10": {
                    "inputs": {
                        "image": "a.png",
                    },
                    "class_type": "LoadImage",
                    "_meta": {"title": "Node 10", "configurable": ["image"]},
                },
                "11": {
                    "inputs": {
                        "image": "b.png",
                    },
                    "class_type": "LoadImage",
                    "_meta": {"title": "Node 11", "configurable": ["image"]},
                },
            },
        )
        self.assertTrue(workflow.valid)
        self.assertEqual([field.node_id for field in workflow.fields], ["10", "30", "11", "20"])

    def test_render_all_compact_layout_expands_left_panel(self) -> None:
        app = self.make_app()
        app.is_mounted = True

        class FakeWidget:
            def __init__(self) -> None:
                self.styles = SimpleNamespace(width=None, padding_right=None)
                self.hidden = False
                self.content = None

            def add_class(self, name: str) -> None:
                if name == "hidden":
                    self.hidden = True

            def remove_class(self, name: str) -> None:
                if name == "hidden":
                    self.hidden = False

            def update(self, content: str) -> None:
                self.content = content

        top = FakeWidget()
        top_left_container = FakeWidget()
        top_left_content = FakeWidget()
        top_right = FakeWidget()
        bottom = FakeWidget()

        def fake_query_one(selector: str, _widget_type: object) -> FakeWidget:
            return {
                "#top": top,
                "#top_left": top_left_container,
                "#top_left_content": top_left_content,
                "#top_right": top_right,
                "#bottom": bottom,
            }[selector]

        with patch.object(app, "query_one", side_effect=fake_query_one), patch.object(app, "is_compact_layout", return_value=True):
            app.render_all()

        self.assertEqual(top_left_container.styles.width, "100%")
        self.assertEqual(top_left_container.styles.padding_right, 0)
        self.assertTrue(top_right.hidden)
        self.assertIsInstance(top_left_content.content, str)
        self.assertIn("Workflow Runner", top_left_content.content)

    def test_render_bottom_highlights_selected_pending_item_and_shows_wait_seconds(self) -> None:
        app = self.make_app()
        app.focus_area = "bottom"
        app.pending_index = 1
        app.runtime.pending = [
            QueueItem("p1", "1", "wf1", raw=None, queued_at=datetime.now()),
            QueueItem("p2", "2", "wf2", raw=None, queued_at=datetime.now()),
        ]
        bottom = app.render_bottom()
        self.assertIn("wait", bottom)
        self.assertIn("wf2", bottom)
        self.assertIn("p2", bottom)

    async def test_refresh_status_preserves_pending_wait_time_across_refreshes(self) -> None:
        app = self.make_app()
        try:
            app.session_tasks["p1"] = "demo"
            first = datetime(2026, 1, 1, 12, 0, 0)
            second = datetime(2026, 1, 1, 12, 0, 10)
            queue_payload = {"queue_running": [], "queue_pending": [["1", "p1"]]}
            with patch.object(app.client, "queue", new=AsyncMock(return_value=queue_payload)), patch(
                "comfyui_helper.app.datetime"
            ) as mock_datetime:
                mock_datetime.now.side_effect = [first, second]
                await app.refresh_status()
                self.assertEqual(app.runtime.pending[0].queued_at, first)
                await app.refresh_status()
                self.assertEqual(app.runtime.pending[0].queued_at, first)
        finally:
            await app.client.close()

    def test_load_config_uses_default_comfyui_server_when_missing(self) -> None:
        config = load_config(self.root)
        self.assertEqual(config.comfyui_server, DEFAULT_COMFYUI_SERVER)

    def test_load_config_reads_custom_comfyui_server(self) -> None:
        (self.root / "comfy-helper.yaml").write_text("comfyui_server: 10.0.0.5:9000\n", encoding="utf-8")
        config = load_config(self.root)
        self.assertEqual(config.comfyui_server, "10.0.0.5:9000")

    async def test_comfy_client_normalizes_http_and_https_urls(self) -> None:
        client = ComfyClient("https://example.com:8188")
        try:
            self.assertEqual(client.base_url, "https://example.com:8188")
            self.assertEqual(client.ws_url, "wss://example.com:8188/ws")
        finally:
            await client.close()

    async def test_comfy_client_defaults_to_http_without_scheme(self) -> None:
        client = ComfyClient("example.com:8188")
        try:
            self.assertEqual(client.base_url, "http://example.com:8188")
            self.assertEqual(client.ws_url, "ws://example.com:8188/ws")
        finally:
            await client.close()

    async def test_refresh_workflow_history_cache_loads_disk_history_into_right_panel(self) -> None:
        app = self.make_app()
        try:
            workflow = self.make_workflow()
            serialized = app.serialize_history_values(
                {
                    ("1", "seed"): RandomSeedValue(),
                    ("3", "prompt"): "from disk",
                }
            )
            app.save_workflow_history(workflow, serialized)
            app.load_workflow_history()
            app.workflows = [workflow]
            await app.refresh_workflow_history_cache()

            panel = app.render_workflow_history_panel()
            self.assertIn("Workflow: demo", panel)
            self.assertIn("1/seed", panel)
            self.assertIn("3/prompt", panel)
            self.assertIn("from disk", panel)
            self.assertNotIn("images from", panel)
        finally:
            await app.client.close()

    async def test_submit_workflow_records_last_submission_before_request_failure(self) -> None:
        app = self.make_app()
        try:
            workflow = replace(self.make_workflow(), data={"1": {"inputs": {}}})
            values = {
                ("1", "seed"): 7,
                ("2", "image"): ImageBatch(source="/tmp/images", images=["a.png"], shuffle=True),
                ("3", "prompt"): "hello",
            }

            async def fake_submit(*_args, **_kwargs) -> None:
                self.assertIsNotNone(app.last_submission)
                self.assertEqual(app.last_submission.workflow_name, workflow.name)
                self.assertEqual(app.last_submission.values[("3", "prompt")], "hello")
                self.assertIn(app.workflow_history_key(workflow), app.workflow_last_submissions)
                self.assertEqual(app.workflow_last_submissions[app.workflow_history_key(workflow)].values["1|seed"], 7)
                raise RuntimeError("boom")

            with patch("comfyui_helper.app.apply_field_values", return_value={}), patch.object(
                app.client, "submit", side_effect=fake_submit
            ), patch.object(app, "render_all"), patch.object(app, "refresh_status"):
                await app.submit_workflow(workflow, values)

            self.assertIsNotNone(app.last_submission)
            self.assertEqual(app.last_submission.workflow_name, workflow.name)
            self.assertEqual(app.last_submission.values[("3", "prompt")], "hello")
            self.assertEqual(app.workflow_last_submissions[app.workflow_history_key(workflow)].values["2|image"]["shuffle"], True)
        finally:
            await app.client.close()

    async def test_submit_workflow_counts_completion_that_arrives_during_request(self) -> None:
        app = self.make_app()
        try:
            workflow = replace(
                self.make_workflow(),
                data={
                    "1": {"inputs": {"seed": 1}},
                    "2": {"inputs": {"image": ""}},
                    "3": {"inputs": {"prompt": ""}},
                },
            )
            values = {
                ("1", "seed"): 7,
                ("2", "image"): "image.png",
                ("3", "prompt"): "hello",
            }

            async def fake_submit(prompt, client_id, prompt_id) -> None:
                self.assertEqual(app.session_tasks[prompt_id], workflow.name)
                await app.handle_ws_message(
                    {
                        "type": "executing",
                        "data": {
                            "prompt_id": prompt_id,
                            "node": None,
                        },
                    }
                )

            with patch("comfyui_helper.app.apply_field_values", return_value={}), patch.object(
                app.client, "submit", side_effect=fake_submit
            ), patch.object(app, "refresh_status", new=AsyncMock(return_value=None)), patch.object(
                app, "render_all"
            ):
                await app.submit_workflow(workflow, values)

            self.assertEqual(app.runtime.recent_success_count, 1)
            self.assertEqual(len(app.runtime.recent), 1)
        finally:
            await app.client.close()

    async def test_repeat_last_submission_uses_internal_tuple_values(self) -> None:
        app = self.make_app()
        try:
            workflow = replace(self.make_workflow(), data={"1": {"inputs": {}}})
            app.workflows = [workflow]
            app.runtime.online = True
            app.last_submission = LastSubmission(
                workflow_name=workflow.name,
                values={
                    ("1", "seed"): 7,
                    ("3", "prompt"): "repeat me",
                },
                count=1,
            )

            captured: dict[str, object] = {}

            async def fake_submit(submitted_workflow, values, count=1):
                captured["workflow"] = submitted_workflow
                captured["values"] = values
                captured["count"] = count

            with patch.object(app, "submit_workflow", side_effect=fake_submit), patch.object(app, "render_all"):
                await app.action_repeat_last_submission()

            self.assertIs(captured["workflow"], workflow)
            self.assertEqual(captured["values"][("3", "prompt")], "repeat me")
            self.assertEqual(captured["count"], 1)
        finally:
            await app.client.close()

    async def test_capture_current_field_value_stores_text_value(self) -> None:
        app = self.make_app()
        try:
            workflow = self.make_workflow()
            app.active_workflow = workflow
            app.active_field_index = 2
            app.active_values = {("1", "seed"): 7}

            ok = await app.capture_current_field_value("updated prompt")

            self.assertTrue(ok)
            self.assertEqual(app.active_values[("3", "prompt")], "updated prompt")
            self.assertEqual(app.input_error, None)
        finally:
            await app.client.close()

    def test_mark_recent_increments_success_count_for_completed_prompts(self) -> None:
        app = self.make_app()
        app.session_tasks["p1"] = "demo"
        app.session_tasks["p2"] = "demo"
        app.mark_recent("p1", "completed")
        app.mark_recent("p2", "completed")
        self.assertEqual(app.runtime.recent_success_count, 2)
        self.assertEqual(len(app.runtime.recent), 2)

    async def test_capture_current_field_value_stores_seed_marker(self) -> None:
        app = self.make_app()
        try:
            workflow = self.make_workflow()
            app.active_workflow = workflow
            app.active_field_index = 0

            ok = await app.capture_current_field_value(":seed")

            self.assertTrue(ok)
            self.assertIsInstance(app.active_values[("1", "seed")], RandomSeedValue)
        finally:
            await app.client.close()

    async def test_capture_current_field_value_keeps_loadimage_value_before_shuffle_prompt(self) -> None:
        comfyui_dir = self.root / "ComfyUI"
        (comfyui_dir / "input").mkdir(parents=True)
        image_dir = self.root / "images"
        image_dir.mkdir()
        (image_dir / "a.png").write_bytes(b"1")
        (image_dir / "b.png").write_bytes(b"2")

        app = self.make_app(comfyui_dir=comfyui_dir)
        try:
            workflow = self.make_workflow()
            app.active_workflow = workflow
            app.active_field_index = 1

            prompt = AsyncMock()
            with patch("comfyui_helper.app.asyncio.to_thread", new=self.immediate_to_thread), patch.object(
                app, "prompt_image_batch_shuffle", prompt
            ):
                ok = await app.capture_current_field_value(str(image_dir))

            self.assertFalse(ok)
            stored = app.active_values[("2", "image")]
            self.assertIsInstance(stored, ImageBatch)
            self.assertFalse(stored.shuffle)
            prompt.assert_awaited_once()
        finally:
            await app.client.close()

    async def test_cleanup_finished_prompts_removes_only_finished_prompt_state(self) -> None:
        app = self.make_app()
        try:
            app.session_tasks["done"] = "wf"
            app.session_tasks["active"] = "wf"
            app.session_total_nodes["done"] = 3
            app.session_total_nodes["active"] = 4
            app.session_executed_nodes["done"] = {"1", "2"}
            app.session_executed_nodes["active"] = {"1"}
            app.session_cached_nodes["done"] = {"3"}
            app.session_cached_nodes["active"] = {"2"}
            app.runtime.progress["done"] = "1/1"
            app.runtime.progress["active"] = "2/5"
            app.runtime.current_node["done"] = "3"
            app.runtime.current_node["active"] = "4"
            app.session_finished.add("done")
            app.session_finished.add("active")

            app.cleanup_finished_prompts({"active"})

            self.assertNotIn("done", app.session_tasks)
            self.assertNotIn("done", app.session_total_nodes)
            self.assertNotIn("done", app.session_executed_nodes)
            self.assertNotIn("done", app.session_cached_nodes)
            self.assertNotIn("done", app.runtime.progress)
            self.assertNotIn("done", app.runtime.current_node)
            self.assertNotIn("done", app.session_finished)

            self.assertIn("active", app.session_tasks)
            self.assertIn("active", app.session_total_nodes)
            self.assertIn("active", app.session_executed_nodes)
            self.assertIn("active", app.session_cached_nodes)
            self.assertIn("active", app.runtime.progress)
            self.assertIn("active", app.runtime.current_node)
            self.assertIn("active", app.session_finished)
        finally:
            await app.client.close()

    async def test_complete_path_keeps_trailing_slash_for_directory_prefix(self) -> None:
        app = self.make_app()
        image_dir = self.root / "images"
        image_dir.mkdir()
        (image_dir / "one.png").write_bytes(b"1")
        (image_dir / "two.jpg").write_bytes(b"2")
        try:
            common, matches = app.complete_path_value(str(image_dir) + "/")
            self.assertIsNotNone(common)
            self.assertTrue(common.endswith("/"))
            self.assertEqual(len(matches), 2)
        finally:
            await app.client.close()

    async def test_complete_path_orders_directories_before_files(self) -> None:
        app = self.make_app()
        base = self.root / "paths"
        base.mkdir()
        (base / "dir_a").mkdir()
        (base / "file_a.txt").write_text("x", encoding="utf-8")
        try:
            _, matches = app.complete_path_value(str(base) + "/")
            self.assertGreaterEqual(len(matches), 2)
            self.assertTrue(matches[0].endswith("/"))
            self.assertFalse(matches[1].endswith("/"))
        finally:
            await app.client.close()

    async def test_complete_path_supports_fields_whose_name_contains_path(self) -> None:
        app = self.make_app()
        base = self.root / "paths"
        base.mkdir()
        (base / "dir_a").mkdir()
        (base / "file_a.txt").write_text("x", encoding="utf-8")
        try:
            workflow = WorkflowInfo(
                name="path-workflow",
                path=self.root / "workflows" / "path-workflow.json",
                modified=0.0,
                valid=True,
                error=None,
                fields=[ConfigField("1", "PrimitiveString", None, "output_path", "", True, False)],
                unsupported_count=0,
                data={},
            )
            app.active_workflow = workflow
            app.mode = "input"
            app.active_field_index = 0

            class FakeInput:
                def __init__(self) -> None:
                    self.value = str(base) + "/"
                    self.cursor_position = len(self.value)
                    self.placeholder = ""
                    self.can_focus = True

                def add_class(self, _name: str) -> None:
                    pass

                def remove_class(self, _name: str) -> None:
                    pass

                def focus(self) -> None:
                    pass

            fake_input = FakeInput()
            with patch.object(app, "query_one", return_value=fake_input), patch.object(app, "render_all"):
                app.complete_path()

            self.assertEqual(fake_input.value, str(base) + "/")
            self.assertIn(str(base / "dir_a") + "/", app.completion_matches)
            self.assertIn(str(base / "file_a.txt"), app.completion_matches)
        finally:
            await app.client.close()


if __name__ == "__main__":
    unittest.main()
