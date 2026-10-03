"""Contract-shape tests for schema-/2 UnifiedLauncher offline builders."""

from dataclasses import replace

import pytest
from eth_account import Account
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from black_market_sdk.abyss import AbyssPoolProfile, TokenKind
from black_market_sdk.launch import (
    ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
    DUAL_DIVIDENDS_TEMPLATE_ID,
    STANDARD_TEMPLATE_ID,
    AtomicInitialBuy,
    AtomicLaunchRequest,
    AtomicPoolConfig,
    AtomicTokenConfig,
    FeeDisposition,
    get_sqrt_ratio_at_tick,
)
from black_market_sdk.unified import (
    ABYSS_POOL_TYPE,
    LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2,
    LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2,
    UNISWAP_V4_V3_POOL_TYPE,
    LaunchPoolKind,
    UnifiedLaunchPool,
    UnifiedLaunchRequest,
    UnifiedTokenConfig,
    build_unified_launch_calldata,
    build_unified_launch_transaction,
    decode_unified_launch_receipt,
    decode_v4_pool_data,
    enabled_launch_pool_adapter,
    encode_abyss_pool_config,
    encode_uniswap_v4_pool_config_v2,
    launch_pool_type_id,
    to_unified_launch_request,
    to_uniswap_v4_pool_config_v2,
    unified_launch_value,
)


CREATOR = "0x1000000000000000000000000000000000000001"
PAIRED_TOKEN = "0x2000000000000000000000000000000000000002"
CURRENT_LAUNCHER = "0xa7a4755fb907593f05fd1e289aa780f0d57f3a12"
SCHEMA_ONE_LAUNCHER = "0x17313fd1cf6cf6f7990c297f7e8094a0c4e43f6b"
SUPPLY = 1_000_000_000 * 10**18

LAUNCH_REQUEST_TYPE = (
    "(address,bytes32,bytes32,uint32,(bytes32,bytes,string,string,uint8,uint256),"
    "bytes,(uint256,uint256,uint160),(uint16,uint16,uint16),(uint16,uint16,uint16),uint256)"
)
ABYSS_POOL_CONFIG_TYPE = "(address,bool,uint8,uint24,bytes32,int24,uint128,uint256,uint256)"
V4_POOL_CONFIG_V2_TYPE = "(address,uint8,bytes32,int24,int24,uint160,uint128,uint256,uint24,bool)"
LAUNCH_RECEIPT_TYPE = (
    "(address,address,uint256,uint256,uint256,uint256,uint256,address,address,address,bytes)"
)
V4_POOL_DATA_TYPE = "(address,address,address,address,uint128,uint128,uint256,uint256)"


def _atomic_request(
    *,
    template_id: bytes = STANDARD_TEMPLATE_ID,
    token_kind: TokenKind = TokenKind.BURNABLE,
    profile: AbyssPoolProfile = AbyssPoolProfile.QUOTE_ORACLE,
    launched_fees: FeeDisposition | None = None,
    paired_fees: FeeDisposition | None = None,
) -> AtomicLaunchRequest:
    return AtomicLaunchRequest(
        creator=CREATOR,
        template_id=template_id,
        template_version=1,
        token=AtomicTokenConfig(
            kind=token_kind,
            name="Unified Test",
            symbol="UNIFIED",
            decimals=18,
            supply=SUPPLY,
        ),
        pool=AtomicPoolConfig(
            paired_token=PAIRED_TOKEN,
            launched_token_is_quote=False,
            profile=profile,
            fee=3_000,
            oracle_config_id=ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
            launch_tick=-120_000,
            liquidity=1_000_000,
            launched_token_amount_maximum=900_000_000 * 10**18,
            paired_token_amount_maximum=0,
        ),
        initial_buy=AtomicInitialBuy(
            paired_token_amount_in=0,
            launched_token_amount_out_minimum=0,
            sqrt_price_limit_x96=0,
        ),
        launched_token_fees=launched_fees
        if launched_fees is not None
        else FeeDisposition(owner_bps=0, rewards_bps=0, burn_bps=0),
        paired_token_fees=paired_fees
        if paired_fees is not None
        else FeeDisposition(owner_bps=10_000, rewards_bps=0, burn_bps=0),
        deadline=1_900_000_000,
    )


def _v4_request(kind: LaunchPoolKind = LaunchPoolKind.UNISWAP_V4_V3) -> UnifiedLaunchRequest:
    atomic = _atomic_request()
    config = to_uniswap_v4_pool_config_v2(
        atomic.pool,
        launch_sqrt_price_x96=1 << 96,
        external_liquidity_disabled=True,
    )
    return to_unified_launch_request(atomic, UnifiedLaunchPool(kind=kind, config=config))

