"""Failure and restart tests for the CHESTER NDJSON subprocess protocol."""
from __future__ import annotations

import json

import numpy as np
import pytest

import app.chester_runtime as runtime_module
from app.chester_runtime import ChesterModel


READY = json.dumps(
    {
        "type": "ready",
        "inputSize": 224,
        "outputSize": 18,
        "outputNode": "Sigmoid_435",
    }
)


def _write_runtime(tmp_path, source: str):
    script = tmp_path / "fake_chester_runtime.cjs"
    script.write_text(source)
    return script


def test_startup_timeout_reaps_process(tmp_path, monkeypatch):
    script = _write_runtime(
        tmp_path,
        f"""
        setTimeout(() => process.stdout.write({json.dumps(READY + "\n")}), 1500);
        setInterval(() => {{}}, 1000);
        """,
    )
    monkeypatch.setattr(runtime_module, "RUNTIME_SCRIPT", script)
    model = ChesterModel(timeout_seconds=0.3)

    with pytest.raises(RuntimeError, match="timed out"):
        model.start()

    assert model._process is None


def test_response_timeout_restarts_with_clean_stream(tmp_path, monkeypatch):
    marker = tmp_path / "first-request-seen"
    scores = json.dumps([0.1] * 18)
    script = _write_runtime(
        tmp_path,
        f"""
        const fs = require("node:fs");
        const readline = require("node:readline");
        const marker = {json.dumps(str(marker))};
        process.stdout.write({json.dumps(READY + "\n")});
        (async () => {{
          const lines = readline.createInterface({{ input: process.stdin }});
          for await (const line of lines) {{
            const request = JSON.parse(line);
            const response = JSON.stringify({{ id: request.id, scores: {scores} }}) + "\\n";
            if (!fs.existsSync(marker)) {{
              fs.writeFileSync(marker, "1");
              setTimeout(() => process.stdout.write(response), 1500);
            }} else {{
              process.stdout.write(response);
            }}
          }}
        }})();
        """,
    )
    monkeypatch.setattr(runtime_module, "RUNTIME_SCRIPT", script)
    model = ChesterModel(timeout_seconds=0.4)
    image = np.full((224, 224), 128, dtype=np.float32)

    with pytest.raises(RuntimeError, match="timed out"):
        model.infer(image)
    assert model._process is None

    scores_after_restart = model.infer(image)
    try:
        assert scores_after_restart.shape == (18,)
        assert np.all(scores_after_restart == np.float32(0.1))
    finally:
        model.close()


@pytest.mark.parametrize(
    "response_source,error_match",
    [
        (r'process.stdout.write("not-json\n");', "malformed output"),
        (
            r'process.stdout.write(JSON.stringify({ id: "wrong", scores: Array(18).fill(0.1) }) + "\n");',
            "unexpected response",
        ),
        ("process.exit(3);", "stopped unexpectedly"),
    ],
)
def test_protocol_errors_reap_process(
    tmp_path,
    monkeypatch,
    response_source,
    error_match,
):
    script = _write_runtime(
        tmp_path,
        f"""
        const readline = require("node:readline");
        process.stdout.write({json.dumps(READY + "\n")});
        (async () => {{
          const lines = readline.createInterface({{ input: process.stdin }});
          for await (const line of lines) {{
            {response_source}
          }}
        }})();
        """,
    )
    monkeypatch.setattr(runtime_module, "RUNTIME_SCRIPT", script)
    model = ChesterModel(timeout_seconds=1)

    with pytest.raises(RuntimeError, match=error_match):
        model.infer(np.full((224, 224), 128, dtype=np.float32))

    assert model._process is None