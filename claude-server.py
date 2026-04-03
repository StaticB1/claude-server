#!/usr/bin/env python3
"""
claude-server — Local HTTP server that wraps `claude -p`.

Supports both a simple JSON API and an OpenAI-compatible API (with streaming).

Start:  python3 claude-server.py [--port 8080]

Simple API:
    curl -s -d '{"prompt":"hi"}' http://localhost:8080
    curl -s "http://localhost:8080?q=hi"

OpenAI-compatible API:
    curl -s -d '{"model":"claude","messages":[{"role":"user","content":"hi"}]}' \
         http://localhost:8080/v1/chat/completions
"""

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.stdout.reconfigure(line_buffering=True)

MODEL_NAME = "claude"


def ask_claude(prompt, skip_permissions=False):
    cmd = ["claude", "-p", prompt]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            return {"response": result.stdout.strip()}
        else:
            return {"error": result.stderr.strip() or "claude exited with error"}
    except FileNotFoundError:
        return {"error": "claude CLI not found"}
    except subprocess.TimeoutExpired:
        return {"error": "timed out"}


def extract_content(content):
    """Extract plain text from content that may be a string or array of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content) if content else ""


def strip_openclaw_metadata(text):
    """Strip OpenClaw's injected metadata header, keeping only the actual user text."""
    cleaned = re.sub(r'^.*?```\s*\n', '', text, flags=re.DOTALL)
    return cleaned.strip() if cleaned.strip() else text.strip()


def messages_to_prompt(messages):
    """Convert OpenAI messages list to a concise prompt string."""
    if not messages:
        return ""

    # Pass full system prompt so OpenClaw's injected memory context is preserved
    system = ""
    for msg in messages:
        if msg.get("role") == "system":
            system = extract_content(msg.get("content", "")).strip()
            break

    # Last 6 turns for context
    turns = [m for m in messages if m.get("role") in ("user", "assistant")]
    recent = turns[-6:]

    parts = []
    if system:
        parts.append(system)
    for msg in recent:
        role = msg.get("role")
        text = extract_content(msg.get("content", ""))
        if role == "user":
            text = strip_openclaw_metadata(text)
            parts.append(f"User: {text}")
        elif role == "assistant" and text:
            parts.append(f"Assistant: {text}")

    return "\n".join(parts)


def sse_line(chunk):
    return f"data: {json.dumps(chunk)}\n\n".encode()


def sse_chunk(content_delta, cid, model, role_only=False, finish=False):
    """Build one SSE chunk following the exact OpenAI streaming spec."""
    now = int(time.time())
    if role_only:
        delta = {"role": "assistant", "content": ""}
    elif finish:
        delta = {}
    else:
        delta = {"content": content_delta}
    return sse_line({
        "id": cid,
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": "stop" if finish else None}],
    })


def openai_response(content, model=MODEL_NAME):
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def openai_error(message, code=500):
    return {
        "error": {
            "message": message,
            "type": "server_error",
            "code": code,
        }
    }


class Handler(BaseHTTPRequestHandler):
    skip_permissions = False

    def _respond(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw), None
        except Exception:
            return None, "invalid JSON"

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._respond({"status": "ok"})
            return

        if path == "/v1/models":
            self._respond({
                "object": "list",
                "data": [
                    {"id": MODEL_NAME, "object": "model", "owned_by": "claude"},
                ],
            })
            return

        q = parse_qs(urlparse(self.path).query).get("q", [None])[0]
        if not q:
            self._respond({"usage": 'curl "http://localhost:8080?q=hello"'})
            return
        print(f"→ {q[:80]}")
        result = ask_claude(q, self.skip_permissions)
        self._respond(result, 200 if "response" in result else 500)

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/v1/chat/completions":
            self._handle_chat_completions()
            return

        body, err = self._read_body()
        if err:
            self._respond({"error": err}, 400)
            return
        prompt = body.get("prompt", "").strip()
        if not prompt:
            self._respond({"error": "missing prompt"}, 400)
            return
        print(f"→ {prompt[:80]}")
        result = ask_claude(prompt, self.skip_permissions)
        self._respond(result, 200 if "response" in result else 500)

    def _handle_chat_completions(self):
        body, err = self._read_body()
        if err:
            self._respond(openai_error(err, 400), 400)
            return

        messages = body.get("messages", [])
        if not messages:
            self._respond(openai_error("missing messages", 400), 400)
            return

        prompt = messages_to_prompt(messages)
        if not prompt:
            self._respond(openai_error("empty prompt", 400), 400)
            return

        stream = bool(body.get("stream", False))
        model = body.get("model", MODEL_NAME)
        print(f"→ [openai] stream={stream} {prompt[:80]}")

        result = ask_claude(prompt, self.skip_permissions)
        print(f"← {str(result)[:120]}")

        if "error" in result:
            if stream:
                self._stream_error(result["error"], model)
            else:
                self._respond(openai_error(result["error"]), 500)
            return

        text = result["response"]

        if stream:
            self._stream_response(text, model)
        else:
            self._respond(openai_response(text, model))

    def _stream_response(self, text, model):
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            # First chunk: role only (required by OpenAI streaming spec)
            self.wfile.write(sse_chunk("", cid, model, role_only=True))
            self.wfile.flush()
            # Content chunks
            chunk_size = 20
            for i in range(0, len(text), chunk_size):
                piece = text[i:i + chunk_size]
                self.wfile.write(sse_chunk(piece, cid, model))
                self.wfile.flush()
            # Final chunk with finish_reason
            self.wfile.write(sse_chunk("", cid, model, finish=True))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except BrokenPipeError:
            pass

    def _stream_error(self, message, model):
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(sse_chunk(f"Error: {message}", cid, model))
            self.wfile.write(sse_chunk("", cid, model, finish=True))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except BrokenPipeError:
            pass

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument(
        "--skip-permissions",
        action="store_true",
        help="Pass --dangerouslySkipPermissions to claude (for agentic/automated use)",
    )
    args = p.parse_args()

    Handler.skip_permissions = args.skip_permissions

    print(f"claude-server running on http://{args.host}:{args.port}")
    print(f"  Simple:  curl -s 'http://{args.host}:{args.port}?q=hello'")
    print(f"  OpenAI:  curl -s -d '{{\"model\":\"claude\",\"messages\":[{{\"role\":\"user\",\"content\":\"hello\"}}]}}' http://{args.host}:{args.port}/v1/chat/completions")
    if args.skip_permissions:
        print("  [!] --skip-permissions enabled: claude will run without tool permission prompts")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
