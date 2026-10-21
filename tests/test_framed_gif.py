import os
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageSequence

from camera_engine import PhotoBoothEngine
from preview_window import PreviewWindow
from upload_manager import UploadManager


class FramedGifTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = PhotoBoothEngine.__new__(PhotoBoothEngine)
        self.engine.config = {
            "width": 120, "height": 180,
            "layout": [[10, 10, 100, 60], [10, 90, 80, 80]],
        }
        self.engine.output_filename = os.path.join(self.temp.name, "session.jpg")
        self.engine.upload_manager = UploadManager()
        self.photos = [Image.new("RGB", (100, 100), color) for color in ("red", "lime")]
        self.overlay = Image.new("RGBA", (120, 180), "magenta")
        draw = ImageDraw.Draw(self.overlay)
        draw.rectangle((10, 10, 109, 69), fill=(0, 0, 0, 0))
        draw.rectangle((10, 90, 89, 169), fill=(0, 0, 0, 0))

    def test_gif_keeps_entire_frame_and_animates_all_slots(self):
        gif_path = self.engine._save_gif(self.photos, overlay=self.overlay)
        self.assertIsNotNone(gif_path)
        with Image.open(gif_path) as gif:
            frames = [frame.convert("RGB") for frame in ImageSequence.Iterator(gif)]
            self.assertEqual(gif.info["loop"], 0)
        self.assertGreaterEqual(len(frames), 2)
        for frame in frames:
            self.assertEqual(frame.size, (120, 180))
            self.assertEqual(frame.getpixel((3, 3)), (255, 0, 255))
            self.assertEqual(frame.getpixel((115, 175)), (255, 0, 255))
        self.assertEqual(frames[0].getpixel((60, 40)), (255, 0, 0))
        self.assertEqual(frames[0].getpixel((50, 130)), (0, 255, 0))
        self.assertEqual(frames[1].getpixel((60, 40)), (0, 255, 0))
        self.assertEqual(frames[1].getpixel((50, 130)), (255, 0, 0))

    def test_gif_resizes_whole_template_without_changing_aspect(self):
        gif_path = self.engine._save_gif(self.photos, thumb_size=(60, 90), overlay=self.overlay)
        with Image.open(gif_path) as gif:
            self.assertEqual(gif.size, (60, 90))
            self.assertEqual(gif.convert("RGB").getpixel((1, 1)), (255, 0, 255))

    def test_process_produces_framed_jpg_and_gif(self):
        overlay_path = os.path.join(self.temp.name, "overlay.png")
        self.overlay.save(overlay_path)
        self.engine.config["overlay_full_path"] = overlay_path
        self.engine.photos = [cv2.cvtColor(np.array(photo), cv2.COLOR_RGB2BGR) for photo in self.photos]
        self.engine._x1 = self.engine._y1 = 0
        self.engine._x2 = self.engine._y2 = 100
        jpg_path, gif_path = self.engine.process()
        with Image.open(jpg_path) as jpg, Image.open(gif_path) as gif:
            self.assertEqual(jpg.size, (120, 180))
            self.assertEqual(gif.size, jpg.size)
            red, green, blue = jpg.getpixel((3, 3))
            self.assertGreater(red, 230)
            self.assertLess(green, 25)
            self.assertGreater(blue, 230)

    def make_results(self):
        self.photos[0].save(self.engine.output_filename)
        return self.engine._save_gif(self.photos, overlay=self.overlay)

    def test_bundle_contains_exactly_this_sessions_original_files(self):
        gif_path = self.make_results()
        bundle_path = self.engine.upload_manager.create_bundle(self.engine.output_filename, gif_path)
        with zipfile.ZipFile(bundle_path) as bundle:
            self.assertEqual(bundle.namelist(), ["foto.jpg", "animasi.gif"])
            self.assertIsNone(bundle.testzip())
            for path, name in ((self.engine.output_filename, "foto.jpg"), (gif_path, "animasi.gif")):
                with open(path, "rb") as source:
                    self.assertEqual(bundle.read(name), source.read())

    def test_missing_gif_does_not_create_incomplete_bundle(self):
        self.photos[0].save(self.engine.output_filename)
        with self.assertRaises(FileNotFoundError):
            self.engine.upload_manager.create_bundle(self.engine.output_filename, "missing.gif")
        self.assertFalse(os.path.exists(os.path.splitext(self.engine.output_filename)[0] + ".zip"))

    def test_bundle_upload_sends_one_zip_and_returns_one_link(self):
        gif_path = self.make_results()
        callback = Mock()
        upload_file = Mock(return_value="https://tmpfiles.org/dl/123/session.zip")
        self.engine.upload_manager.upload_file = upload_file
        worker = self.engine.upload_async(self.engine.output_filename, on_done=callback, gif_path=gif_path)
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        upload_file.assert_called_once_with(os.path.splitext(self.engine.output_filename)[0] + ".zip")
        callback.assert_called_once_with("https://tmpfiles.org/dl/123/session.zip")

    def test_bundle_preparation_failure_completes_with_failure_callback(self):
        callback = Mock()
        self.engine.upload_manager.upload_file = Mock()
        with self.assertLogs(level="ERROR"):
            worker = self.engine.upload_async(self.engine.output_filename, on_done=callback, gif_path="missing.gif")
            worker.join(timeout=3)
        self.engine.upload_manager.upload_file.assert_not_called()
        callback.assert_called_once_with(None)

    def test_bundle_preview_button_calls_bundle_action(self):
        bundle_callback = Mock()
        preview = PreviewWindow("photo.jpg", Mock(), Mock(), Mock(), "photo.gif", bundle_callback)
        preview.root = Mock()
        preview._on_upload_bundle()
        self.assertEqual(preview.result, "upload_bundle")
        preview.root.destroy.assert_called_once()
        bundle_callback.assert_called_once_with()
        preview.upload_callback.assert_not_called()

    def test_qr_encodes_single_bundle_link(self):
        url = "https://tmpfiles.org/dl/123/session.zip"
        qr_path = self.engine.upload_manager.generate_qr(url, os.path.join(self.temp.name, "qr.png"))
        decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(qr_path))
        self.assertEqual(decoded, url)

    def test_upload_keeps_official_download_page_url(self):
        gif_path = self.make_results()
        bundle_path = self.engine.upload_manager.create_bundle(self.engine.output_filename, gif_path)
        response = Mock(status_code=200)
        url = "https://tmpfiles.org/session123/photo.zip"
        response.json.return_value = {"status": "success", "data": {"url": url}}
        with patch("upload_manager.requests.post", return_value=response) as post:
            self.assertEqual(self.engine.upload_manager.upload_file(bundle_path), url)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["files"]["file"][0], "session.zip")


if __name__ == "__main__":
    unittest.main()
