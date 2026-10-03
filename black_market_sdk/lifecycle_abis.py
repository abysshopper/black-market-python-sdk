"""Versioned ABI fragments for the opt-in LaunchPlanV1 lifecycle stack.

These are the tuple layouts from ``launch/lifecycle/v1/LaunchTypesV1.sol`` and
``ILaunchLifecycleV1.sol``. Existing Unified and historical Atomic ABIs retain
their original meaning; no address substitution is performed.
"""

from __future__ import annotations


def _argument(name, abi_type, *, components=None, indexed=None):
    value = {"name": name, "type": abi_type}
    if components is not None:
        value["components"] = components
    if indexed is not None:
        value["indexed"] = indexed
    return value


def _function(name, inputs=(), outputs=(), state_mutability="view"):
    return {"type": "function", "name": name, "stateMutability": state_mutability, "inputs": list(inputs), "outputs": list(outputs)}


def _event(name, inputs):
    return {"type": "event", "name": name, "inputs": list(inputs), "anonymous": False}


TOKEN_CONFIG_COMPONENTS_V1 = [
    _argument("kind", "uint8"), _argument("rewardMode", "uint8"),
    _argument("name", "string"), _argument("symbol", "string"),
    _argument("supply", "uint256"), _argument("nftUnit", "uint256"),
    _argument("metadataURI", "string"), _argument("salt", "bytes32"),
    _argument("inventoryRecipient", "address"), _argument("burnOnCancel", "bool"),
]
ASSET_FUNDING_COMPONENTS_V1 = [
    _argument("asset", "address"), _argument("amount", "uint256"),
    _argument("kind", "uint8"), _argument("inputAsset", "address"),
    _argument("inputAmount", "uint256"), _argument("target", "address"), _argument("data", "bytes"),
]
FEE_ASSET_POLICY_COMPONENTS_V2 = [
    _argument("asset", "address"), _argument("ownerBps", "uint16"),
    _argument("rewardsBps", "uint16"), _argument("burnBps", "uint16"),
]
MARKET_CONFIG_COMPONENTS_V1 = [
    _argument("adapterId", "bytes32"), _argument("profileId", "bytes32"),
    _argument("quoteAsset", "address"), _argument("tokenBudget", "uint256"),
    _argument("configVersion", "uint32"), _argument("config", "bytes"),
]
INITIAL_BUY_COMPONENTS_V1 = [
    _argument("marketIndex", "uint32"), _argument("quoteAmountIn", "uint256"),
    _argument("minTokenOut", "uint256"), _argument("recipient", "address"), _argument("sqrtPriceLimitX96", "uint160"),
]
LAUNCH_PLAN_COMPONENTS_V1 = [
    _argument("chainId", "uint256"), _argument("orchestrator", "address"),
    _argument("creator", "address"), _argument("nonce", "uint256"),
    _argument("token", "tuple", components=TOKEN_CONFIG_COMPONENTS_V1),
    _argument("funding", "tuple[]", components=ASSET_FUNDING_COMPONENTS_V1),
    _argument("feeAssets", "tuple[]", components=FEE_ASSET_POLICY_COMPONENTS_V2),
    _argument("markets", "tuple[]", components=MARKET_CONFIG_COMPONENTS_V1),
    _argument("buys", "tuple[]", components=INITIAL_BUY_COMPONENTS_V1),
    _argument("deadline", "uint256"), _argument("executorFeeBps", "uint16"),
]
MARKET_IDENTITY_COMPONENTS_V1 = [
    _argument("venue", "uint8"), _argument("canonicalId", "bytes32"),
    _argument("manager", "address"), _argument("factory", "address"), _argument("pool", "address"),
    _argument("poolId", "bytes32"), _argument("profileId", "bytes32"),
    _argument("currency0", "address"), _argument("currency1", "address"),
    _argument("fee", "uint24"), _argument("tickSpacing", "int24"), _argument("hook", "address"),
    _argument("openingSqrtPriceX96", "uint160"),
]
PREPARED_MARKET_COMPONENTS_V1 = [
    _argument("identity", "tuple", components=MARKET_IDENTITY_COMPONENTS_V1),
    _argument("feeSource", "address"), _argument("custody", "address"),
    _argument("mintExecutor", "address"), _argument("buyExecutor", "address"),
    _argument("exclusions", "address[]"), _argument("positionCount", "uint32"),
]
POSITION_IDENTITY_COMPONENTS_V1 = [
    _argument("canonicalId", "bytes32"), _argument("marketId", "bytes32"),
    _argument("manager", "address"), _argument("custody", "address"), _argument("tokenId", "uint256"),
    _argument("tickLower", "int24"), _argument("tickUpper", "int24"), _argument("salt", "bytes32"),
    _argument("liquidity", "uint128"),
]
LAUNCH_PROGRESS_COMPONENTS_V1 = [
    _argument("launchId", "bytes32"), _argument("planHash", "bytes32"), _argument("creator", "address"),
    _argument("nonce", "uint256"), _argument("mode", "uint8"), _argument("phase", "uint8"),
    _argument("token", "address"), _argument("feeHub", "address"), _argument("rewards", "address"),
    _argument("preparedMarkets", "uint32"), _argument("marketCount", "uint32"),
    _argument("buyCount", "uint32"), _argument("positionCount", "uint32"), _argument("deadline", "uint256"),
]
LAUNCH_RECEIPT_COMPONENTS_V1 = [
    _argument("launchId", "bytes32"), _argument("planHash", "bytes32"), _argument("token", "address"),
    _argument("feeHub", "address"), _argument("rewards", "address"),
    _argument("marketCount", "uint32"), _argument("positionCount", "uint32"),
    _argument("quoteSpent", "uint256[]"), _argument("tokenOut", "uint256[]"),
]
EXECUTION_CONTEXT_COMPONENTS_V1 = [
    _argument("launchId", "bytes32"), _argument("marketIndex", "uint32"), _argument("operation", "uint8"),
    _argument("adapter", "address"), _argument("executor", "address"), _argument("token", "address"),
    _argument("quoteAsset", "address"), _argument("manager", "address"), _argument("custody", "address"),
    _argument("recipient", "address"), _argument("amount", "uint256"),
]
ADAPTER_REGISTRATION_COMPONENTS_V1 = [
    _argument("implementation", "address"), _argument("codeHash", "bytes32"),
    _argument("capabilities", "uint64"), _argument("configVersion", "uint32"), _argument("enabled", "bool"),
]
PROFILE_REGISTRATION_COMPONENTS_V1 = [
    _argument("adapterId", "bytes32"), _argument("configSchema", "bytes32"),
    _argument("dependencyDigest", "bytes32"), _argument("venue", "address"),
    _argument("factory", "address"), _argument("hook", "address"),
    _argument("capabilities", "uint64"), _argument("enabled", "bool"),
]
MARKET_LIVE_STATE_COMPONENTS_V1 = [
    _argument("sqrtPriceX96", "uint160"), _argument("tick", "int24"), _argument("liquidity", "uint128"),
    _argument("publicTrading", "bool"), _argument("oracleReadyAt", "uint256"),
]
V4_POSITION_CONFIG_COMPONENTS_V1 = [
    _argument("tickLower", "int24"), _argument("tickUpper", "int24"), _argument("liquidity", "uint128"),
    _argument("salt", "bytes32"), _argument("maxTokenAmount", "uint256"),
]
V4_MARKET_CONFIG_COMPONENTS_V2 = [
    _argument("version", "uint16"), _argument("lpFeePips", "uint24"), _argument("tickSpacing", "int24"),
    _argument("sqrtPriceX96", "uint160"), _argument("hookFeePips", "uint24"), _argument("feeMode", "uint8"),
    _argument("protocolFeeDenominator", "uint8"), _argument("treasury", "address"),
    _argument("externalLiquidityDisabled", "bool"),
    _argument("oracleConfigId", "bytes32"),
    _argument("positions", "tuple[]", components=V4_POSITION_CONFIG_COMPONENTS_V1),
]
ABYSS_POSITION_CONFIG_COMPONENTS_V1 = [
    _argument("tickLower", "int24"), _argument("tickUpper", "int24"),
    _argument("liquidity", "uint128"), _argument("tokenAmountMaximum", "uint256"),
]
ABYSS_MARKET_CONFIG_COMPONENTS_V1 = [
    _argument("profile", "uint8"), _argument("fee", "uint24"), _argument("oracleConfigId", "bytes32"),
    _argument("openingSqrtPriceX96", "uint160"), _argument("positions", "tuple[]", components=ABYSS_POSITION_CONFIG_COMPONENTS_V1),
]

