"""Offline current-launch scenario matrix for the schema-/2 UnifiedLauncher.

Each scenario is a reproducible, canonical current-launcher example.  Named
scenarios expose exactly the ``LaunchTemplateDefaultsV2.registerAll`` token
mapping: burnable templates (standard, quote-staking, dual-staking, fee-burn)
use the burnable deployer with an empty config, and the dividend templates
(quote-dividends, dual-dividends) use the holder-dividend deployer with the
pinned ``abi.encode(bool dualRewards)`` config.  Importing this module only
performs deterministic local construction; it never creates a client, signs,
or performs network I/O.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from black_market_sdk.abyss import AbyssPoolProfile, TokenKind
from black_market_sdk.launch import (
    ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
    AUCTION_SUPPLY,
    AtomicInitialBuy,
    AtomicLaunchBuySqrtPriceLimitInput,
    AtomicLaunchPoolRecipeInput,
    AtomicLaunchRequest,
    AtomicPoolConfig,
    AtomicTokenConfig,
    FeeDisposition,
    LAUNCH_TEMPLATES,
    derive_atomic_launch_buy_sqrt_price_limit_x96,
    derive_atomic_launch_pool_recipe,
    get_launch_template,
)
from black_market_sdk.unified import (
    LaunchPoolKind,
    UnifiedLaunchPool,
    UnifiedLaunchRequest,
    UniswapV4PoolConfigV2,
    build_unified_launch_calldata,
    to_unified_launch_request,
    to_uniswap_v4_pool_config_v2,
)

__all__ = [
    "LaunchScenario",
    "iter_launch_scenarios",
    "build_scenario_request",
    "v4_lighthouse_liquidity_shares",
    "main",
]


@dataclass(frozen=True)
class LaunchScenario:
    """One reproducible canonical current launcher example."""

    scenario_id: str
    pool_kind: LaunchPoolKind
    template_id: str
    token_kind: TokenKind
    dual_rewards: bool | None
    profile: AbyssPoolProfile
    launched_token_fees: FeeDisposition
    paired_token_fees: FeeDisposition


@dataclass(frozen=True)
class _FeeVariant:
    identifier: str
    launched: FeeDisposition
    paired: FeeDisposition


@dataclass(frozen=True)
class _ScenarioBuild:
    request: UnifiedLaunchRequest
    calldata: bytes
    recipe_liquidity: int
    v4_config: UniswapV4PoolConfigV2 | None


# LaunchTemplateDefaultsV2.registerAll pins each named template to exactly
# one canonical token type: burnable for non-dividend templates, and
# holder-dividend with a pinned dualRewards bool for the dividend templates.
_NON_DIVIDEND_TEMPLATE_IDS = frozenset(
    {"standard", "quote-staking", "dual-staking", "fee-burn"}
)
_PINNED_HOLDER_DIVIDEND_CONFIGS = {
    "quote-dividends": False,
    "dual-dividends": True,
}

_CURRENT_POOL_KINDS = (LaunchPoolKind.ABYSS, LaunchPoolKind.UNISWAP_V4_V3)

_OWNER_ONLY = FeeDisposition(owner_bps=10_000, rewards_bps=0, burn_bps=0)
_REWARDS_ONLY = FeeDisposition(owner_bps=0, rewards_bps=10_000, burn_bps=0)
_MIXED_OWNER_REWARDS = FeeDisposition(owner_bps=6_000, rewards_bps=4_000, burn_bps=0)
_MIXED_LAUNCHED_OWNER_REWARDS_BURN = FeeDisposition(
    owner_bps=5_000,
    rewards_bps=3_000,
    burn_bps=2_000,
)
_BURN_ONLY = FeeDisposition(owner_bps=0, rewards_bps=0, burn_bps=10_000)
_NO_FEES = FeeDisposition(owner_bps=0, rewards_bps=0, burn_bps=0)

_TOKEN_CONFIG_SLUGS = {
    (TokenKind.BURNABLE, None): "burnable",
    (TokenKind.HOLDER_DIVIDEND, False): "holder-dividend-false",
    (TokenKind.HOLDER_DIVIDEND, True): "holder-dividend-true",
}
_TOKEN_SYMBOL_CODES = {
    (TokenKind.BURNABLE, None): "B",
    (TokenKind.HOLDER_DIVIDEND, False): "H0",
    (TokenKind.HOLDER_DIVIDEND, True): "H1",
}
_ROUTE_SYMBOL_CODES = {
    LaunchPoolKind.ABYSS: "A",
    LaunchPoolKind.UNISWAP_V4_V3: "V3",
}
_TEMPLATE_SYMBOL_CODES = {
    "standard": "ST",
    "quote-staking": "QS",
    "quote-dividends": "QD",
    "dual-staking": "DS",
    "dual-dividends": "DD",
    "fee-burn": "FB",
}
_PROFILE_SYMBOL_CODES = {
    AbyssPoolProfile.STANDARD_ORACLE: "SO",
    AbyssPoolProfile.QUOTE_ORACLE: "QO",
}
_TEMPLATE_NAME_LABELS = {
    "standard": "Standard",
    "quote-staking": "QStake",
    "quote-dividends": "QDiv",
    "dual-staking": "DStake",
    "dual-dividends": "DDiv",
    "fee-burn": "FeeBurn",
}
_FEE_SYMBOL_CODES = {
    "owner-only": "O",
    "rewards-only": "R",
    "mixed-owner-rewards": "M",
    "mixed-launched-owner-rewards-burn": "X",
    "burn-only": "F",
}


def _token_variants(template_id: str) -> tuple[tuple[TokenKind, bool | None], ...]:
    """Return the canonical token configuration registered by this template."""

    pinned = _PINNED_HOLDER_DIVIDEND_CONFIGS.get(template_id)
    if pinned is not None:
        return ((TokenKind.HOLDER_DIVIDEND, pinned),)
    if template_id in _NON_DIVIDEND_TEMPLATE_IDS:
        return ((TokenKind.BURNABLE, None),)
    raise ValueError("Launch matrix does not have a canonical record for this template")


def _fee_variants(template_id: str, profile: AbyssPoolProfile) -> tuple[_FeeVariant, ...]:
    """Return materially distinct valid fee policies for one template/profile."""

    if template_id == "standard":
        if profile == AbyssPoolProfile.STANDARD_ORACLE:
            return (_FeeVariant("owner-only", _OWNER_ONLY, _OWNER_ONLY),)
        return (_FeeVariant("owner-only", _NO_FEES, _OWNER_ONLY),)

    if template_id in {"quote-staking", "quote-dividends"}:
        return (
            _FeeVariant("owner-only", _NO_FEES, _OWNER_ONLY),
            _FeeVariant("rewards-only", _NO_FEES, _REWARDS_ONLY),
            _FeeVariant("mixed-owner-rewards", _NO_FEES, _MIXED_OWNER_REWARDS),
        )

    if template_id in {"dual-staking", "dual-dividends"}:
        return (
            _FeeVariant("owner-only", _OWNER_ONLY, _OWNER_ONLY),
            _FeeVariant("rewards-only", _REWARDS_ONLY, _REWARDS_ONLY),
            _FeeVariant(
                "mixed-owner-rewards",
                _MIXED_OWNER_REWARDS,
                _MIXED_OWNER_REWARDS,
            ),
            _FeeVariant(
                "mixed-launched-owner-rewards-burn",
                _MIXED_LAUNCHED_OWNER_REWARDS_BURN,
                _MIXED_OWNER_REWARDS,
            ),
        )

    if template_id == "fee-burn":
        return (_FeeVariant("burn-only", _BURN_ONLY, _NO_FEES),)

    raise ValueError("Launch matrix does not have fee policies for this template")


def _profile_slug(profile: AbyssPoolProfile) -> str:
    if profile is AbyssPoolProfile.STANDARD_ORACLE:
        return "standard-oracle"
    if profile is AbyssPoolProfile.QUOTE_ORACLE:
        return "quote-oracle"
    raise ValueError("Launch matrix only supports oracle-backed profiles")


def _scenario_id(
    pool_kind: LaunchPoolKind,
    template_id: str,
    profile: AbyssPoolProfile,
    token_kind: TokenKind,
    dual_rewards: bool | None,
    fee_variant: _FeeVariant,
) -> str:
    return ".".join(
        (
            pool_kind.value,
            template_id,
            _profile_slug(profile),
            _TOKEN_CONFIG_SLUGS[(token_kind, dual_rewards)],
            fee_variant.identifier,
        )
    )


def _build_scenarios(pool_kinds: Sequence[LaunchPoolKind]) -> tuple[LaunchScenario, ...]:
    scenarios: list[LaunchScenario] = []
    for pool_kind in pool_kinds:
        for template in LAUNCH_TEMPLATES:
            for profile in template.pool_profiles:
                for token_kind, dual_rewards in _token_variants(template.id):
                    for fee_variant in _fee_variants(template.id, profile):
                        scenarios.append(
                            LaunchScenario(
                                scenario_id=_scenario_id(
                                    pool_kind,
                                    template.id,
                                    profile,
                                    token_kind,
                                    dual_rewards,
                                    fee_variant,
                                ),
                                pool_kind=pool_kind,
                                template_id=template.id,
                                token_kind=token_kind,
                                dual_rewards=dual_rewards,
                                profile=profile,
                                launched_token_fees=fee_variant.launched,
                                paired_token_fees=fee_variant.paired,
                            )
                        )
    return tuple(scenarios)


_CURRENT_SCENARIOS = _build_scenarios(_CURRENT_POOL_KINDS)


def iter_launch_scenarios() -> tuple[LaunchScenario, ...]:
    """Return deterministic runnable examples for current routes.

    The matrix covers exactly the current routes: Abyss and the optimized
    Uniswap V4 V3 pool adapter.
    """

    return _CURRENT_SCENARIOS


def _require_matrix_scenario(scenario: LaunchScenario) -> None:
    if not isinstance(scenario, LaunchScenario) or scenario not in _CURRENT_SCENARIOS:
        raise ValueError("scenario is not a registered launch-matrix scenario")


def _nonnegative_integer(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value



def _fee_variant_identifier(scenario: LaunchScenario) -> str:
    return scenario.scenario_id.rsplit(".", 1)[-1]


def _default_token_identity(scenario: LaunchScenario) -> tuple[str, str]:
    """Derive compact, human-readable metadata from stable scenario dimensions."""

    token_key = (scenario.token_kind, scenario.dual_rewards)
    # At most 11 characters: ``LM`` + a two-character route + four compact
    # dimensions.  This remains within the web launch form's 12-character
    # symbol limit while preserving a unique replay identity for every row.
    symbol = "".join(
        (
            "LM",
            _ROUTE_SYMBOL_CODES[scenario.pool_kind],
            _TEMPLATE_SYMBOL_CODES[scenario.template_id],
            _PROFILE_SYMBOL_CODES[scenario.profile],
            _TOKEN_SYMBOL_CODES[token_key],
            _FEE_SYMBOL_CODES[_fee_variant_identifier(scenario)],
        )
    )
    return f"Matrix {_TEMPLATE_NAME_LABELS[scenario.template_id]} {symbol}", symbol


def _build_scenario(
    scenario: LaunchScenario,
    *,
    creator: str,
    paired_token: str,
    paired_token_decimals: int,
    paired_token_usd_price_x18: int,
    target_market_cap_usd_x18: int,
    fee: int,
    deadline: int,
    name: str | None,
    symbol: str | None,
    initial_buy_amount: int,
    native_buy_amount: int,
    slippage_bps: int,
    external_liquidity_disabled: bool,
    oracle_config_id: bytes | str,
) -> _ScenarioBuild:
    _require_matrix_scenario(scenario)
    initial_buy_amount = _nonnegative_integer(initial_buy_amount, "initial_buy_amount")
    native_buy_amount = _nonnegative_integer(native_buy_amount, "native_buy_amount")
    if not isinstance(external_liquidity_disabled, bool):
        raise ValueError("external_liquidity_disabled must be a boolean")
    if native_buy_amount and scenario.pool_kind is not LaunchPoolKind.ABYSS:
        raise ValueError("Uniswap V4 launch routes require zero native_buy_amount")

    template = get_launch_template(scenario.template_id)
    default_name, default_symbol = _default_token_identity(scenario)
    token_name = default_name if name is None else name
    token_symbol = default_symbol if symbol is None else symbol
    recipe = derive_atomic_launch_pool_recipe(
        AtomicLaunchPoolRecipeInput(
            paired_token_decimals=paired_token_decimals,
            paired_token_usd_price_x18=paired_token_usd_price_x18,
            target_market_cap_usd_x18=target_market_cap_usd_x18,
            launched_token_is_quote=template.launched_token_is_quote,
            fee=fee,
        )
    )

    effective_buy_amount = initial_buy_amount + native_buy_amount
    sqrt_price_limit_x96 = (
        derive_atomic_launch_buy_sqrt_price_limit_x96(
            AtomicLaunchBuySqrtPriceLimitInput(
                launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
                launched_token_is_quote=template.launched_token_is_quote,
                slippage_bps=slippage_bps,
            )
        )
        if effective_buy_amount
        else 0
    )
    atomic = AtomicLaunchRequest(
        creator=creator,
        template_id=template.template_id,
        template_version=template.version,
        token=AtomicTokenConfig(
            kind=template.token_kinds[0],
            name=token_name,
            symbol=token_symbol,
            decimals=18,
            supply=AUCTION_SUPPLY,
        ),
        pool=AtomicPoolConfig(
            paired_token=paired_token,
            launched_token_is_quote=template.launched_token_is_quote,
            profile=scenario.profile,
            fee=fee,
            oracle_config_id=oracle_config_id,
            launch_tick=recipe.launch_tick,
            liquidity=recipe.liquidity,
            launched_token_amount_maximum=recipe.launched_token_amount_maximum,
            paired_token_amount_maximum=recipe.paired_token_amount_maximum,
        ),
        initial_buy=AtomicInitialBuy(
            paired_token_amount_in=initial_buy_amount,
            # The directional price guard is the offline safety bound.  A live
            # submitter simulates immediately before broadcast to choose any
            # additional amount-out minimum from current state.
            launched_token_amount_out_minimum=0,
            sqrt_price_limit_x96=sqrt_price_limit_x96,
        ),
        launched_token_fees=scenario.launched_token_fees,
        paired_token_fees=scenario.paired_token_fees,
        deadline=deadline,
    )

    v4_config: UniswapV4PoolConfigV2 | None = None
    if scenario.pool_kind is LaunchPoolKind.ABYSS:
        pool = UnifiedLaunchPool(kind=scenario.pool_kind, config=atomic.pool)
    else:
        v4_config = to_uniswap_v4_pool_config_v2(
            atomic.pool,
            launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
            external_liquidity_disabled=external_liquidity_disabled,
        )
        pool = UnifiedLaunchPool(kind=scenario.pool_kind, config=v4_config)

    request = to_unified_launch_request(atomic, pool)

    # The canonical conversion already validates the fee policies, profile,
    # and directional initial buy.  For an offline native example, the paired
    # asset stands in as the required wrapped-native identity; a submitter must
    # verify that live adapter fact.
    calldata = build_unified_launch_calldata(
        request,
        native_buy_amount=native_buy_amount,
        wrapped_native_token=paired_token if native_buy_amount else None,
    )
    return _ScenarioBuild(
        request=request,
        calldata=calldata,
        recipe_liquidity=recipe.liquidity,
        v4_config=v4_config,
    )


def build_scenario_request(
    scenario: LaunchScenario,
    *,
    creator: str,
    paired_token: str,
    paired_token_decimals: int = 18,
    paired_token_usd_price_x18: int = 2_500 * 10**18,
    target_market_cap_usd_x18: int = 5_000 * 10**18,
    fee: int = 3_000,
    deadline: int = 1_800_000_000,
    name: str | None = None,
    symbol: str | None = None,
    initial_buy_amount: int = 0,
    native_buy_amount: int = 0,
    slippage_bps: int = 50,
    external_liquidity_disabled: bool = True,
    oracle_config_id: bytes | str = ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
) -> UnifiedLaunchRequest:
    """Build one fully validated offline generic launch envelope.

    ``native_buy_amount`` is only an Abyss input and is intentionally not stored
    in the returned envelope.  Reuse it when encoding/submitting the request,
    after the live adapter confirms that ``paired_token`` is its wrapped-native
    asset.  V4 routes reject native input before any envelope is returned.
    """

    return _build_scenario(
        scenario,
        creator=creator,
        paired_token=paired_token,
        paired_token_decimals=paired_token_decimals,
        paired_token_usd_price_x18=paired_token_usd_price_x18,
        target_market_cap_usd_x18=target_market_cap_usd_x18,
        fee=fee,
        deadline=deadline,
        name=name,
        symbol=symbol,
        initial_buy_amount=initial_buy_amount,
        native_buy_amount=native_buy_amount,
        slippage_bps=slippage_bps,
        external_liquidity_disabled=external_liquidity_disabled,
        oracle_config_id=oracle_config_id,
    ).request


def v4_lighthouse_liquidity_shares(config: UniswapV4PoolConfigV2) -> tuple[int, int]:
    """Return ``(v4_liquidity, abyss_lighthouse_liquidity)`` for V4 config.

    The V4 adapter takes the floored one-percent Abyss lighthouse share from the
    *reduced* V4 config liquidity and assigns the exact residual to V4.  The
    residual preserves the total under integer rounding; it is not a split of
    the pre-conversion Atomic recipe liquidity.
    """

    if not isinstance(config, UniswapV4PoolConfigV2):
        raise ValueError("config must be a UniswapV4PoolConfigV2")
    if (
        not isinstance(config.liquidity, int)
        or isinstance(config.liquidity, bool)
        or config.liquidity < 100
    ):
        raise ValueError("config.liquidity must support the V4 99/1 split")
    abyss_lighthouse = (config.liquidity * 100) // 10_000
    return config.liquidity - abyss_lighthouse, abyss_lighthouse


_OFFLINE_CREATOR = "0x1000000000000000000000000000000000000001"
_OFFLINE_PAIRED_TOKEN = "0x2000000000000000000000000000000000000002"


def _parse_nonnegative_integer(value: str) -> int:
    parsed = int(value, 0)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Construct current UnifiedLauncher launch-matrix envelopes offline."
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--scenario",
        action="append",
        metavar="ID",
        help="Build one stable scenario ID (repeat only for distinct IDs).",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="Build every current scenario (the default when no scenario is selected).",
    )
    parser.add_argument("--list", action="store_true", help="List selected IDs without building.")
    parser.add_argument("--creator", default=_OFFLINE_CREATOR)
    parser.add_argument("--paired-token", default=_OFFLINE_PAIRED_TOKEN)
    parser.add_argument("--paired-token-decimals", type=_parse_nonnegative_integer, default=18)
    parser.add_argument(
        "--paired-token-usd-price-x18",
        type=_parse_nonnegative_integer,
        default=2_500 * 10**18,
    )
    parser.add_argument(
        "--target-market-cap-usd-x18",
        type=_parse_nonnegative_integer,
        default=5_000 * 10**18,
    )
    parser.add_argument("--fee", type=_parse_nonnegative_integer, default=3_000)
    parser.add_argument("--deadline", type=_parse_nonnegative_integer, default=1_800_000_000)
    parser.add_argument(
        "--initial-buy-amount",
        type=_parse_nonnegative_integer,
        default=0,
        help="ERC-20 paired-token input; no wallet, approval, or submission occurs here.",
    )
    parser.add_argument(
        "--native-buy-amount",
        type=_parse_nonnegative_integer,
        default=0,
        help="Abyss-only wrapped-native input; V4 rejects a nonzero value.",
    )
    parser.add_argument("--slippage-bps", type=_parse_nonnegative_integer, default=50)
    parser.add_argument(
        "--external-liquidity-disabled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Set the V4 config flag (default: enabled).",
    )
    parser.add_argument(
        "--oracle-config-id",
        default=f"0x{ATOMIC_LAUNCH_ORACLE_CONFIG_ID.hex()}",
    )
    return parser


def _select_scenarios(scenario_ids: Sequence[str] | None) -> tuple[LaunchScenario, ...]:
    catalog = iter_launch_scenarios()
    if not scenario_ids:
        return catalog
    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("scenario IDs must not repeat")
    by_id = {scenario.scenario_id: scenario for scenario in catalog}
    try:
        return tuple(by_id[scenario_id] for scenario_id in scenario_ids)
    except KeyError as error:
        raise ValueError("requested scenario is not in the selected offline catalog") from error


def _format_disposition(disposition: FeeDisposition) -> str:
    return (
        f"owner={disposition.owner_bps}, rewards={disposition.rewards_bps}, "
        f"burn={disposition.burn_bps} bps"
    )



def _print_built_scenario(scenario: LaunchScenario, build: _ScenarioBuild) -> None:
    """Print public construction facts without calldata, credentials, or URLs."""

    print(scenario.scenario_id)
    print(f"  route: {scenario.pool_kind.value}; template: {scenario.template_id}")
    print(
        "  fee destinations: "
        f"launched [{_format_disposition(scenario.launched_token_fees)}]; "
        f"paired [{_format_disposition(scenario.paired_token_fees)}]"
    )
    print(
        "  offline generic envelope: "
        f"selector=0x{build.calldata[:4].hex()}, bytes={len(build.calldata)}"
    )
    if build.v4_config is not None:
        v4_liquidity, abyss_lighthouse_liquidity = v4_lighthouse_liquidity_shares(
            build.v4_config
        )
        print(
            "  immutable V4/Abyss allocation (offline configuration, not a live receipt): "
            f"recipe input liquidity={build.recipe_liquidity}; "
            f"reduced V4 config liquidity={build.v4_config.liquidity}; "
            f"V4 99% residual={v4_liquidity}; "
            f"permanently locked Abyss lighthouse 1% floor={abyss_lighthouse_liquidity}."
        )
        print("  Fee destinations above are independent of the V4/Abyss liquidity allocation.")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline matrix CLI without creating a network client."""

    parser = _build_argument_parser()
    args = parser.parse_args(argv)
    try:
        scenarios = _select_scenarios(args.scenario)
        if args.list:
            for scenario in scenarios:
                print(scenario.scenario_id)
            return 0
        builds = tuple(
            _build_scenario(
                scenario,
                creator=args.creator,
                paired_token=args.paired_token,
                paired_token_decimals=args.paired_token_decimals,
                paired_token_usd_price_x18=args.paired_token_usd_price_x18,
                target_market_cap_usd_x18=args.target_market_cap_usd_x18,
                fee=args.fee,
                deadline=args.deadline,
                name=None,
                symbol=None,
                initial_buy_amount=args.initial_buy_amount,
                native_buy_amount=args.native_buy_amount,
                slippage_bps=args.slippage_bps,
                external_liquidity_disabled=args.external_liquidity_disabled,
                oracle_config_id=args.oracle_config_id,
            )
            for scenario in scenarios
        )
    except (TypeError, ValueError):
        parser.error("Unable to construct the offline scenario from the supplied public arguments.")

    print("Offline launch matrix: no client, signature, or network request was created.")
    for scenario, build in zip(scenarios, builds):
        _print_built_scenario(scenario, build)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
