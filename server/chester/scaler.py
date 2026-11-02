"""Apply the worker queue's replica recommendation to a Railway service.

Run this as a separate, continuously running process from the API and worker.
Keeping the scaler alive at zero worker replicas lets it notice new jobs and
start worker replicas again.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from chester import worker
from chester.config import settings
from chester.db import session_scope

logger = logging.getLogger(__name__)

RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"
RAILWAY_MAX_REPLICAS = 50
REPLICAS_QUERY = """
query ($serviceId: String!, $environmentId: String!) {
  serviceInstance(serviceId: $serviceId, environmentId: $environmentId) {
    numReplicas
    region
  }
}
"""
REPLICAS_MUTATION = """
mutation ($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
  serviceInstanceUpdate(serviceId: $serviceId, environmentId: $environmentId, input: $input)
}
"""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured for the Railway worker scaler.")
    return value


def validate_configuration() -> None:
    """Fail this isolated controller early for incomplete or impossible config."""
    if settings.database_url.startswith("sqlite"):
        raise RuntimeError("DATABASE_URL must point to the shared PostgreSQL database.")
    _required_env("RAILWAY_API_TOKEN")
    _required_env("RAILWAY_TARGET_WORKER_SERVICE_ID")
    _required_env("RAILWAY_TARGET_ENVIRONMENT_ID")
    token_type = os.environ.get("RAILWAY_TOKEN_TYPE", "project").strip().lower()
    if token_type not in {"project", "account", "workspace"}:
        raise RuntimeError("RAILWAY_TOKEN_TYPE must be project, account, or workspace.")
    ceiling = min(settings.worker_max_replicas, RAILWAY_MAX_REPLICAS)
    if settings.worker_min_replicas > ceiling:
        raise RuntimeError(
            "WORKER_MIN_REPLICAS cannot exceed the configured or Railway replica ceiling."
        )


def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    token = _required_env("RAILWAY_API_TOKEN")
    token_type = os.environ.get("RAILWAY_TOKEN_TYPE", "project").strip().lower()
    auth_header = "Project-Access-Token" if token_type == "project" else "Authorization"
    auth_value = token if token_type == "project" else f"Bearer {token}"

    request = Request(
        RAILWAY_GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            auth_header: auth_value,
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Railway's edge rejects urllib's default Python user agent (1010).
            "User-Agent": "torax-queue-scaler/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            error_payload = json.loads(exc.read(8192).decode("utf-8"))
            messages = [
                str(error.get("message", ""))[:300].replace(token, "[redacted]")
                for error in error_payload.get("errors", [])
                if isinstance(error, dict)
            ]
            detail = " " + "; ".join(messages) if messages else ""
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"Railway GraphQL returned HTTP {exc.code}.{detail}") from None

    if not isinstance(payload, dict):
        raise RuntimeError("Railway GraphQL returned an invalid response.")
    errors = payload.get("errors")
    if errors:
        messages = "; ".join(
            str(error.get("message", "unknown GraphQL error"))
            for error in errors
            if isinstance(error, dict)
        )
        raise RuntimeError(f"Railway GraphQL request failed: {messages or 'unknown error'}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Railway GraphQL response did not contain data.")
    return data


def worker_instance() -> dict[str, Any]:
    data = _graphql(
        REPLICAS_QUERY,
        {
            "serviceId": _required_env("RAILWAY_TARGET_WORKER_SERVICE_ID"),
            "environmentId": _required_env("RAILWAY_TARGET_ENVIRONMENT_ID"),
        },
    )
    instance = data.get("serviceInstance")
    if not isinstance(instance, dict):
        raise RuntimeError("Railway did not return the configured worker service.")
    regions = instance.get("multiRegionConfig")
    if regions and (not isinstance(regions, dict) or len(regions) != 1):
        raise RuntimeError("The queue scaler requires a single worker region.")
    return instance


def current_replicas() -> int:
    instance = worker_instance()
    regions = instance.get("multiRegionConfig")
    if regions:
        config = next(iter(regions.values()))
        return int(config["numReplicas"])
    # A null setting means the service is using Railway's default of one replica.
    return int(instance["numReplicas"]) if instance.get("numReplicas") is not None else 1


def set_replicas(target: int) -> None:
    instance = worker_instance()
    regions = instance.get("multiRegionConfig")
    update: dict[str, Any] = {"numReplicas": target}
    if regions:
        # An explicit region map overrides the legacy top-level replica count.
        region, config = next(iter(regions.items()))
        update["multiRegionConfig"] = {
            region: {**config, "numReplicas": target},
        }
    else:
        region = os.environ.get("RAILWAY_TARGET_WORKER_REGION") or instance.get("region")
        if not region:
            raise RuntimeError("RAILWAY_TARGET_WORKER_REGION must identify the worker region.")
        update["multiRegionConfig"] = {region: {"numReplicas": target}}
    _graphql(
        REPLICAS_MUTATION,
        {
            "serviceId": _required_env("RAILWAY_TARGET_WORKER_SERVICE_ID"),
            "environmentId": _required_env("RAILWAY_TARGET_ENVIRONMENT_ID"),
            "input": update,
        },
    )


def recommended_replicas() -> tuple[dict[str, int | float], int]:
    with session_scope() as db:
        pressure = worker.queue_pressure(db)
    desired = worker.desired_worker_replicas(pressure)
    ceiling = min(settings.worker_max_replicas, RAILWAY_MAX_REPLICAS)
    target = min(ceiling, max(settings.worker_min_replicas, desired))
    return pressure, target


def scale_once() -> int:
    """Read queue pressure and apply its target; raise on failure for the loop."""
    pressure, target = recommended_replicas()
    current = current_replicas()
    if current == target:
        logger.info(
            "Worker scale unchanged: replicas=%d queued=%d oldest_ready_seconds=%.1f",
            current,
            pressure["queued_total"],
            pressure["oldest_ready_seconds"],
        )
        return target

    # Railway chooses which replicas to remove. Do not reduce a busy fleet:
    # an empty *waiting* queue does not mean the claimed jobs have finished.
    if target < current and (
        pressure["queued_total"] or pressure["processing_total"]
    ):
        logger.info(
            "Worker scale-down deferred: replicas=%d queued=%d processing=%d",
            current,
            pressure["queued_total"],
            pressure["processing_total"],
        )
        return current

    set_replicas(target)
    logger.info(
        "Worker scale applied: replicas=%d->%d queued=%d oldest_ready_seconds=%.1f",
        current,
        target,
        pressure["queued_total"],
        pressure["oldest_ready_seconds"],
    )
    return target


def run(stop: threading.Event | None = None, *, once: bool = False) -> None:
    """Poll independently; a failed database/API call is retried next interval."""
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            scale_once()
        except Exception:
            if once:
                raise
            logger.exception("Worker autoscaling check failed; retrying next interval")
        if once:
            return
        stop.wait(timeout=settings.worker_scaler_poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scale Railway worker replicas from queue pressure"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="perform one scaling check and exit",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    validate_configuration()

    stop = threading.Event()

    def _handle_signal(signum, _frame):
        logger.info("Received signal %s; stopping scaler", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run(stop, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