_PLAN = _argument("plan", "tuple", components=LAUNCH_PLAN_COMPONENTS_V1)
_ID = _argument("launchId", "bytes32")
_PROGRESS = _argument("progress", "tuple", components=LAUNCH_PROGRESS_COMPONENTS_V1)
_RECEIPT = _argument("receipt", "tuple", components=LAUNCH_RECEIPT_COMPONENTS_V1)
_MARKET = _argument("market", "tuple", components=MARKET_CONFIG_COMPONENTS_V1)
_IDENTITY = _argument("identity", "tuple", components=MARKET_IDENTITY_COMPONENTS_V1)
_POSITION = _argument("position", "tuple", components=POSITION_IDENTITY_COMPONENTS_V1)

LAUNCH_LIFECYCLE_V1_ABI = [
    _function("hashPlan", [_PLAN], [_argument("", "bytes32")], "pure"),
    _function("launchIdOf", [_PLAN], [_argument("", "bytes32")], "pure"),
    _function("predictToken", [_PLAN], [_argument("", "address")]),
    _function("launchAtomic", [_PLAN], [_RECEIPT], "payable"),
    _function("beginLaunch", [_PLAN, _argument("mode", "uint8")], [_PROGRESS], "payable"),
    _function("prepareMarkets", [_PLAN, _argument("firstMarket", "uint32"), _argument("count", "uint32")], [], "nonpayable"),
    _function("activateLaunch", [_PLAN], [_RECEIPT], "nonpayable"),
    _function("cancelLaunch", [_PLAN], [], "nonpayable"),
    _function("readLaunchProgress", [_ID], [_PROGRESS]),
    _function("executionContext", [], [_argument("", "tuple", components=EXECUTION_CONTEXT_COMPONENTS_V1)]),
    _function("isLaunchActive", [_ID], [_argument("", "bool")]),
    _function("escrowBalance", [_ID, _argument("asset", "address")], [_argument("", "uint256")]),
    _function("authorizeTokenTransfer", [_argument("token", "address"), _argument("caller", "address"), _argument("from", "address"), _argument("to", "address"), _argument("amount", "uint256"), _argument("nft", "bool")], [_argument("", "bool")]),
    *[_function(name, [], [_argument("", "address")]) for name in ("registry", "directory", "tokenFactory", "fundingEscrow")],
    _event("LaunchBegun", [_argument("launchId", "bytes32", indexed=True), _argument("planHash", "bytes32", indexed=True), _argument("creator", "address", indexed=True), _argument("token", "address", indexed=False), _argument("feeHub", "address", indexed=False), _argument("rewards", "address", indexed=False), _argument("mode", "uint8", indexed=False)]),
    _event("MarketPrepared", [_argument("launchId", "bytes32", indexed=True), _argument("marketIndex", "uint32", indexed=True), _argument("canonicalId", "bytes32", indexed=True), _argument("adapter", "address", indexed=False), _argument("feeSource", "address", indexed=False), _argument("positionCount", "uint32", indexed=False)]),
    _event("LaunchReady", [_argument("launchId", "bytes32", indexed=True)]),
    _event("InitialBuyExecuted", [_argument("launchId", "bytes32", indexed=True), _argument("buyIndex", "uint32", indexed=True), _argument("marketIndex", "uint32", indexed=True), _argument("quoteAsset", "address", indexed=False), _argument("quoteSpent", "uint256", indexed=False), _argument("tokenOut", "uint256", indexed=False), _argument("recipient", "address", indexed=False)]),
    _event("LaunchActivated", [_argument("launchId", "bytes32", indexed=True), _argument("planHash", "bytes32", indexed=True), _argument("token", "address", indexed=True), _argument("marketCount", "uint32", indexed=False), _argument("positionCount", "uint32", indexed=False)]),
    _event("LaunchCancelled", [_argument("launchId", "bytes32", indexed=True), _argument("creator", "address", indexed=True), _argument("inventoryBurned", "bool", indexed=False)]),
    _event("AssetRefunded", [_argument("launchId", "bytes32", indexed=True), _argument("asset", "address", indexed=True), _argument("creator", "address", indexed=True), _argument("amount", "uint256", indexed=False)]),
]

LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI = [
    _function("core", [], [_argument("", "address")]),
    _function("admin", [], [_argument("", "address")]),
    _function("registerAdapter", [_argument("id", "bytes32"), _argument("implementation", "address"), _argument("capabilities", "uint64"), _argument("configVersion", "uint32")], [], "nonpayable"),
    _function("registerProfile", [_argument("id", "bytes32"), _argument("registration", "tuple", components=PROFILE_REGISTRATION_COMPONENTS_V1)], [], "nonpayable"),
    *[_function(name, [_argument("id", "bytes32")], [], "nonpayable") for name in ("disableAdapter", "disableProfile")],
    _function("setAssetAllowed", [_argument("asset", "address"), _argument("allowed", "bool")], [], "nonpayable"),
    _function("registerFundingTarget", [_argument("target", "address"), _argument("spender", "address")], [], "nonpayable"),
    _function("disableFundingTarget", [_argument("target", "address")], [], "nonpayable"),
    *[_function(name, [], [_argument("", "uint256")]) for name in ("adapterCount", "profileCount")],
    _function("adapter", [_argument("adapterId", "bytes32")], [_argument("", "tuple", components=ADAPTER_REGISTRATION_COMPONENTS_V1)]),
    _function("profile", [_argument("profileId", "bytes32")], [_argument("", "tuple", components=PROFILE_REGISTRATION_COMPONENTS_V1)]),
    _function("assetAllowed", [_argument("asset", "address")], [_argument("", "bool")]),
    _function("requireEligible", [_argument("adapterId", "bytes32"), _argument("profileId", "bytes32"), _argument("configVersion", "uint32"), _argument("requiredCapabilities", "uint64")], [_argument("", "address")]),
    _function("fundingTarget", [_argument("target", "address")], [_argument("spender", "address"), _argument("codeHash", "bytes32"), _argument("enabled", "bool")]),
    *[_function(name, [_argument("offset", "uint256"), _argument("limit", "uint256")], [_argument("", "bytes32[]")]) for name in ("adapterIds", "profileIds")],
]
LAUNCH_DIRECTORY_V1_ABI = [
    _function("core", [], [_argument("", "address")]),
    _function("market", [_ID, _argument("marketIndex", "uint32")], [_argument("adapter", "address"), _argument("prepared", "tuple", components=PREPARED_MARKET_COMPONENTS_V1)]),
    _function("positions", [_ID, _argument("marketIndex", "uint32"), _argument("offset", "uint256"), _argument("limit", "uint256")], [_argument("", "tuple[]", components=POSITION_IDENTITY_COMPONENTS_V1)]),
    _function("launches", [_argument("offset", "uint256"), _argument("limit", "uint256")], [_argument("", "bytes32[]")]),
    _function("launchOfToken", [_argument("token", "address")], [_argument("", "bytes32")]),
    _function("marketCount", [_ID], [_argument("", "uint256")]),
    _function("positionCount", [_ID, _argument("marketIndex", "uint32")], [_argument("", "uint256")]),
]
LAUNCH_MARKET_ADAPTER_V1_ABI = [
    _function("core", [], [_argument("", "address")]),
    _function("dependencyDigest", [], [_argument("", "bytes32")]),
    _function("resolve", [_ID, _argument("token", "address"), _MARKET], [_IDENTITY]),
    _function("prepareMarket", [_ID, _argument("marketIndex", "uint32"), _argument("token", "address"), _argument("hub", "address"), _MARKET], [_argument("", "tuple", components=PREPARED_MARKET_COMPONENTS_V1)], "nonpayable"),
    _function("validatePrepared", [_ID, _argument("marketIndex", "uint32"), _argument("token", "address"), _MARKET, _IDENTITY], []),
    _function("mintAndLock", [_ID, _argument("marketIndex", "uint32"), _argument("token", "address"), _MARKET], [_argument("positions", "tuple[]", components=POSITION_IDENTITY_COMPONENTS_V1), _argument("tokenSpent", "uint256")], "nonpayable"),
    _function("executeBuy", [_ID, _argument("marketIndex", "uint32"), _argument("token", "address"), _MARKET, _argument("buy", "tuple", components=INITIAL_BUY_COMPONENTS_V1)], [_argument("quoteSpent", "uint256"), _argument("tokenOut", "uint256")], "nonpayable"),
    _function("activateMarket", [_ID, _argument("marketIndex", "uint32")], [], "nonpayable"),
    _function("authorizeTokenTransfer", [_ID, _argument("marketIndex", "uint32"), _argument("operation", "uint8"), _argument("caller", "address"), _argument("from", "address"), _argument("to", "address"), _argument("amount", "uint256"), _argument("nft", "bool")], [_argument("", "bool")]),
    _function("readMarket", [_ID, _argument("marketIndex", "uint32")], [_argument("", "tuple", components=MARKET_LIVE_STATE_COMPONENTS_V1)]),
    _function("readPosition", [_POSITION], [_argument("liquidity", "uint128"), _argument("owner", "address")]),
]
LAUNCH_TOKEN_FACTORY_V1_ABI = [
    _function("core", [], [_argument("", "address")]),
    _function("predictToken", [_ID, _argument("config", "tuple", components=TOKEN_CONFIG_COMPONENTS_V1)], [_argument("", "address")]),
    _function("deployToken", [_ID, _argument("config", "tuple", components=TOKEN_CONFIG_COMPONENTS_V1)], [_argument("token", "address")], "nonpayable"),
    _function("createRewards", [_argument("token", "address"), _argument("hub", "address"), _argument("rewardAssets", "address[]")], [_argument("rewards", "address")], "nonpayable"),
]
LAUNCH_FEE_HUB_V2_ABI = [
    *[_function(name, [], [_argument("", "address")]) for name in ("launchToken", "feeOwnerRegistry", "configurator", "rewards")],
    *[_function(name, [], [_argument("", "bool")]) for name in ("requiresOwner", "requiresRewards", "finalized")],
    _function("executorFeeBps", [], [_argument("", "uint16")]),
    _function("MAX_EXECUTOR_FEE_BPS", [], [_argument("", "uint16")]),
    _function("setExecutorFeeBps", [_argument("newFeeBps", "uint16")], [], "nonpayable"),
    {"type": "event", "name": "ExecutorFeeUpdated", "anonymous": False, "inputs": [
        _argument("feeOwner", "address", indexed=True),
        _argument("previousFeeBps", "uint16", indexed=False),
        _argument("newFeeBps", "uint16", indexed=False),
    ]},
    *[_function(name, [], [_argument("", "address[]")]) for name in ("assets", "sources")],
    _function("policy", [_argument("asset", "address")], [_argument("", "tuple", components=FEE_ASSET_POLICY_COMPONENTS_V2)]),
    _function("sourceId", [_argument("source", "address")], [_argument("", "bytes32")]),
    _function("sourceById", [_argument("id", "bytes32")], [_argument("", "address")]),
    _function("sourceAssets", [_argument("source", "address")], [_argument("", "address[]")]),
    _function("rewardAssets", [], [_argument("", "address[]")]),
    _function("rewardAssetsHash", [], [_argument("", "bytes32")]),
    _function("configureSources", [_argument("sources", "address[]"), _argument("rewards", "address")], [], "nonpayable"),
    _function("claimAndSplit", [], [_argument("payments", "tuple[]", components=[_argument("asset", "address"), _argument("amount", "uint256")])], "nonpayable"),
    _function("claimableOwnerFees", [_argument("owner", "address"), _argument("asset", "address")], [_argument("", "uint256")]),
    _function("reservedOwnerFees", [_argument("asset", "address")], [_argument("", "uint256")]),
    _function("claimOwnerFees", [_argument("asset", "address"), _argument("recipient", "address")], [_argument("amount", "uint256")], "nonpayable"),
    _event("OwnerFeesCredited", [_argument("owner", "address", indexed=True), _argument("asset", "address", indexed=True), _argument("amount", "uint256", indexed=False)]),
    _event("OwnerFeesClaimed", [_argument("owner", "address", indexed=True), _argument("asset", "address", indexed=True), _argument("recipient", "address", indexed=True), _argument("amount", "uint256", indexed=False)]),
]

