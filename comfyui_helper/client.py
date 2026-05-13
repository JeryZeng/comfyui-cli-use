from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class ComfyClient:
    def __init__(self, server: str = "http://127.0.0.1:8188") -> None:
        self.server = server
        self.base_url, self.ws_url = self._build_urls(server)
        self._http = httpx.AsyncClient(timeout=10.0)

    def _build_urls(self, server: str) -> tuple[str, str]:
        value = server.strip().rstrip("/")
        if "://" not in value:
            return f"http://{value}", f"ws://{value}/ws"

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https", "ws", "wss"}:
            raise ValueError(f"Unsupported ComfyUI server URL: {server}")
        if parsed.scheme in {"http", "https"}:
            ws_scheme = "wss" if parsed.scheme == "https" else "ws"
            base_url = value
            ws_url = f"{ws_scheme}://{parsed.netloc}/ws"
            return base_url, ws_url

        http_scheme = "https" if parsed.scheme == "wss" else "http"
        base_url = f"{http_scheme}://{parsed.netloc}"
        ws_url = value if parsed.path.endswith("/ws") else f"{value}/ws"
        return base_url, ws_url

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