def _v4_config_values(config):
    return (
        config.paired_token,
        int(config.profile),
        config.oracle_config_id,
        config.tick_lower,
        config.tick_upper,
        config.sqrt_price_x96,
        config.liquidity,
        config.launched_token_amount_maximum,
        config.abyss_fee_pips,
        config.external_liquidity_disabled,
    )


def test_fixed_registry_and_token_ids_match_current_contract_source():
    assert ABYSS_POOL_TYPE.hex() == "86f65430a719d524357a8944e191cb986db9cdd72c32fb2b8c1dc745be058350"
    assert (
        UNISWAP_V4_V3_POOL_TYPE.hex()
        == "52362a00895b4bb591cc297790d75b37d24c0b702f0adba2d6feb192914f4101"
    )
    assert (
        LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2.hex()
        == "e586aada1251ca18ae2d1ae0dbc7b67412291ae7184e8bae678cd4a652351868"
    )
    assert (
        LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2.hex()
        == "84895d7e94d7d03c557ea01d250250b3d77eb2a648bb918acf50ec481843fa37"
    )
    assert launch_pool_type_id("abyss") == ABYSS_POOL_TYPE
    assert launch_pool_type_id("uniswap-v4-v3") == UNISWAP_V4_V3_POOL_TYPE

    adapter = "0x3000000000000000000000000000000000000003"
    assert enabled_launch_pool_adapter(None) is None
    assert enabled_launch_pool_adapter(("0x0000000000000000000000000000000000000000", False)) is None
    assert enabled_launch_pool_adapter((adapter, True)) is None
    assert enabled_launch_pool_adapter((adapter, False)) == adapter


def test_v4_calldata_has_the_exact_selector_and_decoded_ten_field_payload():
    request = _v4_request()
    calldata = build_unified_launch_calldata(request)

    expected_selector = keccak(text=f"launch({LAUNCH_REQUEST_TYPE})")[:4]
    assert calldata[:4] == expected_selector
    decoded = abi_decode([LAUNCH_REQUEST_TYPE], calldata[4:])[0]
    assert decoded[0] == CREATOR
    assert decoded[1] == UNISWAP_V4_V3_POOL_TYPE
    assert decoded[4][0] == LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2
    assert decoded[4][1] == b""

    pool = abi_decode([V4_POOL_CONFIG_V2_TYPE], decoded[5])[0]
    assert len(decoded[5]) == 320
    assert pool == (
        PAIRED_TOKEN,
        int(AbyssPoolProfile.QUOTE_ORACLE),
        ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
        -887_160,
        -120_000,
        1 << 96,
        999_998,
        900_000_000 * 10**18,
        3_000,
        True,
    )



def test_abyss_calldata_round_trips_the_nine_field_pool_config():
    atomic = _atomic_request()
    request = to_unified_launch_request(
        atomic,
        UnifiedLaunchPool(kind=LaunchPoolKind.ABYSS, config=atomic.pool),
    )
    decoded = abi_decode([LAUNCH_REQUEST_TYPE], build_unified_launch_calldata(request)[4:])[0]
    assert decoded[1] == ABYSS_POOL_TYPE
    assert decoded[4][0] == LAUNCH_TOKEN_TYPE_BURNABLE_FIXED_V2
    assert abi_decode([ABYSS_POOL_CONFIG_TYPE], decoded[5])[0] == (
        PAIRED_TOKEN,
        False,
        int(AbyssPoolProfile.QUOTE_ORACLE),
        3_000,
        ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
        -120_000,
        1_000_000,
        900_000_000 * 10**18,
        0,
    )
    preserved = replace(
        request,
        pool_config=encode_abyss_pool_config(
            replace(atomic.pool, paired_token_amount_maximum=17)
        ),
    )
    preserved_pool = abi_decode(
        [ABYSS_POOL_CONFIG_TYPE],
        abi_decode(
            [LAUNCH_REQUEST_TYPE],
            build_unified_launch_calldata(preserved)[4:],
        )[0][5],
    )[0]
    assert preserved_pool[8] == 17

    with pytest.raises(ValueError, match="pool.fee"):
        build_unified_launch_calldata(
            replace(
                request,
                pool_config=encode_abyss_pool_config(replace(atomic.pool, fee=0)),
            )
        )



