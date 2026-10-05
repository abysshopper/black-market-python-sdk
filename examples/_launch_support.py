"""Launch runner wallet boundaries and durable, redacted diagnostic artifacts."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import time
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data

from black_market_sdk.lifecycle_rpc import hex_bytes, quantity, rpc


REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"signature|privatekey|secret|capability|authorization|signedheaders|password|"
    r"apikey|accesskey|accesstoken|bearer|cookie|mnemonic|idempotencykey|credential|rawtransaction",
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
        for name in os.environ:
            if _SECRET_ENV_KEY.search(re.sub(r"[^a-z0-9]", "", name.lower())):
                self.remember(os.environ[name])

    def remember(self, value):
        if isinstance(value, str) and len(value) >= 4:
            self.secrets.add(value)
            if re.fullmatch(r"0x[0-9a-fA-F]{64,}", value):
                self.secrets.add(value[2:])
        elif isinstance(value, (bytes, bytearray)):
            self.secrets.add("0x" + bytes(value).hex())
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.remember(item)
        elif isinstance(value, Mapping):
            for item in value.values():
                self.remember(item)

    def remember_url(self, value):
        if not isinstance(value, str):
            return
        self.remember(value)
        try:
            parts = urlsplit(value)
            for item in (parts.username, parts.password, parts.path, *parts.path.split("/"),
                         *(item for _, item in parse_qsl(parts.query))):
                if item and item != "/":
                    self.secrets.add(item)
                    self.secrets.add(unquote(item))
        except ValueError:
            pass

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
            value = value.replace(secret, REDACTED) if len(secret) >= 4 else re.sub(r"(?<!\w)" + re.escape(secret) + r"(?!\w)", REDACTED, value)
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
        raise ValueError("simulation endpoints must use literal loopback HTTP(S)")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("loopback endpoints cannot contain credentials, queries, or fragments")
    if parts.port is None:
        raise ValueError("loopback endpoints require an explicit owned service port")
    if rpc and parts.path not in {"", "/"}:
        raise ValueError("owned Anvil RPC endpoints must have a root path")
    return value.rstrip("/")


def mainnet_url(value: str, *, api: bool = False) -> str:
    """Mainnet source is HTTPS; only API integration may use owned loopback HTTP."""
    if not isinstance(value, str) or not value or any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        raise ValueError("an explicit safe HTTPS URL is required")
    parts = urlsplit(value)
    if parts.fragment or not parts.hostname:
        raise ValueError("endpoint must have a hostname and no fragment")
    if api and (parts.username is not None or parts.password is not None or parts.query):
        raise ValueError("API endpoints cannot contain credentials or queries")
    if api and parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        return loopback_url(value)
    if parts.scheme != "https":
        raise ValueError("mainnet RPC and nonlocal API endpoints require HTTPS")
    _ = parts.port  # Reject malformed ports before constructing transport.
    return value.rstrip("/")




class ExampleFailure(RuntimeError):
    def __init__(self, message: str, *, code: str, diagnostic=None):
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic



class LocalSigningWallet:
    """Exact local signer, also usable against an owned chain4663 Anvil."""

    def __init__(self, client, artifacts, *, private_key: str, creator: str, chain_id: int):
        artifacts.redact.remember(private_key)
        self.account = Account.from_key(private_key)
        artifacts.redact.remember(bytes(self.account.key))
        artifacts.redact.remember(bytes(self.account.key).hex())
        if self.account.address.lower() != creator.lower():
            raise ExampleFailure("signing key does not match launch creator", code="WALLET_IDENTITY_MISMATCH")
        self.client = client
        self.artifacts = artifacts
        self.chain_id = chain_id

    def check_chain(self):
        if quantity(rpc(self.client, "eth_chainId", [])) != self.chain_id:
            raise ExampleFailure("wallet RPC chain differs from configured chain", code="WALLET_IDENTITY_MISMATCH")

    def sign_typed_data(self, typed_data, *, verifying_contract: str):
        self.check_chain()
        domain = typed_data.get("domain", {})
        if (quantity(domain.get("chainId", 0)) != self.chain_id
                or domain.get("name") != "Abyss Launch Attribution" or domain.get("version") != "1"
                or typed_data.get("primaryType") != "LaunchAttribution"
                or quantity(typed_data.get("message", {}).get("chainId", 0)) != self.chain_id
                or str(domain.get("verifyingContract", "")).lower() != verifying_contract.lower()
                or str(typed_data.get("message", {}).get("wallet", "")).lower() != self.account.address.lower()):
            raise ExampleFailure("attribution domain or wallet differs from launch", code="WALLET_IDENTITY_MISMATCH")
        message = encode_typed_data(full_message=typed_data)
        signed = self.account.sign_message(message)
        signature = hex_bytes(signed.signature)
        self.artifacts.redact.remember(signature)
        self.artifacts.redact.remember(signature[2:])
        if Account.recover_message(message, signature=signature).lower() != self.account.address.lower():
            raise ExampleFailure("typed data signature recovered another creator", code="WALLET_IDENTITY_MISMATCH")
        return signature

    def prepare_transaction(self, transaction):
        self.check_chain()
        envelope = dict(transaction)
        if (quantity(envelope.get("chainId", 0)) != self.chain_id
                or str(envelope.get("from", "")).lower() != self.account.address.lower()):
            raise ExampleFailure("transaction sender or chain differs from wallet", code="WALLET_IDENTITY_MISMATCH")
        # SDK gas/value/calldata are authoritative. Never estimate or rewrite them.
        if not {"gas", "value", "data", "to"} <= envelope.keys():
            raise ExampleFailure("SDK transaction lacks an exact execution envelope", code="TRANSACTION_IDENTITY_MISMATCH")
        if "nonce" not in envelope:
            envelope["nonce"] = quantity(rpc(self.client, "eth_getTransactionCount", [self.account.address, "pending"]))
        if "gasPrice" not in envelope:
            if "maxFeePerGas" not in envelope and "maxPriorityFeePerGas" not in envelope:
                envelope["gasPrice"] = quantity(rpc(self.client, "eth_gasPrice", []))
            elif "maxPriorityFeePerGas" not in envelope:
                envelope["maxPriorityFeePerGas"] = quantity(rpc(self.client, "eth_maxPriorityFeePerGas", []))
            elif "maxFeePerGas" not in envelope:
                block = rpc(self.client, "eth_getBlockByNumber", ["latest", False])
                envelope["maxFeePerGas"] = 2 * quantity(block["baseFeePerGas"]) + quantity(envelope["maxPriorityFeePerGas"])
        return envelope

    def send_transaction(self, kind: str, transaction):
        envelope = self.prepare_transaction(transaction)
        signing = {key: value for key, value in envelope.items() if key != "from"}
        signing["to"] = Web3.to_checksum_address(signing["to"])
        signed = self.account.sign_transaction(signing)
        raw = hex_bytes(signed.raw_transaction)
        transaction_hash = hex_bytes(Web3.keccak(signed.raw_transaction))
        self.artifacts.redact.remember(raw)
        self.artifacts.redact.remember(raw[2:])
        self.artifacts.save("signed-" + transaction_hash[2:] + ".private.json",
                            {"rawTransaction": raw, "transactionHash": transaction_hash, "transaction": envelope}, private=True)
        # Hash + exact unsigned envelope are fsynced BEFORE even attempting transport.
        row = self.artifacts.broadcast_attempt(kind, envelope, transaction_hash)
        returned_hash = hex_bytes(rpc(self.client, "eth_sendRawTransaction", [raw]))
        if returned_hash.lower() != transaction_hash.lower():
            raise ExampleFailure("RPC returned a different signed transaction hash", code="BROADCAST_HASH_MISMATCH",
                               diagnostic={"computedHash": transaction_hash, "returnedHash": returned_hash})
        row.update(broadcastStatus="submitted", submittedAt=utc_now())
        self.artifacts.save("receipts.json", self.artifacts.receipts)
        self.artifacts.event("transaction-submitted", **row)
        return row

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
            "schema": "black-market.launch-example-result.v1", "sdk": "python", "caseId": case_id,
            "status": "failed", "stage": self.stage, "scope": "end-to-end", "execution": "not-run",
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


    def broadcast_attempt(self, kind: str, transaction: dict, transaction_hash: str):
        row = {"kind": kind, "transaction": transaction, "transactionHash": transaction_hash,
               "broadcastStatus": "attempted", "attemptedAt": utc_now(), "receipt": None}
        self.receipts.append(row)
        self.result["chain"]["transactions"].append(transaction_hash)
        self.save("receipts.json", self.receipts)
        self.event("broadcast-attempt", **row)
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

    def __init__(self, url: str, artifacts: Artifacts, *, node: str,
                 simulation_creator: str | None = None):
        artifacts.redact.remember_url(url)
        super().__init__(
            url, request_kwargs={"timeout": 120}, exception_retry_configuration=None,
        )
        self.artifacts = artifacts
        self.node = node
        self.simulation_creator = simulation_creator
        if simulation_creator is not None and node != "simulation":
            raise ExampleFailure("impersonation is confined to an executing controlled fork", code="SOURCE_STATE_MUTATION_FORBIDDEN")
        if simulation_creator is not None:
            loopback_url(url, rpc=True)

    def unlock_simulation_creator(self):
        if self.simulation_creator is not None:
            response = self.make_request("anvil_impersonateAccount", [self.simulation_creator])
            if "error" in response:
                raise ExampleFailure("controlled fork could not unlock creator", code="SIMULATION_UNLOCK_FAILED", diagnostic=response["error"])

    def make_request(self, method, params):
        method = str(method)
        writes = method.startswith(("eth_send", "eth_sign", "personal_", "evm_", "anvil_", "hardhat_")) and method != "anvil_nodeInfo"
        if self.node == "execution" and method.startswith(("evm_", "anvil_", "hardhat_")) and method != "anvil_nodeInfo":
            raise ExampleFailure("the execution chain must be preserved for debugging", code="SOURCE_STATE_MUTATION_FORBIDDEN")
        if self.node == "execution" and (method.startswith(("eth_sign", "personal_")) or method == "eth_sendTransaction"):
            raise ExampleFailure("execution requires local wallet signing", code="REMOTE_SIGNING_FORBIDDEN")
        if self.node == "simulation" and method.startswith(("anvil_set", "hardhat_set")):
            raise ExampleFailure("simulation balance/code/storage mutation is forbidden", code="SIMULATION_STATE_MUTATION_FORBIDDEN")
        started = time.monotonic()
        try:
            response = super().make_request(method, params)
        except Exception as error:
            self.artifacts.event("rpc-error", node=self.node, method=method, error=error_details(error), durationMs=(time.monotonic() - started) * 1000)
            raise
        duration = (time.monotonic() - started) * 1000
        if "error" in response:
            safe_params = "[RAW SIGNED TRANSACTION OMITTED]" if method == "eth_sendRawTransaction" else {"wallet": params[0], "typedData": "[SIGNING REQUEST OMITTED]"} if method == "eth_signTypedData_v4" else params
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
        if method == "anvil_reset" and "error" not in response:
            self.unlock_simulation_creator()
        return response
