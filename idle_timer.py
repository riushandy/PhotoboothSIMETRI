import time
import tkinter as tk


class IdleTimer:
    """Timer tidak aktif dengan satu callback terjadwal dan cleanup saat window tutup."""

    def __init__(self, window, seconds, on_idle, on_activity=None):
        self.window = window
        self.seconds = seconds
        self.on_idle = on_idle
        self.on_activity = on_activity
        self._closed = False
        self._after = None
        self._deadline = time.monotonic() + seconds
        self._bindings = [
            (event, window.bind(event, self.touch, add="+"))
            for event in ("<Motion>", "<ButtonPress>", "<KeyPress>", "<MouseWheel>")
        ]
        self._bindings.append(("<Destroy>", window.bind("<Destroy>", self._on_destroy, add="+")))
        self._schedule()

    def _schedule(self):
        if not self._closed and self._after is None:
            self._after = self.window.after(500, self._tick)

    def touch(self, _event=None):
        if self._closed:
            return
        self._deadline = time.monotonic() + self.seconds
        if self.on_activity:
            self.on_activity()
        self._schedule()

    def _tick(self):
        self._after = None
        if self._closed:
            return
        if time.monotonic() >= self._deadline:
            self.on_idle()
        else:
            self._schedule()

    def _on_destroy(self, event):
        if event.widget is self.window:
            self.cancel()

    def cancel(self):
        if self._closed:
            return
        self._closed = True
        if self._after is not None:
            try:
                self.window.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        for event, binding in self._bindings:
            try:
                self.window.unbind(event, binding)
            except tk.TclError:
                pass
        self._bindings.clear()
