import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from camera_engine import PhotoBoothEngine, create_output_path


class AutoHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history_dir = os.path.join(self.temp.name, "riwayat")
        patcher = patch("camera_engine.OUTPUT_DIR", self.history_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_engine(self, color=(0, 0, 255)):
        engine = PhotoBoothEngine.__new__(PhotoBoothEngine)
        engine.output_filename = create_output_path()
        engine.config = {"width": 80, "height": 100, "layout": [[10, 10, 60, 80]]}
        engine.photos = [np.full((100, 80, 3), color, dtype=np.uint8)]
        engine.photo_boxes = [(0, 0, 80, 100)]
        return engine

    def test_results_exist_without_opening_preview_or_pressing_save(self):
        engine = self.make_engine()
        jpg_path, gif_path = engine.process()
        self.assertEqual(os.path.dirname(jpg_path), self.history_dir)
        self.assertEqual(os.path.dirname(gif_path), self.history_dir)
        with Image.open(jpg_path) as jpg, Image.open(gif_path) as gif:
            self.assertEqual(jpg.size, (80, 100))
            self.assertEqual(gif.size, (80, 100))

    def test_same_timestamp_sessions_do_not_overwrite_history(self):
        frozen_clock = Mock()
        frozen_clock.now.return_value = datetime(2026, 10, 6, 12, 0, 0)
        with patch("camera_engine.datetime", frozen_clock):
            first_engine = self.make_engine()
            first_jpg, first_gif = first_engine.process()
            with open(first_jpg, "rb") as file:
                first_bytes = file.read()
            second_engine = self.make_engine(color=(0, 255, 0))
            second_jpg, second_gif = second_engine.process()
        self.assertNotEqual(first_jpg, second_jpg)
        self.assertNotEqual(first_gif, second_gif)
        with open(first_jpg, "rb") as file:
            self.assertEqual(file.read(), first_bytes)
        self.assertEqual(len(os.listdir(self.history_dir)), 4)

    def test_photo_stays_saved_when_gif_generation_fails(self):
        engine = self.make_engine()
        with patch.object(engine, "_save_gif", return_value=None):
            jpg_path, gif_path = engine.process()
        self.assertIsNone(gif_path)
        with Image.open(jpg_path) as jpg:
            jpg.verify()


if __name__ == "__main__":
    unittest.main()
