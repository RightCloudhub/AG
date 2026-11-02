#!/usr/bin/env python3
"""Import UTF-8 corpus files through the running Web application's tenant-scoped API."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = (ROOT / "data/raw", ROOT / "data/pilot/raw")
EXTENSIONS = {".md", ".txt"}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_BATCH_FILES = 20
TERMINAL = {"done", "review", "failed", "empty"}


class ImportFailure(Exception):
    """An actionable error that does not contain authentication material."""


@dataclass(frozen=True)
class Document:
    path: Path
    name: str
    size: int
    sha256: str

    def content(self) -> bytes:
        content = read_document(self.path)
        if hashlib.sha256(content).hexdigest() != self.sha256:
            raise ImportFailure(f"File changed after validation: {self.path}")
        return content


def read_document(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ImportFailure(f"File exceeds 5 MiB: {path}")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportFailure(f"File must be UTF-8: {path}") from exc
    return content


def source_files(source: Path) -> list[Path]:
    if source.is_symlink() or not source.exists():
        raise ImportFailure(f"Source must exist and must not be a symlink: {source}")
    if source.is_file():
        if source.suffix.lower() not in EXTENSIONS:
            raise ImportFailure(f"Only .md and .txt files are supported: {source}")
        return [source]
    return sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in EXTENSIONS
    )


def collect_documents(sources: list[Path]) -> list[Document]:
    documents: dict[str, Document] = {}
    for source in sources:
        for candidate in source_files(source):
            path = candidate.resolve()
            previous = documents.get(path.name)
            if previous and previous.path != path:
                raise ImportFailure(f"Duplicate filename: {previous.path} and {path}")
            content = read_document(path)
            documents[path.name] = Document(
                path, path.name, len(content), hashlib.sha256(content).hexdigest()
            )
    if not documents:
        raise ImportFailure("No UTF-8 .md or .txt documents found")
    return list(documents.values())


class WebClient:
    def __init__(self, origin: str) -> None:
        self.http = httpx.Client(
            base_url=origin, timeout=60, trust_env=False, follow_redirects=False
        )
        self.csrf = ""
        self.identity: dict[str, Any] = {}
        self.logged_in = False

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"Origin": str(self.http.base_url).rstrip("/")}
        if self.logged_in and path not in {"/v1/auth/csrf", "/v1/auth/refresh"}:
            headers.update(
                {
                    "X-AGR-User": self.identity["user_id"],
                    "X-AGR-Tenant": self.identity["tenant_id"],
                }
            )
        if method not in {"GET", "HEAD"}:
            headers["X-CSRF-Token"] = self.csrf
        return self.http.request(method, path, headers=headers, **kwargs)

    def api(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._send(method, path, **kwargs)
        refreshable = not path.startswith("/v1/auth/") or path == "/v1/auth/logout"
        if response.status_code == 401 and self.logged_in and refreshable:
            self._accept(self.api("POST", "/v1/auth/refresh")["data"])
            response = self._send(method, path, **kwargs)
        try:
            body = response.json()
        except ValueError as exc:
            raise ImportFailure("Expected Web API JSON; check --url") from exc
        if not isinstance(body, dict):
            raise ImportFailure("Expected a Web API response envelope")
        if response.is_success and body.get("success") is True:
            return body
        error = body.get("error") or {}
        code = (
            error.get("code", "INVALID_RESPONSE") if isinstance(error, dict) else "INVALID_RESPONSE"
        )
        if code == "CSRF_FAILED":
            raise ImportFailure(
                "CSRF check failed: --url must match AGR_WEB_ORIGIN exactly "
                "(localhost and 127.0.0.1 are different origins)"
            )
        raise ImportFailure(f"{method} {path}: HTTP {response.status_code} {code}")

    def _accept(self, data: dict[str, Any]) -> None:
        self.identity = data
        self.csrf = data["csrf_token"]
        self.logged_in = True

    def authenticate(self, secret: str, *, email: str | None, tenant_id: str | None) -> None:
        if email:
            self.csrf = self.api("GET", "/v1/auth/csrf")["data"]["csrf_token"]
            self._accept(
                self.api(
                    "POST",
                    "/v1/auth/login",
                    json={
                        "email": email,
                        "password": secret,
                    },
                )["data"]
            )
            if tenant_id and tenant_id != self.identity["tenant_id"]:
                self._accept(
                    self.api("POST", f"/v1/tenants/{quote(tenant_id, safe='')}/switch")["data"]
                )
        else:
            self.http.headers["Authorization"] = f"Bearer {secret}"
        self.identity = self.api("GET", "/v1/me")["data"]
        if tenant_id and self.identity["tenant_id"] != tenant_id:
            raise ImportFailure("The service key belongs to a different tenant")
        if not self.identity.get("capabilities", {}).get("knowledge"):
            raise ImportFailure("Import requires admin or operator access to the target tenant")

    def upload(self, documents: list[Document]) -> dict[str, Any]:
        files = [("files", (doc.name, doc.content(), "text/plain")) for doc in documents]
        task = self.api("POST", "/v1/docs", files=files)["data"]
        if not isinstance(task, dict):
            raise ImportFailure("Expected an ingest task in the upload response")
        return task

    def wait(self, task: dict[str, Any], *, timeout: float, interval: float) -> None:
        deadline = time.monotonic() + timeout
        while task["status"] not in TERMINAL:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ImportFailure(
                    f"Task {task['id']} is still {task['status']}; check the saved report and "
                    "start agr-webapp with AGR_INGEST_INLINE=1 for local processing"
                )
            time.sleep(min(interval, remaining))
            task.update(self.api("GET", f"/v1/ingest-tasks/{quote(task['id'], safe='')}")["data"])
        if task["status"] not in {"done", "review"}:
            raise ImportFailure(f"Task {task['id']} finished with status {task['status']}")

    def close(self) -> None:
        try:
            if self.logged_in:
                self.api("POST", "/v1/auth/logout")
        except (ImportFailure, httpx.HTTPError):
            print(
                "Could not revoke this script's session; check Account security.", file=sys.stderr
            )
        finally:
            self.http.close()


def origin(value: str) -> str:
    parsed = urlsplit(value)
    port = parsed.port
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise argparse.ArgumentTypeError("Use an HTTP(S) origin without credentials, path or query")
    return value.rstrip("/")


def positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Must be a positive, finite number of seconds")
    return number


def arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", type=Path, help="Files/directories; defaults to both raw corpora"
    )
    parser.add_argument("--url", type=origin, default="http://localhost:8000")
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--email", help="Login email; password is prompted without echo")
    auth.add_argument("--api-key-stdin", action="store_true", help="Read a service key from stdin")
    parser.add_argument(
        "--password-stdin", action="store_true", help="Read the login password from stdin"
    )
    parser.add_argument(
        "--tenant-id", help="Switch the script's session to this tenant before uploading"
    )
    parser.add_argument(
        "--batch-size", type=int, choices=range(1, MAX_BATCH_FILES + 1), default=MAX_BATCH_FILES
    )
    parser.add_argument(
        "--no-wait", action="store_true", help="Return after submission without polling"
    )
    parser.add_argument(
        "--wait-timeout",
        type=positive_seconds,
        default=300,
        help="Seconds per batch (default: 300)",
    )
    parser.add_argument(
        "--poll-interval",
        type=positive_seconds,
        default=2,
        help="Seconds between polls (default: 2)",
    )
    parser.add_argument(
        "--report", type=Path, help="JSON report; defaults to reports/webapp-import-<time>.json"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate local files without connecting or logging in",
    )
    args = parser.parse_args(argv)
    if args.password_stdin and not args.email:
        parser.error("--password-stdin requires --email")
    if not args.dry_run and not (args.email or args.api_key_stdin):
        parser.error("Provide --email or --api-key-stdin")
    return args


def save_report(path: Path, report: dict[str, Any]) -> None:
    report["status_counts"] = dict(Counter(task["status"] for task in report["tasks"]))
    report["submitted_documents"] = sum(len(task["docs"]) for task in report["tasks"])
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, ensure_ascii=False, indent=2, default=str)
            stream.write("\n")
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def run_import(args: argparse.Namespace, documents: list[Document], report: dict[str, Any]) -> None:
    secret = (
        sys.stdin.readline().rstrip("\r\n")
        if args.password_stdin or args.api_key_stdin
        else getpass.getpass("Account password: ")
    )
    if not secret:
        raise ImportFailure("No password or service key provided")
    client = WebClient(args.url)
    try:
        client.authenticate(secret, email=args.email, tenant_id=args.tenant_id)
        del secret
        report["tenant_id"] = client.identity["tenant_id"]
        report["user_id"] = client.identity["user_id"]
        print(f"Target tenant: {report['tenant_id']} ({client.identity['role']})", flush=True)
        for start in range(0, len(documents), args.batch_size):
            batch = documents[start : start + args.batch_size]
            task = client.upload(batch)
            report["tasks"].append(task)
            save_report(args.report, report)
            if len(task["docs"]) != len(batch) or any(row.get("error") for row in task["docs"]):
                raise ImportFailure(f"Task {task['id']} contains document-save errors; see report")
            if task["tenant_id"] != report["tenant_id"]:
                raise ImportFailure("Upload response does not match the target tenant")
            print(
                f"Batch {len(report['tasks'])}: {len(batch)} documents; task {task['id']}",
                flush=True,
            )
            if not args.no_wait:
                client.wait(task, timeout=args.wait_timeout, interval=args.poll_interval)
                print(f"  {task['status']}: {task.get('message', '')}", flush=True)
            save_report(args.report, report)
            time.sleep(0.1)
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    report: dict[str, Any] = {"url": args.url, "files": [], "tasks": [], "status": "preparing"}
    report_ready = False
    args.report = args.report or ROOT / "reports" / f"webapp-import-{time.time_ns()}.json"
    try:
        documents = collect_documents(args.paths or list(DEFAULT_SOURCES))
        if args.report.resolve() in {doc.path for doc in documents}:
            raise ImportFailure("The report path must not overwrite a source document")
        report["files"] = [asdict(doc) for doc in documents]
        args.report.parent.mkdir(parents=True, exist_ok=True)
        save_report(args.report, report)
        report_ready = True
        print(f"Validated {len(documents)} documents ({sum(doc.size for doc in documents)} bytes).")
        if args.dry_run:
            report["status"] = "dry_run"
        else:
            run_import(args, documents, report)
            report["status"] = "submitted" if args.no_wait else "processed"
        save_report(args.report, report)
        print(f"{report['status']}: {report['status_counts']}; report: {args.report}")
        return 0
    except (ImportFailure, OSError, httpx.HTTPError, EOFError, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = str(exc) if isinstance(exc, ImportFailure) else type(exc).__name__
        print(f"Import failed: {report['error']}", file=sys.stderr)
        if report_ready:
            try:
                save_report(args.report, report)
                print(f"Partial results: {args.report}", file=sys.stderr)
            except OSError:
                print(
                    "Could not update the report; previous results are retained.", file=sys.stderr
                )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
