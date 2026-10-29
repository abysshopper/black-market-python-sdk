"""Pinned, invocation-local reads for unsigned launch lifecycle planning."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, replace
from typing import Any, Literal
from contextlib import contextmanager
from functools import wraps
from threading import BoundedSemaphore, RLock, Thread
from typing import Callable, Protocol
import asyncio
import inspect
import re
from weakref import WeakKeyDictionary, ref


from web3 import Web3


class LaunchRpcError(RuntimeError):
    def __init__(self, method: str, error: Mapping[str, Any]) -> None:
        self.method = method
        code = error.get("code")
        self.code = code if isinstance(code, int) and not isinstance(code, bool) and abs(code) <= (1 << 53) - 1 else None
        self._native = dict(error)
        super().__init__(f"{method} failed" + (f" (RPC code {self.code})" if self.code is not None else ""))


class LaunchStateChanged(RuntimeError):
    """The pinned source chain or canonical block changed during a read."""

    code: str | None = None


def quantity(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("a boolean is not an RPC quantity")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str):
        result = int(value, 16 if value.startswith("0x") else 10)
        if result >= 0:
            return result
    raise ValueError("invalid nonnegative RPC quantity")


def hex_bytes(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, str) and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        return value.lower()
    raise ValueError("expected hexadecimal bytes")


class LifecycleRpcClient(Protocol):
    """A raw request client can be supplied instead of a Web3 transport."""
    def request(self, args: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True)
class LifecycleDiagnosticEvent:
    stage: str
    phase: Literal["queued", "start", "success", "failure", "reuse"]
    duration_ms: float | None = None
    queue_ms: float | None = None
    request_id: int | None = None
    method: str | None = None


LifecycleDiagnosticListener = Callable[[LifecycleDiagnosticEvent], Any]
_DIAGNOSTIC_METHODS = frozenset((
    "eth_chainId", "eth_getBlockByNumber", "eth_getBlockByHash", "eth_getCode",
    "eth_getBalance", "eth_call", "eth_gasPrice",
    "eth_getTransactionCount", "eth_getTransactionReceipt", "eth_getTransactionByHash",
))


_diagnostic_loop: asyncio.AbstractEventLoop | None = None
_diagnostic_loop_lock = RLock()


def _observer_loop() -> asyncio.AbstractEventLoop:
    """Start no background work unless an explicitly supplied observer awaits."""
    global _diagnostic_loop
    with _diagnostic_loop_lock:
        if _diagnostic_loop is None:
            _diagnostic_loop = asyncio.new_event_loop()
            Thread(target=_diagnostic_loop.run_forever, name="launch-diagnostics", daemon=True).start()
        return _diagnostic_loop


def _emit(listener: LifecycleDiagnosticListener | None, event: LifecycleDiagnosticEvent) -> None:
    if listener is None:
        return
    try:
        pending = listener(event)
        if inspect.isawaitable(pending):
            async def observe() -> None:
                try:
                    await pending
                except BaseException:
                    pass
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            loop = running_loop or getattr(listener, "_diagnostic_loop", None) or _observer_loop()
            observer = observe()
            try:
                if loop is running_loop:
                    loop.create_task(observer)
                else:
                    asyncio.run_coroutine_threadsafe(observer, loop)
            except BaseException:
                observer.close()
                if inspect.iscoroutine(pending):
                    pending.close()
                raise
    except BaseException:
        pass  # Local observers cannot change execution, policy or admission.


def _transport_identity(client: Any) -> tuple[Any, ...]:
    provider = getattr(client, "provider", None)
    request = getattr(client, "request", None) if hasattr(client, "request") else getattr(provider, "make_request", None)
    return (provider, getattr(provider, "endpoint_uri", None),
            getattr(request, "__self__", None), getattr(request, "__func__", request))

_session_lock = RLock()
_session_facts: WeakKeyDictionary = WeakKeyDictionary()
# Identity keys mirror a JS WeakMap even for value-equal/unhashable clients.
# A client that cannot be weak-referenced retains its session identity.
_client_facts: dict[int, tuple[Callable[[], Any], dict[tuple[Any, ...], Future]]] = {}


def _capability_facts(source: Any) -> set[tuple[Any, ...]]:
    """Successful transport capabilities survive pins, never mutable state."""
    owner = getattr(source, "provider", None) or source
    identity = tuple(value if isinstance(value, (str, int, type(None))) else id(value)
        for value in _transport_identity(source))
    with _session_lock:
        try:
            previous = _session_facts.get(owner)
            if previous is None or previous[0] != identity:
                previous = (identity, set())
                _session_facts[owner] = previous
            return previous[1]
        except TypeError:
            # A non-weak-referenceable custom client still supports uncached reads.
            return set()



class _LifecycleReadClient:
    def __init__(self, source: Any, *, verify_source: bool = True) -> None:
        self.source = source
        self.provider = getattr(source, "provider", None)
        self.on_diagnostic = getattr(source, "on_diagnostic", None)
        if self.on_diagnostic is not None:
            listener = self.on_diagnostic
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            def observe(event):
                return listener(event)
            observe._diagnostic_loop = loop
            self.on_diagnostic = observe
        self.supports_read_batching = getattr(source, "supports_read_batching", False) is True
        self._source = source
        self._transport = _transport_identity(source)
        self._batching = getattr(source, "supports_read_batching", None)
        self._semaphore = BoundedSemaphore(8 if self.supports_read_batching else 1)
        self._lock = RLock()
        self._reads: dict[str, Any] = {}
        self._pending: dict[str, Future] = {}
        self._sequence = 0
        self.closed = False
        self.capabilities = _capability_facts(source)
        self.verify_source = verify_source
        self.lens_snapshots: dict[tuple[int, str, str], Any] = {}
        self.immutable_predictions: dict[str, str] = {}
        self._gas_prices: dict[tuple[int, str], int] = {}
        self._canonical_pins: dict[int, LaunchBlock] = {}
        self.receipt_headers: dict[int, LaunchBlock | None] = {}
        self._expected_chain: int | None = None
        self.completion_checks: list[Callable[[], None]] = []

    def assert_open(self) -> None:
        if self.closed:
            from .lifecycle import LifecyclePlanningError
            raise LifecyclePlanningError("READ_SCOPE_CLOSED", "Lifecycle read invocation has already settled")
        if self.source is not self._source or _transport_identity(self.source) != self._transport or getattr(self.source, "supports_read_batching", None) != self._batching:
            from .lifecycle import LifecyclePlanningError
            raise LifecyclePlanningError("LIMIT_CONTEXT_MISMATCH", "Lifecycle source transport or batching changed during its invocation")

    def run(self, method: str, params: Sequence[Any]) -> Any:
        self.assert_open()
        listener = self.on_diagnostic
        if listener is not None:
            queued = time.perf_counter()
            with self._lock:
                self._sequence += 1
                request_id = self._sequence
            observed = method if method in _DIAGNOSTIC_METHODS else "other"
            _emit(listener, LifecycleDiagnosticEvent("rpc", "queued", request_id=request_id, method=observed))
        with self._semaphore:
            self.assert_open()
            if listener is not None:
                start = time.perf_counter()
                _emit(listener, LifecycleDiagnosticEvent("rpc", "start", queue_ms=(start - queued) * 1000, request_id=request_id, method=observed))
            try:
                result = _request(self.source, method, params)
                self.assert_open()
            except Exception:
                if listener is not None:
                    _emit(listener, LifecycleDiagnosticEvent("rpc", "failure", duration_ms=(time.perf_counter() - start) * 1000, request_id=request_id, method=observed))
                raise
            if listener is not None:
                _emit(listener, LifecycleDiagnosticEvent("rpc", "success", duration_ms=(time.perf_counter() - start) * 1000, request_id=request_id, method=observed))
            self.assert_open()
            return result


    def pinned_key(self, method: str, params: Sequence[Any], block: LaunchBlock) -> str:
        return json.dumps([block.number, block.block_hash.lower(), method, params], separators=(",", ":"))

    def has_read(self, method: str, params: Sequence[Any], block: LaunchBlock) -> bool:
        self.assert_open()
        with self._lock:
            return self.pinned_key(method, params, block) in self._reads

    def seed_read(self, method: str, params: Sequence[Any], block: LaunchBlock, value: Any) -> None:
        self.assert_open()
        with self._lock:
            self._reads[self.pinned_key(method, params, block)] = value

    def gas_price(self, block: LaunchBlock) -> int:
        self.assert_open()
        with self._lock:
            key = (block.number, block.block_hash.lower())
            if key not in self._gas_prices:
                self._gas_prices[key] = quantity(self.run("eth_gasPrice", []))
            return self._gas_prices[key]

    def check_source(self, chain_id: int, blocks: Sequence[LaunchBlock]) -> None:
        self.assert_open()
        if self._expected_chain is not None and self._expected_chain != chain_id:
            raise LaunchStateChanged("Lifecycle observations belong to different source chains")
        self._expected_chain = chain_id
        for block in blocks:
            previous = self._canonical_pins.get(block.number)
            if previous is not None and previous != block:
                error = LaunchStateChanged("Lifecycle observations disagree on a canonical block")
                error.code = "STATE_REORGED"
                raise error
            self._canonical_pins[block.number] = replace(block)

    def finalize(self) -> None:
        self.assert_open()
        if self.verify_source:
            for block in self._canonical_pins.values():
                assert_canonical(self, block)
            if self._expected_chain is not None:
                assert_chain(self, self._expected_chain)
        for check in self.completion_checks:
            check()
        self.assert_open()

    def read(self, method: str, params: Sequence[Any], block: LaunchBlock) -> Any:
        self.assert_open()
        key = self.pinned_key(method, params, block)
        with self._lock:
            if key in self._reads:
                if self.on_diagnostic is not None:
                    _emit(self.on_diagnostic, LifecycleDiagnosticEvent("rpc", "reuse", method=method))
                self.assert_open()
                return self._reads[key]
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = Future()
                self._pending[key] = pending
        if not owner:
            if self.on_diagnostic is not None:
                _emit(self.on_diagnostic, LifecycleDiagnosticEvent("rpc", "reuse", method=method))
            result = pending.result()
            self.assert_open()
            return result
        try:
            result = self.run(method, params)
            with self._lock:
                self.assert_open()
                self._reads[key] = result
                self._pending.pop(key, None)
                pending.set_result(result)
                return result
        except BaseException as error:
            with self._lock:
                self._pending.pop(key, None)
            pending.set_exception(error)
            raise

    def close(self) -> None:
        with self._lock:
            self.closed = True
            self._reads.clear()
            self._pending.clear()
            self.immutable_predictions.clear()
            self._gas_prices.clear()
            self._canonical_pins.clear()
            self.receipt_headers.clear()
            self.lens_snapshots.clear()
            self.completion_checks.clear()


@contextmanager
def lifecycle_stage(client: Any, stage: str):
    listener = getattr(client, "on_diagnostic", None)
    if listener is None:
        yield
        return
    start = time.perf_counter()
    _emit(listener, LifecycleDiagnosticEvent(stage, "start"))
    try:
        yield
    except BaseException:
        _emit(listener, LifecycleDiagnosticEvent(stage, "failure", duration_ms=(time.perf_counter() - start) * 1000))
        raise
    else:
        _emit(listener, LifecycleDiagnosticEvent(stage, "success", duration_ms=(time.perf_counter() - start) * 1000))


def read_invocation(stage: str):
    def decorate(work):
        @wraps(work)
        def invoke(client, *args, **kwargs):
            if isinstance(client, _LifecycleReadClient):
                client.assert_open()
                with lifecycle_stage(client, stage):
                    return work(client, *args, **kwargs)
            scope = _LifecycleReadClient(client, verify_source=not stage.startswith("plan"))
            try:
                with lifecycle_stage(scope, stage):
                    result = work(scope, *args, **kwargs)
                    scope.finalize()
                    return result
            finally:
                scope.close()
        return invoke
    return decorate


def async_read_invocation(stage: str):
    def decorate(work):
        @wraps(work)
        async def invoke(client, *args, **kwargs):
            if isinstance(client, _LifecycleReadClient):
                client.assert_open()
                with lifecycle_stage(client, stage):
                    return await work(client, *args, **kwargs)
            scope = _LifecycleReadClient(client, verify_source=not stage.startswith("plan"))
            try:
                with lifecycle_stage(scope, stage):
                    result = await work(scope, *args, **kwargs)
                    await asyncio.to_thread(scope.finalize)
                    return result
            finally:
                scope.close()
        return invoke
    return decorate


# Mirrors viem's default HTTP transport (3 retries, exponential backoff from 150ms) so a
# throttled public RPC is retried instead of surfacing as a failed execution proof.
_TRANSIENT_RETRY_DELAYS = (0.15, 0.3, 0.6)
_TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
_TRANSIENT_RPC_CODES = {429, -32005}


def _transient(native: Mapping[str, Any], error: Exception | None) -> bool:
    if error is not None and type(error).__name__ in {"ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout", "ChunkedEncodingError", "TimeoutError"}:
        return True
    if getattr(getattr(error, "response", None), "status_code", None) in _TRANSIENT_HTTP_STATUSES:
        return True
    for key in ("code", "status", "statusCode", "status_code"):
        value = native.get(key)
        if isinstance(value, int) and (value in _TRANSIENT_RPC_CODES or (key != "code" and value in _TRANSIENT_HTTP_STATUSES)):
            return True
    return "too many requests" in str(native.get("message", "")).lower()


def _request(client: Any, method: str, params: Sequence[Any]) -> Any:
    for delay in (*_TRANSIENT_RETRY_DELAYS, None):
        try:
            return _request_once(client, method, params)
        except LaunchRpcError as error:
            cause = error.__cause__ if isinstance(error.__cause__, Exception) else None
            if delay is None or not _transient(error._native, cause):
                raise
        time.sleep(delay)
    raise AssertionError("unreachable")


def _request_once(client: Any, method: str, params: Sequence[Any]) -> Any:
    try:
        if hasattr(client, "request"):
            return client.request({"method": method, "params": list(params)})
        response = client.provider.make_request(method, list(params))
    except (LaunchRpcError, LaunchStateChanged):
        raise
    except Exception as error:
        from .lifecycle import LifecyclePlanningError
        if isinstance(error, LifecyclePlanningError) or getattr(error, "name", None) == "AbortError":
            raise
        native = error.args[0] if error.args and isinstance(error.args[0], Mapping) else {
            "code": getattr(error, "code", None),
            "message": getattr(error, "message", str(error)),
            **({"data": error.data} if hasattr(error, "data") else {}),
            **({"details": error.details} if hasattr(error, "details") else {}),
            **({"status": error.status} if hasattr(error, "status") else {}),
            **({"statusCode": error.statusCode} if hasattr(error, "statusCode") else {}),
            **({"status_code": error.status_code} if hasattr(error, "status_code") else {}),
        }
        raise LaunchRpcError(method, native) from error
    if not isinstance(response, Mapping):
        raise RuntimeError("JSON-RPC response must be an object")
    if response.get("error") is not None:
        if not isinstance(response["error"], Mapping):
            raise RuntimeError("JSON-RPC error must be an object")
        raise LaunchRpcError(method, response["error"])
    if "result" not in response:
        raise RuntimeError(f"{method}: missing JSON-RPC result")
    return response["result"]


def rpc(client: Any, method: str, params: Sequence[Any]) -> Any:
    return client.run(method, params) if isinstance(client, _LifecycleReadClient) else _request(client, method, params)


def _client_fact(client: Any, key: tuple[Any, ...], load: Callable[[], Any]) -> Any:
    """Share immutable chain observations for a client session."""
    if isinstance(client, _LifecycleReadClient):
        client.assert_open()
    source = client.source if isinstance(client, _LifecycleReadClient) else client
    identity = id(source)
    with _session_lock:
        saved = _client_facts.get(identity)
        if saved is None or saved[0]() is not source:
            def forget(dead) -> None:
                with _session_lock:
                    current = _client_facts.get(identity)
                    if current is not None and current[0] is dead:
                        del _client_facts[identity]
            try:
                lookup = ref(source, forget)
            except TypeError:
                lookup = lambda: source
            saved = (lookup, {})
            _client_facts[identity] = saved
        pending = saved[1].get(key)
        owner = pending is None
        if owner:
            pending = Future()
            saved[1][key] = pending
    if not owner:
        return pending.result()
    try:
        result = load()
        pending.set_result(result)
        return result
    except BaseException as error:
        with _session_lock:
            if saved[1].get(key) is pending:
                del saved[1][key]
        pending.set_exception(error)
        raise


def read_chain_id(client: Any) -> int:
    """Observe the domain once per underlying client, not once per invocation."""
    return _client_fact(client, ("chain-id",), lambda: quantity(rpc(client, "eth_chainId", [])))





def read_gas_price(client: Any, block: LaunchBlock) -> int:
    return client.gas_price(block) if isinstance(client, _LifecycleReadClient) else quantity(rpc(client, "eth_gasPrice", []))


def check_lifecycle_source(client: Any, chain_id: int, *blocks: LaunchBlock) -> None:
    """Defer nested checkpoints to one uncached outer source guard."""
    if isinstance(client, _LifecycleReadClient):
        client.check_source(chain_id, blocks)
    else:
        assert_chain(client, chain_id)
        for block in dict.fromkeys(blocks):
            assert_canonical(client, block)


def failure_reason(failure: Any) -> str:
    """Expose fixed refusal text and numeric codes, never native messages/data."""
    from .lifecycle import LifecyclePlanningError
    for _ in range(8):
        if failure is None:
            break
        native = failure._native if isinstance(failure, LaunchRpcError) else failure
        field = native.get if isinstance(native, Mapping) else lambda key: getattr(native, key, None)
        status = field("status")
        if status is None:
            status = field("statusCode")
        if status is None:
            status = field("status_code")
        if isinstance(status, int) and not isinstance(status, bool) and 400 <= status <= 599:
            return f"HTTP read failed (status {status})"
        code = field("code")
        if isinstance(code, int) and not isinstance(code, bool) and abs(code) <= (1 << 53) - 1:
            return f"RPC request failed (code {code})"
        if isinstance(failure, (LifecyclePlanningError, LaunchStateChanged)):
            return "Lifecycle planning guard rejected the request"
        cause = field("cause")
        failure = cause if cause is not None else getattr(failure, "__cause__", None)
    return "Read failed"


def pinned_rpc(client: Any, method: str, params: Sequence[Any], block: LaunchBlock) -> Any:
    if method not in {"eth_call", "eth_getCode", "eth_getBalance"} or len(params) != 2 or params[1] != block.tag:
        raise ValueError("Reusable reads require an exact pinned block and no state overrides")
    return client.read(method, params, block) if isinstance(client, _LifecycleReadClient) else rpc(client, method, params)


@dataclass(frozen=True)
class LaunchBlock:
    number: int
    block_hash: str
    timestamp: int
    gas_limit: int
    base_fee_per_gas: int | None
    def __post_init__(self) -> None:
        if isinstance(self.gas_limit, bool) or not isinstance(self.gas_limit, int) or not 0 < self.gas_limit < (1 << 64):
            raise ValueError("block gas_limit must be a positive uint64")


    @property
    def tag(self) -> str:
        return hex(self.number)

@dataclass(frozen=True)
class LaunchLimitContext:
    block: LaunchBlock
    chain_id: int
    orchestrator: str
    account: str
    rpc_endpoint: str | None



def read_block(client: Web3 | LifecycleRpcClient | _LifecycleReadClient, tag: str | int = "latest") -> LaunchBlock:
    raw = rpc(client, "eth_getBlockByNumber", [hex(tag) if isinstance(tag, int) else tag, False])
    if raw is None:
        raise LaunchStateChanged("the requested canonical block is unavailable")
    if not isinstance(raw, Mapping):
        raise RuntimeError("Canonical block must be an object")
    block_hash = hex_bytes(raw["hash"])
    if len(block_hash) != 66:
        raise RuntimeError("Canonical block hash must have 32 bytes")
    block = LaunchBlock(
        number=quantity(raw["number"]),
        block_hash=block_hash,
        timestamp=quantity(raw["timestamp"]),
        gas_limit=quantity(raw["gasLimit"]),
        base_fee_per_gas=quantity(raw["baseFeePerGas"]) if raw.get("baseFeePerGas") is not None else None,
    )
    if isinstance(client, _LifecycleReadClient):
        client.receipt_headers[block.number] = block
    return block


def assert_canonical(client: Web3 | LifecycleRpcClient | _LifecycleReadClient, block: LaunchBlock) -> None:
    if read_block(client, block.number).block_hash != block.block_hash.lower():
        error = LaunchStateChanged("the pinned read block was reorganized; read again")
        error.code = "STATE_REORGED"
        raise error


def assert_chain(client: Web3 | LifecycleRpcClient | _LifecycleReadClient, chain_id: int) -> None:
    if read_chain_id(client) != chain_id:
        error = LaunchStateChanged("the read source chain changed; read again")
        error.code = "CHAIN_MISMATCH"
        raise error


@dataclass(frozen=True)
class LaunchExecutionLimits:
    """Optional gas/calldata policy, resolved against the chain's native ceiling."""

    headroom_bps: int = 1500
    chain_transaction_gas_limit: int | None = None
    rpc_transaction_gas_limit: int | None = None
    account_transaction_gas_limit: int | None = None
    max_calldata_bytes: int | None = None
    account_max_calldata_bytes: int | None = None
    observed_block_number: int | None = None
    observed_block_hash: str | None = None
    chain_id: int | None = None
    account: str | None = None
    orchestrator: str | None = None
    source: str | None = None
    protocol: Literal["evm", "nitro"] = "evm"
    execution_gas_ceiling: int | None = None
    transaction_gas_ceiling: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.headroom_bps, bool) or not isinstance(self.headroom_bps, int) or not 0 <= self.headroom_bps <= 10_000:
            raise ValueError("headroom_bps must be an integer from 0 to 10000")
        for name in (
            "chain_transaction_gas_limit", "rpc_transaction_gas_limit", "account_transaction_gas_limit",
            "execution_gas_ceiling", "transaction_gas_ceiling",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 < value < (1 << 64)):
                raise ValueError(f"{name} must be a positive uint64 when specified")
        for name in ("max_calldata_bytes", "account_max_calldata_bytes"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= (1 << 53) - 1):
                raise ValueError(f"{name} must be a positive safe integer")
        if self.protocol not in {"evm", "nitro"}:
            raise ValueError("protocol must be 'evm' or 'nitro'")
        if self.observed_block_number is not None and (
            isinstance(self.observed_block_number, bool) or not isinstance(self.observed_block_number, int)
            or not 0 <= self.observed_block_number < (1 << 256)
        ):
            raise ValueError("observed_block_number must be uint256")
        if self.observed_block_hash is not None and (not isinstance(self.observed_block_hash, str) or len(hex_bytes(self.observed_block_hash)) != 66):
            raise ValueError("observed_block_hash must be bytes32")
        if self.chain_id is not None and (
            isinstance(self.chain_id, bool) or not isinstance(self.chain_id, int) or not 0 < self.chain_id < (1 << 256)
        ):
            raise ValueError("limits chain_id must be a positive uint256")

    def gas_cap(self, block: LaunchBlock) -> int:
        caps = [value for value in (
            self.chain_transaction_gas_limit, self.rpc_transaction_gas_limit,
            self.account_transaction_gas_limit, self.transaction_gas_ceiling,
        ) if value is not None]
        return min(caps + ([block.gas_limit] if self.protocol == "evm" else [(1 << 64) - 1]))

    def compute_cap(self, block: LaunchBlock) -> int:
        if self.protocol == "nitro":
            if self.execution_gas_ceiling is None:
                raise ValueError("The native transaction ceiling has not been resolved")
            return self.execution_gas_ceiling
        return self.gas_cap(block)



    def calldata_cap(self) -> int | None:
        caps = [value for value in (self.max_calldata_bytes, self.account_max_calldata_bytes) if value is not None]
        return min(caps) if caps else None

    def buffered_gas(self, gas_used: int) -> int:
        if isinstance(gas_used, bool) or not isinstance(gas_used, int) or gas_used < 0:
            raise ValueError("gas_used must be a nonnegative integer")
        return (gas_used * (10_000 + self.headroom_bps) + 9_999) // 10_000


















def rpc_transaction(transaction: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("from", "to", "data", "value", "gas", "nonce", "chainId", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas"):
        if key not in transaction:
            continue
        value = transaction[key]
        if key in {"data"}:
            result[key] = hex_bytes(value)
        elif key in {"from", "to"}:
            result[key] = value
        else:
            result[key] = hex(quantity(value))
    return result

















__all__ = [
    "LaunchBlock", "LaunchExecutionLimits", "LaunchRpcError",
    "LaunchStateChanged", "LaunchLimitContext",
]
