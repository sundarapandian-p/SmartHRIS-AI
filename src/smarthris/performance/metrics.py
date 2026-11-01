"""Process metrics helpers for performance runs."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class MemorySample:
    peak_rss_bytes: int | None
    psutil_available: bool


class PeakRssSampler:
    """Background sampler for peak RSS of the current process."""

    def __init__(self, *, interval_seconds: float = 0.1) -> None:
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak_rss: int | None = None
        self._available = psutil is not None

    def start(self) -> None:
        if not self._available:
            return
        self._peak_rss = 0
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="perf-rss-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> MemorySample:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        return MemorySample(peak_rss_bytes=self._peak_rss, psutil_available=self._available)

    def _run(self) -> None:
        assert psutil is not None
        process = psutil.Process()
        while not self._stop.is_set():
            rss = int(process.memory_info().rss)
            if self._peak_rss is None or rss > self._peak_rss:
                self._peak_rss = rss
            time.sleep(self._interval)


def measure_callable(fn: Callable[[], None], *, sample_rss: bool = True) -> tuple[float, MemorySample]:
    """Run ``fn`` and return (duration_seconds, memory_sample)."""
    sampler = PeakRssSampler() if sample_rss else None
    if sampler is not None:
        sampler.start()
    started = time.perf_counter()
    try:
        fn()
    finally:
        duration = time.perf_counter() - started
        memory = (
            sampler.stop()
            if sampler is not None
            else MemorySample(peak_rss_bytes=None, psutil_available=psutil is not None)
        )
    return duration, memory
