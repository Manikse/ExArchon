"""
kernel/watchdog.py
System Watchdog — Hardware + Software.
Cross-platform: /dev/watchdog is Linux-only. Windows uses software-only.
"""
import os
import time
import asyncio
import sys
import platform
from typing import Optional, Callable


class SystemWatchdog:
    """
    Dual watchdog:
    - Software: asyncio timer, resets if kernel loop responsive
    - Hardware: /dev/watchdog on Linux (if available and permitted)
    """

    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout = timeout_seconds
        self._last_pet = time.time()
        self._running = False
        self._hw_watchdog: Optional[object] = None
        self._panic_handler: Optional[Callable] = None
        self._task = None
        self._is_linux = platform.system() == "Linux"

        # Try to open hardware watchdog (Linux only)
        if self._is_linux and os.path.exists("/dev/watchdog"):
            try:
                self._hw_watchdog = open("/dev/watchdog", "w")
                print("[Watchdog] Hardware watchdog enabled (/dev/watchdog)")
            except PermissionError:
                print("[Watchdog] No permission for /dev/watchdog, using software only")
            except Exception as e:
                print(f"[Watchdog] Cannot open /dev/watchdog: {e}")

    def set_panic_handler(self, handler: Callable):
        """Called when watchdog triggers."""
        self._panic_handler = handler

    def start(self):
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        if self._hw_watchdog:
            try:
                # Magic close byte 'V' disables hardware watchdog on Linux
                self._hw_watchdog.write("V")
                self._hw_watchdog.flush()
                self._hw_watchdog.close()
            except Exception:
                pass

    def pet(self):
        """Call this regularly to keep watchdog alive."""
        self._last_pet = time.time()
        if self._hw_watchdog:
            try:
                self._hw_watchdog.write("\n")
                self._hw_watchdog.flush()
            except Exception:
                pass

    async def _watch_loop(self):
        while self._running:
            await asyncio.sleep(self.timeout / 3)
            elapsed = time.time() - self._last_pet
            if elapsed > self.timeout:
                print(f"[WATCHDOG] KERNEL UNRESPONSIVE for {elapsed:.1f}s! Triggering recovery...")
                if self._panic_handler:
                    try:
                        self._panic_handler()
                    except Exception as e:
                        print(f"[Watchdog] Panic handler failed: {e}")
                # Cross-platform exit
                if sys.platform == "win32":
                    # On Windows, os.kill with SIGTERM may not work as expected
                    # Use sys.exit for clean shutdown attempt
                    try:
                        import os
                        os._exit(1)
                    except Exception:
                        pass
                else:
                    import signal
                    os.kill(os.getpid(), signal.SIGTERM)