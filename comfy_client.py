"""ComfyUI Python API Client — single-file CLI."""

import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
import websockets

DEFAULT_SERVER = os.environ.get("COMFYUI_SERVER", "http://127.0.0.1:8188")
DEFAULT_TEMPLATE = "t2iv2.json"
DEFAULT_OUTPUT_DIR = "./outputs"


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------

def find_node_by_title(workflow: dict, title: str) -> dict:
    for node in workflow.values():
        if node.get("_meta", {}).get("title") == title:
            return node
    raise ValueError(f"Node '{title}' not found in workflow")


def apply_workflow_args(workflow: dict, args: argparse.Namespace, seed: int) -> None:
    """Mutate *workflow* in place with values from *args*."""
    find_node_by_title(workflow, "Positive Prompt")["inputs"]["value"] = args.prompt
    find_node_by_title(workflow, "Seed")["inputs"]["value"] = seed

    latent = find_node_by_title(workflow, "Empty Latent Image")["inputs"]
    latent["width"] = args.width
    latent["height"] = args.height
    latent["batch_size"] = args.batch

    if args.character is not None:
        find_node_by_title(workflow, "Character Prompt")["inputs"]["text"] = args.character


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def display_progress(value: int, max_val: int) -> None:
    bar_width = 20
    pct = value / max_val if max_val else 0
    filled = int(bar_width * pct)
    bar = "=" * filled + " " * (bar_width - filled)
    print(
        f"\r[Progress ] Step {value:>2}/{max_val} [{bar}] {int(pct * 100):>3}%",
        end="",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Async core
# ---------------------------------------------------------------------------

async def listen_and_download(
    ws_url: str,
    server: str,
    workflow: dict,
    client_id: str,
    output_dir: Path,
    include_temp: bool = False,
) -> list[Path]:
    """Submit the workflow, monitor via WebSocket, download images."""

    # Connect WebSocket BEFORE submitting
    try:
        ws_conn = websockets.connect(ws_url)
        ws = await ws_conn.__aenter__()
    except Exception as exc:
        print(f"[ERROR] WebSocket connection failed ({ws_url}): {exc}")
        sys.exit(1)

    try:
        # Submit prompt
        payload = {"prompt": workflow, "client_id": client_id}
        try:
            resp = requests.post(f"{server}/prompt", json=payload, timeout=30)
            resp.raise_for_status()
        except requests.HTTPError as exc:
            print(f"[ERROR] HTTP {exc.response.status_code}: {exc.response.text}")
            sys.exit(1)
        except requests.RequestException as exc:
            print(f"[ERROR] Request failed: {exc}")
            sys.exit(1)

        prompt_id = resp.json()["prompt_id"]

        # Listen on WebSocket
        collected_images: list[dict] = []
        current_node = ""
        last_was_progress = False

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            data = msg.get("data", {})

            # Filter to our prompt only (once we have a prompt_id)
            if "prompt_id" in data and data["prompt_id"] != prompt_id:
                continue

            if mtype == "executing":
                node = data.get("node")
                if node is None:
                    # Execution finished signal (node=null)
                    continue
                if last_was_progress:
                    print()  # newline after progress bar
                    last_was_progress = False
                current_node = node
                # Try to get a friendly name
                node_info = workflow.get(node, {})
                node_title = node_info.get("_meta", {}).get("title") or node_info.get("class_type", node)
                print(f"[Executing] Node: {node_title}")

            elif mtype == "progress":
                value = data.get("value", 0)
                max_val = data.get("max", 1)
                display_progress(value, max_val)
                last_was_progress = True
                if value >= max_val:
                    print()  # newline on completion
                    last_was_progress = False

            elif mtype == "executed":
                if "output" in data:
                    output = data["output"]
                    images = output.get("images", [])
                    collected_images.extend(images)

            elif mtype == "execution_success":
                if last_was_progress:
                    print()
                break

            elif mtype == "execution_error":
                if last_was_progress:
                    print()
                error_msg = data.get("exception_message", "Unknown error")
                raise RuntimeError(f"Execution error: {error_msg}")

    finally:
        await ws_conn.__aexit__(None, None, None)

    # Download images
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nExecution complete. Downloading {len(collected_images)} image(s)...")

    saved_paths: list[Path] = []
    for img_info in collected_images:
        filename = img_info.get("filename", "")
        subfolder = img_info.get("subfolder", "")
        img_type = img_info.get("type", "output")

        if not include_temp and filename.startswith("ComfyUI_temp_"):
            print(f"  Skipped (temp): {filename}")
            continue

        params = {"filename": filename, "subfolder": subfolder, "type": img_type}
        try:
            dl = requests.get(f"{server}/view", params=params, timeout=60)
            dl.raise_for_status()
        except requests.RequestException as exc:
            print(f"  [WARN] Failed to download {filename}: {exc}")
            continue

        dest = _unique_path(output_dir / filename)
        dest.write_bytes(dl.content)
        print(f"  Saved: {dest}")
        saved_paths.append(dest)

    return saved_paths


def _unique_path(path: Path) -> Path:
    """Return *path*, appending _N suffix if it already exists."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_STDIN_EPILOG = """\
STDIN USAGE (multi-line prompts)
  Omit -p to read the positive prompt from standard input.

  Windows CMD — pipe from file:
    type prompt.txt | python comfy_client.py -W 1152 -H 896 -b 1

  PowerShell — here-string:
    @"
    a beautiful landscape, detailed
    high quality, 8k
    "@ | python comfy_client.py -W 1152 -H 896 -b 1

  PowerShell — pipe from file:
    Get-Content prompt.txt -Raw | python comfy_client.py -W 1152 -H 896 -b 1

  Bash / shell script — heredoc:
    python comfy_client.py -W 1152 -H 896 -b 1 <<'EOF'
    a beautiful landscape, detailed
    high quality, 8k
    EOF

  Bash / shell script — redirect from file:
    python comfy_client.py -W 1152 -H 896 -b 1 < prompt.txt
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="comfy_client",
        description="Submit a ComfyUI workflow and download the results.",
        epilog=_STDIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-p", "--prompt", default=None,
        help="Positive prompt text. Omit to read from stdin (supports multi-line).",
    )
    parser.add_argument("-W", "--width", type=int, default=1024, help="Image width (default: 1024)")
    parser.add_argument("-H", "--height", type=int, default=1024, help="Image height (default: 1024)")
    parser.add_argument("-b", "--batch", type=int, default=1, help="Batch size (default: 1)")
    parser.add_argument("-c", "--character", default=None, help="Character prompt (optional)")
    parser.add_argument("-s", "--seed", type=int, default=None, help="Seed (random if omitted)")
    parser.add_argument("-o", "--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help="ComfyUI server URL (env: COMFYUI_SERVER)",
    )
    parser.add_argument("-t", "--template", default=DEFAULT_TEMPLATE, help="Workflow template path")
    parser.add_argument(
        "--include-temp", action="store_true",
        help="Also save ComfyUI_temp_* files (skipped by default)",
    )
    return parser.parse_args()


