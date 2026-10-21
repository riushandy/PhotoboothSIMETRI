import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from app import App, LiveCamSidebar
from camera_engine import PhotoBoothEngine
from preview_window import PreviewWindow
from upload_manager import UploadManager


def make_engine():
    # Avoid filesystem output and real camera/sticker initialization.
    engine = PhotoBoothEngine.__new__(PhotoBoothEngine)
    engine.camera_index = 2
    engine.cancel_event = threading.Event()
    engine.config = {"photo_count": 1, "layout": [[0, 0, 20, 20]]}
    engine.photos = []
    engine.filter_idx = 0
    engine.meme_manager = SimpleNamespace(
        meme_mode=False, apply_meme=lambda frame: frame
    )
    return engine


def make_sidebar():
    # Run worker logic without creating a Tk window.
    return SimpleNamespace(
        current_filter=0, PREVIEW_W=240, PREVIEW_H=180,
        _frame_queue=queue.Queue(maxsize=1), _camera_status="",
    )


class CameraRecoveryTests(unittest.TestCase):
    def test_disconnect_aborts_session_and_releases_camera(self):
        engine = make_engine()
        cap = Mock()
        cap.read.return_value = (False, None)
        with patch("camera_engine.cv2.VideoCapture", return_value=cap), patch(
            "camera_engine.cv2.destroyAllWindows"
        ) as destroy:
            with self.assertLogs(level="WARNING"):
                self.assertFalse(engine.capture())
        cap.release.assert_called_once()
        destroy.assert_called_once()
        self.assertEqual(engine.photos, [])

    def test_fallback_uses_selected_camera_and_releases_both_handles(self):
        engine = make_engine()
        first, second = Mock(), Mock()
        first.isOpened.return_value = False
        second.isOpened.return_value = False
        with patch("camera_engine.cv2.VideoCapture", side_effect=[first, second]) as open_cam, patch(
            "camera_engine.cv2.destroyAllWindows"
        ):
            with self.assertRaisesRegex(RuntimeError, "diakses"):
                engine.capture()
        self.assertEqual(open_cam.call_args_list[1].args, (2,))
        first.release.assert_called_once()
        second.release.assert_called_once()

    def test_unexpected_capture_error_always_releases_camera(self):
        engine = make_engine()
        cap = Mock()
        with patch("camera_engine.cv2.VideoCapture", return_value=cap), patch(
            "camera_engine.cv2.destroyAllWindows"
        ), patch.object(engine, "_capture", side_effect=ValueError("bad config")):
            with self.assertRaises(ValueError):
                engine.capture()
        cap.release.assert_called_once()

    def test_cancelled_session_does_not_open_camera(self):
        engine = make_engine()
        engine.cancel_event.set()
        with patch("camera_engine.cv2.VideoCapture") as open_cam, patch(
            "camera_engine.cv2.destroyAllWindows"
        ):
            self.assertFalse(engine.capture())
        open_cam.assert_not_called()

    def test_countdown_disconnect_does_not_produce_partial_result(self):
        engine = make_engine()
        cap = Mock()
        cap.read.side_effect = [(True, np.zeros((80, 100, 3), dtype=np.uint8)), (False, None)]
        with patch("camera_engine.cv2.VideoCapture", return_value=cap), patch(
            "camera_engine.cv2.destroyAllWindows"
        ), patch("camera_engine.cv2.imshow"), patch("camera_engine.cv2.waitKey", return_value=32):
            with self.assertRaisesRegex(RuntimeError, "terputus"):
                engine.capture()
        cap.release.assert_called_once()
        self.assertEqual(engine.photos, [])

    def test_normal_session_still_captures_photo(self):
        engine = make_engine()
        cap = Mock()
        cap.read.return_value = (True, np.zeros((80, 100, 3), dtype=np.uint8))
        with patch("camera_engine.cv2.VideoCapture", return_value=cap), patch(
            "camera_engine.cv2.destroyAllWindows"
        ), patch("camera_engine.cv2.imshow"), patch("camera_engine.cv2.waitKey", return_value=32), patch(
            "camera_engine.time.monotonic", side_effect=[0, 0, 0, 0, 7]
        ), patch("camera_engine._beep"):
            self.assertTrue(engine.capture())
        self.assertEqual(len(engine.photos), 1)
        cap.release.assert_called_once()

    def test_preview_reconnects_after_failed_reads_with_retry_delay(self):
        sidebar = make_sidebar()
        event = Mock()
        event.is_set.return_value = False
        event.wait.side_effect = lambda delay: setattr(event.is_set, "return_value", True) if delay == 2.0 else None
        cap = Mock()
        cap.read.return_value = (False, None)
        with patch("app.cv2.VideoCapture", return_value=cap):
            LiveCamSidebar._loop(sidebar, 2, event)
        self.assertEqual(cap.read.call_count, 10)
        cap.release.assert_called_once()
        event.wait.assert_any_call(2.0)
        self.assertIn("terputus", sidebar._camera_status)

    def test_preview_recovers_when_camera_returns(self):
        sidebar = make_sidebar()
        event = threading.Event()
        first, second = Mock(), Mock()
        first.read.return_value = (False, None)

        def recovered_read():
            event.set()
            return True, np.zeros((80, 100, 3), dtype=np.uint8)

        second.read.side_effect = recovered_read
        with patch("app.cv2.VideoCapture", side_effect=[first, second]) as open_cam, patch.object(
            event, "wait", return_value=False
        ):
            LiveCamSidebar._loop(sidebar, 2, event)
        self.assertEqual(open_cam.call_count, 2)
        second.read.assert_called_once()
        first.release.assert_called_once()
        second.release.assert_called_once()

    def test_preview_driver_exception_still_releases_and_retries(self):
        sidebar = make_sidebar()
        event = threading.Event()
        cap = Mock()
        cap.read.side_effect = RuntimeError("driver failure")
        with patch("app.cv2.VideoCapture", return_value=cap), patch.object(
            event, "wait", side_effect=lambda delay: event.set()
        ) as wait, self.assertLogs(level="ERROR"):
            LiveCamSidebar._loop(sidebar, 2, event)
        wait.assert_called_once_with(2.0)
        cap.release.assert_called_once()

    def test_failed_camera_status_clears_queued_stale_frame_and_shows_placeholder(self):
        frames = queue.Queue(maxsize=1)
        frames.put("stale")
        sidebar = SimpleNamespace(
            _destroyed=False, _displayed_status="📷 Live Preview",
            _camera_status="Kamera terputus, mencoba lagi...",
            _status_label=Mock(), _stop_event=threading.Event(), _frame_queue=frames,
            _show_camera_placeholder=Mock(), _clear_frame_queue=lambda: LiveCamSidebar._clear_frame_queue(sidebar),
            _update_canvas=Mock(), after=Mock(), _poll_frame=Mock(),
        )
        LiveCamSidebar._poll_frame(sidebar)
        self.assertTrue(frames.empty())
        sidebar._show_camera_placeholder.assert_called_once_with(sidebar._camera_status)
        sidebar._update_canvas.assert_not_called()
        sidebar._status_label.configure.assert_called_once()

    def test_live_camera_status_restores_new_frame_after_placeholder(self):
        frames = queue.Queue(maxsize=1)
        frames.put("fresh")
        sidebar = SimpleNamespace(
            _destroyed=False, _displayed_status="Kamera terputus, mencoba lagi...",
            _camera_status="📷 Live Preview", _status_label=Mock(),
            _stop_event=threading.Event(), _frame_queue=frames,
            canvas=SimpleNamespace(delete=Mock(), image="placeholder"),
            _update_canvas=Mock(), after=Mock(), _poll_frame=Mock(),
        )
        LiveCamSidebar._poll_frame(sidebar)
        sidebar._update_canvas.assert_called_once_with("fresh")
        sidebar.canvas.delete.assert_called_once_with("all")

    def test_template_card_keeps_selected_border_after_pointer_leaves(self):
        card = SimpleNamespace(_hovered=False, _selected=True, configure=Mock())
        from app import TemplateCard
        card._sync_border = lambda: TemplateCard._sync_border(card)
        card._configure_border = card.configure
        TemplateCard._on_leave(card)
        card.configure.assert_called_once_with(border_color="#f0627b", border_width=2)

    def test_template_choice_marks_matching_card_selected(self):
        card = SimpleNamespace(template_data={"name": "picked"}, set_selected=Mock())
        other = SimpleNamespace(template_data={"name": "other"}, set_selected=Mock())
        app = SimpleNamespace(
            _session_active=False, _closing=False, _template_cards=[card, other],
            _session_cancel=threading.Event(), _set_ui_enabled=Mock(),
            status_label=Mock(), live_cam=SimpleNamespace(stop=Mock(return_value=True), current_filter=0),
            _cam_index=Mock(return_value=0), _run_session=Mock(),
        )
        with patch("app.threading.Thread") as thread:
            App.on_template_select(app, card.template_data)
        card.set_selected.assert_called_once_with(True)
        other.set_selected.assert_called_once_with(False)
        thread.assert_called_once()

    def test_stop_waits_for_worker_without_releasing_its_capture(self):
        worker = Mock()
        worker.is_alive.return_value = True
        sidebar = SimpleNamespace(
            _start_after=None, _stop_event=threading.Event(),
            _thread=worker, _frame_queue=queue.Queue(maxsize=1),
        )
        self.assertFalse(LiveCamSidebar.stop(sidebar))
        self.assertTrue(sidebar._stop_event.is_set())
        worker.join.assert_called_once_with(timeout=1.0)
        self.assertIs(sidebar._thread, worker)

    def test_start_does_not_open_second_camera_while_worker_is_blocked(self):
        sidebar = SimpleNamespace(_destroyed=False, stop=Mock(return_value=False), after=Mock(), start=Mock())
        with patch("app.threading.Thread") as thread:
            LiveCamSidebar.start(sidebar, 2)
        thread.assert_not_called()
        sidebar.after.assert_called_once_with(500, sidebar.start, 2)

    def test_process_failure_restores_session(self):
        app = SimpleNamespace(
            _session_cancel=threading.Event(), _finish_session=Mock(),
            _run_on_ui=Mock(), _show_preview=Mock(),
        )
        engine = Mock()
        engine.capture.return_value = True
        engine.process.side_effect = OSError("disk full")
        with patch("app.PhotoBoothEngine", return_value=engine), self.assertLogs(level="ERROR"):
            App._run_session(app, {}, 0, 0)
        app._finish_session.assert_called_once()
        self.assertEqual(app._run_on_ui.call_args.args[1], "Sesi Foto Gagal")

    def test_failed_callback_does_not_stop_ui_queue(self):
        ui_queue = queue.Queue()
        ui_queue.put((Mock(side_effect=ValueError("bad preview")), ()))
        next_callback = Mock()
        ui_queue.put((next_callback, ()))
        app = SimpleNamespace(
            _closing=False, _session_active=True, _ui_queue=ui_queue,
            _finish_session_ui=Mock(), after=Mock(return_value="next-poll"),
            _poll_ui_queue=Mock(),
        )
        with self.assertLogs(level="ERROR"):
            App._poll_ui_queue(app)
        app._finish_session_ui.assert_called_once()
        next_callback.assert_called_once()
        app.after.assert_called_once()

    def test_preview_close_cancels_animation_before_destroy(self):
        preview = PreviewWindow("test.jpg", Mock(), Mock(), Mock())
        preview.root = Mock()
        preview._animation_after = "animation-timer"
        preview._on_save()
        self.assertEqual(preview.root.method_calls[0].args, ("animation-timer",))
        preview.root.after_cancel.assert_called_once_with("animation-timer")
        preview.root.destroy.assert_called_once()
        preview.save_callback.assert_called_once()

    def test_closing_qr_window_cancels_countdown(self):
        root = Mock()
        root.after.return_value = "qr-timer"
        root.wait_window.side_effect = lambda: root.protocol.call_args.args[1]()
        close_callback = Mock()
        with patch("upload_manager.tk.Toplevel", return_value=root), patch(
            "upload_manager.tk.Label"
        ), patch("upload_manager.tk.Frame"), patch("upload_manager.tk.Button"), patch(
            "upload_manager.tk.StringVar"
        ), patch("upload_manager.Image.open"), patch("upload_manager.ImageTk.PhotoImage"):
            UploadManager().show_qr_window("qr.png", "https://example.com", close_callback=close_callback)
        root.after_cancel.assert_called_once_with("qr-timer")
        root.destroy.assert_called_once()
        close_callback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
