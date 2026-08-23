"""Persistent local TensorFlow.js runtime for the CHESTER GraphModel."""
from __future__ import annotations

import json
import logging
import select
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_DIRECTORY = PROJECT_ROOT / "models" / "xrv-all-45rot15trans15scale"
RUNTIME_SCRIPT = PROJECT_ROOT / "scripts" / "chester_runtime.cjs"
MODEL_VERSION = "chester-tfjs:xrv-all-45rot15trans15scale"


class ChesterModel:
    """Own a long-lived Node process with one loaded CHESTER GraphModel."""

    def __init__(
        self,
        model_directory: str | Path = DEFAULT_MODEL_DIRECTORY,
        timeout_seconds: float = 90.0,
    ) -> None:
        model_path = Path(model_directory)
        if not model_path.is_absolute():
            model_path = PROJECT_ROOT / model_path
        self.model_directory = model_path.resolve()
        self.timeout_seconds = timeout_seconds
        self.version = MODEL_VERSION
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._stderr_thread: threading.Thread | None = None

        self._load_config()

    def _load_config(self) -> None:
        config_path = self.model_directory / "config.json"
        model_path = self.model_directory / "model.json"
        if not config_path.is_file() or not model_path.is_file():
            raise RuntimeError(
                f"CHESTER model assets are missing from {self.model_directory}"
            )

        try:
            config: dict[str, Any] = json.loads(config_path.read_text())
            self.image_size = int(config["IMAGE_SIZE"])
            self.image_scale = float(config["IMAGE_SCALE"])
            self.output_node = str(config["OUTPUT_NODE"])
            self.pathologies = [str(label) for label in config["LABELS"]]
            self.op_threshs = [float(value) for value in config["OP_POINT"]]
            self.scale_upper = float(config.get("SCALE_UPPER", 1.0))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid CHESTER model configuration: {exc}") from exc

        if self.image_size <= 0:
            raise RuntimeError("CHESTER IMAGE_SIZE must be positive")
        if len(self.pathologies) != 18 or len(self.op_threshs) != 18:
            raise RuntimeError("CHESTER configuration must define 18 outputs")

    def start(self) -> None:
        """Start the runtime and wait until the model is loaded."""
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        self._close_locked()
        if not RUNTIME_SCRIPT.is_file():
            raise RuntimeError(f"CHESTER runtime script is missing: {RUNTIME_SCRIPT}")

        logger.info("Loading CHESTER model from %s", self.model_directory)
        try:
            process = subprocess.Popen(
                ["node", str(RUNTIME_SCRIPT), str(self.model_directory)],
                cwd=PROJECT_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise RuntimeError(f"Unable to start CHESTER runtime: {exc}") from exc

        self._process = process
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(process,),
            daemon=True,
            name="chester-runtime-stderr",
        )
        self._stderr_thread.start()

        try:
            message = self._read_message_locked(self.timeout_seconds)
            if message.get("type") != "ready":
                error = message.get("error") or "runtime did not report readiness"
                raise RuntimeError(f"Unable to load CHESTER model: {error}")
            if message.get("inputSize") != self.image_size:
                raise RuntimeError(
                    "CHESTER runtime input size does not match config"
                )
            if message.get("outputSize") != len(self.pathologies):
                raise RuntimeError(
                    "CHESTER runtime output size does not match config"
                )
        except Exception:
            self._close_locked()
            raise

        logger.info("Model loaded: %s", self.version)

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            message = line.strip()
            if message:
                logger.debug("CHESTER runtime: %s", message)

    def _read_message_locked(self, timeout_seconds: float) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeError("CHESTER runtime is not running")

        ready, _, _ = select.select([process.stdout], [], [], timeout_seconds)
        if not ready:
            raise RuntimeError(
                f"CHESTER runtime timed out after {timeout_seconds:g} seconds"
            )

        line = process.stdout.readline()
        if not line:
            status = process.poll()
            raise RuntimeError(
                f"CHESTER runtime stopped unexpectedly (exit status {status})"
            )
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CHESTER runtime returned malformed output") from exc

    def infer(self, pixel_array: np.ndarray) -> np.ndarray:
        """Run one CHESTER inference and return its 18 sigmoid scores."""
        prepared = self.preprocess(pixel_array)
        request_id = uuid.uuid4().hex
        payload = json.dumps(
            {"id": request_id, "pixels": prepared.reshape(-1).tolist()},
            separators=(",", ":"),
        )

        with self._lock:
            try:
                self._start_locked()
                process = self._process
                if process is None or process.stdin is None:
                    raise RuntimeError("CHESTER runtime is not available")
                process.stdin.write(payload + "\n")
                process.stdin.flush()
                response = self._read_message_locked(self.timeout_seconds)

                if response.get("id") != request_id:
                    raise RuntimeError(
                        "CHESTER runtime returned an unexpected response"
                    )
                if response.get("error"):
                    raise RuntimeError(
                        f"CHESTER inference failed: {response['error']}"
                    )

                scores = np.asarray(response.get("scores"), dtype=np.float32)
                if (
                    scores.shape != (len(self.pathologies),)
                    or not np.isfinite(scores).all()
                ):
                    raise RuntimeError(
                        "CHESTER runtime returned an invalid score vector"
                    )
                return scores
            except (BrokenPipeError, OSError) as exc:
                self._close_locked()
                raise RuntimeError("CHESTER runtime stopped during inference") from exc
            except Exception:
                # A timeout, malformed frame, unexpected ID, or invalid payload
                # makes a line-oriented session unsafe to reuse. Reap it so the
                # next retry starts with a clean protocol stream.
                self._close_locked()
                raise

    def preprocess(self, pixel_array: np.ndarray) -> np.ndarray:
        """Transform an already-rendered 0..255 grayscale raster for CHESTER."""
        arr = np.asarray(pixel_array, dtype=np.float32)
        if arr.ndim != 2 or arr.size == 0:
            raise ValueError("CHESTER expects a non-empty 2D grayscale image")
        if not np.isfinite(arr).all():
            raise ValueError("CHESTER input contains non-finite pixel values")
        display_pixels = np.clip(arr, 0.0, 255.0)

        height, width = display_pixels.shape
        if width < height:
            resized_width = self.image_size
            resized_height = max(
                self.image_size,
                int(self.image_size * height / width),
            )
        else:
            resized_height = self.image_size
            resized_width = max(
                self.image_size,
                int(self.image_size * width / height),
            )

        image = Image.fromarray(display_pixels)
        resized = image.resize(
            (resized_width, resized_height),
            Image.Resampling.BILINEAR,
        )
        left = resized_width // 2 - self.image_size // 2
        top = resized_height // 2 - self.image_size // 2
        cropped = resized.crop(
            (left, top, left + self.image_size, top + self.image_size)
        )
        values = np.asarray(cropped, dtype=np.float32)
        return (values / 255.0 * 2.0 - 1.0) * self.image_scale

    def close(self) -> None:
        """Terminate the local runtime process."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        process = self._process
        stderr_thread = self._stderr_thread
        self._process = None
        self._stderr_thread = None
        if process is None:
            return

        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if (
            stderr_thread is not None
            and stderr_thread is not threading.current_thread()
        ):
            stderr_thread.join(timeout=1)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()