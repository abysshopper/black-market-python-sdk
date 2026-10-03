"""Execute one verified UnifiedLauncher launch, or preview cases offline.

The default command path is an offline case preview: it builds each selected
request and validates calldata without constructing a Web3 or launch-API
client.  ``--execute`` launches exactly one ``--case``: it reads the private
key from ``--private-key``, ``--private-key-env NAME``, or the ``PRIVATE_KEY``
environment variable, performs the launch API session (with the image upload
when ``--image`` is supplied), executes the on-chain launch, and polls
publication to a final state.  ``--resume-publish`` finishes a previously
interrupted publication from retained private recovery material with no
private key, Web3 client, or broadcast.

The live flow mirrors the exercised Node runner: a decimal ``--initial-buy``
converted with the paired-token decimals, Chainlink ETH/USD pricing for WETH
paired tokens, shortfall-only WETH wrapping, the exact launcher allowance, a
gross post-buy sqrt-price guard, a provisional quote that becomes the
amount-out minimum, one final simulation, one broadcast, and
awaiting-indexer publication polling.

Run ``python examples/launch_submit.py --help`` for the complete command-line
surface.  This example intentionally owns transaction lifecycle policy; the
SDK remains responsible for calldata construction, ABI encoding, typed-data
construction, and API request serialization.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from eth_account.messages import encode_typed_data
from eth_utils import is_address

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from black_market_sdk import (
    ABYSS_FACTORY_ABI,
    ABYSS_FEE_TIERS,
    ABYSS_LAUNCH_COORDINATOR_ABI,
    ABYSS_LAUNCH_POOL_ADAPTER_V3_ABI,
    ABYSS_POSITION_MANAGER_ABI,
    ABYSS_POSITION_LOCKER_ABI,
    AGGREGATOR_V3_ABI,
    ATOMIC_LAUNCH_DEADLINE_SECONDS,
    ERC20_ABI,
    LAUNCH_POOL_REGISTRY_V3_ABI,
    MAX_LAUNCH_IMAGE_BYTES,
    ROBINHOOD_MAINNET_CHAIN_ID,
    UNIFIED_LAUNCHER_ABI,
    UNISWAP_V4_LAUNCH_POOL_ADAPTER_V3_ABI,
    WETH_ABI,
    AtomicLaunchBuySqrtPriceLimitInput,
    AtomicLaunchPoolRecipeInput,
    LaunchApiClient,
    LaunchApiConfig,
    LaunchApiError,
    LaunchAttributionAuthorization,
    LaunchPoolKind,
    LaunchPublishPending,
    LaunchSessionCreateRequest,
    LaunchSessionImageDescriptor,
    LaunchSessionMetadata,
    LaunchSessionPublishRequest,
    LaunchUploadError,
    build_launch_attribution_typed_data,
    build_unified_launch_calldata,
    build_unified_launch_transaction,
    create_protocol_wallet_web3,
    decode_unified_launch_receipt,
    derive_atomic_launch_buy_sqrt_price_limit_x96,
    derive_atomic_launch_pool_recipe,
    enabled_launch_pool_adapter,
    get_addresses,
    get_launch_template,
    get_unified_launch_addresses,
    parse_amount_to_units,
    sha256_hex,
)
from web3 import Web3

# ``examples`` is a namespace directory when imported from the repository, but
# executing this file directly puts its directory on sys.path instead.
try:  # pragma: no branch - one branch is selected by the invocation form.
    from examples.launch_matrix import (
        LaunchScenario,
        build_scenario_request,
        iter_launch_scenarios,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by direct invocation.
    from launch_matrix import LaunchScenario, build_scenario_request, iter_launch_scenarios


_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_OFFLINE_CREATOR = "0x1000000000000000000000000000000000000001"
_DEFAULT_DEADLINE = 1_800_000_000
_DEFAULT_PAIRED_USD_PRICE_X18 = 2_500 * 10**18
_DEFAULT_TARGET_MARKET_CAP_USD_X18 = 5_000 * 10**18


class LaunchSubmissionError(RuntimeError):
    """A deliberately non-sensitive operational error for this CLI."""


@dataclass(frozen=True)
class LiveRoute:
    """Dynamic route facts read from the selected on-chain adapter."""

    launcher: str
    registry: str
    adapter: str
    abyss_factory: str
    fee_tick_spacing: int
    oracle_max_abs_tick_move: int
    oracle_cardinality: int
    launch_fee: int
    wrapped_native: str | None
    v4_liquidity_locker: str | None
    abyss_bonus_distributor: str | None
    abyss_coordinator: str | None


@dataclass(frozen=True)
class ApiSessionContext:
    """Private API recovery material retained only in caller-designated state."""

    client: Any
    session_id: str
    capability: str


def _positive_int(value: str) -> int:
    try:
        parsed = int(value, 0)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value, 0)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a number") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the import-safe command-line parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Preview UnifiedLauncher launch cases offline, or execute exactly one "
            "--case with the launch API lifecycle, approvals, and one broadcast."
        )
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case", help="one stable case ID from the launch matrix")
    selection.add_argument(
        "--all",
        action="store_true",
        help="preview every matrix case offline; never combined with --execute",
    )

    resume = parser.add_argument_group("resume publication")
    resume.add_argument(
        "--resume-publish",
        action="store_true",
        help="finish an interrupted publication from retained recovery material; no key, Web3, or broadcast",
    )
    resume.add_argument("--session", help="private launch API session ID for --resume-publish")
    resume.add_argument("--capability", help="private session recovery capability for --resume-publish")
    resume.add_argument(
        "--transaction",
        help="already-confirmed launch transaction hash passed to --resume-publish",
    )

    parser.add_argument("--chain-id", type=_positive_int, default=ROBINHOOD_MAINNET_CHAIN_ID)
    parser.add_argument(
        "--rpc-url",
        help=(
            "explicit RPC URL used only with --execute; it is never echoed "
            "(default: the SDK Robinhood mainnet endpoint)"
        ),
    )

    parser.add_argument("--launcher", help="explicit current UnifiedLauncher address")
    parser.add_argument("--creator", help="offline creator address; live execution must match the signer")
    parser.add_argument("--paired-token", help="paired ERC-20 address (defaults to the selected chain WETH)")
    parser.add_argument("--paired-token-decimals", type=_positive_int, default=18)
    parser.add_argument(
        "--paired-token-usd-price-x18",
        type=_positive_int,
        help=(
            "current paired-asset USD price scaled by 1e18; overrides the "
            "Chainlink ETH/USD feed for WETH and is required for --execute "
            "with any non-WETH paired token"
        ),
    )
    parser.add_argument(
        "--target-market-cap-usd-x18",
        type=_positive_int,
        default=_DEFAULT_TARGET_MARKET_CAP_USD_X18,
    )
    parser.add_argument("--fee-pips", type=_positive_int, default=10_000)
    parser.add_argument(
        "--oracle-config-id",
        default="0xc0e9bed88d70a13fd3ab31451fefdd073b7266e838aee0ad1c236c8c9eff855d",
        help="bytes32 oracle configuration ID",
    )
    parser.add_argument(
        "--deadline",
        type=_positive_int,
        help="launch deadline; default is now plus the SDK atomic-launch deadline for --execute, or a static value for offline previews",
    )
    parser.add_argument("--name", help="on-chain token name")
    parser.add_argument("--symbol", help="on-chain token symbol")
    parser.add_argument(
        "--initial-buy",
        default="0",
        help=(
            "decimal paired-token initial buy (e.g. 0.0025); for a live Abyss "
            "launch paired with the chain WETH this rides msg.value as a native "
            "buy instead of an ERC-20 WETH pull"
        ),
    )
    parser.add_argument(
        "--native-buy-amount",
        type=_nonnegative_int,
        default=0,
        help=(
            "explicit native buy in wei; conflicts with --initial-buy on a live "
            "Abyss/WETH launch and with any V4 route"
        ),
    )
    parser.add_argument("--slippage-bps", type=_positive_int, default=50)
    parser.add_argument(
        "--external-liquidity-disabled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep external V4 liquidity disabled (the default); use --no-external-liquidity-disabled to opt out",
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help="launch exactly one --case: launch API session, approvals, one broadcast, and publication",
    )
    key_source = parser.add_mutually_exclusive_group()
    key_source.add_argument(
        "--private-key",
        help="private key used only when --execute is set; PRIVATE_KEY is read when no explicit key is given",
    )
    key_source.add_argument(
        "--private-key-env",
        metavar="NAME",
        help="read a private key from this explicitly named environment variable only",
    )
    parser.add_argument(
        "--receipt-timeout",
        type=_positive_float,
        default=180.0,
        help="bounded receipt wait in seconds",
    )
    parser.add_argument(
        "--private-state-output",
        help=(
            "optional 0600 recovery-state file for --execute"
        ),
    )

    parser.add_argument("--api-base-url", help="optional explicit launch API base URL; never echoed")
    parser.add_argument("--api-timeout", type=_positive_float, default=15.0)
    parser.add_argument("--api-deadline", type=_positive_int)
    parser.add_argument("--idempotency-key", help="one API session idempotency key; never echoed")
    parser.add_argument("--description", help="optional public launch description")
    parser.add_argument("--website-url", help="optional public website URL")
    parser.add_argument("--twitter-url", help="optional public Twitter/X URL")
    parser.add_argument("--telegram-url", help="optional public Telegram URL")
    parser.add_argument("--discord-url", help="optional public Discord URL")
    parser.add_argument("--image", help="optional PNG, JPEG, or WebP image path")
    parser.add_argument(
        "--image-content-type",
        help="override inferred image content type (image/png, image/jpeg, or image/webp)",
    )
    parser.add_argument(
        "--api-ready-polls",
        type=_positive_int,
        default=30,
        help="maximum bounded private-session GET polls after asynchronous completion",
    )
    parser.add_argument(
        "--api-ready-delay-seconds",
        type=_positive_float,
        default=1.0,
        help="delay between bounded private-session GET polls",
    )
    return parser


def _validate_cli_args(args: argparse.Namespace) -> None:
    if args.resume_publish:
        if not args.session or not args.capability or not args.transaction:
            raise LaunchSubmissionError(
                "--resume-publish requires --session, --capability, and --transaction"
            )
        if args.execute:
            raise LaunchSubmissionError("--resume-publish cannot be combined with --execute")
        return

    if args.execute:
        if args.all:
            raise LaunchSubmissionError(
                "--execute launches exactly one case and cannot be combined with --all"
            )
        if not args.case:
            raise LaunchSubmissionError("live execution requires --case ID")
        if args.slippage_bps > 9_999:
            raise LaunchSubmissionError("--slippage-bps must be at most 9,999")
    elif args.private_key or args.private_key_env:
        raise LaunchSubmissionError("a private-key source is accepted only together with --execute")

    if args.api_base_url and not args.execute:
        raise LaunchSubmissionError("--api-base-url requires --execute")
    if (args.image or args.image_content_type) and not args.execute:
        raise LaunchSubmissionError("--image and --image-content-type require --execute")
    if args.image_content_type and not args.image:
        raise LaunchSubmissionError("--image-content-type requires --image")
    if args.idempotency_key and not args.execute:
        raise LaunchSubmissionError("--idempotency-key requires --execute")

def _safe_address(value: object, label: str) -> str:
    if not isinstance(value, str) or not is_address(value) or value.lower() == _ZERO_ADDRESS:
        raise LaunchSubmissionError(f"{label} must be a nonzero address")
    try:
        return Web3.to_checksum_address(value)
    except (TypeError, ValueError):
        raise LaunchSubmissionError(f"{label} must be a nonzero address") from None


def _same_address(left: object, right: object) -> bool:
    return isinstance(left, str) and isinstance(right, str) and left.lower() == right.lower()


def _bytes32(value: str) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 66:
        raise LaunchSubmissionError("oracle configuration ID must be a 0x-prefixed bytes32 value")
    try:
        parsed = bytes.fromhex(value[2:])
    except ValueError:
        raise LaunchSubmissionError("oracle configuration ID must be hexadecimal") from None
    if len(parsed) != 32:
        raise LaunchSubmissionError("oracle configuration ID must be a bytes32 value")
    return parsed


def _bytes32_equal(left: object, right: bytes) -> bool:
    if isinstance(left, str):
        try:
            return bytes.fromhex(left.removeprefix("0x")) == right
        except ValueError:
            return False
    try:
        return bytes(left) == right
    except (TypeError, ValueError):
        return False


def _value_at(value: object, name: str, index: int) -> object:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
    try:
        return value[index]  # type: ignore[index]
    except (IndexError, KeyError, TypeError):
        raise LaunchSubmissionError("an on-chain response had an unexpected shape") from None


def _receipt_field(receipt: object, name: str) -> object:
    if isinstance(receipt, Mapping):
        if name in receipt:
            return receipt[name]
        camel_name = "".join(
            part.capitalize() if position else part
            for position, part in enumerate(name.split("_"))
        )
        if camel_name in receipt:
            return receipt[camel_name]
    try:
        return getattr(receipt, name)
    except AttributeError:
        camel_name = "".join(
            part.capitalize() if position else part
            for position, part in enumerate(name.split("_"))
        )
        try:
            return getattr(receipt, camel_name)
        except AttributeError:
            raise LaunchSubmissionError("transaction receipt had an unexpected shape") from None


def _rpc(call: Callable[[], Any], failure: str) -> Any:
    """Strip provider exception bodies, which may include signed/RPC URLs."""

    try:
        return call()
    except LaunchSubmissionError:
        raise
    except Exception:
        raise LaunchSubmissionError(failure) from None


def _api_call(call: Callable[[], Any], failure: str) -> Any:
    """Keep API exceptions typed while suppressing arbitrary transport bodies."""

    try:
        return call()
    except (LaunchApiError, LaunchUploadError, LaunchPublishPending):
        raise
    except Exception:
        raise LaunchSubmissionError(failure) from None


def _has_runtime_code(value: object) -> bool:
    if isinstance(value, str):
        return value.lower() not in ("", "0x", "0x0", "0x00")
    try:
        return len(bytes(value)) > 0
    except (TypeError, ValueError):
        return False


def _contract(w3: Any, address: str, abi: Sequence[Mapping[str, Any]], label: str) -> Any:
    address = _safe_address(address, f"{label} address")
    return _rpc(
        lambda: w3.eth.contract(address=address, abi=abi),
        f"could not construct the {label} contract surface",
    )


def _selected_scenarios(args: argparse.Namespace) -> tuple[LaunchScenario, ...]:
    scenarios = iter_launch_scenarios()
    if args.case:
        matched = tuple(item for item in scenarios if item.scenario_id == args.case)
        if not matched:
            raise LaunchSubmissionError("the requested case ID is not in the selected matrix")
        return matched
    return scenarios


def _paired_token_for_args(args: argparse.Namespace) -> str:
    if args.paired_token:
        return _safe_address(args.paired_token, "paired token")
    try:
        paired_token = get_addresses(args.chain_id).weth
    except (KeyError, ValueError):
        raise LaunchSubmissionError("--paired-token is required for this chain") from None
    return _safe_address(paired_token, "configured paired token")


def _chain_weth(chain_id: int) -> str | None:
    try:
        return get_addresses(chain_id).weth
    except (KeyError, ValueError):
        return None


def _launcher_for_args(args: argparse.Namespace) -> str:
    if args.launcher:
        return _safe_address(args.launcher, "launcher")
    try:
        launcher = get_unified_launch_addresses(args.chain_id).unified_launcher
    except (KeyError, ValueError):
        raise LaunchSubmissionError("--launcher is required for this chain") from None
    return _safe_address(launcher, "configured launcher")


def _fee_pips_tier(fee: int) -> bool:
    return any(tier.fee_pips == fee for tier in ABYSS_FEE_TIERS)


def _initial_buy_units(args: argparse.Namespace) -> int:
    """Convert the decimal initial-buy string to base units (Node semantics)."""

    amount = args.initial_buy
    if not isinstance(amount, str) or not re.fullmatch(r"\d+(?:\.\d+)?", amount):
        raise LaunchSubmissionError(
            "--initial-buy must be a nonnegative decimal within paired-token precision"
        )
    frac = amount.partition(".")[2]
    if len(frac) > args.paired_token_decimals:
        raise LaunchSubmissionError(
            "--initial-buy must be a nonnegative decimal within paired-token precision"
        )
    try:
        return parse_amount_to_units(amount, args.paired_token_decimals)
    except (TypeError, ValueError):
        raise LaunchSubmissionError(
            "--initial-buy must be a nonnegative decimal within paired-token precision"
        ) from None


def _abyss_weth_native_buy(
    *,
    execute: bool,
    scenario: LaunchScenario,
    paired_token: str,
    chain_id: int,
    initial_buy_units: int,
    native_buy_amount: int,
) -> tuple[int, int]:
    """Split an Abyss launch buy into ERC-20 and native (wrapped-native) parts.

    A live Abyss launch paired with the chain's WETH maps a positive decimal
    ``--initial-buy`` to a native buy riding ``msg.value``: the request keeps
    a zero ERC-20 ``pairedTokenAmountIn`` and the launch transaction carries
    ``launchFee`` plus the buy amount.  Offline previews, non-WETH paired
    tokens, and V4 routes keep the exact ERC-20 wrap/approve path.
    """

    if scenario.pool_kind is not LaunchPoolKind.ABYSS or not execute:
        return initial_buy_units, native_buy_amount
    if not _same_address(paired_token, _chain_weth(chain_id)):
        return initial_buy_units, native_buy_amount
    if native_buy_amount and initial_buy_units:
        raise LaunchSubmissionError(
            "--native-buy-amount conflicts with --initial-buy for a live Abyss/WETH launch"
        )
    return 0, native_buy_amount or initial_buy_units


def _deadline_for_args(args: argparse.Namespace) -> int:
    if args.deadline is not None:
        return args.deadline
    if args.execute:
        return int(time.time()) + ATOMIC_LAUNCH_DEADLINE_SECONDS
    return _DEFAULT_DEADLINE


def _build_request(
    scenario: LaunchScenario,
    args: argparse.Namespace,
    *,
    creator: str,
    paired_token: str,
    oracle_config_id: bytes,
    initial_buy_units: int,
    native_buy_amount: int,
    paired_token_usd_price_x18: int,
) -> Any:
    return build_scenario_request(
        scenario,
        creator=creator,
        paired_token=paired_token,
        paired_token_usd_price_x18=paired_token_usd_price_x18,
        fee=args.fee_pips,
        deadline=args.deadline,
        name=args.name,
        symbol=args.symbol,
        initial_buy_amount=initial_buy_units,
        native_buy_amount=native_buy_amount,
        slippage_bps=args.slippage_bps,
        external_liquidity_disabled=args.external_liquidity_disabled,
        oracle_config_id=oracle_config_id,
    )


def _disposition_summary(disposition: Any) -> dict[str, int]:
    return {
        "owner_bps": int(disposition.owner_bps),
        "rewards_bps": int(disposition.rewards_bps),
        "burn_bps": int(disposition.burn_bps),
    }


def _offline_summary(
    scenario: LaunchScenario,
    request: Any,
    args: argparse.Namespace,
    *,
    launcher: str,
    wrapped_native: str,
    native_buy_amount: int,
) -> dict[str, Any]:
    calldata = build_unified_launch_calldata(
        request,
        native_buy_amount=native_buy_amount,
        wrapped_native_token=wrapped_native,
    )
    is_v4 = scenario.pool_kind is LaunchPoolKind.UNISWAP_V4_V3
    return {
        "mode": "offline",
        "scenario_id": scenario.scenario_id,
        "pool_kind": scenario.pool_kind.value,
        "template_id": scenario.template_id,
        "creator": request.creator,
        "launcher": launcher,
        "token": {
            "name": request.token.name,
            "symbol": request.token.symbol,
            "decimals": request.token.decimals,
            "supply": request.token.supply,
            "token_type": "0x" + bytes(request.token.token_type).hex(),
        },
        "profile": int(scenario.profile),
        "dual_rewards": scenario.dual_rewards,
        "calldata_bytes": len(calldata),
        "initial_buy_paired_token_amount": request.initial_buy.paired_token_amount_in,
        "native_value": (
            0
            if is_v4
            else {
                "native_initial_buy_amount": native_buy_amount,
                "launch_fee": "read from the selected live Abyss adapter before submission",
            }
        ),
        "launched_token_fees": _disposition_summary(request.launched_token_fees),
        "paired_token_fees": _disposition_summary(request.paired_token_fees),
    }


def _read_private_key(args: argparse.Namespace) -> str:
    if args.private_key is not None:
        return args.private_key
    if args.private_key_env is not None:
        value = os.environ.get(args.private_key_env)
        if not value:
            raise LaunchSubmissionError("the explicitly named private-key environment variable is unset")
        return value
    value = os.environ.get("PRIVATE_KEY")
    if not value:
        raise LaunchSubmissionError(
            "--execute requires a private key from --private-key, --private-key-env, or the PRIVATE_KEY environment variable"
        )
    return value


def _state_path_for(args: argparse.Namespace, scenario: LaunchScenario) -> Path | None:
    if not args.private_state_output:
        return None
    scenario_id = scenario.scenario_id
    if not scenario_id or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in scenario_id
    ):
        raise LaunchSubmissionError("matrix scenario ID cannot be used safely in a state-file path")
    return Path(args.private_state_output.replace("{scenario}", scenario_id))


def _write_private_state(path: Path, state: Mapping[str, Any]) -> None:
    """Write recovery material only to a caller-selected 0600 regular file."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(state, handle, separators=(",", ":"), sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if descriptor != -1:
                os.close(descriptor)
    except OSError:
        raise LaunchSubmissionError("could not write the caller-designated private recovery state") from None