# Claim amounts and RewardPaid are net beneficiary receipts; earned is gross entitlement.
MULTI_ASSET_REWARDS_V1_ABI = [
    _function("rewardAssets", [], [_argument("", "address[]")]),
    _function("earned", [_argument("beneficiary", "address"), _argument("asset", "address")], [_argument("", "uint256")]),
    _function("pendingRewards", [_argument("beneficiary", "address")], [_argument("", "uint256[]")]),
    _function("lifetimeRewardsPaid", [_argument("asset", "address"), _argument("beneficiary", "address")], [_argument("", "uint256")]),
    _function("claim", [], [_argument("", "uint256[]")], "nonpayable"),
    _function("claimFor", [_argument("beneficiary", "address")], [_argument("", "uint256[]")], "nonpayable"),
    _function("claimRange", [_argument("beneficiary", "address"), _argument("start", "uint256"), _argument("count", "uint256")], [_argument("", "uint256[]")], "nonpayable"),
    _event("RewardPaid", [_argument("beneficiary", "address", indexed=True), _argument("asset", "address", indexed=True), _argument("amount", "uint256", indexed=False)]),
    _event("RewardClaimBountyPaid", [_argument("executor", "address", indexed=True), _argument("beneficiary", "address", indexed=True), _argument("asset", "address", indexed=True), _argument("amount", "uint256", indexed=False)]),
]
LIFECYCLE_DIVIDEND_V1_ABI = [
    *MULTI_ASSET_REWARDS_V1_ABI,
    _function("MAX_DIVIDEND_BOUNTY_BPS", [], [_argument("", "uint16")]),
    _function("dividendBountyBps", [], [_argument("", "uint16")]),
    _function("setDividendBountyBps", [_argument("newBountyBps", "uint16")], [], "nonpayable"),
    _event("DividendBountyUpdated", [_argument("feeOwner", "address", indexed=True), _argument("previousBountyBps", "uint16", indexed=False), _argument("newBountyBps", "uint16", indexed=False)]),
]
LAUNCH_FUNDING_ESCROW_V1_ABI = [
    _function("core", [], [_argument("", "address")]),
    _function("wrappedNative", [], [_argument("", "address")]),
]

