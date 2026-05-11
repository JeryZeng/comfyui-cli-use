from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx


class ComfyClient:
    def __init__(self, server: str = "127.0.0.1:8188") -> None:
        self.server = server
        self.base_url = f"http://{server}"
        self.ws_url = f"ws://{server}/ws"
        self._http = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def queue(self) -> dict[str, Any]:
        response = await self._http.get(f"{self.base_url}/queue")
        response.raise_for_status()
        return response.json()

    async def submit(self, workflow: dict[str, Any], client_id: str, prompt_id: str) -> dict[str, Any]:
        response = await self._http.post(
            f"{self.base_url}/prompt",
            json={"prompt": workflow, "client_id": client_id, "prompt_id": prompt_id},
        )
        response.raise_for_status()
        return response.json()

    async def interrupt(self) -> None:
        response = await self._http.post(f"{self.base_url}/interrupt", json={})
        response.raise_for_status()

    async def delete_pending(self, prompt_id: str) -> None:
        response = await self._http.post(f"{self.base_url}/queue", json={"delete": [prompt_id]})
        response.raise_for_status()

    async def clear_pending(self) -> None:
        response = await self._http.post(f"{self.base_url}/queue", json={"clear": True})
        response.raise_for_status()

    async def upload_image(self, path: Path) -> str:
        with path.open("rb") as file:
            files = {"image": (path.name, file)}
            data = {"type": "input", "overwrite": "false"}
            response = await self._http.post(f"{self.base_url}/upload/image", data=data, files=files)
        response.raise_for_status()
        payload = response.json()
        name = payload.get("name")
        subfolder = payload.get("subfolder", "")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Unexpected upload response: {json.dumps(payload)}")
        if isinstance(subfolder, str) and subfolder:
            return f"{subfolder}/{name}"
        return name
