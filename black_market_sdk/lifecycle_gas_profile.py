"""Release-owned capacity bounds, never signing-gas sizing or execution proof."""
from dataclasses import dataclass, replace
from collections.abc import Sequence

from .launch import get_tick_at_sqrt_ratio
from .lifecycle import (
    LaunchPlanV1, LaunchSimulation, LifecycleFundingKind, LifecycleGasProfileEvidence,
    LifecycleLaunchGasBounds, LifecycleOpeningBuyQuote, LifecyclePlanningError,
    LifecycleRewardMode, LifecycleTokenKind, LifecycleTransaction,
    _PROFILED_QUOTE_IDENTITIES, _execution_account, _forwarding_reserve,
    _launch_simulation, _nitro_poster_gas_bound, _read_nitro_prices,
    build_launch_transactions, build_lifecycle_calldata, compute_lifecycle_opening_buys,
    decode_lifecycle_abyss_market_config, decode_lifecycle_pool_bound_v4_market_config,
)
from .lifecycle_buy_math import has_profiled_opening_math
from .lifecycle_presets import get_known_lifecycle_profile
from .lifecycle_rpc import (
    LaunchBlock, LaunchExecutionLimits, LaunchRpcSimulation, pinned_rpc, quantity,
    _read_lifecycle_account_code,
)
from .ufg import UFG_LAUNCH_PROXY_ADDRESS, UfgLaunchConfig, build_ufg_launch_transactions

_PROFILE_VERSION = "robinhood-4663-arbos61-v1"


def _approval(transaction: LifecycleTransaction) -> bool:
    return transaction.kind in {"approve", "approve-reset"}


def _covered(plan: LaunchPlanV1) -> bool:
    return (plan.chain_id == 4663 and has_profiled_opening_math(plan)
        and all(market.quote_asset.lower() in _PROFILED_QUOTE_IDENTITIES for market in plan.markets)
        and all(funding.kind != LifecycleFundingKind.ERC20
            or funding.input_asset.lower() in _PROFILED_QUOTE_IDENTITIES for funding in plan.funding))


@dataclass(frozen=True)
class _MarketGas:
    abyss: bool
    hmm: bool
    ufg_hook: bool
    positions: int
    spacing: int
    opening_tick: int
    ticks: frozenset[int]


def _market_gas(plan: LaunchPlanV1) -> tuple[_MarketGas, ...]:
    result = []
    for market in plan.markets:
        preset = get_known_lifecycle_profile(chain_id=plan.chain_id,
            orchestrator=plan.orchestrator, profile_id=market.profile_id)
        abyss = market.config_version == 1
        config = decode_lifecycle_abyss_market_config(market.config) if abyss else decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=6)
        price = config.opening_sqrt_price_x96 if abyss else config.sqrt_price_x96
        spacing = (1 if config.fee == 100 else 10 if config.fee == 500 else 60 if config.fee == 3000 else 200) if abyss else config.tick_spacing
        ticks = frozenset(tick for position in config.positions for tick in (position.tick_lower, position.tick_upper))
        result.append(_MarketGas(abyss, "hmm" in preset.key.value, "ufg" in preset.key.value,
            len(config.positions), spacing, get_tick_at_sqrt_ratio(price), ticks))
    return tuple(result)