def test_v4_recipe_conversion_keeps_one_sided_band_and_split_rounding_headroom():
    base = _atomic_request().pool
    divisible = to_uniswap_v4_pool_config_v2(
        replace(base, liquidity=200),
        launch_sqrt_price_x96=1 << 96,
        external_liquidity_disabled=False,
    )
    nondivisible = to_uniswap_v4_pool_config_v2(
        replace(base, liquidity=201),
        launch_sqrt_price_x96=1 << 96,
        external_liquidity_disabled=False,
    )
    deployed_vector = to_uniswap_v4_pool_config_v2(
        replace(base, liquidity=2_570_896_757_742_785_419),
        launch_sqrt_price_x96=1 << 96,
        external_liquidity_disabled=True,
    )

    assert (divisible.tick_lower, divisible.tick_upper) == (-887_160, -120_000)
    assert divisible.liquidity == 198
    assert nondivisible.liquidity == 200
    assert deployed_vector.liquidity == 2_570_896_757_742_785_418
    with pytest.raises(ValueError, match="split rounding"):
        to_uniswap_v4_pool_config_v2(
            replace(base, liquidity=100),
            launch_sqrt_price_x96=1 << 96,
            external_liquidity_disabled=False,
        )


def test_native_value_uses_only_abyss_fee_plus_permitted_wrapped_native_buy():
    assert unified_launch_value("abyss", launch_fee=5, native_buy_amount=7) == 12
    assert unified_launch_value("uniswap-v4-v3", launch_fee=5, native_buy_amount=7) == 0

    atomic = replace(
        _atomic_request(),
        initial_buy=AtomicInitialBuy(
            paired_token_amount_in=0,
            launched_token_amount_out_minimum=0,
            sqrt_price_limit_x96=4_295_128_740,
        ),
    )
    abyss_request = to_unified_launch_request(
        atomic,
        UnifiedLaunchPool(kind=LaunchPoolKind.ABYSS, config=atomic.pool),
    )
    transaction = build_unified_launch_transaction(
        abyss_request,
        chain_id=4663,
        launch_fee=5,
        native_buy_amount=7,
        wrapped_native_token=PAIRED_TOKEN,
    )
    assert transaction["to"].lower() == CURRENT_LAUNCHER
    assert transaction["value"] == 12
    with pytest.raises(ValueError, match="launch_fee"):
        build_unified_launch_transaction(
            abyss_request,
            chain_id=4663,
            native_buy_amount=7,
            wrapped_native_token=PAIRED_TOKEN,
        )

    with pytest.raises(ValueError, match="wrapped_native_token"):
        build_unified_launch_calldata(
            abyss_request,
            native_buy_amount=1,
            wrapped_native_token="0x4000000000000000000000000000000000000004",
        )
    with pytest.raises(ValueError, match="zero native_buy_amount"):
        build_unified_launch_calldata(_v4_request(), native_buy_amount=1)


