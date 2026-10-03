"""ABIs for the schema-/2 UnifiedLauncher deployment stack.

The constants in this module are public ABI fragments suitable for
``web3.eth.contract(abi=...)``. They are transcribed from the current
``contracts/out`` artifacts and V3 launch structs. ``UNIFIED_LAUNCHER_ABI`` also
includes delegate-emitted logs and bubbled launch-path errors, because they are
observed at the launcher address. The optimized Uniswap V4 V3 route uses the
10-word ``V4PoolConfigV2`` payload; the retired 11-field V1 shape is not
represented here.
"""

from __future__ import annotations


def _argument(name, abi_type, *, components=None, indexed=None):
    value = {"name": name, "type": abi_type}
    if components is not None:
        value["components"] = components
    if indexed is not None:
        value["indexed"] = indexed
    return value


def _function(name, inputs=(), outputs=(), state_mutability="nonpayable"):
    return {
        "type": "function",
        "name": name,
        "stateMutability": state_mutability,
        "inputs": list(inputs),
        "outputs": list(outputs),
    }


def _event(name, inputs):
    return {"type": "event", "name": name, "inputs": list(inputs), "anonymous": False}


def _error(name, inputs=()):
    return {"type": "error", "name": name, "inputs": list(inputs)}


# contracts/src/launch/v3/LaunchTypesV3.sol
TOKEN_CONFIG_COMPONENTS_V3 = [
    _argument("tokenType", "bytes32"),
    _argument("tokenConfig", "bytes"),
    _argument("name", "string"),
    _argument("symbol", "string"),
    _argument("decimals", "uint8"),
    _argument("supply", "uint256"),
]

INITIAL_BUY_COMPONENTS_V3 = [
    _argument("pairedTokenAmountIn", "uint256"),
    _argument("launchedTokenAmountOutMinimum", "uint256"),
    _argument("sqrtPriceLimitX96", "uint160"),
]

DISPOSITION_COMPONENTS_V3 = [
    _argument("ownerBps", "uint16"),
    _argument("rewardsBps", "uint16"),
    _argument("burnBps", "uint16"),
]

ABYSS_POOL_CONFIG_COMPONENTS_V3 = [
    _argument("pairedToken", "address"),
    _argument("launchedTokenIsQuote", "bool"),
    _argument("profile", "uint8"),
    _argument("fee", "uint24"),
    _argument("oracleConfigId", "bytes32"),
    _argument("launchTick", "int24"),
    _argument("liquidity", "uint128"),
    _argument("launchedTokenAmountMaximum", "uint256"),
    _argument("pairedTokenAmountMaximum", "uint256"),
]

# contracts/src/launch/v3/V4PoolConfigV2.sol.  This is deliberately ten fields
# (320 static bytes), including ``externalLiquidityDisabled`` as the final word.
UNISWAP_V4_POOL_CONFIG_COMPONENTS_V2 = [
    _argument("pairedToken", "address"),
    _argument("profile", "uint8"),
    _argument("oracleConfigId", "bytes32"),
    _argument("tickLower", "int24"),
    _argument("tickUpper", "int24"),
    _argument("sqrtPriceX96", "uint160"),
    _argument("liquidity", "uint128"),
    _argument("launchedTokenAmountMaximum", "uint256"),
    _argument("abyssFeePips", "uint24"),
    _argument("externalLiquidityDisabled", "bool"),
]

V4_POOL_DATA_COMPONENTS_V3 = [
    _argument("hook", "address"),
    _argument("v4LiquidityLocker", "address"),
    _argument("abyssBonusDistributor", "address"),
    _argument("abyssPool", "address"),
    _argument("uniswapLiquidity", "uint128"),
    _argument("abyssLiquidity", "uint128"),
    _argument("uniswapTokenAmount", "uint256"),
    _argument("abyssTokenAmount", "uint256"),
]

LAUNCH_REQUEST_COMPONENTS_V3 = [
    _argument("creator", "address"),
    _argument("poolType", "bytes32"),
    _argument("templateId", "bytes32"),
    _argument("templateVersion", "uint32"),
    _argument("token", "tuple", components=TOKEN_CONFIG_COMPONENTS_V3),
    _argument("poolConfig", "bytes"),
    _argument("initialBuy", "tuple", components=INITIAL_BUY_COMPONENTS_V3),
    _argument("launchedTokenFees", "tuple", components=DISPOSITION_COMPONENTS_V3),
    _argument("pairedTokenFees", "tuple", components=DISPOSITION_COMPONENTS_V3),
    _argument("deadline", "uint256"),
]