def _persist_state(path: Path | None, state: dict[str, Any], stage: str) -> None:
    if path is None:
        return
    state["stage"] = stage
    _write_private_state(path, state)


def _api_state(state: dict[str, Any]) -> dict[str, Any]:
    api = state.get("api")
    if not isinstance(api, dict):
        api = {}
        state["api"] = api
    return api


def _string_field(mapping: Mapping[str, Any], key: str, failure: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise LaunchSubmissionError(failure)
    return value


def _session_status(mapping: Mapping[str, Any]) -> str:
    status = mapping.get("status")
    if not isinstance(status, str) or not status:
        raise LaunchSubmissionError("launch API session response omitted its status")
    return status


def _image_for_args(args: argparse.Namespace) -> tuple[bytes | None, LaunchSessionImageDescriptor | None]:
    if not args.image:
        return None, None
    image_path = Path(args.image)
    try:
        image_size = image_path.stat().st_size
    except OSError:
        raise LaunchSubmissionError("could not read the requested image") from None
    if image_size < 1 or image_size > MAX_LAUNCH_IMAGE_BYTES:
        raise LaunchSubmissionError("image size is outside the API-supported range")
    content_type = args.image_content_type or mimetypes.guess_type(str(image_path))[0]
    if content_type == "image/jpg":
        content_type = "image/jpeg"
    if content_type is None:
        raise LaunchSubmissionError("could not infer an allowed image content type")
    try:
        image = image_path.read_bytes()
    except OSError:
        raise LaunchSubmissionError("could not read the requested image") from None
    try:
        descriptor = LaunchSessionImageDescriptor(
            sha256=sha256_hex(image),
            content_type=content_type,
            content_length=len(image),
        )
    except (TypeError, ValueError):
        raise LaunchSubmissionError("image content type is not accepted by the launch API") from None
    return image, descriptor


def _signature_hex(account: Any, typed_data: Mapping[str, Any]) -> str:
    signed = _rpc(
        lambda: account.sign_message(encode_typed_data(full_message=typed_data)),
        "could not sign the launch API attribution locally",
    )
    signature = getattr(signed, "signature", None)
    try:
        rendered = signature.hex()
    except (AttributeError, TypeError):
        raise LaunchSubmissionError("local attribution signer returned no signature") from None
    return rendered if rendered.startswith("0x") else "0x" + rendered


def _wait_for_ready_api_session(
    client: Any,
    *,
    chain_id: int,
    session_id: str,
    capability: str,
    initial: Mapping[str, Any],
    poll_limit: int,
    poll_delay_seconds: float,
    sleeper: Callable[[float], None],
) -> Mapping[str, Any]:
    current = initial
    for attempt in range(poll_limit):
        if _session_status(current) == "ready_to_launch":
            return current
        if attempt + 1 == poll_limit:
            break
        sleeper(poll_delay_seconds)
        current = _api_call(
            lambda: client.get_launch_upload_session(chain_id, session_id, capability),
            "could not poll the private launch API session",
        )
        if not isinstance(current, Mapping):
            raise LaunchSubmissionError("launch API returned an invalid private session response")
    raise LaunchSubmissionError(
        "launch API session did not become ready within the configured bound; recovery state was saved"
    )


def _prepare_api_session(
    *,
    args: argparse.Namespace,
    request: Any,
    account: Any,
    account_address: str,
    launcher: str,
    state_path: Path | None,
    state: dict[str, Any],
    api_client_factory: Callable[[LaunchApiConfig], Any],
    sleeper: Callable[[float], None],
) -> ApiSessionContext:
    image_bytes, image_descriptor = _image_for_args(args)
    metadata = LaunchSessionMetadata(
        name=request.token.name,
        symbol=request.token.symbol,
        description=args.description,
        website_url=args.website_url,
        twitter_url=args.twitter_url,
        telegram_url=args.telegram_url,
        discord_url=args.discord_url,
    )
    idempotency_key = args.idempotency_key or secrets.token_urlsafe(24)
    nonce = "0x" + secrets.token_hex(32)
    authorization_deadline = args.api_deadline or request.deadline
    typed_data = build_launch_attribution_typed_data(
        chain_id=args.chain_id,
        wallet=account_address,
        metadata=metadata,
        image=image_descriptor,
        idempotency_key=idempotency_key,
        nonce=nonce,
        deadline=authorization_deadline,
        verifying_contract=launcher,
    )
    signature = _signature_hex(account, typed_data)
    api_state = _api_state(state)
    api_state.update(
        {
            "idempotency_key": idempotency_key,
            "nonce": nonce,
            "deadline": authorization_deadline,
            "metadata_hash": typed_data["message"]["metadataHash"],
            "signature": signature,
            "has_image": image_descriptor is not None,
        }
    )
    _persist_state(state_path, state, "api_authorization_prepared")

    config = (
        LaunchApiConfig(base_url=args.api_base_url, timeout=args.api_timeout)
        if args.api_base_url
        else LaunchApiConfig(timeout=args.api_timeout)
    )
    client = _api_call(
        lambda: api_client_factory(config),
        "could not initialize the launch API client",
    )
    session = _api_call(
        lambda: client.create_launch_upload_session(
            LaunchSessionCreateRequest(
                chain_id=args.chain_id,
                wallet=account_address,
                metadata=metadata,
                image=image_descriptor,
                authorization=LaunchAttributionAuthorization(
                    nonce=nonce,
                    deadline=authorization_deadline,
                    signature=signature,
                ),
            ),
            idempotency_key,
        ),
        "could not create the private launch API session",
    )
    if not isinstance(session, Mapping):
        raise LaunchSubmissionError("launch API returned an invalid session response")
    session_id = _string_field(session, "sessionId", "launch API session omitted its ID")
    capability = _string_field(session, "capability", "launch API session omitted its recovery capability")
    api_state.update({"session_id": session_id, "capability": capability, "status": _session_status(session)})
    # This must happen before the direct upload because the API returns this
    # capability only once and it is the caller's recovery mechanism.
    _persist_state(state_path, state, "api_session_created")

    if image_bytes is not None:
        upload = session.get("upload")
        if not isinstance(upload, Mapping):
            raise LaunchSubmissionError("launch API image session omitted its direct upload contract")
        _api_call(
            lambda: client.put_launch_image(upload, image_bytes),
            "could not upload the launch image",
        )
        api_state["status"] = "image_uploaded"
        _persist_state(state_path, state, "api_image_uploaded")
        completion = _api_call(
            lambda: client.complete_launch_upload(args.chain_id, session_id, capability),
            "could not complete the launch image upload",
        )
        if not isinstance(completion, Mapping):
            raise LaunchSubmissionError("launch API returned an invalid completion response")
        ready_source: Mapping[str, Any] = completion
    else:
        ready_source = session

    ready = _wait_for_ready_api_session(
        client,
        chain_id=args.chain_id,
        session_id=session_id,
        capability=capability,
        initial=ready_source,
        poll_limit=args.api_ready_polls,
        poll_delay_seconds=args.api_ready_delay_seconds,
        sleeper=sleeper,
    )
    api_state["status"] = _session_status(ready)
    _persist_state(state_path, state, "api_ready_to_launch")
    return ApiSessionContext(client=client, session_id=session_id, capability=capability)


def _adapter_abi(pool_kind: LaunchPoolKind) -> Sequence[Mapping[str, Any]]:
    if pool_kind is LaunchPoolKind.ABYSS:
        return ABYSS_LAUNCH_POOL_ADAPTER_V3_ABI
    if pool_kind is LaunchPoolKind.UNISWAP_V4_V3:
        return UNISWAP_V4_LAUNCH_POOL_ADAPTER_V3_ABI
    raise LaunchSubmissionError("selected scenario has no supported live route")


def _read_live_route(
    w3: Any,
    *,
    request: Any,
    scenario: LaunchScenario,
    chain_id: int,
    launcher: str,
    fee: int,
    oracle_config_id: bytes,
) -> LiveRoute:
    launcher = _safe_address(launcher, "effective launcher")
    launcher_code = _rpc(
        lambda: w3.eth.get_code(launcher),
        "could not read the effective launcher code",
    )
    if not _has_runtime_code(launcher_code):
        raise LaunchSubmissionError("the effective launcher address has no runtime code")

    launcher_contract = _contract(w3, launcher, UNIFIED_LAUNCHER_ABI, "UnifiedLauncher")
    registry_address = _safe_address(
        _rpc(
            lambda: launcher_contract.functions.poolRegistry().call(),
            "could not read the UnifiedLauncher pool registry",
        ),
        "on-chain pool registry",
    )
    registry = _contract(w3, registry_address, LAUNCH_POOL_REGISTRY_V3_ABI, "launch pool registry")
    entry = _rpc(
        lambda: registry.functions.poolTypes(request.pool_type).call(),
        "could not read the selected registry route",
    )
    try:
        adapter_address = enabled_launch_pool_adapter(entry)
    except (TypeError, ValueError):
        raise LaunchSubmissionError("selected registry route had an invalid shape") from None
    if adapter_address is None:
        raise LaunchSubmissionError("selected registry route is disabled or has no adapter")
    adapter_address = _safe_address(adapter_address, "selected registry adapter")
    reported_adapter = _safe_address(
        _rpc(
            lambda: registry.functions.adapter(request.pool_type).call(),
            "could not verify the selected registry adapter",
        ),
        "reported registry adapter",
    )
    if not _same_address(adapter_address, reported_adapter):
        raise LaunchSubmissionError("registry route and adapter lookup disagree; no route was substituted")
    if scenario.pool_kind is LaunchPoolKind.UNISWAP_V4_V3:
        try:
            recorded_adapter = get_unified_launch_addresses(chain_id).uniswap_v4_launch_pool_adapter_v3
        except (KeyError, ValueError):
            recorded_adapter = None
        if (
            recorded_adapter
            and recorded_adapter.lower() != _ZERO_ADDRESS
            and not _same_address(recorded_adapter, adapter_address)
        ):
            raise LaunchSubmissionError(
                "The active Uniswap V4 V3 deployment is not enabled at the recorded registry binding."
            )

    adapter = _contract(w3, adapter_address, _adapter_abi(scenario.pool_kind), "selected launch adapter")
    adapter_pool_type = _rpc(
        lambda: adapter.functions.poolType().call(),
        "could not verify the selected adapter binding",
    )
    if not _bytes32_equal(adapter_pool_type, bytes(request.pool_type)):
        raise LaunchSubmissionError("selected adapter is not bound to the requested pool type")
    factory_address = _safe_address(
        _rpc(
            lambda: adapter.functions.abyssFactory().call(),
            "could not read the selected adapter factory",
        ),
        "selected adapter Abyss factory",
    )
    factory = _contract(w3, factory_address, ABYSS_FACTORY_ABI, "selected adapter Abyss factory")
    tick_spacing = _rpc(
        lambda: factory.functions.feeAmountTickSpacing(fee).call(),
        "could not read the current factory fee tick spacing",
    )
    try:
        tick_spacing = int(tick_spacing)
    except (TypeError, ValueError):
        raise LaunchSubmissionError("current factory fee tick spacing had an invalid value") from None
    if tick_spacing <= 0:
        raise LaunchSubmissionError("selected fee is not registered by the current factory")
    oracle = _rpc(
        lambda: factory.functions.oracleConfigs(oracle_config_id).call(),
        "could not read the current factory oracle configuration",
    )
    try:
        max_abs_tick_move = int(_value_at(oracle, "maxAbsTickMove", 0))
        cardinality = int(_value_at(oracle, "cardinality", 1))
    except (TypeError, ValueError):
        raise LaunchSubmissionError("current factory oracle configuration had an invalid value") from None
    if max_abs_tick_move <= 0 or cardinality <= 0:
        raise LaunchSubmissionError("selected oracle configuration is not registered by the current factory")

    if scenario.pool_kind is LaunchPoolKind.ABYSS:
        launch_fee = _rpc(
            lambda: adapter.functions.launchFee().call(),
            "could not read the current Abyss launch fee",
        )
        try:
            launch_fee = int(launch_fee)
        except (TypeError, ValueError):
            raise LaunchSubmissionError("current Abyss launch fee had an invalid value") from None
        if launch_fee <= 0:
            raise LaunchSubmissionError("current Abyss launch fee is invalid")
        wrapped_native = _safe_address(
            _rpc(
                lambda: adapter.functions.wrappedNative().call(),
                "could not read the current Abyss wrapped-native asset",
            ),
            "current Abyss wrapped-native asset",
        )
        return LiveRoute(
            launcher=launcher,
            registry=registry_address,
            adapter=adapter_address,
            abyss_factory=factory_address,
            fee_tick_spacing=tick_spacing,
            oracle_max_abs_tick_move=max_abs_tick_move,
            oracle_cardinality=cardinality,
            launch_fee=launch_fee,
            wrapped_native=wrapped_native,
            v4_liquidity_locker=None,
            abyss_bonus_distributor=None,
            abyss_coordinator=None,
        )

    if scenario.pool_kind is not LaunchPoolKind.UNISWAP_V4_V3:
        raise LaunchSubmissionError("selected scenario has no supported live route")
    if request.initial_buy.paired_token_amount_in < 0:
        raise LaunchSubmissionError("initial buy amount is invalid")
    # Current V4 adapters reject all msg.value.  The pure SDK builder enforces
    # this again when it constructs the final transaction.
    return LiveRoute(
        launcher=launcher,
        registry=registry_address,
        adapter=adapter_address,
        abyss_factory=factory_address,
        fee_tick_spacing=tick_spacing,
        oracle_max_abs_tick_move=max_abs_tick_move,
        oracle_cardinality=cardinality,
        launch_fee=0,
        wrapped_native=None,
        v4_liquidity_locker=_safe_address(
            _rpc(
                lambda: adapter.functions.v4LiquidityLocker().call(),
                "could not read the V4 permanent locker",
            ),
            "V4 permanent locker",
        ),
        abyss_bonus_distributor=_safe_address(
            _rpc(
                lambda: adapter.functions.abyssBonusDistributor().call(),
                "could not read the V4 Abyss fee distributor",
            ),
            "V4 Abyss fee distributor",
        ),
        abyss_coordinator=_safe_address(
            _rpc(
                lambda: adapter.functions.abyssCoordinator().call(),
                "could not read the V4 Abyss coordinator",
            ),
            "V4 Abyss coordinator",
        ),
    )


def _transaction_hash_text(value: object) -> str:
    try:
        rendered = value.hex()
    except (AttributeError, TypeError):
        raise LaunchSubmissionError("RPC returned an invalid transaction hash") from None
    if not isinstance(rendered, str):
        raise LaunchSubmissionError("RPC returned an invalid transaction hash")
    return rendered if rendered.startswith("0x") else "0x" + rendered


def _signed_raw_transaction(signed: object) -> object:
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = getattr(signed, "rawTransaction", None)
    if raw is None:
        raise LaunchSubmissionError("local signer returned no signed transaction")
    return raw


def _transaction_execution_fields(
    w3: Any,
    transaction: Mapping[str, Any],
    *,
    account_address: str,
    chain_id: int,
) -> dict[str, Any]:
    prepared = dict(transaction)
    account_address = _safe_address(account_address, "signer address")
    prepared["from"] = account_address
    if "to" in prepared:
        prepared["to"] = _safe_address(prepared["to"], "transaction target")
    prepared["chainId"] = chain_id
    prepared["nonce"] = _rpc(
        lambda: w3.eth.get_transaction_count(account_address, "pending"),
        "could not read the signer transaction nonce",
    )
    if "gas" not in prepared:
        prepared["gas"] = _rpc(
            lambda: w3.eth.estimate_gas(prepared),
            "could not estimate transaction gas",
        )
    if "gasPrice" not in prepared and "maxFeePerGas" not in prepared:
        prepared["gasPrice"] = _rpc(
            lambda: w3.eth.gas_price,
            "could not read the current gas price",
        )
    return prepared


def _broadcast_once(
    w3: Any,
    account: Any,
    transaction: Mapping[str, Any],
    *,
    timeout: float,
    state_path: Path | None,
    state: dict[str, Any] | None,
    state_key: str,
) -> tuple[str, Any]:
    signed = _rpc(
        lambda: account.sign_transaction(dict(transaction)),
        "could not sign the transaction locally",
    )
    transaction_hash = _transaction_hash_text(
        _rpc(
            lambda: w3.eth.send_raw_transaction(_signed_raw_transaction(signed)),
            "transaction broadcast was not accepted",
        )
    )
    if state is not None:
        on_chain = state.setdefault("on_chain", {})
        if not isinstance(on_chain, dict):
            raise LaunchSubmissionError("private recovery state had an invalid shape")
        hashes = on_chain.setdefault(state_key, [])
        if not isinstance(hashes, list):
            raise LaunchSubmissionError("private recovery state had an invalid shape")
        hashes.append(transaction_hash)
        _persist_state(state_path, state, f"{state_key}_broadcast")
    receipt = _rpc(
        lambda: w3.eth.wait_for_transaction_receipt(transaction_hash, timeout=timeout),
        "transaction was broadcast once but its receipt was unavailable; consult recovery state",
    )
    try:
        successful = int(_receipt_field(receipt, "status")) == 1
    except (TypeError, ValueError):
        raise LaunchSubmissionError("transaction receipt had an invalid status") from None
    if not successful:
        if state is not None:
            _persist_state(state_path, state, f"{state_key}_reverted")
        raise LaunchSubmissionError("broadcast transaction reverted; it was not retried")
    return transaction_hash, receipt


def _function_transaction(
    w3: Any,
    function: Any,
    *,
    account_address: str,
    chain_id: int,
    value: int | None = None,
) -> dict[str, Any]:
    """Build a signed-function transaction envelope, optionally payable."""

    account_address = _safe_address(account_address, "signer address")
    nonce = _rpc(
        lambda: w3.eth.get_transaction_count(account_address, "pending"),
        "could not read the signer transaction nonce",
    )
    fields: dict[str, Any] = {"from": account_address, "chainId": chain_id, "nonce": nonce}
    if value is not None:
        if not isinstance(value, int) or value < 0:
            raise LaunchSubmissionError("payable function transaction had an invalid value")
        fields["value"] = value
    transaction = _rpc(
        lambda: function.build_transaction(fields),
        "could not construct the function transaction",
    )
    if not isinstance(transaction, Mapping):
        raise LaunchSubmissionError("function transaction builder returned an invalid value")
    prepared = dict(transaction)
    if "to" in prepared:
        prepared["to"] = _safe_address(prepared["to"], "function transaction target")
    prepared.setdefault("from", account_address)
    prepared.setdefault("chainId", chain_id)
    prepared.setdefault("nonce", nonce)
    if value is not None:
        prepared["value"] = value
    if "gas" not in prepared:
        prepared["gas"] = _rpc(
            lambda: w3.eth.estimate_gas(prepared),
            "could not estimate function transaction gas",
        )
    if "gasPrice" not in prepared and "maxFeePerGas" not in prepared:
        prepared["gasPrice"] = _rpc(
            lambda: w3.eth.gas_price,
            "could not read the current gas price",
        )
    return prepared


def _ensure_exact_allowance(
    w3: Any,
    account: Any,
    account_address: str,
    *,
    paired_token: str,
    launcher: str,
    amount: int,
    chain_id: int,
    timeout: float,
    state_path: Path | None,
    state: dict[str, Any] | None,
) -> None:
    account_address = _safe_address(account_address, "signer address")
    paired_token = _safe_address(paired_token, "paired token")
    launcher = _safe_address(launcher, "launcher")
    if amount == 0:
        return
    token = _contract(w3, paired_token, ERC20_ABI, "paired ERC-20")
    balance = _rpc(
        lambda: token.functions.balanceOf(account_address).call(),
        "could not read paired-token balance",
    )
    allowance = _rpc(
        lambda: token.functions.allowance(account_address, launcher).call(),
        "could not read paired-token allowance",
    )
    try:
        balance = int(balance)
        allowance = int(allowance)
    except (TypeError, ValueError):
        raise LaunchSubmissionError("paired-token balance or allowance had an invalid value") from None
    if balance < amount:
        raise LaunchSubmissionError("paired-token balance is insufficient for the requested initial buy")
    if allowance >= amount:
        return

    exact_approval = token.functions.approve(launcher, amount)
    approval_requires_reset = False
    try:
        simulation = exact_approval.call({"from": account_address})
        approval_requires_reset = simulation is False
    except Exception:
        # Some ERC-20s require zero before replacing a nonzero allowance.  Only
        # choose that known safe sequence when the current allowance is nonzero.
        approval_requires_reset = allowance != 0
    if approval_requires_reset:
        if allowance == 0:
            raise LaunchSubmissionError("exact paired-token approval could not be simulated")
        reset_transaction = _function_transaction(
            w3,
            token.functions.approve(launcher, 0),
            account_address=account_address,
            chain_id=chain_id,
        )
        _broadcast_once(
            w3,
            account,
            reset_transaction,
            timeout=timeout,
            state_path=state_path,
            state=state,
            state_key="approval_transactions",
        )
    approval_transaction = _function_transaction(
        w3,
        exact_approval,
        account_address=account_address,
        chain_id=chain_id,
    )
    _broadcast_once(
        w3,
        account,
        approval_transaction,
        timeout=timeout,
        state_path=state_path,
        state=state,
        state_key="approval_transactions",
    )


def _require_native_balance(w3: Any, account_address: str, required: int) -> None:
    account_address = _safe_address(account_address, "signer address")
    balance = _rpc(
        lambda: w3.eth.get_balance(account_address),
        "could not read signer native balance",
    )
    try:
        balance = int(balance)
    except (TypeError, ValueError):
        raise LaunchSubmissionError("signer native balance had an invalid value") from None
    if balance < required:
        raise LaunchSubmissionError("signer native balance is insufficient for this transaction")


def _wrap_weth_shortfall(
    w3: Any,
    account: Any,
    account_address: str,
    *,
    paired_token: str,
    units: int,
    chain_id: int,
    timeout: float,
    state_path: Path | None,
    state: dict[str, Any] | None,
) -> None:
    """Wrap only the WETH shortfall, never more, before the exact allowance."""

    account_address = _safe_address(account_address, "signer address")
    paired_token = _safe_address(paired_token, "paired token")
    weth = _contract(w3, paired_token, WETH_ABI, "chain WETH")
    balance = _rpc(
        lambda: weth.functions.balanceOf(account_address).call(),
        "could not read the WETH balance",
    )
    try:
        balance = int(balance)
    except (TypeError, ValueError):
        raise LaunchSubmissionError("WETH balance had an invalid value") from None
    if balance >= units:
        return
    shortfall = units - balance
    _require_native_balance(
        w3,
        account_address,
        shortfall + 50_000 * int(_rpc(lambda: w3.eth.gas_price, "could not read the current gas price")),
    )
    wrap_transaction = _function_transaction(
        w3,
        weth.functions.deposit(),
        account_address=account_address,
        chain_id=chain_id,
        value=shortfall,
    )
    _broadcast_once(
        w3,
        account,
        wrap_transaction,
        timeout=timeout,
        state_path=state_path,
        state=state,
        state_key="weth_wrap_transactions",
    )


def _event_args(event: object) -> Mapping[str, Any]:
    if isinstance(event, Mapping):
        arguments = event.get("args")
    else:
        arguments = getattr(event, "args", None)
    if not isinstance(arguments, Mapping):
        raise LaunchSubmissionError("launch receipt event had an invalid shape")
    return arguments


def _matching_event(
    events: Sequence[object],
    *,
    token: str,
    event_name: str,
) -> Mapping[str, Any]:
    for event in events:
        arguments = _event_args(event)
        candidate = arguments.get("token")
        if candidate is None:
            candidate = arguments.get("launchedToken")
        if _same_address(candidate, token):
            return arguments
    raise LaunchSubmissionError(f"confirmed receipt omitted {event_name} for the launched token")


def _decode_launch_events(
    launcher_contract: Any,
    receipt: Any,
    *,
    request: Any,
    account_address: str,
) -> dict[str, Any]:
    completed_events = _rpc(
        lambda: launcher_contract.events.LaunchCompleted().process_receipt(receipt),
        "could not decode LaunchCompleted from the confirmed receipt",
    )
    if not isinstance(completed_events, Sequence):
        raise LaunchSubmissionError("confirmed receipt returned invalid LaunchCompleted events")
    # We cannot know the token before decoding, so validate candidates by the
    # public creator and exact indexed pool type first.
    completed: Mapping[str, Any] | None = None
    for event in completed_events:
        candidate = _event_args(event)
        if not _same_address(candidate.get("creator"), account_address):
            continue
        if not _bytes32_equal(candidate.get("poolType"), bytes(request.pool_type)):
            continue
        completed = candidate
        break
    if completed is None:
        raise LaunchSubmissionError("confirmed receipt omitted a matching LaunchCompleted event")
    token = _safe_address(completed.get("token"), "launched token in receipt")
    market = _safe_address(completed.get("pool"), "launch market in receipt")
    try:
        token_id = int(completed.get("tokenId"))
        liquidity_launched = int(completed.get("liquidityLaunchedTokenAmount"))
        liquidity_paired = int(completed.get("liquidityPairedTokenAmount"))
        initial_buy_paired = int(completed.get("initialBuyPairedTokenAmount"))
        initial_buy_launched = int(completed.get("initialBuyLaunchedTokenAmount"))
    except (TypeError, ValueError):
        raise LaunchSubmissionError("LaunchCompleted receipt event had invalid numeric fields") from None

    module_events = _rpc(
        lambda: launcher_contract.events.LaunchModulesDeployed().process_receipt(receipt),
        "could not decode LaunchModulesDeployed from the confirmed receipt",
    )
    if not isinstance(module_events, Sequence):
        raise LaunchSubmissionError("confirmed receipt returned invalid LaunchModulesDeployed events")
    modules = _matching_event(module_events, token=token, event_name="LaunchModulesDeployed")
    return {
        "token": token,
        "market": market,
        "token_id": token_id,
        "liquidity_launched_token_amount": liquidity_launched,
        "liquidity_paired_token_amount": liquidity_paired,
        "initial_buy_paired_token_amount": initial_buy_paired,
        "initial_buy_launched_token_amount": initial_buy_launched,
        "rewards": _safe_address(modules.get("rewards"), "receipt rewards module"),
        "splitter": _safe_address(modules.get("splitter"), "receipt splitter module"),
        "fee_claimer": _safe_address(modules.get("feeClaimer"), "receipt fee claimer module"),
    }


def _v4_companion_details(w3: Any, route: LiveRoute, *, token_id: int) -> dict[str, Any]:
    if not route.abyss_coordinator or not route.v4_liquidity_locker or not route.abyss_bonus_distributor:
        raise LaunchSubmissionError("selected V4 adapter omitted its companion route bindings")
    coordinator = _contract(
        w3,
        route.abyss_coordinator,
        ABYSS_LAUNCH_COORDINATOR_ABI,
        "V4 Abyss coordinator",
    )
    position_manager_address = _safe_address(
        _rpc(
            lambda: coordinator.functions.positionManager().call(),
            "could not read the V4 companion position manager",
        ),
        "V4 companion position manager",
    )
    position_manager = _contract(
        w3,
        position_manager_address,
        ABYSS_POSITION_MANAGER_ABI,
        "V4 companion position manager",
    )
    position = _rpc(
        lambda: position_manager.functions.positions(token_id).call(),
        "could not read the V4 companion position",
    )
    companion_pool = _safe_address(
        _value_at(position, "pool", 1),
        "V4 companion Abyss pool",
    )
    try:
        companion_liquidity = int(_value_at(position, "liquidity", 4))
    except (TypeError, ValueError):
        raise LaunchSubmissionError("V4 companion position had an invalid liquidity value") from None
    if companion_liquidity <= 0:
        raise LaunchSubmissionError("V4 companion position has no observed liquidity")

    abyss_locker_address = _safe_address(
        _rpc(
            lambda: coordinator.functions.positionLocker().call(),
            "could not read the V4 companion position locker",
        ),
        "V4 companion position locker",
    )
    abyss_locker = _contract(
        w3,
        abyss_locker_address,
        ABYSS_POSITION_LOCKER_ABI,
        "V4 companion position locker",
    )
    lock = _rpc(
        lambda: abyss_locker.functions.locks(token_id).call(),
        "could not read the V4 companion lock",
    )
    try:
        lock_owner = _safe_address(_value_at(lock, "owner", 0), "V4 companion lock owner")
        claim_authority = _safe_address(
            _value_at(lock, "claimAuthority", 1), "V4 companion claim authority"
        )
        fee_recipient = _safe_address(
            _value_at(lock, "feeRecipient", 2), "V4 companion fee recipient"
        )
        unlock_time = int(_value_at(lock, "unlockTime", 3))
        permissionless_claim = _value_at(lock, "permissionlessClaim", 4)
    except (TypeError, ValueError):
        raise LaunchSubmissionError("V4 companion lock had invalid values") from None
    if (
        not _same_address(lock_owner, route.abyss_coordinator)
        or not _same_address(claim_authority, route.abyss_bonus_distributor)
        or not _same_address(fee_recipient, route.abyss_bonus_distributor)
        or unlock_time != 0
        or permissionless_claim is not False
    ):
        raise LaunchSubmissionError("V4 companion lock did not have the permanent protocol custody terms")

    return {
        # Unified LaunchCompleted.pool is the V4 hook, not this Abyss market.
        "permanent_v4_locker": route.v4_liquidity_locker,
        "v4_liquidity_share_bps": 9_900,
        "abyss_lighthouse_pool": companion_pool,
        "abyss_lighthouse_position_id": token_id,
        "abyss_lighthouse_liquidity": companion_liquidity,
        "abyss_lighthouse_share_bps": 100,
        "protocol_fee_distributor": route.abyss_bonus_distributor,
        "abyss_lighthouse_lock": {
            "locker": abyss_locker_address,
            "owner": lock_owner,
            "claim_authority": claim_authority,
            "fee_recipient": fee_recipient,
            "unlock_time": unlock_time,
            "permissionless_claim": permissionless_claim,
        },
        "note": (
            "The hook keeps the permanent V4 99% position in the locker. The separate "
            "permanent 1% Abyss lighthouse position accrues fees to the protocol-controlled "
            "distributor; its custody is coordinator-owned with no permissionless claim, not "
            "an interpretation of unlock_time=0 as an infinite time lock."
        ),
    }


def _await_publish_projection(
    context: ApiSessionContext,
    *,
    chain_id: int,
    initial: Mapping[str, Any],
    retry_after_ms: int,
    poll_limit: int,
    sleeper: Callable[[float], None],
    state_path: Path | None,
    state: dict[str, Any] | None,
) -> Mapping[str, Any]:
    """Poll the private session until publication leaves its pending states."""

    current = initial
    for attempt in range(poll_limit):
        status = _session_status(current)
        if status not in ("awaiting_indexer", "transaction_submitted"):
            if status in ("reorged", "rejected", "expired"):
                raise LaunchSubmissionError(
                    f"launch publication reached terminal state: {status}"
                )
            return current
        if attempt + 1 == poll_limit:
            break
        sleeper(retry_after_ms / 1000)
        polled = _api_call(
            lambda: context.client.get_launch_upload_session(
                chain_id, context.session_id, context.capability
            ),
            "could not poll the private launch publication",
        )
        if not isinstance(polled, Mapping):
            raise LaunchSubmissionError("launch API returned an invalid private session response")
        current = polled
        if state is not None:
            api_state = _api_state(state)
            api_state["status"] = status
            _persist_state(state_path, state, "api_publish_pending")
    raise LaunchSubmissionError(
        "launch publication did not settle within the configured bound; recovery state was saved"
    )


def _publish_and_await(
    context: ApiSessionContext,
    *,
    args: argparse.Namespace,
    chain_id: int,
    transaction_hash: str,
    state_path: Path | None,
    state: dict[str, Any] | None,
    sleeper: Callable[[float], None],
) -> Mapping[str, Any]:
    """Publish once, then await the indexed projection like the Node runner."""

    retry_after_ms = 1_000
    try:
        published = context.client.publish_launch_upload_session(
            chain_id,
            context.session_id,
            context.capability,
            LaunchSessionPublishRequest(chain_id=chain_id, transaction_hash=transaction_hash),
        )
    except LaunchPublishPending as pending:
        published = pending.session
        retry_after_ms = pending.retry_after_ms
    except (LaunchApiError, LaunchUploadError):
        raise
    except Exception:
        raise LaunchSubmissionError(
            "could not publish the confirmed transaction to the launch API; recovery state was saved"
        ) from None
    if not isinstance(published, Mapping):
        raise LaunchSubmissionError("launch API returned an invalid publish response")
    final = _await_publish_projection(
        context,
        chain_id=chain_id,
        initial=published,
        retry_after_ms=retry_after_ms,
        poll_limit=args.api_ready_polls,
        sleeper=sleeper,
        state_path=state_path,
        state=state,
    )
    if state is not None:
        api_state = _api_state(state)
        api_state["status"] = _session_status(final)
        _persist_state(state_path, state, "api_published")
    return final


def _publish_api_session(
    context: ApiSessionContext,
    *,
    args: argparse.Namespace,
    chain_id: int,
    transaction_hash: str,
    state_path: Path | None,
    state: dict[str, Any] | None,
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    final = _publish_and_await(
        context,
        args=args,
        chain_id=chain_id,
        transaction_hash=transaction_hash,
        state_path=state_path,
        state=state,
        sleeper=sleeper,
    )
    return {"status": _session_status(final)}


def _quote_request_with_minimum(
    w3: Any,
    *,
    request: Any,
    route: LiveRoute,
    args: argparse.Namespace,
    native_buy_amount: int,
    account_address: str,
    quote_transaction: Mapping[str, Any],
) -> tuple[Any, dict[str, int] | None]:
    """Use the launcher return value as a quote, never as receipt evidence."""

    try:
        paired_amount = int(request.initial_buy.paired_token_amount_in)
        effective_amount = paired_amount + native_buy_amount
    except (TypeError, ValueError):
        raise LaunchSubmissionError("initial buy amount had an invalid value") from None
    if effective_amount == 0:
        return request, None
    if (
        request.initial_buy.launched_token_amount_out_minimum != 0
        or request.initial_buy.sqrt_price_limit_x96 == 0
    ):
        raise LaunchSubmissionError(
            "matrix quote request must use zero output minimum and a directional price guard"
        )
    quote_call = dict(quote_transaction)
    quote_call["from"] = account_address
    quoted_payload = _rpc(
        lambda: w3.eth.call(quote_call),
        "initial-buy quote simulation failed; no launch transaction was broadcast",
    )
    try:
        quoted_receipt = decode_unified_launch_receipt(quoted_payload)
        quoted_amount = int(quoted_receipt.initial_buy_launched_token_amount)
    except (TypeError, ValueError):
        raise LaunchSubmissionError("initial-buy quote returned an invalid UnifiedLaunchReceipt") from None
    if quoted_amount < 0:
        raise LaunchSubmissionError("initial-buy quote returned an invalid output amount")
    minimum = (quoted_amount * (10_000 - args.slippage_bps)) // 10_000
    return (
        replace(
            request,
            initial_buy=replace(
                request.initial_buy,
                launched_token_amount_out_minimum=minimum,
            ),
        ),
        {
            "quoted_launched_token_amount": quoted_amount,
            "minimum_launched_token_amount": minimum,
        },
    )


def _submit_one(
    *,
    w3: Any,
    account: Any,
    scenario: LaunchScenario,
    request: Any,
    args: argparse.Namespace,
    launcher: str,
    paired_token: str,
    paired_token_usd_price_x18: int,
    native_buy_amount: int,
    oracle_config_id: bytes,
    state_path: Path | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    account_address = _safe_address(getattr(account, "address", None), "signer address")
    if not _same_address(request.creator, account_address):
        raise LaunchSubmissionError("launch request creator does not match the explicit signer")
    actual_chain_id = _rpc(
        lambda: w3.eth.chain_id,
        "could not read the RPC chain ID",
    )
    try:
        actual_chain_id = int(actual_chain_id)
    except (TypeError, ValueError):
        raise LaunchSubmissionError("RPC chain ID had an invalid value") from None
    if actual_chain_id != args.chain_id:
        raise LaunchSubmissionError("RPC chain ID does not match --chain-id")

    route = _read_live_route(
        w3,
        request=request,
        scenario=scenario,
        chain_id=args.chain_id,
        launcher=launcher,
        fee=args.fee_pips,
        oracle_config_id=oracle_config_id,
    )
    if scenario.pool_kind is LaunchPoolKind.UNISWAP_V4_V3:
        if native_buy_amount != 0:
            raise LaunchSubmissionError("Uniswap V4 launches require exactly zero native initial buy")

    erc20_buy_units = int(request.initial_buy.paired_token_amount_in)

    # Gross (pre-slippage) post-buy sqrt-price guard from the exercised Node
    # runner: recompute the pool price after absorbing the initial buy and
    # re-derive the directional limit from that post-buy price.
    buy_units = erc20_buy_units + native_buy_amount
    if buy_units > 0:
        template = get_launch_template(scenario.template_id)
        recipe = derive_atomic_launch_pool_recipe(
            AtomicLaunchPoolRecipeInput(
                paired_token_decimals=args.paired_token_decimals,
                paired_token_usd_price_x18=paired_token_usd_price_x18,
                target_market_cap_usd_x18=args.target_market_cap_usd_x18,
                launched_token_is_quote=template.launched_token_is_quote,
                fee=args.fee_pips,
            )
        )
        q96 = 1 << 96
        if template.launched_token_is_quote:
            post_buy_sqrt_price_x96 = recipe.launch_sqrt_price_x96 + (
                buy_units * q96
            ) // recipe.liquidity
        else:
            denominator = recipe.liquidity * q96 + buy_units * recipe.launch_sqrt_price_x96
            post_buy_sqrt_price_x96 = (
                recipe.liquidity * q96 * recipe.launch_sqrt_price_x96 + denominator - 1
            ) // denominator
        request = replace(
            request,
            initial_buy=replace(
                request.initial_buy,
                sqrt_price_limit_x96=derive_atomic_launch_buy_sqrt_price_limit_x96(
                    AtomicLaunchBuySqrtPriceLimitInput(
                        launch_sqrt_price_x96=post_buy_sqrt_price_x96,
                        launched_token_is_quote=template.launched_token_is_quote,
                        slippage_bps=args.slippage_bps,
                    )
                ),
            ),
        )

    # This first envelope is deliberately the matrix quote request: output
    # minimum zero, but with its matrix-built directional sqrt-price bound.
    quote_transaction = build_unified_launch_transaction(
        request,
        chain_id=args.chain_id,
        launch_factory=route.launcher,
        launch_fee=route.launch_fee,
        native_buy_amount=native_buy_amount,
        wrapped_native_token=route.wrapped_native,
    )
    try:
        quote_transaction["to"] = _safe_address(
            quote_transaction["to"], "SDK launch transaction target"
        )
        quote_value = int(quote_transaction["value"])
    except (KeyError, TypeError, ValueError):
        raise LaunchSubmissionError("SDK transaction builder returned an invalid launch transaction") from None
    if not _same_address(quote_transaction["to"], route.launcher):
        raise LaunchSubmissionError("SDK transaction builder changed the selected launcher target")
    if (
        scenario.pool_kind is LaunchPoolKind.UNISWAP_V4_V3
        and quote_value != 0
    ):
        raise LaunchSubmissionError("Uniswap V4 transaction value was not zero")

    if erc20_buy_units > 0 and _same_address(paired_token, _chain_weth(args.chain_id)):
        _wrap_weth_shortfall(
            w3,
            account,
            account_address,
            paired_token=paired_token,
            units=erc20_buy_units,
            chain_id=args.chain_id,
            timeout=args.receipt_timeout,
            state_path=state_path,
            state=state,
        )
    _ensure_exact_allowance(
        w3,
        account,
        account_address=account_address,
        paired_token=paired_token,
        launcher=route.launcher,
        amount=erc20_buy_units,
        chain_id=args.chain_id,
        timeout=args.receipt_timeout,
        state_path=state_path,
        state=state,
    )
    _require_native_balance(w3, account_address, quote_value)
    request, quote = _quote_request_with_minimum(
        w3,
        request=request,
        route=route,
        args=args,
        native_buy_amount=native_buy_amount,
        account_address=account_address,
        quote_transaction=quote_transaction,
    )
    if quote is not None and state is not None:
        on_chain = state.setdefault("on_chain", {})
        if not isinstance(on_chain, dict):
            raise LaunchSubmissionError("private recovery state had an invalid shape")
        on_chain["initial_buy_quote"] = quote
        _persist_state(state_path, state, "initial_buy_quoted")

    # Rebuild with the simulation-derived minimum and simulate this exact final
    # calldata before its one permitted broadcast.
    transaction = build_unified_launch_transaction(
        request,
        chain_id=args.chain_id,
        launch_factory=route.launcher,
        launch_fee=route.launch_fee,
        native_buy_amount=native_buy_amount,
        wrapped_native_token=route.wrapped_native,
    )
    try:
        transaction["to"] = _safe_address(transaction["to"], "SDK launch transaction target")
        transaction_value = int(transaction["value"])
    except (KeyError, TypeError, ValueError):
        raise LaunchSubmissionError("SDK transaction builder returned an invalid launch transaction") from None
    if not _same_address(transaction["to"], route.launcher):
        raise LaunchSubmissionError("SDK transaction builder changed the selected launcher target")
    if (
        scenario.pool_kind is LaunchPoolKind.UNISWAP_V4_V3
        and transaction_value != 0
    ):
        raise LaunchSubmissionError("Uniswap V4 transaction value was not zero")

    simulation_transaction = dict(transaction)
    simulation_transaction["from"] = account_address
    _rpc(
        lambda: w3.eth.call(simulation_transaction),
        "final launch simulation failed; no launch transaction was broadcast",
    )
    prepared_transaction = _transaction_execution_fields(
        w3,
        transaction,
        account_address=account_address,
        chain_id=args.chain_id,
    )
    try:
        required_native_balance = transaction_value + int(prepared_transaction["gas"]) * int(
            prepared_transaction.get("gasPrice", 0)
        )
    except (KeyError, TypeError, ValueError):
        raise LaunchSubmissionError("launch gas fields had invalid values") from None
    _require_native_balance(w3, account_address, required_native_balance)
    if state is not None:
        _persist_state(state_path, state, "launch_simulated")
    transaction_hash, receipt = _broadcast_once(
        w3,
        account,
        prepared_transaction,
        timeout=args.receipt_timeout,
        state_path=state_path,
        state=state,
        state_key="launch_transactions",
    )

    launcher_contract = _contract(w3, route.launcher, UNIFIED_LAUNCHER_ABI, "UnifiedLauncher")
    receipt_details = _decode_launch_events(
        launcher_contract,
        receipt,
        request=request,
        account_address=account_address,
    )
    if state is not None:
        on_chain = state.setdefault("on_chain", {})
        if not isinstance(on_chain, dict):
            raise LaunchSubmissionError("private recovery state had an invalid shape")
        on_chain.update(
            {
                "transaction_hash": transaction_hash,
                "token": receipt_details["token"],
                "market": receipt_details["market"],
                "token_id": receipt_details["token_id"],
            }
        )
        _persist_state(state_path, state, "launch_confirmed")

    launch: dict[str, Any] = {
        "token": receipt_details["token"],
        "market": receipt_details["market"],
        "token_id": receipt_details["token_id"],
        "liquidity_launched_token_amount": receipt_details["liquidity_launched_token_amount"],
        "liquidity_paired_token_amount": receipt_details["liquidity_paired_token_amount"],
        "initial_buy_paired_token_amount": receipt_details["initial_buy_paired_token_amount"],
        "initial_buy_launched_token_amount": receipt_details["initial_buy_launched_token_amount"],
        "rewards": receipt_details["rewards"],
        "splitter": receipt_details["splitter"],
        "fee_claimer": receipt_details["fee_claimer"],
    }
    if scenario.pool_kind is LaunchPoolKind.UNISWAP_V4_V3:
        launch["v4_hook"] = launch.pop("market")
        launch["v4_companion"] = _v4_companion_details(
            w3,
            route,
            token_id=receipt_details["token_id"],
        )

    return {
        "mode": "submitted",
        "scenario_id": scenario.scenario_id,
        "chain_id": args.chain_id,
        "pool_kind": scenario.pool_kind.value,
        "transaction_hash": transaction_hash,
        "preflight": {
            "launcher": route.launcher,
            "registry": route.registry,
            "adapter": route.adapter,
            "abyss_factory": route.abyss_factory,
            "fee_tick_spacing": route.fee_tick_spacing,
            "oracle_max_abs_tick_move": route.oracle_max_abs_tick_move,
            "oracle_cardinality": route.oracle_cardinality,
        },
        "fees": {
            "adapter_launch_fee": route.launch_fee,
            "transaction_value": transaction_value,
            "launched_token": _disposition_summary(request.launched_token_fees),
            "paired_token": _disposition_summary(request.paired_token_fees),
        },
        "launch": launch,
    }


def _chainlink_eth_usd_price_x18(w3: Any, chain_id: int) -> int:
    """Read the Chainlink ETH/USD feed and scale its answer to 1e18."""

    feed_address = _rpc(
        lambda: get_addresses(chain_id).eth_usd_feed,
        "could not resolve the recorded ETH/USD price feed",
    )
    feed = _contract(w3, feed_address, AGGREGATOR_V3_ABI, "ETH/USD price feed")
    round_data = _rpc(
        lambda: feed.functions.latestRoundData().call(),
        "could not read the ETH/USD price feed round",
    )
    answer = _rpc(
        lambda: _value_at(round_data, "answer", 1),
        "the ETH/USD price feed returned an unexpected round shape",
    )
    decimals = _rpc(
        lambda: feed.functions.decimals().call(),
        "could not read the ETH/USD price feed decimals",
    )
    try:
        answer = int(answer)
        decimals = int(decimals)
    except (TypeError, ValueError):
        raise LaunchSubmissionError("the ETH/USD price feed returned invalid values") from None
    if answer <= 0:
        raise LaunchSubmissionError("the ETH/USD price feed returned a nonpositive answer")
    if decimals <= 18:
        return answer * 10 ** (18 - decimals)
    return answer // 10 ** (decimals - 18)


def _paired_usd_price_x18(
    args: argparse.Namespace,
    *,
    w3: Any,
    paired_token: str,
) -> int:
    if args.paired_token_usd_price_x18 is not None:
        return args.paired_token_usd_price_x18
    if _same_address(paired_token, _chain_weth(args.chain_id)):
        return _chainlink_eth_usd_price_x18(w3, args.chain_id)
    raise LaunchSubmissionError(
        "--paired-token-usd-price-x18 is required for a non-WETH paired token"
    )


def _resume_publish(
    args: argparse.Namespace,
    *,
    api_client_factory: Callable[[LaunchApiConfig], Any],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    """Finish an interrupted publication without a key, Web3, or broadcast."""

    config = (
        LaunchApiConfig(base_url=args.api_base_url, timeout=args.api_timeout)
        if args.api_base_url
        else LaunchApiConfig(timeout=args.api_timeout)
    )
    client = _api_call(
        lambda: api_client_factory(config),
        "could not initialize the launch API client",
    )
    context = ApiSessionContext(
        client=client,
        session_id=args.session,
        capability=args.capability,
    )
    final = _publish_and_await(
        context,
        args=args,
        chain_id=args.chain_id,
        transaction_hash=args.transaction,
        state_path=None,
        state=None,
        sleeper=sleeper,
    )
    token = final.get("token")
    if token is not None and not isinstance(token, str):
        raise LaunchSubmissionError("launch API returned an invalid published token")
    return {
        "mode": "resume_publish",
        "transaction_hash": args.transaction,
        "session_id": context.session_id,
        "token": token,
        "status": _session_status(final),
    }


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    wallet_web3_factory: Callable[..., tuple[Any, Any]] | None = None,
    api_client_factory: Callable[[LaunchApiConfig], Any] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> list[dict[str, Any]]:
    """Run the CLI workflow and return only public-safe result summaries.

    Dependency injection is intentionally narrow: tests can provide a Web3,
    signer, API client, and clock without opening RPC/API connections or using
    a real credential.  Normal callers use the SDK factories below.
    """

    args = build_parser().parse_args(argv)
    _validate_cli_args(args)
    sleep = sleeper or time.sleep
    if args.resume_publish:
        return [_resume_publish(args, api_client_factory=api_client_factory or LaunchApiClient, sleeper=sleep)]

    scenarios = _selected_scenarios(args)
    paired_token = _paired_token_for_args(args)
    launcher = _launcher_for_args(args)
    oracle_config_id = _bytes32(args.oracle_config_id)
    args.deadline = _deadline_for_args(args)
    initial_buy_units = _initial_buy_units(args)
    if args.execute and not _fee_pips_tier(args.fee_pips):
        raise LaunchSubmissionError("--fee-pips must be one of the recorded Abyss fee tiers")

    if not args.execute:
        creator = _safe_address(args.creator or _OFFLINE_CREATOR, "offline creator")
        try:
            wrapped_native = get_addresses(args.chain_id).weth
        except (KeyError, ValueError):
            wrapped_native = paired_token
        wrapped_native = _safe_address(wrapped_native, "configured wrapped-native token")
        return [
            _offline_summary(
                scenario,
                _build_request(
                    scenario,
                    args,
                    creator=creator,
                    paired_token=paired_token,
                    oracle_config_id=oracle_config_id,
                    initial_buy_units=initial_buy_units,
                    native_buy_amount=args.native_buy_amount,
                    paired_token_usd_price_x18=(
                        args.paired_token_usd_price_x18
                        if args.paired_token_usd_price_x18 is not None
                        else _DEFAULT_PAIRED_USD_PRICE_X18
                    ),
                ),
                args,
                launcher=launcher,
                wrapped_native=wrapped_native,
                native_buy_amount=args.native_buy_amount,
            )
            for scenario in scenarios
        ]

    private_key = _read_private_key(args)
    factory = wallet_web3_factory or create_protocol_wallet_web3
    w3, account = _rpc(
        lambda: factory(private_key=private_key, chain_id=args.chain_id, rpc_url=args.rpc_url),
        "could not initialize the explicit signing Web3 client",
    )
    account_address = _safe_address(getattr(account, "address", None), "signer address")
    if args.creator and not _same_address(args.creator, account_address):
        raise LaunchSubmissionError("--creator must match the explicit live signer")
    api_factory = api_client_factory or LaunchApiClient
    paired_token_usd_price_x18 = _paired_usd_price_x18(args, w3=w3, paired_token=paired_token)

    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        erc20_buy_units, native_buy_amount = _abyss_weth_native_buy(
            execute=args.execute,
            scenario=scenario,
            paired_token=paired_token,
            chain_id=args.chain_id,
            initial_buy_units=initial_buy_units,
            native_buy_amount=args.native_buy_amount,
        )
        request = _build_request(
            scenario,
            args,
            creator=account_address,
            paired_token=paired_token,
            oracle_config_id=oracle_config_id,
            initial_buy_units=erc20_buy_units,
            native_buy_amount=native_buy_amount,
            paired_token_usd_price_x18=paired_token_usd_price_x18,
        )
        state_path = _state_path_for(args, scenario)
        state: dict[str, Any] = {
            "version": 1,
            "scenario_id": scenario.scenario_id,
            "chain_id": args.chain_id,
            "creator": account_address,
            "launcher": launcher,
            "token_name": request.token.name,
            "token_symbol": request.token.symbol,
        }
        if state_path is not None:
            _persist_state(state_path, state, "initialized")
        api_context = _prepare_api_session(
            args=args,
            request=request,
            account=account,
            account_address=account_address,
            launcher=launcher,
            state_path=state_path,
            state=state,
            api_client_factory=api_factory,
            sleeper=sleep,
        )

        submitted = _submit_one(
            w3=w3,
            account=account,
            scenario=scenario,
            request=request,
            args=args,
            launcher=launcher,
            paired_token=paired_token,
            paired_token_usd_price_x18=paired_token_usd_price_x18,
            native_buy_amount=native_buy_amount,
            oracle_config_id=oracle_config_id,
            state_path=state_path,
            state=state,
        )
        submitted["api"] = _publish_api_session(
            api_context,
            args=args,
            chain_id=args.chain_id,
            transaction_hash=submitted["transaction_hash"],
            state_path=state_path,
            state=state,
            sleeper=sleep,
        )
        results.append(submitted)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    """Print public-safe results and never echo secret-bearing exception data."""

    try:
        results = run_cli(argv)
    except LaunchApiError as error:
        print(
            f"launch_submit: launch API operation failed safely (status {error.status}, code {error.code})",
            file=sys.stderr,
        )
        return 1
    except LaunchUploadError as error:
        status = "unknown" if error.status is None else str(error.status)
        print(f"launch_submit: image upload failed safely (status {status})", file=sys.stderr)
        return 1
    except LaunchSubmissionError as error:
        print(f"launch_submit: {error}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "launch_submit: operation failed without exposing remote or credential-bearing details",
            file=sys.stderr,
        )
        return 1

    for result in results:
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