LIVE_POSITION_COMPONENTS_V1 = [
    _argument("identity", "tuple", components=POSITION_IDENTITY_COMPONENTS_V1),
    _argument("liquidity", "uint128"), _argument("owner", "address"),
]
FEE_ROUTE_COMPONENTS_V1 = [
    _argument("hub", "address"), _argument("ownerRegistry", "address"),
    _argument("feeOwner", "address"), _argument("rewards", "address"),
    _argument("executorFeeBps", "uint16"), _argument("finalized", "bool"),
    _argument("assets", "address[]"), _argument("sources", "address[]"),
]
LAUNCH_LENS_V1_ABI = [
    _function("core", [], [_argument("", "address")]),
    _function("directory", [], [_argument("", "address")]),
    _function("progress", [_ID], [_PROGRESS]),
    _function("launchPage", [_argument("offset", "uint256"), _argument("limit", "uint256")], [_argument("", "tuple[]", components=LAUNCH_PROGRESS_COMPONENTS_V1)]),
    _function("liveMarket", [_ID, _argument("marketIndex", "uint32")], [_argument("adapter", "address"), _argument("identity", "tuple", components=PREPARED_MARKET_COMPONENTS_V1), _argument("state", "tuple", components=MARKET_LIVE_STATE_COMPONENTS_V1)]),
    _function("livePositions", [_ID, _argument("marketIndex", "uint32"), _argument("offset", "uint256"), _argument("limit", "uint256")], [_argument("", "tuple[]", components=LIVE_POSITION_COMPONENTS_V1)]),
    _function("feeRoute", [_argument("token", "address")], [_argument("", "tuple", components=FEE_ROUTE_COMPONENTS_V1)]),
]

