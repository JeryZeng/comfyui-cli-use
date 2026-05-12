from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from comfyui_helper.app import ComfyHelperApp, ImageBatch, RandomSeedValue
from comfyui_helper.state import QueueItem
from comfyui_helper.workflow import ConfigField, WorkflowInfo


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

            values = {
                ("1", "seed"): RandomSeedValue(),
                ("2", "image"): batch_value,
                ("3", "prompt"): "hello",
            }
            serialized = app.serialize_history_values(values)
            self.assertEqual(serialized["1|seed"], {"type": "random_seed"})
            self.assertEqual(serialized["2|image"], {"type": "image_batch", "dir": str(symlink_dir.absolute())})
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


if __name__ == "__main__":
    unittest.main()
