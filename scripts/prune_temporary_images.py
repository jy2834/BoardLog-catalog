#!/usr/bin/env python3
"""Delete only old rejected or safely aged orphaned submission images."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PRUNE_BATCH = 500
SAFE_PATH = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    r"\.(?:jpg|webp)\Z",
    re.IGNORECASE,
)


class PruneError(RuntimeError):
    """Abort image cleanup before acknowledging unverified deletion."""


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, _request, _file_pointer, _code, _message, _headers, _new_url):
        return None


class PruneRemote(Protocol):
    def fetch_candidates(self, rejected_before: str, orphan_before: str, limit: int) -> list[Mapping[str, Any]]: ...
    def delete_images(self, paths: Sequence[str]) -> None: ...
    def acknowledge(self, paths: Sequence[str]) -> None: ...


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PruneError("Cleanup time must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise PruneError("Cleanup time is invalid") from error
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def prune_once(
    remote: PruneRemote,
    *,
    now: str,
    limit: int = 100,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PRUNE_BATCH:
        raise PruneError("Cleanup limit must be between 1 and 500")
    instant = _parse_utc(now)
    rows = remote.fetch_candidates(
        _format_utc(instant - timedelta(days=30)),
        _format_utc(instant - timedelta(days=1)),
        limit,
    )
    if not isinstance(rows, list) or len(rows) > limit:
        raise PruneError("Cleanup candidate response is invalid or unbounded")
    paths: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise PruneError("Cleanup candidate is invalid")
        path = row.get("objectPath")
        reason = row.get("reason")
        if not isinstance(path, str) or not SAFE_PATH.fullmatch(path):
            raise PruneError("Cleanup candidate has an unsafe path")
        if reason not in ("REJECTED_OLD", "ORPHAN"):
            raise PruneError("Cleanup candidate has an unsafe reason")
        if path in paths:
            raise PruneError("Cleanup candidate list contains a duplicate")
        paths.append(path)
    if not paths:
        return {"deletedCount": 0, "paths": []}
    remote.delete_images(paths)
    remote.acknowledge(paths)
    return {"deletedCount": len(paths), "paths": paths}


class SupabasePruneRemote:
    def __init__(self, url: str, secret_key: str, *, opener: Callable[..., Any] | None = None) -> None:
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
            raise PruneError("SUPABASE_URL must be an HTTPS project origin")
        if not secret_key.startswith("sb_secret_"):
            raise PruneError("SUPABASE_SECRET_KEY must use a new secret API key")
        self.url = url.rstrip("/")
        self.headers = {"apikey": secret_key, "User-Agent": "BoardLog-image-pruner/1.0"}
        self._open = opener or build_opener(NoRedirectHandler()).open

    def _request(self, path: str, *, body: Mapping[str, Any], method: str = "POST") -> Any:
        request = Request(
            self.url + path,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            method=method,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        try:
            with self._open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError) as error:
            raise PruneError(
                f"Supabase cleanup request failed: {getattr(error, 'code', 'network error')}"
            ) from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise PruneError("Supabase cleanup response exceeded its safety limit")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PruneError("Supabase cleanup response was not valid JSON") from error

    def fetch_candidates(self, rejected_before: str, orphan_before: str, limit: int) -> list[Mapping[str, Any]]:
        value = self._request(
            "/rest/v1/rpc/catalog_prunable_images",
            body={
                "p_rejected_before": rejected_before,
                "p_orphan_before": orphan_before,
                "p_limit": limit,
            },
        )
        if not isinstance(value, list):
            raise PruneError("Supabase returned invalid cleanup candidates")
        return value

    def delete_images(self, paths: Sequence[str]) -> None:
        value = self._request(
            "/storage/v1/object/submission-images",
            method="DELETE",
            body={"prefixes": list(paths)},
        )
        if not isinstance(value, list):
            raise PruneError("Storage did not confirm image deletion")

    def acknowledge(self, paths: Sequence[str]) -> None:
        value = self._request(
            "/rest/v1/rpc/acknowledge_pruned_submission_images",
            body={"p_paths": list(paths)},
        )
        if value not in ({}, None):
            raise PruneError("Supabase returned an invalid cleanup acknowledgement")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    url = os.environ.get("SUPABASE_URL", "").strip()
    secret = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not secret:
        print("SUPABASE_URL and SUPABASE_SECRET_KEY are required", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        result = prune_once(SupabasePruneRemote(url, secret), now=now, limit=args.limit)
        print(json.dumps({"deletedCount": result["deletedCount"]}, sort_keys=True))
        return 0
    except PruneError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
