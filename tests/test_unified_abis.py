"""Contract-boundary tests for schema-/2 unified ABI fragments."""

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

from black_market_sdk.unified_abis import (
    ABYSS_LAUNCH_POOL_ADAPTER_V3_ABI,
    ABYSS_STATIC_FEE_HOOK_DEPLOYER_V2_ABI,
    ABYSS_STATIC_FEE_HOOK_DEPLOYER_V3_ABI,
    LAUNCH_POOL_REGISTRY_V3_ABI,
    LAUNCH_REQUEST_COMPONENTS_V3,
    TOKEN_CONFIG_COMPONENTS_V3,
    UNIFIED_LAUNCHER_ABI,
    UNISWAP_V4_LAUNCH_POOL_ADAPTER_V3_ABI,
    UNISWAP_V4_POOL_CONFIG_COMPONENTS_V2,
    V4_POOL_DATA_COMPONENTS_V3,
)


def _entry(abi, entry_type, name):
    return next(entry for entry in abi if entry["type"] == entry_type and entry["name"] == name)


def test_v4_v3_uses_the_exact_ten_word_config_payload():
    assert [(field["name"], field["type"]) for field in UNISWAP_V4_POOL_CONFIG_COMPONENTS_V2] == [
        ("pairedToken", "address"),
        ("profile", "uint8"),
        ("oracleConfigId", "bytes32"),
        ("tickLower", "int24"),
        ("tickUpper", "int24"),
        ("sqrtPriceX96", "uint160"),
        ("liquidity", "uint128"),
        ("launchedTokenAmountMaximum", "uint256"),
        ("abyssFeePips", "uint24"),
        ("externalLiquidityDisabled", "bool"),
    ]
    assert len(UNISWAP_V4_POOL_CONFIG_COMPONENTS_V2) == 10
    assert "protocolBps" not in {
        field["name"] for field in UNISWAP_V4_POOL_CONFIG_COMPONENTS_V2
    }


def test_unified_launcher_exposes_the_v3_envelope_and_canonical_events():
    launch = _entry(UNIFIED_LAUNCHER_ABI, "function", "launch")
    assert launch["stateMutability"] == "payable"
    expected_request = [
        ("creator", "address"),
        ("poolType", "bytes32"),
        ("templateId", "bytes32"),
        ("templateVersion", "uint32"),
        ("token", "tuple"),
        ("poolConfig", "bytes"),
        ("initialBuy", "tuple"),
        ("launchedTokenFees", "tuple"),
        ("pairedTokenFees", "tuple"),
        ("deadline", "uint256"),
    ]
    assert [(field["name"], field["type"]) for field in launch["inputs"][0]["components"]] == (
        expected_request
    )
    assert [(field["name"], field["type"]) for field in LAUNCH_REQUEST_COMPONENTS_V3] == (
        expected_request
    )
    assert [(field["name"], field["type"]) for field in TOKEN_CONFIG_COMPONENTS_V3] == [
        ("tokenType", "bytes32"),
        ("tokenConfig", "bytes"),
        ("name", "string"),
        ("symbol", "string"),
        ("decimals", "uint8"),
        ("supply", "uint256"),
    ]

    completed = _entry(UNIFIED_LAUNCHER_ABI, "event", "LaunchCompleted")
    assert [(field["name"], field["indexed"]) for field in completed["inputs"][:4]] == [
        ("poolType", True),
        ("token", True),
        ("creator", True),
        ("pool", False),
    ]
    assert not any(
        entry["type"] == "function" and entry["name"] == "execute"
        for entry in UNIFIED_LAUNCHER_ABI
    )


def _abi_signature(entry):
    return f"{entry['name']}({','.join(argument['type'] for argument in entry['inputs'])})"


def test_unified_launcher_decodes_delegate_fee_event_and_bubbled_error_selectors():
    fee_paid = _entry(UNIFIED_LAUNCHER_ABI, "event", "LaunchFeePaid")
    assert _abi_signature(fee_paid) == "LaunchFeePaid(address,address,address,uint256)"
    assert keccak(text=_abi_signature(fee_paid)) == keccak(
        text="LaunchFeePaid(address,address,address,uint256)"
    )
    assert [
        (argument["name"], argument["type"], argument["indexed"])
        for argument in fee_paid["inputs"]
    ] == [
        ("token", "address", True),
        ("payer", "address", True),
        ("recipient", "address", True),
        ("amount", "uint256", False),
    ]
    unindexed_types = [
        argument["type"] for argument in fee_paid["inputs"] if not argument["indexed"]
    ]
    assert abi_decode(unindexed_types, abi_encode(unindexed_types, [500_000])) == (500_000,)

    errors = {
        entry["name"]: entry for entry in UNIFIED_LAUNCHER_ABI if entry["type"] == "error"
    }
    assert {
        "InvalidPoolType",
        "ApprovalMismatch",
        "DeadlineExpired",
        "DuplicateLaunch",
        "IncorrectLaunchFee",
        "LaunchFeePaymentFailed",
        "MiningExhausted",
        "SlippageExceeded",
    } <= set(errors)
    assert _abi_signature(errors["InvalidPoolType"]) == "InvalidPoolType()"
    assert keccak(text=_abi_signature(errors["InvalidPoolType"]))[:4] == keccak(
        text="InvalidPoolType()"
    )[:4]
    assert _abi_signature(errors["IncorrectLaunchFee"]) == "IncorrectLaunchFee(uint256,uint256)"
    assert keccak(text=_abi_signature(errors["IncorrectLaunchFee"]))[:4] == keccak(
        text="IncorrectLaunchFee(uint256,uint256)"
    )[:4]
    assert _abi_signature(errors["MiningExhausted"]) == "MiningExhausted(uint256,uint256)"
    assert keccak(text=_abi_signature(errors["MiningExhausted"]))[:4] == keccak(
        text="MiningExhausted(uint256,uint256)"
    )[:4]