def _capacity_basis(plan: LaunchPlanV1, transaction: LifecycleTransaction,
        markets: Sequence[_MarketGas], quotes: Sequence[LifecycleOpeningBuyQuote], ufg_route: bool) -> int:
    token, atomic = plan.token, transaction.kind == "atomic"
    metadata_words = sum((len(value.encode()) + 31) // 32
        for value in (token.name, token.symbol, token.metadata_uri))
    creation = ((2_500_000 if token.kind == LifecycleTokenKind.ERC404 else 0)
        + (2_500_000 if token.reward_mode == LifecycleRewardMode.STAKING else 500_000 if token.reward_mode == LifecycleRewardMode.DIVIDENDS else 0)
        + 22_000 * metadata_words)
    gas = 19_500_000 + creation + (6_500_000 if ufg_route else 0) if atomic else 1_000_000
    for index, market in enumerate(markets):
        if not atomic:
            gas += 2_500_000 + 1_600_000 * market.positions if market.abyss else 2_000_000 + 600_000 * market.positions
        else:
            first = 7_000_000 if market.hmm else 1_000_000 if market.ufg_hook else 500_000 if market.abyss else 0
            later = 15_000_000 if market.hmm else 9_000_000 if market.ufg_hook else 8_000_000
            gas += (first if index == 0 else later) + (1_500_000 if market.abyss else 600_000) * (market.positions - 1)
        if not atomic and market.hmm:
            gas += 6_000_000
    ticks = [market.opening_tick for market in markets]
    for quote in quotes:
        market, before = markets[quote.market_index], ticks[quote.market_index]
        after = get_tick_at_sqrt_ratio(quote.sqrt_price_after_x96)
        low, high = min(before, after), max(before, after)
        words = abs(after // (market.spacing * 256) - before // (market.spacing * 256)) + 2
        crossings = sum(low <= tick <= high for tick in market.ticks)
        gas += 300_000 + 8_000 * words + 80_000 * crossings
        if token.kind == LifecycleTokenKind.ERC404:
            gas += 70_000 * ((quote.token_out + token.nft_unit - 1) // token.nft_unit)
        ticks[quote.market_index] = after
    return gas + 24 * transaction.calldata_bytes + 100_000 * len(plan.fee_assets)


@dataclass(frozen=True)
class _ProfiledLaunchCapacity:
    bounds: LifecycleLaunchGasBounds
    initial_buys: tuple[LifecycleOpeningBuyQuote, ...]
    headroom_bps: int
    atomic: LifecycleTransaction
    activation: LifecycleTransaction


def derive_lifecycle_launch_capacity(plan: LaunchPlanV1, *, predicted_token: str,
        ufg: UfgLaunchConfig | None = None, headroom_bps: int = 1500,
        opening_buys: Sequence[LifecycleOpeningBuyQuote] | None = None) -> _ProfiledLaunchCapacity | None:
    if (not _covered(plan) or any(funding.kind == LifecycleFundingKind.SWAP for funding in plan.funding)
            or (plan.creator.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower()) != (ufg is not None)):
        return None
    if isinstance(headroom_bps, bool) or not isinstance(headroom_bps, int) or not 0 <= headroom_bps <= 10000:
        raise LifecyclePlanningError("INVALID_LIMITS", "Headroom must be between zero and 10000 basis points")
    quotes = tuple(opening_buys) if opening_buys is not None else compute_lifecycle_opening_buys(plan, predicted_token=predicted_token)
    atomic, = (build_launch_transactions(plan, mode="atomic") if ufg is None
        else build_ufg_launch_transactions(plan, config=ufg, mode="atomic"))
    activation = replace(atomic, step_id="activate", kind="activate", value=0,
        data=build_lifecycle_calldata(plan, "activateLaunch"))
    markets, reserve = _market_gas(plan), _forwarding_reserve(plan, "atomic")
    def bound(transaction: LifecycleTransaction) -> int:
        basis = _capacity_basis(plan, transaction, markets, quotes, ufg is not None)
        return max((basis * (10000 + headroom_bps) + 9999) // 10000, basis + reserve)
    return _ProfiledLaunchCapacity(LifecycleLaunchGasBounds(bound(atomic), bound(activation)),
        quotes, headroom_bps, atomic, activation)


def compute_launch_gas_bounds(plan: LaunchPlanV1, *, predicted_token: str,
        ufg: UfgLaunchConfig | None = None, headroom_bps: int = 1500) -> LifecycleLaunchGasBounds | None:
    capacity = derive_lifecycle_launch_capacity(plan, predicted_token=predicted_token,
        ufg=ufg, headroom_bps=headroom_bps)
    return None if capacity is None else capacity.bounds


@dataclass(frozen=True)
class _ProfiledLifecycleTransactions:
    simulation: LaunchSimulation
    profile_only: bool


def profile_lifecycle_transactions(client, plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction], *,
        block: LaunchBlock, predicted: str, limits: LaunchExecutionLimits,
        reviewed_envelope: tuple[int, int] | None = None,
        opening_buys: Sequence[LifecycleOpeningBuyQuote] | None = None,
        ufg: UfgLaunchConfig | None = None,
        capacity: _ProfiledLaunchCapacity | None = None) -> _ProfiledLifecycleTransactions | None:
    only_approvals = all(_approval(transaction) for transaction in transactions)
    if plan.chain_id != 4663 or not only_approvals and (not _covered(plan) or limits.arb_os_version != 61):
        return None
    if _read_lifecycle_account_code(client, _execution_account(plan), plan.chain_id, block) != "0x":
        return None
    prices = _read_nitro_prices(client, block)
    balance = quantity(pinned_rpc(client, "eth_getBalance", [_execution_account(plan), block.tag], block))
    base_fee, ceiling = block.base_fee_per_gas or 0, limits.compute_cap(block)
    price = reviewed_envelope[1] if reviewed_envelope is not None else max(prices[5], base_fee * 2)
    if reviewed_envelope is not None and not only_approvals and reviewed_envelope[0] != ceiling:
        raise LifecyclePlanningError("STALE_REVIEWED_GAS", "Reviewed gas differs from the native transaction ceiling; review the launch again")
    needs_buys = any(transaction.kind in {"atomic", "activate"} for transaction in transactions)
    current = (capacity if capacity is not None and capacity.headroom_bps == limits.headroom_bps else
        derive_lifecycle_launch_capacity(plan, predicted_token=predicted, ufg=ufg,
            headroom_bps=limits.headroom_bps, opening_buys=opening_buys)) if needs_buys else None
    if needs_buys and current is None:
        return None
    quotes, bounds = (current.initial_buys, current.bounds) if current is not None else ((), None)
    profile_only = not any(funding.kind == LifecycleFundingKind.SWAP for funding in plan.funding) or not any(
        transaction.kind in {"begin", "atomic", "activate"} for transaction in transactions)
    for transaction in transactions:
        if transaction.kind not in {"atomic", "activate"}:
            continue
        compute = bounds.atomic_compute_gas_upper_bound if transaction.kind == "atomic" else bounds.activation_compute_gas_upper_bound
        if compute + _nitro_poster_gas_bound(transaction, prices)[0] > ceiling:
            profile_only = False
    launch = next((transaction for transaction in transactions if not _approval(transaction)), None)
    calldata_failure = next((transaction for transaction in transactions
        if limits.calldata_cap() is not None and transaction.calldata_bytes > limits.calldata_cap()), None)
    envelope_failure = launch is not None and ceiling > limits.gas_cap(block)
    fee_failure = reviewed_envelope is not None and price < max(prices[5], base_fee)
    affordability_failure = launch is not None and launch.value + ceiling * price > balance
    semantic_failure = next((quote for quote in quotes if quote.token_out < plan.buys[quote.buy_index].min_token_out), None)
    expired = block.timestamp > plan.deadline and any(transaction.kind != "cancel" for transaction in transactions)
    refused = envelope_failure or calldata_failure is not None or fee_failure or affordability_failure or semantic_failure is not None or expired
    category = constraint = failed_id = reason = None
    if envelope_failure:
        category, constraint, failed_id, reason = "capacity", "gas-envelope", launch.step_id, "Native transaction ceiling exceeds the supplied complete gas policy"
    if calldata_failure is not None:
        category, constraint, failed_id, reason = "capacity", "calldata", calldata_failure.step_id, "Encoded command exceeds the caller's calldata byte policy"
    if affordability_failure:
        category, failed_id, reason = "affordability", launch.step_id, "Creator native balance cannot reserve transaction value and the native gas ceiling"
    if semantic_failure is not None:
        category, failed_id, reason = "semantic", next((transaction.step_id for transaction in transactions if transaction.kind in {"atomic", "activate"}), None), "Opening curve output is below the committed minimum"
    if expired:
        category, failed_id, reason = "semantic", next((transaction.step_id for transaction in transactions if transaction.kind != "cancel"), None), "Launch deadline has expired at the pinned block"
    if fee_failure:
        failed_id, reason = transactions[0].step_id if transactions else None, "Reviewed gas price is below the pinned required fee"
    bound = []
    for index, transaction in enumerate(transactions):
        if index > 0 or _approval(transaction):
            bound.append(replace(transaction, gas_limit=None, gas_price=None))
            continue
        poster = _nitro_poster_gas_bound(transaction, prices)[0]
        fee = ceiling * price
        bound.append(replace(transaction, gas_limit=ceiling, gas_price=price,
            execution_fee=fee, maximum_execution_fee=fee, data_fee=0, total_fee=fee,
            poster_gas=poster, poster_fee=poster * price, data_fee_included_in_gas=True, confidence="profiled"))
    profile = None if bounds is None else LifecycleGasProfileEvidence(_PROFILE_VERSION, bounds, tuple(quotes))
    simulation = _launch_simulation(plan, limits, LaunchRpcSimulation("profiled", "profiled", block, ()),
        bound, () if reason is None else (reason,), admitted=profile_only and not refused,
        execution_proof="not-run", protocol_fit="profiled", unknown=limits.unknown_constraints(),
        failure_category=category, capacity_constraint=constraint, failed_transaction_id=failed_id, gas_profile=profile)
    return _ProfiledLifecycleTransactions(simulation, profile_only or refused)
