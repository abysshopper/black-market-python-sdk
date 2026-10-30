"""Release-owned capacity budgets and pinned input checks, never execution proof."""
from dataclasses import dataclass, replace
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .auto_compound import is_auto_compound_profile
from .lifecycle import (
    LaunchPlanV1, LifecycleFundingKind, LifecycleGasProfile, LifecyclePlanningAssessment,
    LifecycleLaunchGasBounds, LifecycleOpeningBuyQuote, LifecyclePlanningError,
    LifecycleRewardMode, LifecycleTokenKind, LifecycleTransaction, LifecycleTransactionEstimate,
    LifecycleFundingPrerequisite, _execution_account, _forwarding_reserve,
    _nitro_poster_gas_bound, _read_nitro_prices, _read_lifecycle_gas_price,
    _prepare_gas_estimate, _build_lifecycle_command, _LIFECYCLE_CAPACITY_PADDING_BPS,
    compute_lifecycle_opening_buys, decode_lifecycle_abyss_market_config,
    decode_lifecycle_pool_bound_v4_market_config,
)
from .lifecycle_buy_math import has_profiled_opening_math
from .lifecycle_rpc import LaunchBlock, LaunchExecutionLimits, pinned_rpc, quantity
from .hmm import is_hmm_profile
from .ufg import UFG_LAUNCH_PROXY_ADDRESS, UfgLaunchConfig, is_ufg_profile
from .raeto import is_raeto_profile

_PROFILE_VERSION = "robinhood-4663-capacity-v4"


def _approval(transaction: LifecycleTransaction) -> bool:
    return transaction.kind in {"approve", "approve-reset"}


@dataclass(frozen=True)
class _MarketGas:
    abyss: bool
    hmm: bool
    ufg_hook: bool
    raeto: bool
    auto_compound: bool
    positions: int


def _market_gas(plan: LaunchPlanV1) -> tuple[_MarketGas, ...]:
    result = []
    for market in plan.markets:
        abyss = market.config_version == 1
        config = decode_lifecycle_abyss_market_config(market.config) if abyss else decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=6)
        result.append(_MarketGas(abyss, is_hmm_profile(market.profile_id), is_ufg_profile(market.profile_id),
            is_raeto_profile(market.profile_id), is_auto_compound_profile(market.profile_id), len(config.positions)))
    return tuple(result)


