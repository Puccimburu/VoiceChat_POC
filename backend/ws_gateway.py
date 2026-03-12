"""
ws_gateway.py — Single entry point for the Voice Agent Platform.

Starts three servers in one process:
  :8080  WebSocket gateway  (AI voice pipeline)
  :8081  Static file server (widget.js, VAD assets)
  :5001  REST API           (Flask, background thread)
"""

import asyncio
import logging
import os
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# gRPC environment workarounds - MUST be set before any gRPC imports
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")
os.environ.setdefault("GRPC_POLL_STRATEGY", "poll")
os.environ.setdefault("GRPC_DNS_RESOLVER", "native")

try:
    import websockets
except ImportError:
    raise ImportError("Run: pip install websockets")

from routes     import flask_app
from ws_handler import ws_handler
from pipeline.agent import prewarm_connections

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ws_gateway")


# ══════════════════════════════════════════════════════════════════
#  Static file server  (port 8081)
# ══════════════════════════════════════════════════════════════════

_WIDGET_BUILD_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'build')
_WIDGET_PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'public')
_STATIC_FILES = {
    b'/chat-widget.js':            os.path.join(_WIDGET_BUILD_DIR,  'chat-widget.js'),
    b'/widget.js':                 os.path.join(_WIDGET_BUILD_DIR,  'widget.js'),
    b'/vad.worklet.bundle.min.js': os.path.join(_WIDGET_PUBLIC_DIR, 'vad.worklet.bundle.min.js'),
    b'/silero_vad_v5.onnx':        os.path.join(_WIDGET_PUBLIC_DIR, 'silero_vad_v5.onnx'),
}

async def _handle_static(reader, writer):
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            if line in (b'\r\n', b'\n', b''):
                break
        _, path, *_ = (request_line.decode(errors='replace').split() + ['', ''])
        path_bytes = path.split('?')[0].encode()
        if path_bytes in _STATIC_FILES:
            try:
                with open(_STATIC_FILES[path_bytes], 'rb') as f:
                    body = f.read()
                content_type = (
                    b"application/octet-stream" if path_bytes.endswith(b'.onnx')
                    else b"application/javascript"
                )
                response = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: " + content_type + b"\r\n"
                    b"Access-Control-Allow-Origin: *\r\n"
                    b"Cache-Control: no-cache\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + body
                )
            except FileNotFoundError:
                response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
        else:
            response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
        writer.write(response)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


# ══════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════

async def main():
    # Bind to 127.0.0.1 in production (nginx proxies externally).
    # Change to 0.0.0.0 only for local dev without nginx.
    _bind = os.environ.get("BIND_HOST", "0.0.0.0")

    flask_thread = threading.Thread(
        target=lambda: flask_app.run(host=_bind, port=5001, debug=False, use_reloader=False),
        daemon=True,
        name="flask-api",
    )
    flask_thread.start()
    logger.info(f"[flask] REST API on http://{_bind}:5001")

    logger.info(f"[ws_gateway] WebSocket gateway on ws://{_bind}:8080/ws")
    logger.info(f"[ws_gateway] Static file server on http://{_bind}:8081")
    static_server = await asyncio.start_server(_handle_static, _bind, 8081)
    async with websockets.serve(ws_handler, _bind, 8080):
        async with static_server:
            await asyncio.Future()


def _build_widget():
    """Rebuild widget bundles if source files are newer than the built outputs."""
    frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')
    src_dir      = os.path.join(frontend_dir, 'src')

    newest_src = 0.0
    for root, _, files in os.walk(src_dir):
        for f in files:
            mtime = os.path.getmtime(os.path.join(root, f))
            if mtime > newest_src:
                newest_src = mtime

    widgets = [
        ('widget.js',      'build:widget'),
        ('chat-widget.js', 'build:chat-widget'),
    ]

    for out_name, npm_script in widgets:
        out_file    = os.path.join(frontend_dir, 'build', out_name)
        built_mtime = os.path.getmtime(out_file) if os.path.exists(out_file) else 0.0

        if newest_src <= built_mtime:
            logger.info("[widget] %s is up to date — skipping", out_name)
            continue

        logger.info("[widget] Source changed — building %s...", out_name)
        result = subprocess.run(
            f"npm run {npm_script}",
            cwd=frontend_dir,
            capture_output=True,
            text=True,
            shell=True,
        )
        if result.returncode == 0:
            logger.info("[widget] %s built successfully", out_name)
        else:
            logger.warning("[widget] Build failed for %s — serving previous build if available", out_name)
            logger.warning(result.stderr[-500:] if result.stderr else "(no output)")


if __name__ == "__main__":
    _build_widget()
    prewarm_connections()
    asyncio.run(main())
