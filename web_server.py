"""ComfyUI Web Server — FastAPI + SSE frontend for comfy_client."""

import asyncio
import json
import os
import random
import types
import uuid
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from comfy_client import (
    _server_to_ws,
    _unique_path,
    apply_workflow_args,
    find_node_by_title,
)

_raw_server = os.environ.get("COMFYUI_SERVER", "http://127.0.0.1:8188").rstrip("/")
if not _raw_server.startswith(("http://", "https://")):
    _raw_server = "http://" + _raw_server
DEFAULT_SERVER = _raw_server
DEFAULT_TEMPLATE = Path(__file__).parent / "t2iv2.json"
DEFAULT_OUTPUT_DIR = Path(os.environ.get("COMFYUI_OUTPUT_DIR", "./outputs"))

app = FastAPI()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "index.html"
    return FileResponse(html_path, media_type="text/html")


@app.get("/view")
async def view_proxy(request: Request):
    """Proxy GET /view to ComfyUI server."""
    server = DEFAULT_SERVER.rstrip("/")
    params = dict(request.query_params)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{server}/view", params=params, timeout=60)
        return StreamingResponse(
            content=iter([resp.content]),
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
        )


@app.post("/generate")
async def generate(request: Request):
    """SSE endpoint: submit workflow to ComfyUI and stream progress events."""
    body = await request.json()

    prompt = body.get("prompt", "")
    character = body.get("character") or None
    width = int(body.get("width", 1024))
    height = int(body.get("height", 1024))
    batch = int(body.get("batch", 1))
    seed_raw = body.get("seed")
    seed = int(seed_raw) if seed_raw not in (None, "", "null") else random.randint(0, 2**32 - 1)

    # Load workflow template
    try:
        workflow = json.loads(DEFAULT_TEMPLATE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        async def _err():
            yield _sse("error", {"message": f"Template not found: {DEFAULT_TEMPLATE}"})
        return StreamingResponse(_err(), media_type="text/event-stream")

    # Apply args via SimpleNamespace (duck-type compatible with argparse.Namespace)
    ns = types.SimpleNamespace(
        prompt=prompt,
        character=character,
        width=width,
        height=height,
        batch=batch,
    )
    try:
        apply_workflow_args(workflow, ns, seed)
    except ValueError as exc:
        async def _err():
            yield _sse("error", {"message": str(exc)})
        return StreamingResponse(_err(), media_type="text/event-stream")

    server = DEFAULT_SERVER.rstrip("/")
    client_id = str(uuid.uuid4())
    ws_url = f"{_server_to_ws(server)}/ws?clientId={client_id}"

    return StreamingResponse(
        _generate_sse(ws_url, server, workflow, client_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _save_image(server: str, img_info: dict) -> None:
    """Download an image from ComfyUI and save it to DEFAULT_OUTPUT_DIR."""
    filename = img_info.get("filename", "")
    subfolder = img_info.get("subfolder", "")
    img_type = img_info.get("type", "output")
    params = {"filename": filename, "subfolder": subfolder, "type": img_type}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{server}/view", params=params, timeout=60)
            resp.raise_for_status()
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        dest = _unique_path(DEFAULT_OUTPUT_DIR / filename)
        dest.write_bytes(resp.content)
    except Exception:
        pass  # 保存失敗はブラウザ表示に影響させない


async def _generate_sse(ws_url: str, server: str, workflow: dict, client_id: str):
    """Async generator that yields SSE strings while monitoring ComfyUI WebSocket."""
    try:
        ws_conn = websockets.connect(ws_url)
        ws = await ws_conn.__aenter__()
    except Exception as exc:
        yield _sse("error", {"message": f"WebSocket connection failed: {exc}"})
        return

    try:
        # Submit prompt
        payload = {"prompt": workflow, "client_id": client_id}
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(f"{server}/prompt", json=payload, timeout=30)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                yield _sse("error", {"message": f"HTTP {exc.response.status_code}: {exc.response.text}"})
                return
            except httpx.RequestError as exc:
                yield _sse("error", {"message": f"Request failed: {exc}"})
                return

        prompt_id = resp.json()["prompt_id"]

        # Listen on WebSocket
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            data = msg.get("data", {})

            # Filter to our prompt only
            if "prompt_id" in data and data["prompt_id"] != prompt_id:
                continue

            if mtype == "executing":
                node = data.get("node")
                if node is None:
                    continue
                node_info = workflow.get(node, {})
                node_title = node_info.get("_meta", {}).get("title") or node_info.get("class_type", node)
                yield _sse("executing", {"node": node_title})

            elif mtype == "progress":
                value = data.get("value", 0)
                max_val = data.get("max", 1)
                yield _sse("progress", {"value": value, "max": max_val})

            elif mtype == "executed":
                output = data.get("output", {})
                for img in output.get("images", []):
                    if img.get("filename", "").startswith("ComfyUI_temp_"):
                        continue
                    yield _sse("image", img)
                    await _save_image(server, img)

            elif mtype == "execution_success":
                yield _sse("done", {})
                break

            elif mtype == "execution_error":
                error_msg = data.get("exception_message", "Unknown error")
                yield _sse("error", {"message": error_msg})
                break

    except Exception as exc:
        yield _sse("error", {"message": str(exc)})
    finally:
        await ws_conn.__aexit__(None, None, None)
