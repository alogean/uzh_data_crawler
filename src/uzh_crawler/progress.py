from __future__ import annotations

import sys
import threading
import time


class Progress:
    """Line-buffered progress with an estimate of the time still to run."""

    def __init__(self, phase: str, total: int | None = None):
        self.phase = phase
        self.total = total
        self.done = 0
        self.started = time.monotonic()
        self._lock = threading.Lock()

    def set_total(self, total: int) -> None:
        with self._lock:
            self.total = total

    def note(self, message: str) -> None:
        with self._lock:
            elapsed = time.monotonic() - self.started
            self._write(f"[{self.phase}] {message} · écoulé {format_duration(elapsed)}")

    def tick(self, status: str, url: str) -> None:
        with self._lock:
            self.done += 1
            elapsed = time.monotonic() - self.started
            remaining = None
            if self.total and self.done:
                remaining = (elapsed / self.done) * max(self.total - self.done, 0)
            count = f"{self.done}/{self.total}" if self.total else str(self.done)
            parts = [
                f"[{self.phase} {count}]",
                status,
                url,
                f"écoulé {format_duration(elapsed)}",
            ]
            if remaining is not None:
                parts.append(f"reste ~{format_duration(remaining)}")
            self._write(" · ".join(parts))

    def _write(self, line: str) -> None:
        print(line, file=sys.stderr, flush=True)


def format_duration(seconds: float) -> str:
    whole = max(0, int(round(seconds)))
    if whole < 60:
        return f"{whole}s"
    minutes, secs = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}min {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}min"
