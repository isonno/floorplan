"""Focused tests for local file persistence and project validation."""

from __future__ import annotations

import base64
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest

from floorplan import APP_DIR, FloorplanServer, RequestError, validate_project


def sample_project() -> dict:
    return {
        "version": 1,
        "spaces": [
            {"name": "MasterBR", "color": "#14e000", "x": 1.5, "y": 2, "width": 10, "height": 12}
        ],
        "backgroundPath": None,
        "pixelsPerFoot": 24,
        "view": {"zoom": 1.6, "scrollX": 275, "scrollY": 123, "showGrid": True, "showBackground": False},
    }


class ProjectTests(unittest.TestCase):
    def test_rejects_unsnapped_nonfinite_and_nonpositive_dimensions(self) -> None:
        for field, value in (
            ("x", 0.1), ("width", 0), ("height", float("nan")), ("y", True), ("width", 10 ** 400)
        ):
            with self.subTest(field=field, value=value):
                project = sample_project()
                project["spaces"][0][field] = value
                with self.assertRaises(RequestError):
                    validate_project(project)
        project = sample_project()
        project["spaces"][0].update(x=9999, width=2)
        with self.assertRaises(RequestError):
            validate_project(project)

    def test_rejects_bad_color_and_view_without_modifying_input(self) -> None:
        project = sample_project()
        original = deepcopy(project)
        self.assertEqual(validate_project(project), original)
        self.assertEqual(project, original)
        project["spaces"][0]["color"] = "url(http://example.invalid)"
        with self.assertRaises(RequestError):
            validate_project(project)
        project = sample_project()
        project["view"]["zoom"] = 2.01
        with self.assertRaises(RequestError):
            validate_project(project)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FloorplanServer()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix=".floorplan-test-", dir=APP_DIR)
        self.folder = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()
        self.server.images.clear()

    def request(
        self, route: str, payload: object = None, headers: dict | None = None, method: str = "POST"
    ) -> tuple[int, bytes]:
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {"Content-Type": "application/json", "X-Floorplan-Token": self.server.token}
        request_headers.update(headers or {})
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        try:
            connection.request(method, route, body, request_headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_editor_served_without_exposing_other_local_files(self) -> None:
        code, body = self.request("/", method="GET")
        self.assertEqual(code, 200)
        self.assertIn(self.server.token.encode(), body)
        self.assertNotIn(b"__SESSION_TOKEN__", body)
        for route in ("/prompt.md", "/../floorplan.py", "/images/not-registered"):
            self.assertEqual(self.request(route, method="GET")[0], 404)

    def test_save_load_round_trip_unicode_paths_and_overwrite(self) -> None:
        path = self.folder / "plan with spaces \u00e9.json"
        project = sample_project()
        project["spaces"][0]["name"] = "Bedroom \u00e9"
        code, body = self.request("/api/save", {"path": str(path), "project": project})
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), project)
        code, body = self.request("/api/load", {"path": str(path)})
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["project"], project)
        self.assertEqual(self.request("/api/save", {"path": str(path), "project": project})[0], 409)
        project["spaces"][0]["width"] = 8.5
        code, body = self.request("/api/save", {"path": str(path), "project": project, "overwrite": True})
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), project)
        self.assertEqual(list(self.folder.glob(".floorplan-*.tmp")), [])

    def test_load_resolves_relative_background_and_serves_only_registered_image(self) -> None:
        image = self.folder / "background.png"
        image.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGNcAAAAASUVORK5CYII="
        ))
        project = sample_project()
        project["backgroundPath"] = image.name
        path = self.folder / "plan.json"
        path.write_text(json.dumps(project), encoding="utf-8")
        code, body = self.request("/api/load", {"path": str(path)})
        self.assertEqual(code, 200, body)
        result = json.loads(body)
        self.assertEqual(result["project"]["backgroundPath"], str(image))
        code, body = self.request(result["background"]["url"], method="GET")
        self.assertEqual(code, 200)
        self.assertEqual(body, image.read_bytes())

    def test_rejects_file_requests_without_local_session(self) -> None:
        payload = {"path": str(self.folder / "plan.json"), "project": sample_project()}
        for headers in (
            {"X-Floorplan-Token": "wrong"},
            {"Origin": "https://example.invalid"},
            {"Host": f"example.invalid:{self.server.server_port}"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/save", payload, headers)[0], 403)
        self.assertFalse((self.folder / "plan.json").exists())

    def test_invalid_project_does_not_replace_existing_file(self) -> None:
        path = self.folder / "plan.json"
        path.write_text("original", encoding="utf-8")
        project = sample_project()
        project["spaces"][0]["width"] = 1.1
        self.assertEqual(self.request(
            "/api/save", {"path": str(path), "project": project, "overwrite": True}
        )[0], 400)
        self.assertEqual(path.read_text(encoding="utf-8"), "original")

    def test_explicit_errors_for_missing_files_bad_json_and_bad_images(self) -> None:
        self.assertEqual(self.request("/api/load", {"path": str(self.folder / "missing.json")})[0], 400)
        path = self.folder / "bad.json"
        path.write_text("{", encoding="utf-8")
        self.assertEqual(self.request("/api/load", {"path": str(path)})[0], 400)
        self.assertEqual(self.request("/api/load", [str(path)])[0], 400)
        self.assertEqual(self.request("/api/background", {"path": str(path)})[0], 400)
        self.assertEqual(self.request("/api/background", {"path": str(self.folder / "missing.png")})[0], 404)
        self.assertEqual(self.request("/api/save", {"path": str(self.folder / "bad.txt"), "project": sample_project()})[0], 400)

    def test_quit_requires_session_and_stops_server_without_deadlocking(self) -> None:
        server = FloorplanServer()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("POST", "/api/quit", "{}", {"X-Floorplan-Token": "wrong"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            self.assertTrue(thread.is_alive())
            connection.request("POST", "/api/quit", "{}", {"X-Floorplan-Token": server.token})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"stopped": True})
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), "Quit must not deadlock the HTTP request thread.")
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
