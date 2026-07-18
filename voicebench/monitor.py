from __future__ import annotations

import subprocess
import threading
import time
from typing import Any


class GpuSampler(threading.Thread):
    """Sample global GPU telemetry while one isolated engine owns the GPU."""

    def __init__(self, interval_seconds: float = 0.35):
        super().__init__(name="voicebench-gpu-sampler", daemon=True)
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.samples: list[dict[str, float]] = []

    def stop(self) -> None:
        self.stop_event.set()

    def read_sample(self) -> dict[str, float] | None:
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used,utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
            if completed.returncode != 0:
                return None
            values = [float(item.strip()) for item in completed.stdout.strip().split(",")[:3]]
            if len(values) != 3:
                return None
            return {"memory_used_mb": values[0], "gpu_util_percent": values[1], "temperature_c": values[2]}
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def run(self) -> None:
        while not self.stop_event.is_set():
            sample = self.read_sample()
            if sample:
                self.samples.append(sample)
            self.stop_event.wait(self.interval_seconds)

    def summary(self) -> dict[str, Any]:
        if not self.samples:
            return {"sample_count": 0}
        baseline = self.samples[0]
        return {
            "sample_count": len(self.samples),
            "baseline_memory_used_mb": baseline["memory_used_mb"],
            "peak_memory_used_mb": max(item["memory_used_mb"] for item in self.samples),
            "peak_engine_delta_mb": max(item["memory_used_mb"] for item in self.samples)
            - baseline["memory_used_mb"],
            "peak_gpu_util_percent": max(item["gpu_util_percent"] for item in self.samples),
            "peak_temperature_c": max(item["temperature_c"] for item in self.samples),
        }