LAUNCH_LIFECYCLE_V1_ABI += [
    {"type": "error", "name": name, "inputs": []}
    for name in (
        "Unauthorized", "WrongDomain", "PlanMismatch", "LaunchAlreadyExists", "InvalidMode",
        "InvalidPhase", "InvalidPreparationOrder", "DeadlineExpired", "InvalidBinding",
        "InvalidMarket", "InexactTransfer", "InsufficientEscrow", "InvalidPositions",
        "InvalidPlan", "InvalidFunding", "InvalidFeePolicy", "InvalidBuy", "DuplicateMarket",
        "IneligibleImplementation",
    )
]
LAUNCH_IMPLEMENTATION_REGISTRY_V1_ABI += [
    {"type": "error", "name": name, "inputs": []}
    for name in ("Unauthorized", "InvalidRegistration", "AlreadyRegistered", "IneligibleImplementation", "InvalidPage")
]
LAUNCH_FUNDING_ESCROW_V1_ABI += [
    {"type": "error", "name": name, "inputs": []}
    for name in ("Unauthorized", "InvalidFunding", "InexactTransfer", "InsufficientEscrow")
] + [{"type": "error", "name": "ConversionFailed", "inputs": [_argument("reason", "bytes")]}]



__all__ = [name for name in globals() if name.endswith(("_V1", "_V2", "_V1_ABI", "_V2_ABI"))]