def test_atomic_conversion_pins_default_token_configs_but_generic_envelopes_stay_extensible():
    dual_dividend = _atomic_request(
        template_id=DUAL_DIVIDENDS_TEMPLATE_ID,
        token_kind=TokenKind.HOLDER_DIVIDEND,
        profile=AbyssPoolProfile.STANDARD_ORACLE,
        launched_fees=FeeDisposition(owner_bps=10_000, rewards_bps=0, burn_bps=0),
        paired_fees=FeeDisposition(owner_bps=10_000, rewards_bps=0, burn_bps=0),
    )
    dual_config = to_uniswap_v4_pool_config_v2(
        dual_dividend.pool,
        launch_sqrt_price_x96=1 << 96,
        external_liquidity_disabled=False,
    )
    converted = to_unified_launch_request(
        dual_dividend,
        UnifiedLaunchPool(kind=LaunchPoolKind.UNISWAP_V4_V3, config=dual_config),
    )
    assert converted.token.token_type == LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2
    assert converted.token.token_config == abi_encode(["bool"], [True])
    with pytest.raises(ValueError, match="known template"):
        build_unified_launch_calldata(
            replace(
                converted,
                token=replace(converted.token, token_config=abi_encode(["bool"], [False])),
            )
        )
    with pytest.raises(ValueError, match="Pool profile"):
        build_unified_launch_calldata(
            replace(
                converted,
                pool_config=encode_uniswap_v4_pool_config_v2(
                    replace(dual_config, profile=AbyssPoolProfile.QUOTE_ORACLE)
                ),
            )
        )

    base_request = _v4_request()
    generic = replace(
        base_request,
        token=UnifiedTokenConfig(
            token_type=b"\xaa" * 32,
            token_config=b"registered-custom-token-config",
            name="Custom",
            symbol="CUSTOM",
            decimals=18,
            supply=SUPPLY,
        ),
    )
    generic_decoded = abi_decode(
        [LAUNCH_REQUEST_TYPE],
        build_unified_launch_calldata(generic)[4:],
    )[0]
    assert generic_decoded[4][0] == b"\xaa" * 32
    assert generic_decoded[4][1] == b"registered-custom-token-config"

    holder = replace(
        base_request,
        token=UnifiedTokenConfig(
            token_type=LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2,
            token_config=abi_encode(["bool"], [False]),
            name="Holder",
            symbol="HOLDER",
            decimals=18,
            supply=SUPPLY,
        ),
    )
    holder_decoded = abi_decode(
        [LAUNCH_REQUEST_TYPE],
        build_unified_launch_calldata(holder)[4:],
    )[0]
    assert holder_decoded[4][0] == LAUNCH_TOKEN_TYPE_HOLDER_DIVIDEND_V2
    assert holder_decoded[4][1] == abi_encode(["bool"], [False])

    with pytest.raises(ValueError, match="burnable token_config"):
        build_unified_launch_calldata(
            replace(
                base_request,
                token=replace(base_request.token, token_config=b"unexpected"),
            )
        )
    with pytest.raises(ValueError, match="holder-dividend token_config"):
        build_unified_launch_calldata(
            replace(holder, token=replace(holder.token, token_config=b"\x00" * 31))
        )
    with pytest.raises(ValueError, match="paired_token_fees"):
        build_unified_launch_calldata(
            replace(
                base_request,
                paired_token_fees=FeeDisposition(
                    owner_bps=0,
                    rewards_bps=0,
                    burn_bps=0,
                ),
            )
        )

    mismatched_atomic = replace(
        _atomic_request(),
        token=AtomicTokenConfig(
            kind=TokenKind.HOLDER_DIVIDEND,
            name="Wrong",
            symbol="WRONG",
            decimals=18,
            supply=SUPPLY,
        ),
    )
    with pytest.raises(ValueError, match="Token kind"):
        to_unified_launch_request(
            mismatched_atomic,
            UnifiedLaunchPool(kind=LaunchPoolKind.ABYSS, config=mismatched_atomic.pool),
        )
    atomic = _atomic_request()
    abyss_request = to_unified_launch_request(
        atomic,
        UnifiedLaunchPool(kind=LaunchPoolKind.ABYSS, config=atomic.pool),
    )
    with pytest.raises(ValueError, match="orientation"):
        build_unified_launch_calldata(
            replace(
                abyss_request,
                pool_config=encode_abyss_pool_config(
                    replace(atomic.pool, launched_token_is_quote=True)
                ),
            )
        )


def test_v4_builder_enforces_local_bounds_but_not_a_stale_fee_catalog():
    request = _v4_request()
    dynamic_fee_config = to_uniswap_v4_pool_config_v2(
        _atomic_request().pool,
        launch_sqrt_price_x96=1 << 96,
        external_liquidity_disabled=False,
    )
    dynamic_fee_request = to_unified_launch_request(
        _atomic_request(),
        UnifiedLaunchPool(
            kind=LaunchPoolKind.UNISWAP_V4_V3,
            config=replace(dynamic_fee_config, abyss_fee_pips=123),
        ),
    )
    dynamic_decoded = abi_decode(
        [LAUNCH_REQUEST_TYPE],
        build_unified_launch_calldata(dynamic_fee_request)[4:],
    )[0]
    assert abi_decode([V4_POOL_CONFIG_V2_TYPE], dynamic_decoded[5])[0][8] == 123

    decoded_pool = abi_decode(
        [V4_POOL_CONFIG_V2_TYPE],
        abi_decode([LAUNCH_REQUEST_TYPE], build_unified_launch_calldata(request)[4:])[0][5],
    )[0]
    invalid_fee = replace(
        dynamic_fee_request,
        pool_config=abi_encode(
            [V4_POOL_CONFIG_V2_TYPE],
            [_v4_config_values(replace(dynamic_fee_config, abyss_fee_pips=0))],
        ),
    )
    with pytest.raises(ValueError, match="between 1"):
        build_unified_launch_calldata(invalid_fee)
    with pytest.raises(ValueError, match="at least 100"):
        build_unified_launch_calldata(
            replace(
                request,
                pool_config=abi_encode(
                    [V4_POOL_CONFIG_V2_TYPE],
                    [
                        (
                            decoded_pool[0],
                            decoded_pool[1],
                            decoded_pool[2],
                            decoded_pool[3],
                            decoded_pool[4],
                            decoded_pool[5],
                            99,
                            decoded_pool[7],
                            decoded_pool[8],
                            decoded_pool[9],
                        )
                    ],
                ),
            )
        )
    with pytest.raises(ValueError, match="320 bytes"):
        build_unified_launch_calldata(replace(request, pool_config=b"\x00" * 319))
    with pytest.raises(ValueError, match="boolean"):
        encode_uniswap_v4_pool_config_v2(
            replace(dynamic_fee_config, external_liquidity_disabled=1)
        )
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        build_unified_launch_calldata(
            replace(request, token=replace(request.token, token_type=b"\x01" * 31))
        )
    with pytest.raises(ValueError, match="creator"):
        build_unified_launch_calldata(
            replace(request, creator="0x0000000000000000000000000000000000000000")
        )
    with pytest.raises(ValueError, match="token.name"):
        build_unified_launch_calldata(
            replace(request, token=replace(request.token, name=""))
        )
    with pytest.raises(ValueError, match="token.symbol"):
        build_unified_launch_calldata(
            replace(request, token=replace(request.token, symbol=""))
        )



