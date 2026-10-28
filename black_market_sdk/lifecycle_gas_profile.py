"""Release-owned ArbOS61 envelope coverage, distinct from execution admission."""
from dataclasses import dataclass, replace
from collections.abc import Sequence

from .launch import get_tick_at_sqrt_ratio
from .lifecycle import (
    LaunchPlanV1, LaunchSimulation, LifecycleFundingKind, LifecycleGasProfileEvidence,
    LifecycleOpeningBuyQuote, LifecycleRewardMode, LifecycleTokenKind,
    LifecycleTransaction, LifecycleTransactionEstimate, _GasSizingHints,
    _PROFILED_QUOTE_IDENTITIES, _execution_account, _forwarding_reserve,
    _launch_simulation, _nitro_poster_gas_bound, _read_nitro_prices,
    compute_lifecycle_opening_buys, decode_lifecycle_abyss_market_config,
    decode_lifecycle_pool_bound_v4_market_config,
)
from .lifecycle_buy_math import has_profiled_opening_math
from .lifecycle_presets import get_known_lifecycle_profile
from .lifecycle_rpc import (
    LaunchBlock, LaunchExecutionLimits, LaunchRpcSimulation, pinned_rpc, quantity,
    _read_lifecycle_account_code,
)

_PROFILE_VERSION = "robinhood-4663-arbos61-v1"


@dataclass(frozen=True)
class _MarketGas:
    abyss: bool
    hmm: bool
    ufg: bool
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


def _compute_gas_basis(plan: LaunchPlanV1, transaction: LifecycleTransaction,
        markets: Sequence[_MarketGas], quotes: Sequence[LifecycleOpeningBuyQuote]) -> int:
    """Minimum-envelope bases, not refund-reduced receipt gasUsed observations.

    Retained native receipts and isolated-fork minimum envelopes cover creation,
    minting, refunds and EIP150. HMM uses its full manager budgets plus forwarding
    reserve. Caller headroom is applied exactly once by the profile producer.
    """
    encoded = 24 * transaction.calldata_bytes
    policies = 100_000 * len(plan.fee_assets)
    token = plan.token
    token_creation = 0
    if transaction.kind in {"begin", "atomic"}:
        metadata_words = sum((len(value.encode()) + 31) // 32
            for value in (token.name, token.symbol, token.metadata_uri))
        token_creation = ((2_500_000 if token.kind == LifecycleTokenKind.ERC404 else 0)
            + (2_500_000 if token.reward_mode == LifecycleRewardMode.STAKING else 500_000 if token.reward_mode == LifecycleRewardMode.DIVIDENDS else 0)
            + 22_000 * metadata_words)
    if transaction.kind in {"approve-reset", "approve"}:
        return 150_000 + encoded
    if transaction.kind == "cancel":
        return 350_000 + encoded + policies + 30_000 * len(markets)
    if transaction.kind == "begin":
        return 8_750_000 + token_creation + encoded + policies + 150_000 * len(plan.funding) + (6_500_000 if any(market.ufg for market in markets) else 0)
    if transaction.kind == "prepare":
        gas = 500_000 + encoded + policies
        for index in range(transaction.first_market, transaction.first_market + transaction.market_count):
            market = markets[index]
            gas += (16_000_000 if market.hmm else 10_000_000 if market.ufg else 9_500_000 if market.abyss else 9_000_000) + 75_000 * market.positions
        return gas
    gas = 19_500_000 + token_creation if transaction.kind == "atomic" else 1_000_000
    for index, market in enumerate(markets):
        if transaction.kind == "activate":
            gas += 2_500_000 + 1_600_000 * market.positions if market.abyss else 2_000_000 + 600_000 * market.positions
        else:
            first = 7_000_000 if market.hmm else 6_000_000 if market.ufg else 500_000 if market.abyss else 0
            later = 15_000_000 if market.hmm else 9_000_000 if market.ufg else 8_000_000
            gas += (first if index == 0 else later) + (1_500_000 if market.abyss else 600_000) * (market.positions - 1)
        if transaction.kind == "activate" and market.hmm:
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
    return gas + encoded + policies


def requires_exact_lifecycle_execution(plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction]) -> bool:
    return any(funding.kind == LifecycleFundingKind.SWAP for funding in plan.funding) and any(
        transaction.kind in {"begin", "atomic", "activate"} for transaction in transactions)


@dataclass(frozen=True)
class _ProfiledLifecycleTransactions:
    simulation: LaunchSimulation
    sizing: _GasSizingHints
    profile_only: bool


