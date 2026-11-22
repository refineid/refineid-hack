#!/usr/bin/env python3
"""
Gemini Priority Inference Proxy for OpenCode & OpenAI-Compatible Clients
========================================================================

Intercepts OpenAI-compatible requests from clients like OpenCode,
injects `service_tier: "priority"` into the request payload, and streams
the response back from Google AI Studio / Gemini API.

Zero external dependencies (pure Python standard library).

Usage:
    python3 gemini_priority_proxy.py [--port 8080] [--tier priority]

Then point OpenCode to:
    base_url: "http://127.0.0.1:8080/v1"
"""

import argparse
import http.server
import json
import os
import socketserver
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_HOST = os.environ.get("HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("PORT", "8080"))
DEFAULT_UPSTREAM = os.environ.get(
    "UPSTREAM_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
)
DEFAULT_TIER = os.environ.get("SERVICE_TIER", "priority")
DEFAULT_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Hop-by-hop headers that should not be forwarded
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


class ThreadedHttpServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class GeminiPriorityProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Clean custom logger
        sys.stderr.write(f"[Proxy] {self.address_string()} - {format % args}\n")

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-Requested-With, Accept, Origin",
        )

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        # Health check
        if self.path in ("/", "/health", "/v1/health"):
            body = json.dumps({
                "status": "ok",
                "proxy": "gemini-priority-proxy",
                "service_tier": self.server.service_tier,
                "upstream": self.server.upstream_url,
            }).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self._forward_request(method="GET", body=None)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        # Check if JSON payload needs priority tier injection
        modified_body = raw_body
        content_type = self.headers.get("Content-Type", "")

        if "application/json" in content_type and raw_body:
            try:
                data = json.loads(raw_body.decode("utf-8"))
                if isinstance(data, dict):
                    original_tier = data.get("service_tier")
                    data["service_tier"] = self.server.service_tier
                    model = data.get("model", "unknown")

                    print(
                        f"[inject] [INJECT] model='{model}' | service_tier: '{original_tier}' -> '{self.server.service_tier}'"
                    )
                    modified_body = json.dumps(data).encode("utf-8")
            except Exception as e:
                print(f"[warn] [WARN] Could not parse JSON body: {e}")
                modified_body = raw_body

        self._forward_request(method="POST", body=modified_body)

    def _resolve_target_url(self):
        # Normalize incoming path
        path = self.path
        # Strip leading /v1 if client sends /v1/chat/completions
        if path.startswith("/v1/"):
            path = path[3:]
        elif path == "/v1":
            path = "/"

        upstream_base = self.server.upstream_url.rstrip("/")
        clean_path = path.lstrip("/")
        return f"{upstream_base}/{clean_path}" if clean_path else upstream_base

    def _forward_request(self, method: str, body: bytes | None):
        target_url = self._resolve_target_url()
        print(f"[forward] [FORWARD] {method} {self.path} -> {target_url}")

        req = urllib.request.Request(target_url, data=body, method=method)

        # Forward headers from client
        for key, value in self.headers.items():
            if key.lower() not in HOP_BY_HOP:
                req.add_header(key, value)

        # If client didn't supply Authorization, fallback to configured API key
        if "Authorization" not in req.headers and self.server.api_key:
            req.add_header("Authorization", f"Bearer {self.server.api_key}")

        if body is not None:
            req.add_header("Content-Length", str(len(body)))
            if "Content-Type" not in req.headers:
                req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req) as resp:
                self._stream_response(resp)
        except urllib.error.HTTPError as e:
            print(f"[error] [UPSTREAM ERROR] HTTP {e.code} for {target_url}")
            self._stream_response(e)
        except Exception as e:
            print(f"[fatal] [CONNECTION ERROR] {e}")
            err_msg = json.dumps({
                "error": {
                    "message": f"Proxy connection error: {str(e)}",
                    "type": "proxy_error",
                }
            }).encode("utf-8")
            self.send_response(502)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_msg)))
            self.end_headers()
            self.wfile.write(err_msg)

    def _stream_response(self, resp):
        status_code = getattr(resp, "status", getattr(resp, "code", 200))
        self.send_response(status_code)
        self._send_cors_headers()

        resp_headers = resp.headers if hasattr(resp, "headers") else {}
        is_chunked = False
        content_length = None

        for k, v in resp_headers.items():
            k_lower = k.lower()
            if k_lower in HOP_BY_HOP:
                continue
            if k_lower == "content-length":
                content_length = v
            self.send_header(k, v)

        # If upstream didn't specify Content-Length (common for streaming SSE), use chunked transfer
        if content_length is None:
            self.send_header("Transfer-Encoding", "chunked")
            is_chunked = True

        self.end_headers()

        # Stream chunks to client in real-time
        try:
            while True:
                chunk = resp.read(2048)
                if not chunk:
                    break
                if is_chunked:
                    # Write HTTP/1.1 chunk size in hex followed by \r\n, chunk, \r\n
                    chunk_header = f"{len(chunk):X}\r\n".encode("ascii")
                    self.wfile.write(chunk_header + chunk + b"\r\n")
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()

            if is_chunked:
                # Terminal zero-length chunk to close chunked response
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            print("[info] [CLIENT DISCONNECTED] Client closed connection early.")


def main():
    parser = argparse.ArgumentParser(
        description="Gemini Priority Inference Proxy for OpenCode"
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"Host to bind (default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--upstream",
        default=DEFAULT_UPSTREAM,
        help=f"Upstream URL (default: {DEFAULT_UPSTREAM})",
    )
    parser.add_argument(
        "--tier",
        default=DEFAULT_TIER,
        help=f"Service tier to inject (default: {DEFAULT_TIER})",
    )
    parser.add_argument(
        "--api-key",
        default=DEFAULT_API_KEY,
        help="Default Gemini API Key (if not sent by client)",
    )

    args = parser.parse_args()

    server = ThreadedHttpServer((args.host, args.port), GeminiPriorityProxyHandler)
    server.upstream_url = args.upstream
    server.service_tier = args.tier
    server.api_key = args.api_key

    print("=" * 60)
    print("[start] Gemini Priority Proxy is running!")
    print(f"  - Listening on: http://{args.host}:{args.port}")
    print(f"  - Target:       {args.upstream}")
    print(f"  - Injected:     service_tier='{args.tier}'")
    print("=" * 60)
    print("\n-> Configure OpenCode settings:")
    print("   {")
    print('     "provider": "openai",')
    print(f'     "base_url": "http://{args.host}:{args.port}/v1",')
    print('     "api_key": "YOUR_GEMINI_API_KEY",')
    print('     "model": "gemini-2.5-pro"  // or gemini-4-carbon')
    print("   }")
    print("\nPress Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down proxy...")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
