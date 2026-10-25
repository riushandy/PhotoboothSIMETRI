import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import tablet_server


class TabletPrintRouteTests(unittest.TestCase):
    def setUp(self):
        self.original_state = tablet_server.state.copy()
        self.original_session = tablet_server.S.copy()
        self.original_print_selection = tablet_server.PRINT_SEL.copy()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.image_path = os.path.join(self.temp_dir.name, "foto_frame.jpg")
        with open(self.image_path, "wb") as image:
            image.write(b"photo")
        tablet_server.state.update(phase="result", **{"print": ""})
        tablet_server.S["dir"] = self.temp_dir.name
        self.client = tablet_server.app.test_client()
        tablet_server.PRINT_SEL["printer"] = ""

    def tearDown(self):
        tablet_server.state.clear()
        tablet_server.state.update(self.original_state)
        tablet_server.S.clear()
        tablet_server.S.update(self.original_session)
        tablet_server.PRINT_SEL.clear()
        tablet_server.PRINT_SEL.update(self.original_print_selection)
        self.temp_dir.cleanup()

    def test_print_rejects_request_when_photo_result_is_not_ready(self):
        tablet_server.state["phase"] = "idle"

        response = self.client.post("/api/print", json={"printer": "Test Printer"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "tidak bisa")

    def test_print_rejects_unselected_printer(self):
        with patch(
            "tablet_server.api_printers",
            return_value=SimpleNamespace(json={"list": ["Test Printer"]}),
        ):
            response = self.client.post("/api/print", json={"printer": ""})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "printer belum dipilih")

    def test_print_starts_worker_once_for_selected_printer(self):
        api_printers = SimpleNamespace(json={"list": ["Test Printer"]})
        tablet_server.state["print"] = "printing"

        with patch("tablet_server.api_printers", return_value=api_printers), patch(
            "tablet_server.threading.Thread"
        ) as thread:
            duplicate = self.client.post(
                "/api/print", json={"printer": "Test Printer", "copies": 2}
            )

        self.assertEqual(duplicate.status_code, 409)
        thread.assert_not_called()

        tablet_server.state["print"] = ""
        with patch("tablet_server.api_printers", return_value=api_printers), patch(
            "tablet_server.threading.Thread"
        ) as thread:
            response = self.client.post(
                "/api/print", json={"printer": "Test Printer", "copies": 2}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(thread.call_count, 1)
        self.assertEqual(thread.call_args.kwargs["args"], ("Test Printer", 2))
        self.assertEqual(tablet_server.PRINT_SEL["printer"], "Test Printer")


if __name__ == "__main__":
    unittest.main()