def _server_to_ws(server_url: str) -> str:
    """Convert http(s):// URL to ws(s):// equivalent."""
    parsed = urlparse(server_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=ws_scheme))


def main() -> None:
    args = parse_args()

    # Read prompt from stdin if -p was omitted
    if args.prompt is None:
        if sys.stdin.isatty():
            print("Enter positive prompt (Ctrl+Z then Enter on Windows / Ctrl+D on Unix to finish):")
        args.prompt = sys.stdin.read().strip()
        if not args.prompt:
            print("[ERROR] Prompt is empty. Provide -p or pipe text via stdin.")
            sys.exit(1)

    # Resolve template
    template_path = Path(args.template)
    if not template_path.exists():
        print(f"[ERROR] Template file not found: {template_path}")
        sys.exit(1)

    workflow = json.loads(template_path.read_text(encoding="utf-8"))

    seed = args.seed if args.seed is not None else random.randint(0, 2**32 - 1)

    apply_workflow_args(workflow, args, seed)

    server = args.server.rstrip("/")
    if not server.startswith(("http://", "https://")):
        server = "http://" + server
    output_dir = Path(args.output_dir)
    client_id = str(uuid.uuid4())
    ws_url = f"{_server_to_ws(server)}/ws?clientId={client_id}"

    print(f"Server   : {server}")
    print(f"Template : {template_path}")
    print(f"Prompt   : {args.prompt}")
    print(f"Seed     : {seed}")
    print(f"Size     : {args.width}x{args.height}  batch={args.batch}")
    print(f"Output   : {output_dir}")
    print()

    try:
        asyncio.run(
            listen_and_download(ws_url, server, workflow, client_id, output_dir, args.include_temp)
        )
    except RuntimeError as exc:
        print(f"\n[ERROR] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(130)

    print("Done.")


if __name__ == "__main__":
    main()