LAUNCH_RECEIPT_COMPONENTS_V3 = [
    _argument("token", "address"),
    _argument("pool", "address"),
    _argument("tokenId", "uint256"),
    _argument("liquidityLaunchedTokenAmount", "uint256"),
    _argument("liquidityPairedTokenAmount", "uint256"),
    _argument("initialBuyPairedTokenAmount", "uint256"),
    _argument("initialBuyLaunchedTokenAmount", "uint256"),
    _argument("rewards", "address"),
    _argument("splitter", "address"),
    _argument("feeClaimer", "address"),
    _argument("poolData", "bytes"),
]


# contracts/src/launch/v2/ILaunchTokenDeployerV2.sol and LaunchTokenFactoryV2.sol
TOKEN_DEPLOYMENT_SPEC_COMPONENTS_V2 = [
    _argument("creator", "address"),
    _argument("recipient", "address"),
    _argument("name", "string"),
    _argument("symbol", "string"),
    _argument("decimals", "uint8"),
    _argument("supply", "uint256"),
    _argument("pairedToken", "address"),
    _argument("templateConfig", "bytes"),
]

TOKEN_LAUNCH_CONTEXT_COMPONENTS_V2 = [
    _argument("protocolAdmin", "address"),
    _argument("pool", "address"),
    _argument("splitter", "address"),
    _argument("feeClaimer", "address"),
    _argument("rewardsDistributor", "address"),
    _argument("exclusions", "address[]"),
]

TOKEN_TYPE_COMPONENTS_V2 = [
    _argument("deployer", "address"),
    _argument("capabilities", "uint256"),
    _argument("disabled", "bool"),
]

LAUNCH_TEMPLATE_CONFIG_COMPONENTS_V2 = [
    _argument("tokenType", "bytes32"),
    _argument("tokenConfig", "bytes"),
    _argument("requiredCapabilities", "uint256"),
    _argument("poolProfileMask", "uint8"),
    _argument("rewardMode", "uint8"),
    _argument("feeAssetMode", "uint8"),
    _argument("rewardDuration", "uint32"),
    _argument("launchedTokenIsQuote", "bool"),
    _argument("launchedTokenDestinations", "uint8"),
    _argument("pairedTokenDestinations", "uint8"),
]


# contracts/out/UnifiedLauncher.sol/UnifiedLauncher.json
UNIFIED_LAUNCHER_ABI = [
    _function(
        "launch",
        [_argument("request", "tuple", components=LAUNCH_REQUEST_COMPONENTS_V3)],
        [_argument("receipt", "tuple", components=LAUNCH_RECEIPT_COMPONENTS_V3)],
        "payable",
    ),
    _function("poolRegistry", (), [_argument("", "address")], "view"),
    _function(
        "unlockCallback",
        [_argument("data", "bytes")],
        [_argument("", "bytes")],
    ),
    _event(
        "LaunchCompleted",
        [
            _argument("poolType", "bytes32", indexed=True),
            _argument("token", "address", indexed=True),
            _argument("creator", "address", indexed=True),
            _argument("pool", "address", indexed=False),
            _argument("templateId", "bytes32", indexed=False),
            _argument("templateVersion", "uint32", indexed=False),
            _argument("tokenId", "uint256", indexed=False),
            _argument("liquidityLaunchedTokenAmount", "uint256", indexed=False),
            _argument("liquidityPairedTokenAmount", "uint256", indexed=False),
            _argument("initialBuyPairedTokenAmount", "uint256", indexed=False),
            _argument("initialBuyLaunchedTokenAmount", "uint256", indexed=False),
        ],
    ),
    _event(
        "LaunchModulesDeployed",
        [
            _argument("token", "address", indexed=True),
            _argument("rewards", "address", indexed=False),
            _argument("splitter", "address", indexed=False),
            _argument("feeClaimer", "address", indexed=False),
        ],
    ),
    # The Abyss adapter executes by delegatecall, so its log is emitted at the
    # UnifiedLauncher address and must be decodable with the launcher surface.
    _event(
        "LaunchFeePaid",
        [
            _argument("token", "address", indexed=True),
            _argument("payer", "address", indexed=True),
            _argument("recipient", "address", indexed=True),
            _argument("amount", "uint256", indexed=False),
        ],
    ),
    # Own, registry, and delegate-emitted engine errors.  The launch path
    # bubbles these selectors verbatim; adapter `execute` is deliberately not
    # exposed as a callable launcher function.
    _error("Unauthorized"),
    _error("Reentrancy"),
    _error("InvalidPoolType"),
    _error("ApprovalMismatch"),
    _error("DeadlineExpired"),
    _error("DirectCallForbidden"),
    _error("DuplicateLaunch"),
    _error("ExistingPool"),
    _error("InexactTransfer"),
    _error("InvalidBinding"),
    _error("InvalidConfiguration"),
    _error("InvalidSwapResult"),
    _error("InvalidTick"),
    _error("InvalidTemplate"),
    _error("SlippageExceeded"),
    _error("TokenConfigMismatch"),
    _error("UnsupportedTemplate"),
    _error(
        "IncorrectLaunchFee",
        [_argument("provided", "uint256"), _argument("required", "uint256")],
    ),
    _error("LaunchFeePaymentFailed"),
    _error(
        "MiningExhausted",
        [_argument("start", "uint256"), _argument("attempts", "uint256")],
    ),
]