def test_registry_and_adapter_introspection_surfaces_preserve_each_route():
    pool_types = _entry(LAUNCH_POOL_REGISTRY_V3_ABI, "function", "poolTypes")
    assert [(field["name"], field["type"]) for field in pool_types["outputs"]] == [
        ("adapter", "address"),
        ("disabled", "bool"),
    ]
    assert _entry(LAUNCH_POOL_REGISTRY_V3_ABI, "function", "adapter")["stateMutability"] == "view"

    abyss_fee = _entry(ABYSS_LAUNCH_POOL_ADAPTER_V3_ABI, "function", "launchFee")
    assert abyss_fee["outputs"] == [{"name": "", "type": "uint256"}]
    v4_adapter = UNISWAP_V4_LAUNCH_POOL_ADAPTER_V3_ABI
    assert _entry(v4_adapter, "function", "poolType")["outputs"] == [
        {"name": "", "type": "bytes32"}
    ]
    assert _entry(v4_adapter, "function", "v4LiquidityLocker")["stateMutability"] == "view"


def test_static_fee_hook_deployer_v3_preserves_v2_surface_and_adds_mining_error():
    expected_pool_key = [
        ("currency0", "address"),
        ("currency1", "address"),
        ("fee", "uint24"),
        ("tickSpacing", "int24"),
        ("hooks", "address"),
    ]
    expected_hook_config = [
        ("abyssFeePips", "uint24"),
        ("quoteCurrency", "address"),
        ("feeMode", "uint8"),
        ("externalLiquidityDisabled", "bool"),
        ("protocolFeeDenominator", "uint8"),
        ("launchFactory", "address"),
        ("liquidityLocker", "address"),
        ("feeVault", "address"),
        ("splitter", "address"),
        ("maxAbsTickMove", "uint24"),
        ("oracleCardinality", "uint16"),
    ]
    for abi in (
        ABYSS_STATIC_FEE_HOOK_DEPLOYER_V2_ABI,
        ABYSS_STATIC_FEE_HOOK_DEPLOYER_V3_ABI,
    ):
        assert {
            entry["name"] for entry in abi if entry["type"] == "function"
        } == {"deploy", "deployMined", "predict"}
        deploy = _entry(abi, "function", "deploy")
        assert [(argument["name"], argument["type"]) for argument in deploy["inputs"]] == [
            ("poolManager", "address"),
            ("key", "tuple"),
            ("config", "tuple"),
            ("salt", "bytes32"),
        ]
        assert [
            (field["name"], field["type"]) for field in deploy["inputs"][1]["components"]
        ] == expected_pool_key
        assert [
            (field["name"], field["type"]) for field in deploy["inputs"][2]["components"]
        ] == expected_hook_config
        mined = _entry(abi, "function", "deployMined")
        assert [(argument["name"], argument["type"]) for argument in mined["inputs"]] == [
            ("poolManager", "address"),
            ("key", "tuple"),
            ("config", "tuple"),
        ]
        assert mined["outputs"] == [{"name": "hook", "type": "address"}]
        predicted = _entry(abi, "function", "predict")
        assert [(argument["name"], argument["type"]) for argument in predicted["inputs"]] == [
            ("poolManager", "address"),
            ("key", "tuple"),
            ("config", "tuple"),
            ("salt", "bytes32"),
        ]
        assert predicted["outputs"] == [{"name": "", "type": "address"}]
        assert predicted["stateMutability"] == "view"
        assert _abi_signature(_entry(abi, "event", "HookDeployed")) == (
            "HookDeployed(address,bytes32,bytes32)"
        )

    v2_errors = {
        entry["name"] for entry in ABYSS_STATIC_FEE_HOOK_DEPLOYER_V2_ABI if entry["type"] == "error"
    }
    v3_errors = {
        entry["name"] for entry in ABYSS_STATIC_FEE_HOOK_DEPLOYER_V3_ABI if entry["type"] == "error"
    }
    assert v2_errors == {"InvalidHookAddress"}
    assert v3_errors == {"InvalidHookAddress", "InvalidSearchRange", "MiningExhausted"}


def test_v4_pool_data_is_the_current_eight_field_receipt_detail():
    assert [(field["name"], field["type"]) for field in V4_POOL_DATA_COMPONENTS_V3] == [
        ("hook", "address"),
        ("v4LiquidityLocker", "address"),
        ("abyssBonusDistributor", "address"),
        ("abyssPool", "address"),
        ("uniswapLiquidity", "uint128"),
        ("abyssLiquidity", "uint128"),
        ("uniswapTokenAmount", "uint256"),
        ("abyssTokenAmount", "uint256"),
    ]
