#!/usr/bin/env python3
"""Measure verified Supabase free-tier usage and apply BoardLog safeguards."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
LIMITS = {
    "databaseBytes": 500_000_000,
    "storageBytes": 1_000_000_000,
    "monthlyActiveUsers": 50_000,
    "uncachedEgressBytes": 5_000_000_000,
    "cachedEgressBytes": 5_000_000_000,
    "edgeFunctionInvocations": 500_000,
    "realtimeMessages": 2_000_000,
    "realtimePeakConnections": 200,
}
REQUIRED_METRICS = ("databaseBytes", "storageBytes")


class MonitorError(RuntimeError):
    """Abort without changing the last verified public service status."""


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, _request, _file_pointer, _code, _message, _headers, _new_url):
        return None


class UsageRemote(Protocol):
    def fetch_snapshot(self) -> Mapping[str, Any]: ...
    def apply_status(self, report: Mapping[str, Any]) -> None: ...


def _parse_captured_at(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise MonitorError("Usage snapshot has no verified UTC capture time")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise MonitorError("Usage snapshot capture time is invalid") from error
    if parsed.tzinfo is None:
        raise MonitorError("Usage snapshot capture time has no timezone")
    return value


def _validated_metric(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MonitorError(f"Usage metric {name} is not a verified non-negative integer")
    return value


def _status_for_ratio(ratio: Decimal) -> tuple[str, str]:
    if ratio >= Decimal("1"):
        return "EXHAUSTED_100", "SUBMISSION_CLOSED"
    if ratio >= Decimal("0.95"):
        return "CRITICAL_95", "IMAGE_LIMITED"
    if ratio >= Decimal("0.90"):
        return "WARNING_90", "NORMAL"
    if ratio >= Decimal("0.80"):
        return "NOTICE_80", "NORMAL"
    return "NORMAL", "NORMAL"


def evaluate_usage(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise MonitorError("Usage snapshot is missing")
    captured_at = _parse_captured_at(snapshot.get("capturedAt"))
    for name in REQUIRED_METRICS:
        if name not in snapshot:
            raise MonitorError(f"Required usage metric {name} is missing")

    metrics: dict[str, dict[str, Any]] = {}
    limiting_metric = ""
    limiting_ratio = Decimal("-1")
    for name, limit in LIMITS.items():
        if name not in snapshot:
            continue
        value = _validated_metric(name, snapshot[name])
        ratio = Decimal(value) / Decimal(limit)
        metrics[name] = {
            "value": value,
            "limit": limit,
            "ratio": float(round(ratio, 6)),
        }
        if ratio > limiting_ratio:
            limiting_ratio = ratio
            limiting_metric = name

    usage_level, service_state = _status_for_ratio(limiting_ratio)
    return {
        "capturedAt": captured_at,
        "usageLevel": usage_level,
        "serviceState": service_state,
        "limitingMetric": limiting_metric,
        "verifiedMetrics": list(metrics),
        "metrics": metrics,
    }


def monitor_once(remote: UsageRemote) -> dict[str, Any]:
    report = evaluate_usage(remote.fetch_snapshot())
    remote.apply_status(report)
    return report


class SupabaseUsageRemote:
    def __init__(
        self,
        url: str,
        secret_key: str,
        *,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise MonitorError("SUPABASE_URL must be an HTTPS project origin")
        if not secret_key.startswith("sb_secret_"):
            raise MonitorError("SUPABASE_SECRET_KEY must use a new secret API key")
        self.url = url.rstrip("/")
        self.headers = {"apikey": secret_key, "User-Agent": "BoardLog-usage-monitor/1.0"}
        self._open = opener or build_opener(NoRedirectHandler()).open

    def _request(self, path: str, body: Mapping[str, Any]) -> Any:
        request = Request(
            self.url + path,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={**self.headers, "Content-Type": "application/json"},
        )
        try:
            with self._open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError) as error:
            raise MonitorError(
                f"Supabase usage request failed: {getattr(error, 'code', 'network error')}"
            ) from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise MonitorError("Supabase usage response exceeded its safety limit")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MonitorError("Supabase usage response was not valid JSON") from error

    def fetch_snapshot(self) -> Mapping[str, Any]:
        value = self._request("/rest/v1/rpc/catalog_usage_snapshot", {})
        if not isinstance(value, Mapping):
            raise MonitorError("Supabase returned an invalid usage snapshot")
        return value

    def apply_status(self, report: Mapping[str, Any]) -> None:
        response = self._request(
            "/rest/v1/rpc/apply_catalog_usage_status",
            {
                "p_usage_level": report["usageLevel"],
                "p_service_state": report["serviceState"],
                "p_verified_at": report["capturedAt"],
                "p_metrics": report["metrics"],
            },
        )
        if response not in ({}, None):
            raise MonitorError("Supabase returned an invalid usage status acknowledgement")


def _write_github_output(report: Mapping[str, Any]) -> None:
    path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as output:
        output.write(f"usage_level={report['usageLevel']}\n")
        output.write(f"service_state={report['serviceState']}\n")
        output.write(f"limiting_metric={report['limitingMetric']}\n")


def public_summary(report: Mapping[str, Any]) -> dict[str, str]:
    return {
        "usageLevel": str(report["usageLevel"]),
        "serviceState": str(report["serviceState"]),
        "limitingMetric": str(report["limitingMetric"]),
    }


def main(_argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(_argv)
    url = os.environ.get("SUPABASE_URL", "").strip()
    secret = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not secret:
        print("SUPABASE_URL and SUPABASE_SECRET_KEY are required", file=sys.stderr)
        return 2
    try:
        report = monitor_once(SupabaseUsageRemote(url, secret))
        _write_github_output(report)
        print(json.dumps(public_summary(report), ensure_ascii=False, sort_keys=True))
        return 0
    except MonitorError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