# contracts/out/LaunchPoolRegistryV3.sol/LaunchPoolRegistryV3.json
LAUNCH_POOL_REGISTRY_V3_ABI = [
    _function(
        "adapter",
        [_argument("poolType", "bytes32")],
        [_argument("result", "address")],
        "view",
    ),
    _function("disablePoolType", [_argument("poolType", "bytes32")]),
    _function(
        "poolTypes",
        [_argument("poolType", "bytes32")],
        [_argument("adapter", "address"), _argument("disabled", "bool")],
        "view",
    ),
    _function(
        "registerPoolType",
        [_argument("poolType", "bytes32"), _argument("adapter_", "address")],
    ),
    _function("registrar", (), [_argument("", "address")], "view"),
    _event("PoolTypeDeactivated", [_argument("poolType", "bytes32", indexed=True)]),
    _event(
        "PoolTypeRegistered",
        [
            _argument("poolType", "bytes32", indexed=True),
            _argument("adapter", "address", indexed=True),
        ],
    ),
    _error("InvalidPoolType"),
    _error("PoolTypeAlreadyRegistered"),
    _error("Unauthorized"),
]


# contracts/out/AbyssLaunchPoolAdapterV3.sol/AbyssLaunchPoolAdapterV3.json
ABYSS_LAUNCH_POOL_ADAPTER_V3_ABI = [
    _function("DUAL_DIVIDENDS_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
    _function("DUAL_STAKING_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
    _function("FEE_BURN_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
    _function("INITIAL_TEMPLATE_VERSION", (), [_argument("", "uint32")], "view"),
    _function("QUOTE_DIVIDENDS_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
    _function("QUOTE_STAKING_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
    _function("STANDARD_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
    _function("abyssFactory", (), [_argument("", "address")], "view"),
    _function("coordinator", (), [_argument("", "address")], "view"),
    _function(
        "execute",
        [_argument("request", "tuple", components=LAUNCH_REQUEST_COMPONENTS_V3)],
        [_argument("receipt", "tuple", components=LAUNCH_RECEIPT_COMPONENTS_V3)],
        "payable",
    ),
    _function("feeOwnerRegistry", (), [_argument("", "address")], "view"),
    _function("launchFee", (), [_argument("", "uint256")], "view"),
    _function("launchFeeRecipient", (), [_argument("", "address")], "view"),
    _function("moduleFactory", (), [_argument("", "address")], "view"),
    _function("poolType", (), [_argument("", "bytes32")], "view"),
    _function("positionLocker", (), [_argument("", "address")], "view"),
    _function("registry", (), [_argument("", "address")], "view"),
    _function("router", (), [_argument("", "address")], "view"),
    _function("templateRegistry", (), [_argument("", "address")], "view"),
    _function("tokenFactory", (), [_argument("", "address")], "view"),
    _function("wrappedNative", (), [_argument("", "address")], "view"),
    _event(
        "LaunchFeePaid",
        [
            _argument("token", "address", indexed=True),
            _argument("payer", "address", indexed=True),
            _argument("recipient", "address", indexed=True),
            _argument("amount", "uint256", indexed=False),
        ],
    ),
    _error("ApprovalMismatch"),
    _error("DirectCallForbidden"),
    _error("ExistingPool"),
    _error(
        "IncorrectLaunchFee",
        [_argument("provided", "uint256"), _argument("required", "uint256")],
    ),
    _error("InexactTransfer"),
    _error("InvalidBinding"),
    _error("InvalidConfiguration"),
    _error("InvalidSwapResult"),
    _error("InvalidTick"),
    _error("LaunchFeePaymentFailed"),
    _error("TokenConfigMismatch"),
    _error("UnsupportedTemplate"),
]


def _uniswap_v4_launch_pool_adapter_abi():
    """Return the exact ABI surface of the V4 V3 launch pool adapter."""

    return [
        _function("ABYSS_HOLDBACK_BPS", (), [_argument("", "uint256")], "view"),
        _function("DUAL_DIVIDENDS_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
        _function("DUAL_STAKING_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
        _function("FEE_BURN_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
        _function("QUOTE_DIVIDENDS_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
        _function("QUOTE_STAKING_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
        _function("STANDARD_TEMPLATE_ID", (), [_argument("", "bytes32")], "view"),
        _function("abyssBonusDistributor", (), [_argument("", "address")], "view"),
        _function("abyssCoordinator", (), [_argument("", "address")], "view"),
        _function("abyssFactory", (), [_argument("", "address")], "view"),
        _function(
            "execute",
            [_argument("request", "tuple", components=LAUNCH_REQUEST_COMPONENTS_V3)],
            [_argument("receipt", "tuple", components=LAUNCH_RECEIPT_COMPONENTS_V3)],
            "payable",
        ),
        _function("feeOwnerRegistry", (), [_argument("", "address")], "view"),
        _function("hookDeployer", (), [_argument("", "address")], "view"),
        _function("moduleFactory", (), [_argument("", "address")], "view"),
        _function("poolManager", (), [_argument("", "address")], "view"),
        _function("poolType", (), [_argument("", "bytes32")], "view"),
        _function("registry", (), [_argument("", "address")], "view"),
        _function("templateRegistry", (), [_argument("", "address")], "view"),
        _function("tokenFactory", (), [_argument("", "address")], "view"),
        _function(
            "unlockCallback",
            [_argument("data", "bytes")],
            [_argument("result", "bytes")],
        ),
        _function("v4LiquidityLocker", (), [_argument("", "address")], "view"),
        _error("DeadlineExpired"),
        _error("DirectCallForbidden"),
        _error("InexactTransfer"),
        _error("InvalidBinding"),
        _error("InvalidConfiguration"),
        _error("InvalidTemplate"),
        _error("SlippageExceeded"),
        _error("TokenConfigMismatch"),
        _error("Unauthorized"),
    ]


# The optimized V3 adapter is the only current Uniswap V4 launch route surface.
UNISWAP_V4_LAUNCH_POOL_ADAPTER_V3_ABI = _uniswap_v4_launch_pool_adapter_abi()

# contracts/src/hooks/v4/AbyssStaticFeeHookDeployerV2.sol and
# AbyssStaticFeeHookDeployerV3.sol. V3 retains the V2 callable surface.
_V4_POOL_KEY_COMPONENTS = [
    _argument("currency0", "address"),
    _argument("currency1", "address"),
    _argument("fee", "uint24"),
    _argument("tickSpacing", "int24"),
    _argument("hooks", "address"),
]

_ABYSS_STATIC_FEE_HOOK_CONFIG_COMPONENTS_V2 = [
    _argument("abyssFeePips", "uint24"),
    _argument("quoteCurrency", "address"),
    _argument("feeMode", "uint8"),
    _argument("externalLiquidityDisabled", "bool"),
    _argument("protocolFeeDenominator", "uint8"),
    _argument("launchFactory", "address"),
    _argument("liquidityLocker", "address"),
    _argument("feeVault", "address"),
    _argument("splitter", "address"),
    _argument("maxAbsTickMove", "uint24"),
    _argument("oracleCardinality", "uint16"),
]


def _abyss_static_fee_hook_deployer_abi(*, v3: bool):
    abi = [
        _function(
            "deploy",
            [
                _argument("poolManager", "address"),
                _argument("key", "tuple", components=_V4_POOL_KEY_COMPONENTS),
                _argument(
                    "config", "tuple", components=_ABYSS_STATIC_FEE_HOOK_CONFIG_COMPONENTS_V2
                ),
                _argument("salt", "bytes32"),
            ],
            [_argument("hook", "address")],
        ),
        _function(
            "deployMined",
            [
                _argument("poolManager", "address"),
                _argument("key", "tuple", components=_V4_POOL_KEY_COMPONENTS),
                _argument(
                    "config", "tuple", components=_ABYSS_STATIC_FEE_HOOK_CONFIG_COMPONENTS_V2
                ),
            ],
            [_argument("hook", "address")],
        ),
        _function(
            "predict",
            [
                _argument("poolManager", "address"),
                _argument("key", "tuple", components=_V4_POOL_KEY_COMPONENTS),
                _argument(
                    "config", "tuple", components=_ABYSS_STATIC_FEE_HOOK_CONFIG_COMPONENTS_V2
                ),
                _argument("salt", "bytes32"),
            ],
            [_argument("", "address")],
            "view",
        ),
        _event(
            "HookDeployed",
            [
                _argument("hook", "address", indexed=True),
                _argument("salt", "bytes32", indexed=True),
                _argument("configHash", "bytes32", indexed=False),
            ],
        ),
        _error("InvalidHookAddress"),
    ]
    if v3:
        abi.extend(
            [
                _error("InvalidSearchRange"),
                _error(
                    "MiningExhausted",
                    [_argument("start", "uint256"), _argument("attempts", "uint256")],
                ),
            ]
        )
    return abi


ABYSS_STATIC_FEE_HOOK_DEPLOYER_V2_ABI = _abyss_static_fee_hook_deployer_abi(v3=False)
ABYSS_STATIC_FEE_HOOK_DEPLOYER_V3_ABI = _abyss_static_fee_hook_deployer_abi(v3=True)


# contracts/out/LaunchTokenFactoryV2.sol/LaunchTokenFactoryV2.json
LAUNCH_TOKEN_FACTORY_V2_ABI = [
    _function("DEPLOYMENT_SALT_DOMAIN", (), [_argument("", "bytes32")], "view"),
    _function(
        "deployAbove",
        [
            _argument("creator", "address"),
            _argument("pairedToken", "address"),
            _argument("tokenType", "bytes32"),
            _argument("name", "string"),
            _argument("symbol", "string"),
            _argument("decimals", "uint8"),
            _argument("supply", "uint256"),
            _argument("templateConfig", "bytes"),
            _argument("context", "tuple", components=TOKEN_LAUNCH_CONTEXT_COMPONENTS_V2),
        ],
        [_argument("token", "address")],
    ),
    _function(
        "deployBelow",
        [
            _argument("creator", "address"),
            _argument("pairedToken", "address"),
            _argument("tokenType", "bytes32"),
            _argument("name", "string"),
            _argument("symbol", "string"),
            _argument("decimals", "uint8"),
            _argument("supply", "uint256"),
            _argument("templateConfig", "bytes"),
            _argument("context", "tuple", components=TOKEN_LAUNCH_CONTEXT_COMPONENTS_V2),
        ],
        [_argument("token", "address")],
    ),
    _function(
        "deployedTokenType",
        [_argument("token", "address")],
        [_argument("tokenType", "bytes32")],
        "view",
    ),
    _function("deploymentNonce", (), [_argument("", "uint256")], "view"),
    _function("disableTokenType", [_argument("tokenType", "bytes32")]),
    _function(
        "launchAuthority",
        [_argument("token", "address")],
        [_argument("authority", "address")],
        "view",
    ),
    _function("launcher", (), [_argument("", "address")], "view"),
    _function(
        "predictAbove",
        [
            _argument("creator", "address"),
            _argument("pairedToken", "address"),
            _argument("tokenType", "bytes32"),
            _argument("name", "string"),
            _argument("symbol", "string"),
            _argument("decimals", "uint8"),
            _argument("supply", "uint256"),
            _argument("templateConfig", "bytes"),
        ],
        [_argument("token", "address"), _argument("nonce", "uint256")],
        "view",
    ),
    _function(
        "predictBelow",
        [
            _argument("creator", "address"),
            _argument("pairedToken", "address"),
            _argument("tokenType", "bytes32"),
            _argument("name", "string"),
            _argument("symbol", "string"),
            _argument("decimals", "uint8"),
            _argument("supply", "uint256"),
            _argument("templateConfig", "bytes"),
        ],
        [_argument("token", "address"), _argument("nonce", "uint256")],
        "view",
    ),
    _function(
        "registerTokenType",
        [
            _argument("tokenType", "bytes32"),
            _argument("deployer", "address"),
            _argument("capabilities", "uint256"),
        ],
    ),
    _function("registrar", (), [_argument("", "address")], "view"),
    _function("setLauncher", [_argument("launcher_", "address")]),
    _function(
        "supportsCapabilities",
        [_argument("tokenType", "bytes32"), _argument("required", "uint256")],
        [_argument("", "bool")],
        "view",
    ),
    _function(
        "tokenDeployer",
        [_argument("token", "address")],
        [_argument("deployer", "address")],
        "view",
    ),
    _function(
        "tokenTypes",
        [_argument("tokenType", "bytes32")],
        [
            _argument("deployer", "address"),
            _argument("capabilities", "uint256"),
            _argument("disabled", "bool"),
        ],
        "view",
    ),
    _event("LauncherConfigured", [_argument("launcher", "address", indexed=True)]),
    _event(
        "TokenDeployed",
        [
            _argument("token", "address", indexed=True),
            _argument("creator", "address", indexed=True),
            _argument("tokenType", "bytes32", indexed=True),
            _argument("deployer", "address", indexed=False),
            _argument("salt", "bytes32", indexed=False),
            _argument("nonce", "uint256", indexed=False),
            _argument("prevrandao", "uint256", indexed=False),
        ],
    ),
    _event("TokenTypeDeactivated", [_argument("tokenType", "bytes32", indexed=True)]),
    _event(
        "TokenTypeRegistered",
        [
            _argument("tokenType", "bytes32", indexed=True),
            _argument("deployer", "address", indexed=True),
            _argument("capabilities", "uint256", indexed=False),
        ],
    ),
    _error("AlreadyConfigured"),
    _error("InvalidDeployment"),
    _error("InvalidLauncher"),
    _error("InvalidTokenType"),
    _error("TokenTypeAlreadyRegistered"),
    _error("TokenTypeDisabled"),
    _error("Unauthorized"),
]


# contracts/out/BurnableTokenDeployerV2.sol/BurnableTokenDeployerV2.json and
# HolderDividendTokenDeployerV2.sol/HolderDividendTokenDeployerV2.json.
def _launch_token_deployer_v2_abi(context_name):
    return [
        _function(
            "deployToken",
            [
                _argument("salt", "bytes32"),
                _argument("spec", "tuple", components=TOKEN_DEPLOYMENT_SPEC_COMPONENTS_V2),
                _argument(
                    context_name,
                    "tuple",
                    components=TOKEN_LAUNCH_CONTEXT_COMPONENTS_V2,
                ),
            ],
            [_argument("token", "address")],
        ),
        _function("factory", (), [_argument("", "address")], "view"),
        _function(
            "predictToken",
            [
                _argument("salt", "bytes32"),
                _argument("spec", "tuple", components=TOKEN_DEPLOYMENT_SPEC_COMPONENTS_V2),
            ],
            [_argument("token", "address")],
            "view",
        ),
        _error("InvalidConfiguration"),
        _error("Unauthorized"),
    ]


BURNABLE_TOKEN_DEPLOYER_V2_ABI = _launch_token_deployer_v2_abi("")
HOLDER_DIVIDEND_TOKEN_DEPLOYER_V2_ABI = _launch_token_deployer_v2_abi("context")


# contracts/out/LaunchTemplateRegistryV2.sol/LaunchTemplateRegistryV2.json
LAUNCH_TEMPLATE_REGISTRY_V2_ABI = [
    _function("DESTINATION_BURN", (), [_argument("", "uint8")], "view"),
    _function("DESTINATION_OWNER", (), [_argument("", "uint8")], "view"),
    _function("DESTINATION_REWARDS", (), [_argument("", "uint8")], "view"),
    _function("INITIAL_VERSION", (), [_argument("", "uint32")], "view"),
    _function("REWARD_DURATION", (), [_argument("", "uint32")], "view"),
    _function(
        "isRegistered",
        [_argument("templateId", "bytes32"), _argument("version", "uint32")],
        [_argument("", "bool")],
        "view",
    ),
    _function(
        "registerTemplate",
        [
            _argument("templateId", "bytes32"),
            _argument("version", "uint32"),
            _argument("config", "tuple", components=LAUNCH_TEMPLATE_CONFIG_COMPONENTS_V2),
        ],
    ),
    _function("registrar", (), [_argument("", "address")], "view"),
    _function(
        "supportsDispositions",
        [
            _argument("config", "tuple", components=LAUNCH_TEMPLATE_CONFIG_COMPONENTS_V2),
            _argument("profile", "uint8"),
            _argument("launchedTokenFees", "tuple", components=DISPOSITION_COMPONENTS_V3),
            _argument("pairedTokenFees", "tuple", components=DISPOSITION_COMPONENTS_V3),
        ],
        [_argument("", "bool")],
        "pure",
    ),
    _function(
        "supportsPoolProfile",
        [
            _argument("config", "tuple", components=LAUNCH_TEMPLATE_CONFIG_COMPONENTS_V2),
            _argument("profile", "uint8"),
        ],
        [_argument("", "bool")],
        "pure",
    ),
    _function(
        "template",
        [_argument("templateId", "bytes32"), _argument("version", "uint32")],
        [_argument("config", "tuple", components=LAUNCH_TEMPLATE_CONFIG_COMPONENTS_V2)],
        "view",
    ),
    _function(
        "templateKey",
        [_argument("templateId", "bytes32"), _argument("version", "uint32")],
        [_argument("", "bytes32")],
        "pure",
    ),
    _event(
        "TemplateRegistered",
        [
            _argument("templateId", "bytes32", indexed=True),
            _argument("version", "uint32", indexed=True),
            _argument("tokenType", "bytes32", indexed=True),
            _argument("config", "tuple", components=LAUNCH_TEMPLATE_CONFIG_COMPONENTS_V2, indexed=False),
        ],
    ),
    _error("InvalidTemplate"),
    _error("TemplateAlreadyRegistered"),
    _error("Unauthorized"),
]


# contracts/out/LaunchModuleFactoryV2.sol/LaunchModuleFactoryV2.json
LAUNCH_MODULE_FACTORY_V2_ABI = [
    _function("configurator", (), [_argument("", "address")], "view"),
    _function(
        "configureClaimer",
        [_argument("splitter", "address"), _argument("claimer", "address")],
    ),
    _function(
        "configureEmbeddedRewards",
        [_argument("splitter", "address"), _argument("rewards", "address")],
    ),
    _function(
        "configureRewards",
        [_argument("splitter", "address"), _argument("rewards", "address")],
    ),
    _function(
        "deployClaimer",
        [
            _argument("positionLocker", "address"),
            _argument("tokenId", "uint256"),
            _argument("splitter", "address"),
        ],
        [_argument("instance", "address")],
    ),
    _function(
        "deploySplitter",
        [
            _argument("launchToken", "address"),
            _argument("token0", "address"),
            _argument("token1", "address"),
            _argument("feeOwnerRegistry", "address"),
            _argument("token0Policy", "tuple", components=DISPOSITION_COMPONENTS_V3),
            _argument("token1Policy", "tuple", components=DISPOSITION_COMPONENTS_V3),
        ],
        [_argument("instance", "address")],
    ),
    _function(
        "deployStaking",
        [
            _argument("stakingToken", "address"),
            _argument("rewardToken0", "address"),
            _argument("rewardToken1", "address"),
        ],
        [_argument("instance", "address")],
    ),
    _function("launcher", (), [_argument("", "address")], "view"),
    _function("setLauncher", [_argument("launcher_", "address")]),
    _event("LauncherConfigured", [_argument("launcher", "address", indexed=True)]),
    _error("AlreadyConfigured"),
    _error("InvalidLauncher"),
    _error("Unauthorized"),
]


# contracts/out/LaunchFeeOwnerRegistry.sol/LaunchFeeOwnerRegistry.json.  This
# explicit V2-stack name avoids silently treating the historical unversioned
# ABI export as the schema-/2 deployment surface.
LAUNCH_FEE_OWNER_REGISTRY_V2_ABI = [
    _function(
        "bindSplitter",
        [_argument("launch", "address"), _argument("splitter", "address")],
    ),
    _function(
        "feeOwner",
        [_argument("launch", "address")],
        [_argument("owner", "address")],
        "view",
    ),
    _function(
        "feeSplitter",
        [_argument("launch", "address")],
        [_argument("splitter", "address")],
        "view",
    ),
    _function("launcher", (), [_argument("", "address")], "view"),
    _function(
        "overrideFeeOwner",
        [_argument("launch", "address"), _argument("newOwner", "address")],
    ),
    _function("protocolAdmin", (), [_argument("", "address")], "view"),
    _function(
        "registerLaunch",
        [_argument("launch", "address"), _argument("initialOwner", "address")],
    ),
    _function("setLauncher", [_argument("launcher_", "address")]),
    _function(
        "transferFeeOwnership",
        [_argument("launch", "address"), _argument("newOwner", "address")],
    ),
    _event(
        "FeeOwnershipTransferred",
        [
            _argument("launch", "address", indexed=True),
            _argument("previousOwner", "address", indexed=True),
            _argument("newOwner", "address", indexed=True),
        ],
    ),
    _event(
        "FeeSplitterBound",
        [
            _argument("launch", "address", indexed=True),
            _argument("splitter", "address", indexed=True),
        ],
    ),
    _event(
        "LaunchRegistered",
        [
            _argument("launch", "address", indexed=True),
            _argument("feeOwner", "address", indexed=True),
        ],
    ),
    _event("LauncherConfigured", [_argument("launcher", "address", indexed=True)]),
    _error("AlreadyBound"),
    _error("AlreadyConfigured"),
    _error("AlreadyRegistered"),
    _error("InvalidLaunch"),
    _error("InvalidLauncher"),
    _error("InvalidSplitter"),
    _error("SameOwner"),
    _error("SplitterCannotOwnFees"),
    _error("Unauthorized"),
    _error("UnregisteredLaunch"),
    _error("ZeroAddress"),
]


__all__ = [
    "ABYSS_LAUNCH_POOL_ADAPTER_V3_ABI",
    "ABYSS_POOL_CONFIG_COMPONENTS_V3",
    "ABYSS_STATIC_FEE_HOOK_DEPLOYER_V2_ABI",
    "ABYSS_STATIC_FEE_HOOK_DEPLOYER_V3_ABI",
    "BURNABLE_TOKEN_DEPLOYER_V2_ABI",
    "DISPOSITION_COMPONENTS_V3",
    "HOLDER_DIVIDEND_TOKEN_DEPLOYER_V2_ABI",
    "INITIAL_BUY_COMPONENTS_V3",
    "LAUNCH_FEE_OWNER_REGISTRY_V2_ABI",
    "LAUNCH_MODULE_FACTORY_V2_ABI",
    "LAUNCH_POOL_REGISTRY_V3_ABI",
    "LAUNCH_RECEIPT_COMPONENTS_V3",
    "LAUNCH_REQUEST_COMPONENTS_V3",
    "LAUNCH_TEMPLATE_CONFIG_COMPONENTS_V2",
    "LAUNCH_TEMPLATE_REGISTRY_V2_ABI",
    "LAUNCH_TOKEN_FACTORY_V2_ABI",
    "TOKEN_CONFIG_COMPONENTS_V3",
    "TOKEN_DEPLOYMENT_SPEC_COMPONENTS_V2",
    "TOKEN_LAUNCH_CONTEXT_COMPONENTS_V2",
    "TOKEN_TYPE_COMPONENTS_V2",
    "UNIFIED_LAUNCHER_ABI",
    "UNISWAP_V4_LAUNCH_POOL_ADAPTER_V3_ABI",
]
