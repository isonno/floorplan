#!/usr/bin/env python3
"""Launch the local, dependency-free floor plan editor."""

import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit
import webbrowser


APP_DIR = Path(__file__).resolve().parent
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 50 * 1024 * 1024
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class RequestError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def local_path(value: object, base: Path = APP_DIR) -> Path:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise RequestError("Enter a nonempty local file path (at most 4096 characters).")
    path = Path(value.strip()).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def number(value: object, label: str, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not minimum <= value <= maximum
    ):
        raise RequestError(f"{label} must be a finite number from {minimum} to {maximum}.")
    return float(value)


def validate_project(value: object) -> dict:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RequestError("This is not a supported floor plan project (version 1).")
    spaces = value.get("spaces")
    if not isinstance(spaces, list) or len(spaces) > 2000:
        raise RequestError("A project must contain a list of at most 2000 spaces.")
    cleaned = []
    for space in spaces:
        if not isinstance(space, dict):
            raise RequestError("Each space must be an object.")
        name, color = space.get("name"), space.get("color")
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise RequestError("Space names must contain 1–120 characters.")
        if (
            not isinstance(color, str)
            or len(color) != 7
            or color[0] != "#"
            or any(c not in "0123456789abcdefABCDEF" for c in color[1:])
        ):
            raise RequestError("Space colors must be six-digit hexadecimal colors.")
        room = {"name": name, "color": color}
        for key in ("x", "y", "width", "height"):
            coordinate = number(
                space.get(key), f"Space {key}", 0 if key in ("x", "y") else 0.5, 10000
            )
            if not (coordinate * 2).is_integer():
                raise RequestError("All space coordinates and dimensions must use half-foot intervals.")
            room[key] = coordinate
        if room["x"] + room["width"] > 10000 or room["y"] + room["height"] > 10000:
            raise RequestError("Spaces must fit within the 10,000-foot workspace.")
        cleaned.append(room)
    background = value.get("backgroundPath")
    if background is not None and (
        not isinstance(background, str) or not background.strip() or len(background) > 4096
    ):
        raise RequestError("The background path must be a local path or null.")
    view = value.get("view")
    if not isinstance(view, dict):
        raise RequestError("The project is missing its view settings.")
    for key in ("showGrid", "showBackground"):
        if not isinstance(view.get(key), bool):
            raise RequestError(f"{key} must be true or false.")
    return {
        "version": 1,
        "spaces": cleaned,
        "backgroundPath": background,
        "pixelsPerFoot": number(value.get("pixelsPerFoot"), "Scale", 0.05, 10000),
        "view": {
            "zoom": number(view.get("zoom"), "Zoom", 0.5, 2),
            "scrollX": number(view.get("scrollX"), "Horizontal position", 0, 200000000),
            "scrollY": number(view.get("scrollY"), "Vertical position", 0, 200000000),
            "showGrid": view["showGrid"],
            "showBackground": view["showBackground"],
        },
    }


class FloorplanServer(HTTPServer):
    def __init__(self, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), FloorplanHandler)
        self.token = secrets.token_urlsafe(32)
        self.images: dict[str, tuple[Path, str]] = {}

    def register_image(self, path: Path) -> dict:
        mime = IMAGE_TYPES.get(path.suffix.lower())
        if mime is None:
            raise RequestError("Use a PNG, JPEG, GIF, WebP, or BMP background image.")
        if not path.is_file():
            raise RequestError(f"Background image not found: {path}", 404)
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise RequestError("Background images must be no larger than 50 MB.")
        key = secrets.token_urlsafe(24)
        self.images[key] = (path, mime)
        return {"path": str(path), "url": f"/images/{key}"}


class FloorplanHandler(BaseHTTPRequestHandler):
    server: FloorplanServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def permitted_host(self) -> bool:
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def respond(self, status: int, content: bytes, mime: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            f"script-src 'nonce-{self.server.token}'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def json_response(self, value: dict, status: int = 200) -> None:
        self.respond(status, json.dumps(value, allow_nan=False).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        if not self.permitted_host():
            self.json_response({"error": "Only local requests are allowed."}, 403)
            return
        route = urlsplit(self.path).path
        try:
            if route in ("/", "/floorplan.html"):
                html = (APP_DIR / "floorplan.html").read_text(encoding="utf-8")
                self.respond(
                    200,
                    html.replace("__SESSION_TOKEN__", self.server.token).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
            elif route.startswith("/images/") and route[8:] in self.server.images:
                path, mime = self.server.images[route[8:]]
                with path.open("rb") as image_file:
                    image = image_file.read(MAX_IMAGE_BYTES + 1)
                if len(image) > MAX_IMAGE_BYTES:
                    raise RequestError("Background image exceeds the 50 MB limit.")
                self.respond(200, image, mime)
            elif route == "/favicon.ico":
                self.respond(204, b"", "image/x-icon")
            else:
                self.json_response({"error": "Not found."}, 404)
        except (OSError, RequestError) as error:
            self.json_response({"error": str(error)}, getattr(error, "status", 400))

    def do_POST(self) -> None:
        if not self.permitted_host() or self.headers.get("X-Floorplan-Token") != self.server.token:
            self.json_response({"error": "Invalid local session. Reload the editor."}, 403)
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{self.headers.get('Host')}":
            self.json_response({"error": "Cross-origin file access is not allowed."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_JSON_BYTES:
                raise RequestError("Request must contain JSON no larger than 2 MB.", 413)
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise RequestError("Request must be a JSON object.")
            route = urlsplit(self.path).path
            if route == "/api/background":
                self.json_response(self.server.register_image(local_path(value.get("path"))))
            elif route == "/api/load":
                self.load_project(local_path(value.get("path")))
            elif route == "/api/save":
                self.save_project(local_path(value.get("path")), value)
            elif route == "/api/quit":
                self.json_response({"stopped": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                raise RequestError("Unknown operation.", 404)
        except (OSError, ValueError, UnicodeError) as error:
            self.json_response({"error": str(error)}, getattr(error, "status", 400))

    def load_project(self, path: Path) -> None:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise RequestError("Project files must be no larger than 2 MB.")
        project = validate_project(json.loads(path.read_text(encoding="utf-8")))
        background = None
        if project["backgroundPath"]:
            background = self.server.register_image(local_path(project["backgroundPath"], path.parent))
            project["backgroundPath"] = background["path"]
        self.json_response({"path": str(path), "project": project, "background": background})

    def save_project(self, path: Path, value: dict) -> None:
        if path.suffix.lower() != ".json":
            raise RequestError("Use a .json filename for the project.")
        project = validate_project(value.get("project"))
        if project["backgroundPath"]:
            project["backgroundPath"] = str(local_path(project["backgroundPath"]))
        if path.exists() and value.get("overwrite") is not True:
            raise RequestError("That file already exists. Replace it?", 409)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=".floorplan-", suffix=".tmp", delete=False
            ) as output:
                temporary = Path(output.name)
                json.dump(project, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        self.json_response({"path": str(path)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Local port (default: choose an available port)")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening a browser")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    with FloorplanServer(args.port) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Floor plan editor: {url}\nUse Quit in the editor or press Ctrl+C to stop.", flush=True)
        if not args.no_browser and not webbrowser.open(url):
            print("The browser could not be opened automatically. Open the URL above.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nEditor stopped.")


if __name__ == "__main__":
    main()