def profile_lifecycle_transactions(client, plan: LaunchPlanV1, transactions: Sequence[LifecycleTransaction], *,
        block: LaunchBlock, predicted: str, limits: LaunchExecutionLimits,
        reviewed_envelope: tuple[int, int] | None = None,
        opening_buys: Sequence[LifecycleOpeningBuyQuote] | None = None) -> _ProfiledLifecycleTransactions | None:
    """Fresh coverage; a bound that does not fit asks for exact execution."""
    if (plan.chain_id != 4663 or not has_profiled_opening_math(plan)
            or any(market.quote_asset.lower() not in _PROFILED_QUOTE_IDENTITIES for market in plan.markets)
            or any(funding.kind == LifecycleFundingKind.ERC20 and funding.input_asset.lower() not in _PROFILED_QUOTE_IDENTITIES for funding in plan.funding)
            or limits.arb_os_version != 61):
        return None
    account = _execution_account(plan)
    if _read_lifecycle_account_code(client, account, plan.chain_id, block) != "0x":
        return None
    prices = _read_nitro_prices(client, block)
    balance = quantity(pinned_rpc(client, "eth_getBalance", [account, block.tag], block))
    markets = _market_gas(plan)
    needs_buys = any(transaction.kind in {"atomic", "activate"} for transaction in transactions)
    quotes = tuple(opening_buys) if needs_buys and opening_buys is not None else compute_lifecycle_opening_buys(plan, predicted_token=predicted) if needs_buys else ()
    price = reviewed_envelope[1] if reviewed_envelope is not None else max(prices[5], (block.base_fee_per_gas or 0) * 2)
    estimates, values = {}, {}
    exact_execution = requires_exact_lifecycle_execution(plan, transactions)
    profile_only = not exact_execution
    available, policy_failure = balance, None
    compute_cap, envelope_cap, calldata_cap = limits.compute_cap(block), limits.gas_cap(block), limits.calldata_cap()
    bound = []
    for index, transaction in enumerate(transactions):
        if exact_execution and transaction.kind in {"begin", "atomic"}:
            # Opaque router work is not a template-sized computation.
            compute = compute_cap
        else:
            basis = _compute_gas_basis(plan, transaction, markets, quotes)
            compute = max(limits.buffered_gas(basis), basis + _forwarding_reserve(plan, transaction.kind))
        poster_gas = _nitro_poster_gas_bound(transaction, prices)[0]
        gas_limit = reviewed_envelope[0] if index == 0 and reviewed_envelope is not None else compute + poster_gas
        values[transaction.step_id] = compute
        fee = gas_limit * price
        estimate = LifecycleTransactionEstimate(gas_limit=gas_limit, gas_price=price,
            execution_fee=fee, data_fee_included_in_gas=True,
            fee_confidence="profiled-execution-and-data", data_fee=0, total_fee=fee,
            poster_gas=poster_gas, poster_fee=poster_gas * price)
        estimates[transaction.step_id] = estimate
        if (compute > compute_cap or compute + poster_gas > envelope_cap
                or index == 0 and reviewed_envelope is not None and reviewed_envelope[0] < compute + poster_gas
                or gas_limit <= 0 or gas_limit >= 1 << 64 or price >= 1 << 256
                or fee + transaction.value >= 1 << 256):
            profile_only = False
        available -= transaction.value + fee
        if available < 0:
            profile_only = False
        if calldata_cap is not None and transaction.calldata_bytes > calldata_cap:
            policy_failure = transaction.step_id
        if index == 0:
            bound.append(replace(transaction, gas_limit=gas_limit, gas_price=price, execution_fee=fee,
                maximum_execution_fee=fee, data_fee=0, total_fee=fee, poster_gas=poster_gas,
                poster_fee=poster_gas * price, data_fee_included_in_gas=True, confidence="profiled"))
        else:
            bound.append(replace(transaction, gas_limit=None, gas_price=None, admission=None))
    semantic_failure = next((quote for quote in quotes if quote.token_out < plan.buys[quote.buy_index].min_token_out), None)
    expired = block.timestamp > plan.deadline and any(transaction.kind != "cancel" for transaction in transactions)
    failed_id, category, reason = policy_failure, None, None
    if policy_failure is not None:
        category, reason = "capacity", "Encoded command exceeds the caller's calldata byte policy"
    if semantic_failure is not None:
        category, reason = "semantic", "Opening curve output is below the committed minimum"
        failed_id = next((transaction.step_id for transaction in transactions if transaction.kind in {"atomic", "activate"}), None)
    if expired:
        category, reason = "semantic", "Launch deadline has expired at the pinned block"
        failed_id = next((transaction.step_id for transaction in transactions if transaction.kind != "cancel"), None)
    profile = LifecycleGasProfileEvidence(_PROFILE_VERSION, estimates, tuple(quotes))
    simulation = _launch_simulation(plan, limits, LaunchRpcSimulation("profiled", "profiled", block, ()),
        bound, () if reason is None else (reason,), admitted=profile_only and policy_failure is None and semantic_failure is None and not expired,
        execution_proof="not-run", protocol_fit="profiled", unknown=limits.unknown_constraints(),
        failure_category=category, capacity_constraint="calldata" if policy_failure is not None else None,
        failed_transaction_id=failed_id, gas_profile=profile)
    return _ProfiledLifecycleTransactions(simulation, _GasSizingHints("profiled", values),
        profile_only or policy_failure is not None or semantic_failure is not None or expired)
