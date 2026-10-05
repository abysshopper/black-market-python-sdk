"""Local launch runner boundaries and durable, redacted diagnostic artifacts."""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import time
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from web3 import Web3


REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"signature|privatekey|secret|capability|authorization|signedheaders|password|"
    r"apikey|accesskey|accesstoken|bearer|cookie|mnemonic|idempotencykey|credential",
    re.IGNORECASE,
)
_SECRET_ENV_KEY = re.compile(r"privatekey|secret|password|passphrase|apikey|accesskey|token|auth|credential|mnemonic|cookie", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SIGNATURE = re.compile(r"(?<![\w])0x[0-9a-fA-F]{130}(?![0-9a-fA-F])")
_LABELLED_SECRET = re.compile(
    r"([\"']?(?:signature|private[_-]?key|capability|authorization|password|"
    r"api[_-]?key|secret|access[_-]?token)[\"']?\s*[:=]\s*)"
    r"(?:[\"'][^\"'\r\n]*[\"']|[^\s,;}]+)", re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[^\s\"',;}>]+", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def portable(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: portable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): portable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [portable(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class Redactor:
    def __init__(self):
        self.secrets: set[str] = set()
        for name, value in os.environ.items():
            if _SECRET_ENV_KEY.search(re.sub(r"[^a-z0-9]", "", name.lower())):
                self.remember(value)

    def remember(self, value):
        if isinstance(value, str) and len(value) >= 4:
            self.secrets.add(value)
        elif isinstance(value, (bytes, bytearray)):
            self.secrets.add("0x" + bytes(value).hex())
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.remember(item)
        elif isinstance(value, Mapping):
            for item in value.values():
                self.remember(item)

    def learn(self, value):
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if _SECRET_KEY.search(normalized):
                    self.remember(item)
                else:
                    self.learn(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.learn(item)

    def text(self, value: str) -> str:
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, REDACTED)
        value = _SIGNATURE.sub(REDACTED, value)
        value = _BEARER.sub("Bearer " + REDACTED, value)
        value = _LABELLED_SECRET.sub(lambda match: match.group(1) + REDACTED, value)

        def scrub_url(match):
            try:
                parts = urlsplit(match.group())
                host = parts.hostname or "[invalid-host]"
                if ":" in host:
                    host = "[" + host + "]"
                if parts.port is not None:
                    host += ":" + str(parts.port)
                # RPC credentials can live in a remote provider's path, not only its query.
                path = parts.path if parts.hostname in {"127.0.0.1", "localhost", "::1"} else "/" + REDACTED
                return urlunsplit((parts.scheme, host, path, REDACTED if parts.query else "", ""))
            except ValueError:
                return "[REDACTED-URL]"

        return _URL.sub(scrub_url, value)

    def __call__(self, value):
        value = portable(value)
        self.learn(value)
        if isinstance(value, Mapping):
            return {
                self.text(str(key)): REDACTED if _SECRET_KEY.search(re.sub(r"[^a-z0-9]", "", str(key).lower())) else self(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def error_details(error: BaseException, seen=None) -> dict:
    seen = set() if seen is None else seen
    if id(error) in seen:
        return {"name": type(error).__name__, "message": "cyclic exception cause"}
    seen.add(id(error))
    detail = {
        "name": type(error).__name__,
        "code": getattr(error, "code", None),
        "message": str(error),
        "stack": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
    }
    for attr, key in (("data", "revertData"), ("status", "httpStatus"), ("retry_after_ms", "retryAfterMs"), ("method", "rpcMethod"), ("diagnostic", "diagnostic")):
        if hasattr(error, attr):
            detail[key] = portable(getattr(error, attr))
    cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    if cause is not None:
        detail["cause"] = error_details(cause, seen)
    return detail


def loopback_url(value: str, *, rpc: bool = False) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        raise ValueError("a safe explicit loopback URL is required")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("smoke endpoints must use literal loopback HTTP(S), never a live endpoint")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("smoke endpoints cannot contain credentials, queries, or fragments")
    if parts.port is None:
        raise ValueError("smoke endpoints require an explicit owned service port")
    if rpc and parts.path not in {"", "/"}:
        raise ValueError("owned Anvil RPC endpoints must have a root path")
    return value.rstrip("/")


def rpc_identity(url: str) -> tuple[str, int]:
    parts = urlsplit(loopback_url(url, rpc=True))
    return "loopback", parts.port


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("timeout seconds must be finite and positive")
    return seconds


class SmokeFailure(RuntimeError):
    def __init__(self, message: str, *, code: str, diagnostic=None):
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic


class Artifacts:
    def __init__(self, directory: Path, case_id: str):
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.directory = directory
        self.redact = Redactor()
        self.case_id = case_id
        self.stage = "configuration"
        self.started_at = utc_now()
        self.started = time.monotonic()
        self.receipts: list[dict] = []
        self.result = {
            "schema": "black-market.launch-smoke-result.v1", "sdk": "python", "caseId": case_id,
            "status": "failed", "stage": self.stage, "scope": "plan-only", "execution": "not-run",
            "chain": {"status": "not-run", "transactions": []}, "api": {"status": "not-run"},
            "startedAt": self.started_at, "completedAt": None,
        }
        descriptor = os.open(directory / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        self.journal = os.fdopen(descriptor, "w", encoding="utf-8")
        self.save("receipts.json", self.receipts)
        self.event("started")

    def save(self, name: str, value, *, private: bool = False):
        payload = portable(value) if private else self.redact(value)
        temporary = self.directory / (name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o644)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / name)
            directory_descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def event(self, event: str, **observations):
        row = self.redact({"timestamp": utc_now(), "sdk": "python", "caseId": self.case_id, "stage": self.stage,
                           "event": event, "elapsedMs": round((time.monotonic() - self.started) * 1000, 3), **observations})
        self.journal.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        self.journal.flush()
        os.fsync(self.journal.fileno())

    def enter(self, stage: str):
        self.stage = stage
        self.event("stage-started")

    def submitted(self, kind: str, transaction: dict, transaction_hash: str):
        row = {"kind": kind, "transaction": transaction, "transactionHash": transaction_hash, "submittedAt": utc_now(), "receipt": None}
        self.receipts.append(row)
        self.result["chain"]["transactions"].append(transaction_hash)
        self.save("receipts.json", self.receipts)
        self.event("transaction-submitted", **row)
        return row

    def included(self, row: dict, receipt: dict):
        row["receipt"] = receipt
        row["includedAt"] = utc_now()
        self.save("receipts.json", self.receipts)
        self.event("transaction-included", kind=row["kind"], transactionHash=row["transactionHash"], receipt=receipt)

    def finish(self, error: BaseException | None = None):
        self.result.update(status="failed" if error else "passed", stage=self.stage, completedAt=utc_now())
        if error is not None:
            self.result["error"] = error_details(error)
        self.event("failed" if error else "completed", result=self.result)
        self.save("result.json", self.result)
        self.journal.close()


class JournalHTTPProvider(Web3.HTTPProvider):
    """Observe real RPC failures, including errors swallowed into SDK admission reasons."""

    def __init__(self, url: str, artifacts: Artifacts, *, node: str, execute: bool):
        super().__init__(
            url, request_kwargs={"timeout": 120}, exception_retry_configuration=None,
        )
        self.artifacts = artifacts
        self.node = node
        self.execute = execute

    def make_request(self, method, params):
        method = str(method)
        writes = method.startswith(("eth_send", "eth_sign", "personal_", "evm_", "anvil_")) and method != "anvil_nodeInfo"
        if writes and not self.execute:
            raise SmokeFailure("--execute is required for RPC writes or signing", code="EXECUTION_NOT_AUTHORIZED")
        if self.node == "execution" and method.startswith(("evm_", "anvil_")) and method != "anvil_nodeInfo":
            raise SmokeFailure("the execution chain must be preserved for debugging", code="SOURCE_STATE_MUTATION_FORBIDDEN")
        started = time.monotonic()
        try:
            response = super().make_request(method, params)
        except Exception as error:
            self.artifacts.event("rpc-error", node=self.node, method=method, error=error_details(error), durationMs=(time.monotonic() - started) * 1000)
            raise
        duration = (time.monotonic() - started) * 1000
        if "error" in response:
            safe_params = {"wallet": params[0], "typedData": "[SIGNING REQUEST OMITTED]"} if method == "eth_signTypedData_v4" else params
            self.artifacts.event("rpc-error", node=self.node, method=method, params=safe_params, error=response["error"], durationMs=duration)
        elif method == "eth_sendTransaction":
            self.artifacts.event("rpc-submitted", node=self.node, method=method, transaction=params[0], transactionHash=response.get("result"), durationMs=duration)
        elif method == "eth_getTransactionReceipt" and response.get("result") is not None:
            self.artifacts.event("rpc-receipt", node=self.node, transactionHash=params[0], receipt=response["result"], durationMs=duration)
            if self.node == "simulation" and response["result"].get("status") in {0, "0x0", "0x00"}:
                # The SDK restores its fork immediately after this read; retain real
                # revert data now, while the failing transaction is still available.
                try:
                    trace = super().make_request("debug_traceTransaction", [params[0], {"tracer": "callTracer"}])
                except Exception as error:
                    trace = {"error": error_details(error)}
                self.artifacts.event("rpc-revert-trace", node=self.node, transactionHash=params[0], trace=trace)
        elif writes and method != "eth_signTypedData_v4":
            self.artifacts.event("rpc-simulation-boundary", node=self.node, method=method, durationMs=duration)
        return response