def test_known_launch_prices_reject_equal_initial_buy_limits():
    with pytest.raises(ValueError, match="swap direction"):
        build_unified_launch_calldata(
            replace(
                _v4_request(),
                initial_buy=AtomicInitialBuy(
                    paired_token_amount_in=1,
                    launched_token_amount_out_minimum=0,
                    sqrt_price_limit_x96=1 << 96,
                ),
            )
        )

    atomic = replace(
        _atomic_request(),
        initial_buy=AtomicInitialBuy(
            paired_token_amount_in=1,
            launched_token_amount_out_minimum=0,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(-120_000),
        ),
    )
    abyss_request = to_unified_launch_request(
        atomic,
        UnifiedLaunchPool(kind=LaunchPoolKind.ABYSS, config=atomic.pool),
    )
    with pytest.raises(ValueError, match="swap direction"):
        build_unified_launch_calldata(abyss_request)


def test_transaction_target_rejects_retired_or_unknown_and_allows_explicit_override():
    request = _v4_request()
    with pytest.raises(ValueError, match="retired"):
        build_unified_launch_transaction(
            request,
            chain_id=4663,
            launch_factory=SCHEMA_ONE_LAUNCHER,
        )
    with pytest.raises(ValueError, match="Unsupported|configured"):
        build_unified_launch_transaction(
            request,
            chain_id=99_999,
            launch_factory=CURRENT_LAUNCHER,
        )
    override = build_unified_launch_transaction(
        request,
        chain_id=46631,
        launch_factory=CURRENT_LAUNCHER,
    )
    assert override["to"].lower() == CURRENT_LAUNCHER
    with pytest.raises(ValueError, match="configured"):
        build_unified_launch_transaction(request, chain_id=46631)


def test_v4_unsigned_transaction_is_eth_account_signable_offline():
    transaction = build_unified_launch_transaction(_v4_request(), chain_id=4663)
    assert transaction["to"] == to_checksum_address(CURRENT_LAUNCHER)

    signed = Account.from_key("0x" + "11" * 32).sign_transaction(
        {
            **transaction,
            "nonce": 0,
            "gas": 500_000,
            "maxFeePerGas": 2_000_000_000,
            "maxPriorityFeePerGas": 1_000_000_000,
        }
    )
    assert signed.raw_transaction


def test_receipt_and_v4_pool_data_decoders_keep_each_contract_field_readable():
    hook = "0x" + "55" * 20
    locker = "0x" + "66" * 20
    distributor = "0x" + "77" * 20
    abyss_pool = "0x" + "88" * 20
    token = "0x" + "99" * 20
    rewards = "0x" + "aa" * 20
    splitter = "0x" + "bb" * 20
    pool_data = abi_encode(
        [V4_POOL_DATA_TYPE],
        [(hook, locker, distributor, abyss_pool, 99, 1, 900, 9)],
    )
    receipt_data = abi_encode(
        [LAUNCH_RECEIPT_TYPE],
        [
            (
                token,
                hook,
                42,
                1_000,
                0,
                7,
                11,
                rewards,
                splitter,
                hook,
                pool_data,
            )
        ],
    )

    receipt = decode_unified_launch_receipt(receipt_data)
    data = decode_v4_pool_data(receipt.pool_data)
    assert receipt.token_id == 42
    assert receipt.initial_buy_paired_token_amount == 7
    assert data.hook == hook
    assert data.v4_liquidity_locker == locker
    assert data.abyss_liquidity == 1
    assert data.uniswap_token_amount == 900
