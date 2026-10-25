import os
import queue
import tempfile
import threading
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from app import App, LiveCamSidebar
from camera_engine import PhotoBoothEngine
from idle_timer import IdleTimer
from preview_window import PreviewWindow
from upload_manager import UploadManager


class VirtualWindow:
    def __init__(self):
        self.now = 0
        self.jobs = {}
        self.events = {}
        self.counter = 0

    def after(self, milliseconds, callback):
        self.counter += 1
        key = f"timer{self.counter}"
        self.jobs[key] = (self.now + milliseconds / 1000, callback)
        return key

    def after_cancel(self, key):
        self.jobs.pop(key, None)

    def bind(self, event, callback, add=None):
        self.events[event] = callback
        return event

    def unbind(self, event, binding):
        self.events.pop(event, None)

    def advance(self, seconds):
        # Dispatch overdue callbacks once, as happens after the laptop wakes.
        self.now += seconds
        due = [key for key, (deadline, _) in self.jobs.items() if deadline <= self.now]
        for key in due:
            job = self.jobs.pop(key, None)
            if job:
                job[1]()


class IdleRecoveryTests(unittest.TestCase):
    def test_hours_without_input_trigger_once_then_activity_rearms(self):
        window = VirtualWindow()
        idle, activity = Mock(), Mock()
        with patch("idle_timer.time.monotonic", side_effect=lambda: window.now):
            timer = IdleTimer(window, 60, idle, activity)
            window.advance(8 * 3600)
            idle.assert_called_once()
            self.assertEqual(len(window.jobs), 0)
            timer.touch()
            activity.assert_called_once()
            window.advance(59)
            self.assertEqual(idle.call_count, 1)
            window.advance(2)
            self.assertEqual(idle.call_count, 2)
            timer.cancel()

    def test_input_postpones_idle_timeout(self):
        window = VirtualWindow()
        idle = Mock()
        with patch("idle_timer.time.monotonic", side_effect=lambda: window.now):
            timer = IdleTimer(window, 60, idle)
            window.advance(59)
            window.events["<ButtonPress>"](None)
            window.advance(59)
            idle.assert_not_called()
            window.advance(2)
            idle.assert_called_once()
            timer.cancel()

    def test_destroy_cancels_pending_timer_and_event_bindings(self):
        window = VirtualWindow()
        idle = Mock()
        with patch("idle_timer.time.monotonic", side_effect=lambda: window.now):
            IdleTimer(window, 60, idle)
            window.events["<Destroy>"](SimpleNamespace(widget=window))
            window.advance(3600)
        idle.assert_not_called()
        self.assertEqual(window.jobs, {})
        self.assertEqual(window.events, {})

    def test_main_idle_pauses_camera_and_activity_resumes_it(self):
        app = SimpleNamespace(
            _closing=False, _session_active=False, _preview_paused=False,
            live_cam=Mock(), _start_live_cam=Mock(),
        )
        App._pause_idle_camera(app)
        self.assertTrue(app._preview_paused)
        app.live_cam.stop.assert_called_once()
        App._resume_idle_camera(app)
        app._start_live_cam.assert_called_once()

    def test_main_idle_does_not_interrupt_photo_session(self):
        app = SimpleNamespace(_closing=False, _session_active=True, live_cam=Mock())
        App._pause_idle_camera(app)
        app.live_cam.stop.assert_not_called()

    def test_camera_waiting_timeout_releases_device_without_partial_photos(self):
        engine = PhotoBoothEngine.__new__(PhotoBoothEngine)
        engine.camera_index = 0
        engine.cancel_event = threading.Event()
        engine.config = {"photo_count": 3}
        cap = Mock()
        with patch("camera_engine.cv2.VideoCapture", return_value=cap), patch(
            "camera_engine.cv2.destroyAllWindows"
        ) as destroy, patch("camera_engine.time.monotonic", side_effect=[0, 121]):
            self.assertFalse(engine.capture())
        cap.read.assert_not_called()
        cap.release.assert_called_once()
        destroy.assert_called_once()
        self.assertEqual(engine.photos, [])

    def test_frame_render_error_does_not_stop_preview_polling(self):
        frames = queue.Queue()
        frames.put(Image.new("RGB", (10, 10)))
        sidebar = SimpleNamespace(
            _destroyed=False, _displayed_status=None, _status_label=Mock(), _camera_status="Live",
            _stop_event=threading.Event(),
            _frame_queue=frames, _update_canvas=Mock(side_effect=RuntimeError("render failure")),
            after=Mock(), _poll_frame=Mock(),
        )
        with self.assertLogs(level="ERROR"):
            LiveCamSidebar._poll_frame(sidebar)
        sidebar.after.assert_called_once_with(16, sidebar._poll_frame)

    def test_camera_retry_does_not_block_ui_waiting_for_old_worker(self):
        sidebar = SimpleNamespace(
            _destroyed=False, stop=Mock(return_value=False), after=Mock(), start=Mock(),
        )
        LiveCamSidebar.start(sidebar, 0)
        sidebar.stop.assert_called_once_with(timeout=0)
        sidebar.after.assert_called_once_with(500, sidebar.start, 0)

    def test_finished_session_returns_to_ready_without_closing_app(self):
        app = SimpleNamespace(
            _closing=False, _session_active=True, _preview_paused=True,
            _idle_timer=Mock(), _set_ui_enabled=Mock(), _start_live_cam=Mock(),
            status_label=Mock(),
        )
        App._finish_session_ui(app)
        self.assertFalse(app._session_active)
        self.assertFalse(app._preview_paused)
        app._set_ui_enabled.assert_called_once_with(True)
        app._start_live_cam.assert_called_once()
        app._idle_timer.touch.assert_called_once()

    def test_qr_expiry_uses_elapsed_time_and_calls_finish_instead_of_old_preview(self):
        root = Mock()
        clock = Mock(side_effect=[0, 3600])
        finish, old_preview = Mock(), Mock()
        with patch("upload_manager.tk.Toplevel", return_value=root), patch(
            "upload_manager.tk.Label"
        ), patch("upload_manager.tk.Frame"), patch("upload_manager.tk.Button"), patch(
            "upload_manager.tk.StringVar"
        ), patch("upload_manager.Image.open"), patch("upload_manager.ImageTk.PhotoImage"), patch(
            "upload_manager.time.monotonic", clock
        ):
            UploadManager().show_qr_window(
                "test.png", "https://example.com", close_callback=old_preview,
                timeout_callback=finish, wait=False,
            )
            root.after.call_args.args[1]()
        root.wait_window.assert_not_called()
        finish.assert_called_once()
        old_preview.assert_not_called()
        root.destroy.assert_called_once()

    def test_preview_close_is_idempotent(self):
        finish = Mock()
        preview = PreviewWindow("test.jpg", Mock(), Mock(), finish)
        preview.root = Mock()
        preview._on_save()
        preview._on_save()
        finish.assert_called_once()
        preview.root.destroy.assert_called_once()


class TkIdleIntegrationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.root.update_idletasks()
        self.addCleanup(self.root.destroy)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.jpg_path = os.path.join(self.temp.name, "test.jpg")
        self.gif_path = os.path.join(self.temp.name, "test.gif")
        Image.new("RGB", (100, 160), "red").save(self.jpg_path)
        Image.new("RGB", (100, 160), "red").save(
            self.gif_path, save_all=True,
            append_images=[Image.new("RGB", (100, 160), "blue")], duration=100, loop=0,
        )
        real_toplevel = tk.Toplevel

        def hidden_toplevel():
            window = real_toplevel(self.root)
            window.withdraw()
            return window

        patcher = patch("preview_window.tk.Toplevel", side_effect=hidden_toplevel)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ui_queue_keeps_processing_while_real_preview_is_open(self):
        preview = PreviewWindow(self.jpg_path, Mock(), Mock(), Mock(), self.gif_path)
        callbacks = queue.Queue()
        callbacks.put((lambda: preview.show(wait=False), ()))
        next_callback = Mock()
        callbacks.put((next_callback, ()))
        app = SimpleNamespace(
            _closing=False, _session_active=True, _ui_queue=callbacks,
            _finish_session_ui=Mock(), after=Mock(), _poll_ui_queue=Mock(),
        )
        App._poll_ui_queue(app)
        next_callback.assert_called_once()
        self.assertTrue(preview.root.winfo_exists())
        app.after.assert_called_once()
        preview._on_save()

    def test_preview_expires_after_simulated_sleep_and_cleans_timers(self):
        clock = [0]
        finish = Mock()
        errors = []
        self.root.report_callback_exception = lambda *error: errors.append(error)
        with patch("idle_timer.time.monotonic", side_effect=lambda: clock[0]):
            preview = PreviewWindow(self.jpg_path, Mock(), Mock(), finish, self.gif_path)
            preview.show(wait=False)
            clock[0] = 8 * 3600
            self.root.wait_window(preview.root)
            finish.assert_called_once()
            self.assertTrue(preview._closed)
            self.assertIsNone(preview._animation_after)
            self.assertIsNone(preview._idle_timer._after)
        self.root.update()
        self.assertEqual(errors, [])

    def test_repeated_open_and_close_leaves_no_scheduled_preview_callbacks(self):
        before = set(self.root.tk.call("after", "info"))
        for _ in range(25):
            preview = PreviewWindow(self.jpg_path, Mock(), Mock(), Mock(), self.gif_path)
            preview.show(wait=False)
            preview._on_save()
        self.root.update()
        self.assertEqual(set(self.root.tk.call("after", "info")), before)

    def test_real_qr_expiry_cleans_window_and_returns_to_start(self):
        manager = UploadManager()
        qr_path = manager.generate_qr("https://example.com", os.path.join(self.temp.name, "qr.png"))
        finish, old_preview = Mock(), Mock()
        clock = [0]
        before = set(self.root.tk.call("after", "info"))
        errors = []
        self.root.report_callback_exception = lambda *error: errors.append(error)
        with patch("upload_manager.time.monotonic", side_effect=lambda: clock[0]):
            window = manager.show_qr_window(
                qr_path, "https://example.com", close_callback=old_preview,
                timeout_callback=finish, wait=False,
            )
            clock[0] = 3600
            self.root.wait_window(window)
        self.root.update()
        finish.assert_called_once()
        old_preview.assert_not_called()
        self.assertEqual(set(self.root.tk.call("after", "info")), before)
        self.assertEqual(errors, [])
