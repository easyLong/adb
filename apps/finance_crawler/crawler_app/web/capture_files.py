"""Serve captured screenshots as downloadable files."""

from __future__ import annotations

import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from apps.finance_crawler.config import Config

CAPTURE_URL_PREFIX = "/captures/"


def capture_public_url(path_text: str | Path | None, *, base_url: str | None = None) -> str:
    """Return a download URL for a screenshot stored under Config.CAPTURE_DIR."""

    raw = str(path_text or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    try:
        capture_dir = Config.CAPTURE_DIR.resolve()
        path = Path(raw)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        relative = path.relative_to(capture_dir)
    except (OSError, ValueError):
        return raw
    public_base = (base_url if base_url is not None else Config.CAPTURE_PUBLIC_BASE_URL).rstrip("/")
    return f"{public_base}{CAPTURE_URL_PREFIX}{quote(relative.as_posix(), safe='/')}"


class CaptureFileRequestHandler(BaseHTTPRequestHandler):
    server_version = "CaptureFileServer/1.0"

    def do_GET(self) -> None:
        self._send_capture_file(include_body=True)

    def do_HEAD(self) -> None:
        self._send_capture_file(include_body=False)

    def _send_capture_file(self, *, include_body: bool) -> None:
        path = self._resolve_capture_file()
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND, "capture file not found")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            if include_body:
                with path.open("rb") as file:
                    while chunk := file.read(1024 * 256):
                        self.wfile.write(chunk)
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "capture file not found")

    def _resolve_capture_file(self) -> Path | None:
        request_path = unquote(urlsplit(self.path).path)
        if not request_path.startswith(CAPTURE_URL_PREFIX):
            return None
        relative_text = request_path[len(CAPTURE_URL_PREFIX) :]
        if not relative_text or relative_text.endswith("/"):
            return None
        try:
            capture_dir = Config.CAPTURE_DIR.resolve()
            candidate = (capture_dir / relative_text).resolve()
            candidate.relative_to(capture_dir)
        except (OSError, ValueError):
            return None
        return candidate if candidate.is_file() else None

    def log_message(self, format: str, *args: object) -> None:
        return


def run_capture_file_server(*, host: str | None = None, port: int | None = None) -> None:
    bind_host = host or Config.CAPTURE_FILE_SERVER_HOST
    bind_port = int(port or Config.CAPTURE_FILE_SERVER_PORT)
    server = ThreadingHTTPServer((bind_host, bind_port), CaptureFileRequestHandler)
    print(f"capture file server: http://{bind_host}:{bind_port}{CAPTURE_URL_PREFIX}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
