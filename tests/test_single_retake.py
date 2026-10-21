import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PIL import Image, ImageSequence

from app import App
from camera_engine import PhotoBoothEngine, get_group_guide_box
from preview_window import PreviewWindow


class SingleRetakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = PhotoBoothEngine.__new__(PhotoBoothEngine)
        self.engine.config = {
            "photo_count": 3, "width": 120, "height": 220,
            "layout": [[10, 10, 100, 60], [10, 80, 100, 60], [10, 150, 100, 60]],
        }
        self.engine.photos = [
            np.full((80, 100, 3), color, dtype=np.uint8)
            for color in ((0, 0, 255), (255, 0, 0), (0, 255, 0))
        ]
        self.engine.photo_boxes = [get_group_guide_box(100, 80)] * 3
        self.engine.camera_index = 2
        self.engine.filter_idx = 0
        self.engine.cancel_event = threading.Event()
        self.engine.meme_manager = SimpleNamespace(
            meme_mode=False, sticker_idx=0, apply_meme=lambda frame: frame,
        )
        self.engine._x1 = self.engine._y1 = 0
        self.engine._x2, self.engine._y2 = 100, 80
        self.engine._frame_w, self.engine._frame_h = 100, 80
        self.engine.output_filename = os.path.join(self.temp.name, "original.jpg")
        self.new_frame = np.full((120, 160, 3), (0, 255, 255), dtype=np.uint8)
        self.next_path = os.path.join(self.temp.name, "replacement.jpg")

    def capture_retake(self, index, keys=32, read=None, clock=None):
        cap = Mock()
        if read is None:
            cap.read.return_value = (True, self.new_frame.copy())
        else:
            cap.read.side_effect = read
        with patch("camera_engine.create_output_path", return_value=self.next_path), patch(
            "camera_engine.cv2.VideoCapture", return_value=cap
        ), patch("camera_engine.cv2.destroyAllWindows"), patch("camera_engine.cv2.imshow"), patch(
            "camera_engine.cv2.waitKey", side_effect=keys if isinstance(keys, list) else None, return_value=keys if isinstance(keys, int) else -1
        ), patch("camera_engine.time.monotonic", side_effect=[0, 0, 0] + (clock or [0, 7])), patch("camera_engine._beep"):
            result = self.engine.retake_photo(index)
        return result, cap

    def test_retake_captures_only_one_frame_and_keeps_other_photos(self):
        candidate, cap = self.capture_retake(1)
        self.assertIsNotNone(candidate)
        self.assertEqual(cap.read.call_count, 2)
        self.assertEqual(len(candidate.photos), 3)
        self.assertIs(candidate.photos[0], self.engine.photos[0])
        self.assertIs(candidate.photos[2], self.engine.photos[2])
        np.testing.assert_array_equal(candidate.photos[1], self.new_frame)
        self.assertEqual(self.engine.photos[1][0, 0].tolist(), [255, 0, 0])
        cap.release.assert_called_once()

    def test_crop_boxes_remain_correct_when_retake_resolution_changes(self):
        candidate, _ = self.capture_retake(1)
        self.assertEqual(candidate.photo_boxes[1], get_group_guide_box(160, 120))
        self.assertEqual(candidate.photo_boxes[0], self.engine.photo_boxes[0])
        self.assertEqual(candidate.photo_boxes[2], self.engine.photo_boxes[2])
        self.assertEqual(candidate.get_photo_preview(1).size, (150, 112))
        self.assertEqual(candidate.get_photo_preview(0).size, (94, 76))

    def test_escape_preserves_original_session_and_files(self):
        original, gif = self.engine.process()
        with open(original, "rb") as file:
            jpg_before = file.read()
        with open(gif, "rb") as file:
            gif_before = file.read()
        candidate, cap = self.capture_retake(1, keys=27)
        self.assertIsNone(candidate)
        self.assertEqual(len(self.engine.photos), 3)
        self.assertFalse(os.path.exists(self.next_path))
        with open(original, "rb") as file:
            self.assertEqual(file.read(), jpg_before)
        with open(gif, "rb") as file:
            self.assertEqual(file.read(), gif_before)
        cap.release.assert_called_once()

    def test_escape_during_countdown_keeps_old_photo(self):
        candidate, cap = self.capture_retake(1, keys=[32, 27], clock=[0, 0, 0.1])
        self.assertIsNone(candidate)
        self.assertEqual(self.engine.photos[1][0, 0].tolist(), [255, 0, 0])
        cap.release.assert_called_once()

    def test_camera_failure_preserves_old_photo_and_releases_camera(self):
        cap = Mock()
        cap.read.return_value = (False, None)
        with patch("camera_engine.create_output_path", return_value=self.next_path), patch(
            "camera_engine.cv2.VideoCapture", return_value=cap
        ), patch("camera_engine.cv2.destroyAllWindows"):
            with self.assertLogs(level="WARNING"):
                self.assertIsNone(self.engine.retake_photo(1))
        self.assertEqual(self.engine.photos[1][0, 0].tolist(), [255, 0, 0])
        cap.release.assert_called_once()

    def test_invalid_index_does_not_open_camera(self):
        with patch("camera_engine.cv2.VideoCapture") as open_cam:
            for index in (-1, 3):
                with self.assertRaises(IndexError):
                    self.engine.retake_photo(index)
        open_cam.assert_not_called()

    def test_new_jpg_and_gif_use_replacement_photo(self):
        candidate, _ = self.capture_retake(1)
        original_path = self.engine.output_filename
        jpg_path, gif_path = candidate.process()
        self.assertNotEqual(jpg_path, original_path)
        with Image.open(jpg_path) as jpg:
            red, green, blue = jpg.getpixel((60, 110))
            self.assertGreater(red, 230)
            self.assertGreater(green, 230)
            self.assertLess(blue, 25)
        with Image.open(gif_path) as gif:
            first = next(ImageSequence.Iterator(gif)).convert("RGB")
            self.assertEqual(first.getpixel((60, 110)), (255, 255, 0))
            self.assertEqual(first.getpixel((60, 40)), (255, 0, 0))
            self.assertEqual(first.getpixel((60, 180)), (0, 255, 0))

    def test_retake_can_be_repeated_on_another_slot(self):
        candidate, _ = self.capture_retake(0)
        self.engine = candidate
        self.next_path = os.path.join(self.temp.name, "replacement2.jpg")
        self.new_frame = np.full((120, 160, 3), (255, 255, 255), dtype=np.uint8)
        candidate2, _ = self.capture_retake(2)
        self.assertEqual(candidate2.photos[0][0, 0].tolist(), [0, 255, 255])
        self.assertEqual(candidate2.photos[1][0, 0].tolist(), [255, 0, 0])
        self.assertEqual(candidate2.photos[2][0, 0].tolist(), [255, 255, 255])

    def make_app(self):
        return SimpleNamespace(
            _session_cancel=threading.Event(), _run_on_ui=Mock(),
            _show_retake_preview=Mock(),
        )

    def test_app_restores_previous_preview_on_cancel(self):
        app = self.make_app()
        engine = Mock()
        engine.retake_photo.return_value = None
        App._run_single_retake(app, engine, 1, "old.jpg", "old.gif")
        self.assertIs(app._run_on_ui.call_args.args[1], engine)
        self.assertEqual(app._run_on_ui.call_args.args[2:4], ("old.jpg", "old.gif"))

    def test_app_restores_previous_preview_on_processing_failure(self):
        app = self.make_app()
        engine = Mock()
        engine.retake_photo.return_value.process.side_effect = OSError("disk full")
        with self.assertLogs(level="ERROR"):
            App._run_single_retake(app, engine, 1, "old.jpg", "old.gif")
        self.assertEqual(app._run_on_ui.call_count, 2)
        self.assertIs(app._run_on_ui.call_args.args[1], engine)
        self.assertEqual(app._run_on_ui.call_args.args[2:4], ("old.jpg", "old.gif"))

    def test_app_uses_new_engine_and_outputs_after_retake(self):
        app = self.make_app()
        engine = Mock()
        candidate = engine.retake_photo.return_value
        candidate.process.return_value = ("new.jpg", "new.gif")
        App._run_single_retake(app, engine, 2, "old.jpg", "old.gif")
        self.assertIs(app._run_on_ui.call_args.args[1], candidate)
        self.assertEqual(app._run_on_ui.call_args.args[2:4], ("new.jpg", "new.gif"))

    def test_preview_dispatches_selected_slot_only(self):
        callback = Mock()
        preview = PreviewWindow("test.jpg", Mock(), Mock(), Mock(),
                                photo_previews=[Image.new("RGB", (10, 10))] * 3,
                                retake_one_callback=callback)
        preview.root = Mock()
        preview._animation_after = "gif-timer"
        preview._on_retake_one(2)
        callback.assert_called_once_with(2)
        preview.retake_callback.assert_not_called()
        preview.root.after_cancel.assert_called_once_with("gif-timer")
        preview.root.destroy.assert_called_once()

    def test_preview_keyboard_focus_cycles_and_returns_break(self):
        preview = PreviewWindow("test.jpg", Mock(), Mock(), Mock())
        first, second = Mock(), Mock()
        preview.root = Mock()
        preview.root.focus_get.return_value = first
        preview._focus_widgets = [first, second]
        self.assertEqual(preview._focus_next(), "break")
        second.focus_set.assert_called_once()
        preview.root.focus_get.return_value = second
        self.assertEqual(preview._focus_previous(), "break")
        first.focus_set.assert_called_once()

    def test_preview_close_cancels_animation_before_destroy(self):
        preview = PreviewWindow("test.jpg", Mock(), Mock(), Mock())
        preview.root = Mock()
        preview._animation_after = "animation-timer"
        preview._on_save()
        self.assertEqual(preview.root.method_calls[0].args, ("animation-timer",))
        preview.root.after_cancel.assert_called_once_with("animation-timer")
        preview.root.destroy.assert_called_once()
        preview.save_callback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
