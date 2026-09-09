"""Independent local transport: alert callbacks only enqueue, even during blocking Telegram I/O."""
from __future__ import annotations

import atexit
import os
import queue
import threading

import requests

from data_service.notification_events import notification_error, safe_value


class NotificationSender:
    def __init__(self, url: str, token: str):
        self.url, self.token = url, token
        self.queue = queue.Queue(maxsize=256)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="notifications-transport", daemon=True)
        self.thread.start()
        atexit.register(self.close)

    @classmethod
    def from_environment(cls):
        url = os.environ.get("PYNEREAL_NOTIFICATION_URL")
        token = os.environ.get("PYNEREAL_NOTIFICATION_TOKEN")
        return cls(url, token) if url and token else None

    def publish(self, event):
        if self.stopping.is_set():
            notification_error("runner event rejected during shutdown")
            return
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            notification_error("runner queue full; event not saved")

    def close(self):
        if not self.stopping.is_set():
            self.stopping.set()
            try:
                self.queue.put_nowait(None)
            except queue.Full:
                pass  # A full queue already wakes the worker, which drains before exiting.
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            notification_error("runner shutdown timed out; pending events may not be saved")

    def _run(self):
        with requests.Session() as client:
            client.trust_env = False
            while not self.stopping.is_set() or not self.queue.empty():
                event = self.queue.get()
                if event is None:
                    self.queue.task_done()
                    break
                try:
                    for attempt in range(3):
                        try:
                            response = client.post(self.url, json=safe_value(event),
                                                   headers={"Authorization": f"Bearer {self.token}"}, timeout=(1, 2))
                            response.raise_for_status()
                            break
                        except Exception as exc:
                            if attempt == 2:
                                notification_error(f"runner transport failed: {type(exc).__name__}")
                            else:
                                self.stopping.wait(0.25 * (attempt + 1))
                finally:
                    self.queue.task_done()