def _capacity_basis(plan: LaunchPlanV1, transaction: LifecycleTransaction,
        markets: Sequence[_MarketGas], quotes: Sequence[LifecycleOpeningBuyQuote], ufg_route: bool) -> int:
    token, atomic = plan.token, transaction.kind == "atomic"
    metadata_words = sum((len(value.encode()) + 31) // 32
        for value in (token.name, token.symbol, token.metadata_uri))
    creation = ((2_500_000 if token.kind == LifecycleTokenKind.ERC404 else 0)
        + (2_500_000 if token.reward_mode == LifecycleRewardMode.STAKING else 500_000 if token.reward_mode == LifecycleRewardMode.DIVIDENDS else 0)
        + 25_000 * metadata_words)
    # template-gas-matrix.json: Raeto/auto-compound atomic1 <=19.60M,
    # atomic8 <=23.99M; atomic32 exceeds32M. The18M calibration base plus
    # position/buy work and25% padding bounds compute and forwarding envelopes.
    atomic_base = 18_000_000 if markets[0].raeto or markets[0].auto_compound else 22_000_000
    gas = atomic_base + creation + (6_500_000 if ufg_route else 0) if atomic else 1_500_000 + (250_000 if ufg_route else 0)
    for market in markets:
        if atomic:
            gas += (7_000_000 if market.hmm else 1_000_000 if market.ufg_hook else 500_000 if market.abyss else 0)
            gas += (1_500_000 if market.abyss else 600_000) * (market.positions - 1)
        else:
            gas += (500_000 + 1_250_000 * market.positions if market.abyss else
                (500_000 if market.raeto else 750_000 if market.auto_compound else 250_000) + 500_000 * market.positions)
            gas += 1_000_000 if market.hmm else 250_000 if market.ufg_hook else 0
    gas += 500_000 * len(quotes)
    # These collectors revalidate the prepared position set for each opening buy.
    for quote in quotes:
        market = markets[quote.market_index]
        if market.raeto or market.auto_compound:
            gas += 25_000 * market.positions
    if token.kind == LifecycleTokenKind.ERC404:
        gas += sum(100_000 * ((quote.token_out + token.nft_unit - 1) // token.nft_unit) for quote in quotes)
    return gas + 24 * transaction.calldata_bytes + 100_000 * len(plan.fee_assets)


@dataclass(frozen=True)
class _ProfiledLaunchCapacity:
    bounds: LifecycleLaunchGasBounds
    initial_buys: tuple[LifecycleOpeningBuyQuote, ...]
    headroom_bps: int
    atomic: LifecycleTransaction | None
    activation: LifecycleTransaction


def derive_lifecycle_launch_capacity(plan: LaunchPlanV1, *, predicted_token: str,
        ufg: UfgLaunchConfig | None = None, headroom_bps: int = 1500,
        opening_buys: Sequence[LifecycleOpeningBuyQuote] | None = None) -> _ProfiledLaunchCapacity | None:
    if (not has_profiled_opening_math(plan)
            or (plan.creator.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower()) != (ufg is not None)):
        return None
    if isinstance(headroom_bps, bool) or not isinstance(headroom_bps, int) or not 0 <= headroom_bps <= 10000:
        raise LifecyclePlanningError("INVALID_LIMITS", "Headroom must be between zero and 10000 basis points")
    quotes = tuple(opening_buys) if opening_buys is not None else compute_lifecycle_opening_buys(plan, predicted_token=predicted_token)
    atomic = None
    if len(plan.markets) == 1 and not any(funding.kind == LifecycleFundingKind.SWAP for funding in plan.funding):
        atomic = _build_lifecycle_command(plan, "atomic", ufg=ufg)
    activation = _build_lifecycle_command(plan, "activate", ufg=ufg)
    markets, reserve = _market_gas(plan), _forwarding_reserve(plan, "activate")
    def bound(transaction: LifecycleTransaction) -> int:
        basis = _capacity_basis(plan, transaction, markets, quotes, ufg is not None)
        return max((basis * (10000 + max(_LIFECYCLE_CAPACITY_PADDING_BPS, headroom_bps)) + 9999) // 10000, basis + reserve)
    return _ProfiledLaunchCapacity(LifecycleLaunchGasBounds(None if atomic is None else bound(atomic), bound(activation)),
        quotes, headroom_bps, atomic, activation)


def compute_launch_gas_bounds(plan: LaunchPlanV1, *, predicted_token: str,
        ufg: UfgLaunchConfig | None = None, headroom_bps: int = 1500) -> LifecycleLaunchGasBounds | None:
    capacity = derive_lifecycle_launch_capacity(plan, predicted_token=predicted_token,
        ufg=ufg, headroom_bps=headroom_bps)
    return None if capacity is None else capacity.bounds


def assess_lifecycle_transactions(client, plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction], *,
        block: LaunchBlock, predicted: str, limits: LaunchExecutionLimits,
        prerequisites: Sequence[LifecycleFundingPrerequisite] = (),
        reviewed_envelope: tuple[int, int] | None = None,
        opening_buys: Sequence[LifecycleOpeningBuyQuote] | None = None,
        ufg: UfgLaunchConfig | None = None, capacity: _ProfiledLaunchCapacity | None = None,
        data_fee_estimator: Callable[[Any, Mapping[str, Any], LaunchBlock], int] | None = None) -> LifecyclePlanningAssessment:
    """Assess pinned inputs and deterministic capacity; never execute calldata."""
    recommendation = _read_lifecycle_gas_price(client, block, plan.chain_id)
    balance = quantity(pinned_rpc(client, "eth_getBalance", [_execution_account(plan), block.tag], block))
    prices = _read_nitro_prices(client, block) if limits.protocol == "nitro" else None
    base_fee, ceiling = block.base_fee_per_gas or 0, limits.compute_cap(block)
    required_price = max(recommendation, base_fee) if prices is not None else base_fee
    price = reviewed_envelope[1] if reviewed_envelope is not None else max(recommendation, base_fee * 2)
    if reviewed_envelope is not None and reviewed_envelope[0] != ceiling:
        raise LifecyclePlanningError("STALE_REVIEWED_GAS", "Reviewed gas differs from the native transaction ceiling. Refresh the review before continuing.")
    cancel_only = all(transaction.kind == "cancel" for transaction in transactions)
    current = None if cancel_only else capacity if capacity is not None and capacity.headroom_bps == limits.headroom_bps else derive_lifecycle_launch_capacity(
        plan, predicted_token=predicted, ufg=ufg, headroom_bps=limits.headroom_bps, opening_buys=opening_buys)
    profile = None if current is None else LifecycleGasProfile(_PROFILE_VERSION, current.bounds, current.initial_buys)
    assessment = LifecyclePlanningAssessment(True, block.number, block.block_hash, _execution_account(plan), plan.chain_id, limits, gas_profile=profile)
    immediate = transactions[0] if transactions else None
    launch = next((transaction for transaction in transactions if not _approval(transaction)), None)
    def poster(transaction):
        return 0 if prices is None else _nitro_poster_gas_bound(transaction, prices)[0]
    def refuse(code, reason, category, constraint=None, failed=None):
        return replace(assessment, ready=False, code=code, reason=reason,
            failure_category=category, capacity_constraint=constraint, failed_transaction_id=failed)
    if not cancel_only and current is None:
        return refuse("UNKNOWN_GAS_PROFILE", "Gas budgets are unavailable for this launch profile. Use a supported profile or update the SDK.", "capacity")
    if not cancel_only and current.bounds.activation_compute_gas_upper_bound + poster(current.activation) > ceiling:
        return refuse("ACTIVATION_GAS_LIMIT", "Too many positions or pools for one activation. Reduce them to fit the native transaction ceiling.", "capacity", "activation", "activate")
    for transaction in transactions:
        if transaction.kind == "atomic" and (current is None or current.bounds.atomic_compute_gas_upper_bound is None or current.bounds.atomic_compute_gas_upper_bound + poster(transaction) > ceiling):
            return refuse("STAGED_EXECUTION_REQUIRED", "This launch requires staged execution. Refresh the review before continuing.", "capacity", "gas-envelope", transaction.step_id)
        if transaction.kind == "prepare" and ((sum(_prepare_gas_estimate(plan, index)
                for index in range(transaction.first_market, transaction.first_market + transaction.market_count)) + 24 * transaction.calldata_bytes)
                * (10000 + max(_LIFECYCLE_CAPACITY_PADDING_BPS, limits.headroom_bps)) + 9999) // 10000 + poster(transaction) > ceiling:
            return refuse("PREPARATION_GAS_LIMIT", "This preparation batch exceeds the gas budget. Refresh the review to split it.", "capacity", "preparation", transaction.step_id)
        if limits.calldata_cap() is not None and transaction.calldata_bytes > limits.calldata_cap():
            return refuse("CALLDATA_LIMIT", "The launch exceeds the transaction data limit. Reduce its positions or pools.", "capacity", "calldata", transaction.step_id)
    if launch is not None and ceiling > limits.gas_cap(block):
        return refuse("GAS_POLICY_LIMIT", "The configured gas limit is below the chain's native transaction ceiling.", "capacity", "gas-envelope", launch.step_id)
    if reviewed_envelope is not None and price < required_price:
        return refuse("REVIEWED_FEE_TOO_LOW", "The reviewed gas price is below the current required fee. Refresh the review before continuing.", "semantic", failed=None if immediate is None else immediate.step_id)
    if current is not None and any(quote.token_out < plan.buys[quote.buy_index].min_token_out for quote in current.initial_buys):
        return refuse("BUY_MINIMUM_NOT_MET", "An opening buy is below its minimum output. Refresh the review before continuing.", "semantic")
    if block.timestamp > plan.deadline and not cancel_only:
        return refuse("PLAN_EXPIRED", "The launch deadline has passed. Cancel an unfinished launch to recover its funds.", "semantic")
    inputs = {}
    for prerequisite in prerequisites:
        if prerequisite.allowance is None:
            continue
        key = prerequisite.funding.input_asset.lower()
        required, available = inputs.get(key, (0, prerequisite.input_balance))
        inputs[key] = required + prerequisite.required_input, available
    if any(required > available for required, available in inputs.values()):
        return refuse("INSUFFICIENT_FUNDING_BALANCE", "There are not enough tokens to fund this launch. Add funds or reduce the purchase.", "affordability")
    if price >= 1 << 256 or launch is not None and launch.value + ceiling * price >= 1 << 256:
        raise LifecyclePlanningError("INVALID_GAS_ENVELOPE", "Transaction value and native-ceiling fee must fit uint256")
    native_reason = "There is not enough native currency for the launch amount and gas budget. Add funds or reduce the purchase."
    if launch is not None and launch.value + ceiling * price > balance:
        return refuse("INSUFFICIENT_NATIVE_BALANCE", native_reason, "affordability", failed=launch.step_id)
    if immediate is not None and not _approval(immediate):
        envelope = replace(immediate, gas_limit=ceiling, gas_price=price)
        data_fee = 0 if prices is not None else None if data_fee_estimator is None else data_fee_estimator(getattr(client, "source", client), envelope.as_transaction(), block)
        if data_fee is not None and (isinstance(data_fee, bool) or not isinstance(data_fee, int) or not 0 <= data_fee < 1 << 256):
            raise LifecyclePlanningError("INVALID_DATA_FEE", "Data-fee estimate must be a nonnegative uint256 amount")
        execution_fee = ceiling * price
        required = immediate.value + execution_fee + (data_fee or 0)
        if required >= 1 << 256:
            raise LifecyclePlanningError("INVALID_GAS_ENVELOPE", "Transaction value and gas/data fee budget must fit uint256")
        if required > balance:
            return refuse("INSUFFICIENT_NATIVE_BALANCE", native_reason, "affordability", failed=immediate.step_id)
        poster_gas = None if prices is None else poster(immediate)
        estimate = LifecycleTransactionEstimate(ceiling, price, execution_fee, prices is not None,
            "native-ceiling-only" if data_fee is None else "native-ceiling-and-data", data_fee,
            None if data_fee is None else execution_fee + data_fee, poster_gas,
            None if poster_gas is None else poster_gas * price)
        assessment = replace(assessment, estimate=estimate)
    return assessment
