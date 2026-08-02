"""
kernel/watchdog.py
Watchdog Timer для ExArchon Kernel.
"""
import os
import signal
import time
import threading
from typing import Callable, Optional


class Watchdog:
    def __init__(self, timeout_sec: float = 5.0, on_timeout: Optional[Callable[[], None]] = None):
        self.timeout_sec = timeout_sec
        self.on_timeout = on_timeout
        self._last_pet_ns = time.monotonic_ns()
        self._running = False
        self._thread = None
        self._missed_count = 0
        self._max_missed = 2

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def pet(self):
        self._last_pet_ns = time.monotonic_ns()
        self._missed_count = 0

    def _loop(self):
        check_interval = min(self.timeout_sec / 4, 1.0)
        while self._running:
            time.sleep(check_interval)
            elapsed_sec = (time.monotonic_ns() - self._last_pet_ns) / 1e9
            if elapsed_sec > self.timeout_sec:
                self._missed_count += 1
                if self._missed_count >= self._max_missed:
                    self._trigger_timeout()
                    break

    def _trigger_timeout(self):
        print(f"[WATCHDOG] Timeout after {self.timeout_sec}s. Entering SAFE mode.")
        if self.on_timeout:
            try:
                self.on_timeout()
            except Exception as e:
                print(f"[WATCHDOG] on_timeout failed: {e}")
        time.sleep(2.0)
        print("[WATCHDOG] Hard exit.")
        os._exit(1)