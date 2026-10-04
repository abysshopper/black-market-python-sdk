"""Exact current lifecycle, reviewed registry and V3 economic ABI fragments."""

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
PROFILE_TOPOLOGY_COMPONENTS_V1 = [
    _argument("hookTopology", "uint8"), _argument("configVersion", "uint32"),
    _argument("hookDeployer", "address"), _argument("hookCreationCodeHash", "bytes32"),
]
LAUNCH_BOUNDS_COMPONENTS_V2 = [
    _argument("maximumHookFeePips", "uint24"), _argument("maximumLpFeePips", "uint24"),
    _argument("minimumTickSpacing", "int24"), _argument("maximumTickSpacing", "int24"),
    _argument("maximumPositions", "uint16"), _argument("maximumOracleCardinality", "uint16"),
    _argument("feeModeFlags", "uint8"), _argument("externalLiquidityDisabled", "bool"),
    _argument("oracleConfigId", "bytes32"),
]
LAUNCH_GRAPH_COMPONENTS_V2 = [
    *[_argument(name, "address") for name in ("manager", "hookRoot", "oracleFactory", "locker", "collectorFactory", "collectorDeployer", "hookDeployer")],
    *[_argument(name, "bytes32") for name in ("coreCodeHash", "managerCodeHash", "hookRuntimeCodeHash", "oracleFactoryCodeHash", "lockerCodeHash", "collectorFactoryCodeHash", "collectorDeployerCodeHash", "hookDeployerCodeHash", "hookCreationCodeHash")],
    _argument("codeChunk0", "address"), _argument("codeChunk0Hash", "bytes32"),
    _argument("codeChunk1", "address"), _argument("codeChunk1Hash", "bytes32"),
    _argument("sharedHookSalt", "bytes32"),
]
LAUNCH_ENVELOPE_COMPONENTS_V2 = [
    *[_argument(name, "bytes32") for name in ("artifactDigest", "reviewManifestDigest", "configBoundsDigest", "termsDigest")],
    _argument("topology", "uint8"), _argument("configVersion", "uint32"),
    _argument("economicVersion", "uint32"), _argument("capabilities", "uint64"), _argument("flags", "uint64"),
    _argument("callbackFlags", "uint16"), _argument("callbackMask", "uint16"),
    _argument("protocolTreasury", "address"), _argument("protocolFeeDenominator", "uint8"),
    _argument("beneficiary", "address"), _argument("maximumDeveloperFeeBps", "uint16"),
    _argument("bounds", "tuple", components=LAUNCH_BOUNDS_COMPONENTS_V2),
    _argument("graph", "tuple", components=LAUNCH_GRAPH_COMPONENTS_V2),
]
DEVELOPER_TERMS_COMPONENTS_V3 = [
    _argument("adapter", "address"), _argument("beneficiary", "address"),
    _argument("maximumDeveloperFeeBps", "uint16"), _argument("termsDigest", "bytes32"),
    _argument("enabled", "bool"),
]
SOURCE_TERMS_COMPONENTS_V3 = [
    _argument("adapter", "address"), _argument("profileId", "bytes32"), _argument("termsDigest", "bytes32"),
    _argument("beneficiary", "address"), _argument("maximumDeveloperFeeBps", "uint16"),
    _argument("developerFeeBps", "uint16"),
]
DEVELOPER_CLAIM_RESULT_COMPONENTS_V3 = [
    _argument("hub", "address"), _argument("asset", "address"), _argument("amount", "uint256"),
    _argument("status", "uint8"), _argument("errorSelector", "bytes4"),
]
MARKET_LIVE_STATE_COMPONENTS_V1 = [
    _argument("sqrtPriceX96", "uint160"), _argument("tick", "int24"), _argument("liquidity", "uint128"),
    _argument("publicTrading", "bool"), _argument("oracleReadyAt", "uint256"),
]
V4_POSITION_CONFIG_COMPONENTS_V1 = [
    _argument("tickLower", "int24"), _argument("tickUpper", "int24"), _argument("liquidity", "uint128"),
    _argument("salt", "bytes32"), _argument("maxTokenAmount", "uint256"),
]
V4_MARKET_CONFIG_COMPONENTS_V4 = [
    _argument("version", "uint16"), _argument("lpFeePips", "uint24"), _argument("tickSpacing", "int24"),
    _argument("sqrtPriceX96", "uint160"), _argument("hookFeePips", "uint24"), _argument("feeMode", "uint8"),
    _argument("protocolFeeDenominator", "uint8"), _argument("treasury", "address"),
    _argument("externalLiquidityDisabled", "bool"),
    _argument("oracleConfigId", "bytes32"),
    _argument("profileId", "bytes32"), _argument("termsDigest", "bytes32"),
    _argument("developerBeneficiary", "address"), _argument("developerFeeBps", "uint16"),
    _argument("positions", "tuple[]", components=V4_POSITION_CONFIG_COMPONENTS_V1),
]
V4_MARKET_CONFIG_COMPONENTS_V5 = [
    *V4_MARKET_CONFIG_COMPONENTS_V4[:10],
    _argument("hookSalt", "bytes32"),
    *V4_MARKET_CONFIG_COMPONENTS_V4[10:],
]
POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1 = [
    _argument("poolManager", "address"), _argument("registrar", "address"),
    _argument("oracleFactory", "address"), _argument("core", "address"),
    _argument("liquidityLocker", "address"), _argument("token", "address"),
    _argument("quoteCurrency", "address"), _argument("lpFeePips", "uint24"),
    _argument("tickSpacing", "int24"), _argument("sqrtPriceX96", "uint160"),
    _argument("hookFeePips", "uint24"), _argument("feeMode", "uint8"),
    _argument("protocolFeeDenominator", "uint8"), _argument("treasury", "address"),
    _argument("externalLiquidityDisabled", "bool"), _argument("oracleConfigId", "bytes32"),
    _argument("marketCommitment", "bytes32"), _argument("expectedPositionCount", "uint32"),
]
POOL_BOUND_HOOK_DEPLOYMENT_COMPONENTS_V1 = [
    _argument("deployer", "address"), _argument("initCodeHash", "bytes32"),
    _argument("salt", "bytes32"), _argument("predictedHook", "address"),
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
    *[_function(name, [], [_argument("", "address")]) for name in ("registry", "directory", "tokenFactory", "fundingEscrow", "feeFactory")],
    _event("LaunchBegun", [_argument("launchId", "bytes32", indexed=True), _argument("planHash", "bytes32", indexed=True), _argument("creator", "address", indexed=True), _argument("token", "address", indexed=False), _argument("feeHub", "address", indexed=False), _argument("rewards", "address", indexed=False), _argument("mode", "uint8", indexed=False)]),
    _event("MarketPrepared", [_argument("launchId", "bytes32", indexed=True), _argument("marketIndex", "uint32", indexed=True), _argument("canonicalId", "bytes32", indexed=True), _argument("adapter", "address", indexed=False), _argument("feeSource", "address", indexed=False), _argument("positionCount", "uint32", indexed=False)]),
    _event("LaunchReady", [_argument("launchId", "bytes32", indexed=True)]),
    _event("InitialBuyExecuted", [_argument("launchId", "bytes32", indexed=True), _argument("buyIndex", "uint32", indexed=True), _argument("marketIndex", "uint32", indexed=True), _argument("quoteAsset", "address", indexed=False), _argument("quoteSpent", "uint256", indexed=False), _argument("tokenOut", "uint256", indexed=False), _argument("recipient", "address", indexed=False)]),
    _event("LaunchActivated", [_argument("launchId", "bytes32", indexed=True), _argument("planHash", "bytes32", indexed=True), _argument("token", "address", indexed=True), _argument("marketCount", "uint32", indexed=False), _argument("positionCount", "uint32", indexed=False)]),
    _event("LaunchCancelled", [_argument("launchId", "bytes32", indexed=True), _argument("creator", "address", indexed=True), _argument("inventoryBurned", "bool", indexed=False)]),
    _event("AssetRefunded", [_argument("launchId", "bytes32", indexed=True), _argument("asset", "address", indexed=True), _argument("creator", "address", indexed=True), _argument("amount", "uint256", indexed=False)]),
]

LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI = [
    _function("core", [], [_argument("", "address")]),
    _function("admin", [], [_argument("", "address")]),
    _function("registerAdapter", [_argument("id", "bytes32"), _argument("implementation", "address"), _argument("capabilities", "uint64"), _argument("configVersion", "uint32")], [], "nonpayable"),
    _function("registerProfile", [_argument("id", "bytes32"), _argument("registration", "tuple", components=PROFILE_REGISTRATION_COMPONENTS_V1)], [], "nonpayable"),
    *[_function(name, [_argument("id", "bytes32")], [], "nonpayable") for name in ("disableAdapter", "disableProfile")],
    _function("setFundingInputAllowed", [_argument("asset", "address"), _argument("allowed", "bool")], [], "nonpayable"),
    _function("registerFundingTarget", [_argument("target", "address"), _argument("spender", "address")], [], "nonpayable"),
    _function("disableFundingTarget", [_argument("target", "address")], [], "nonpayable"),
    *[_function(name, [], [_argument("", "uint256")]) for name in ("adapterCount", "profileCount")],
    _function("adapter", [_argument("adapterId", "bytes32")], [_argument("", "tuple", components=ADAPTER_REGISTRATION_COMPONENTS_V1)]),
    _function("profile", [_argument("profileId", "bytes32")], [_argument("", "tuple", components=PROFILE_REGISTRATION_COMPONENTS_V1)]),
    _function("profileTopology", [_argument("profileId", "bytes32")], [_argument("", "tuple", components=PROFILE_TOPOLOGY_COMPONENTS_V1)]),
    _function("fundingInputAllowed", [_argument("asset", "address")], [_argument("", "bool")]),
    _function("requireEligible", [_argument("adapterId", "bytes32"), _argument("profileId", "bytes32"), _argument("configVersion", "uint32"), _argument("requiredCapabilities", "uint64")], [_argument("", "address")]),
    _function("fundingTarget", [_argument("target", "address")], [_argument("spender", "address"), _argument("codeHash", "bytes32"), _argument("enabled", "bool")]),
    *[_function(name, [_argument("offset", "uint256"), _argument("limit", "uint256")], [_argument("", "bytes32[]")]) for name in ("adapterIds", "profileIds")],
    _function("protocolMaximumDeveloperFeeBps", [], [_argument("", "uint16")]),
    _function("profileEnvelope", [_argument("profileId", "bytes32")], [_argument("", "tuple", components=LAUNCH_ENVELOPE_COMPONENTS_V2)]),
    _function("profileId", [_argument("envelope", "tuple", components=LAUNCH_ENVELOPE_COMPONENTS_V2)], [_argument("", "bytes32")], "pure"),
    _function("developerTerms", [_argument("profileId", "bytes32")], DEVELOPER_TERMS_COMPONENTS_V3),
    _function("authorPayout", [_argument("authorId", "address")], [_argument("", "address")]),
    _function("setAuthorPayout", [_argument("authorId", "address"), _argument("payout", "address")], [], "nonpayable"),
    _function("authorHubCount", [_argument("authorId", "address")], [_argument("", "uint256")]),
    _function("authorHubs", [_argument("authorId", "address"), _argument("offset", "uint256"), _argument("limit", "uint256")], [_argument("hubs", "address[]"), _argument("nextOffset", "uint256"), _argument("total", "uint256")]),
    _event("AuthorPayoutUpdated", [_argument("authorId", "address", indexed=True), _argument("previousPayout", "address", indexed=True), _argument("newPayout", "address", indexed=True), _argument("operator", "address", indexed=False)]),
    _event("AuthorHubRegistered", [_argument("authorId", "address", indexed=True), _argument("hub", "address", indexed=True), _argument("index", "uint256", indexed=False)]),
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
POOL_MARKET_ADAPTER_V1_ABI = [
    {"type": "constructor", "stateMutability": "nonpayable", "inputs": [*[_argument(name, "address") for name in ("core_", "manager_", "oracleFactory_", "locker_", "deployer_", "helper_", "registry_")], _argument("profileId_", "bytes32")]},
    *LAUNCH_MARKET_ADAPTER_V1_ABI,
    *[_function(name, [], [_argument("", "address")]) for name in ("implementationRegistry", "poolManager", "oracleFactory", "locker", "hookDeployer", "hookRoot", "collectorFactory")],
    *[_function(name, [], [_argument("", "bytes32")]) for name in ("PROFILE_ID", "CONFIG_SCHEMA")],
    _function("CONFIG_VERSION", [], [_argument("", "uint32")]),
    _function("hookDeploymentMetadata", [_argument("token", "address"), _MARKET], POOL_BOUND_HOOK_DEPLOYMENT_COMPONENTS_V1),
]
REVIEWED_SHARED_V4_MARKET_ADAPTER_V1_ABI = [
    {"type": "constructor", "stateMutability": "nonpayable", "inputs": [*[_argument(name, "address") for name in ("core_", "manager_", "root_", "locker_", "helper_", "registry_", "deployer_")], _argument("profileId_", "bytes32")]},
    *[entry for entry in POOL_MARKET_ADAPTER_V1_ABI if entry["type"] != "constructor" and entry.get("name") != "hookDeploymentMetadata"],
]
POOL_BOUND_LAUNCH_FEE_HOOK_V1_ABI = [
    {"type": "constructor", "stateMutability": "nonpayable", "inputs": [_argument("parameters", "tuple", components=POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1)]},
    *[_function(name, [], [_argument("", "address")]) for name in ("poolManager", "registrar", "oracleFactory", "core", "liquidityLocker", "token")],
    *[_function(name, [], [_argument("", "bytes32")]) for name in ("boundPoolId", "deploymentConfigHash", "marketCommitment")],
    _function("openingSqrtPriceX96", [], [_argument("", "uint160")]),
    _function("expectedPositionCount", [], [_argument("", "uint32")]),
    _function("REQUIRED_HOOK_FLAGS", [], [_argument("", "uint160")]),
    _function("ALL_HOOK_MASK", [], [_argument("", "uint160")]),
    *[_function(name, [_argument("poolId", "bytes32")], [_argument("", "bool")]) for name in ("registered", "initialized")],
    _function("openingCompletedAt", [_argument("poolId", "bytes32")], [_argument("", "uint256")]),
]
POOL_HOOK_DEPLOYER_V1_ABI = [
    {"type": "constructor", "stateMutability": "nonpayable", "inputs": [_argument("creationCode", "bytes")]},
    _function("creationCodeHash", [], [_argument("", "bytes32")]),
    *[_function(name, [], [_argument("", "address")]) for name in ("codeChunk0", "codeChunk1")],
    _function("deployedCodeHash", [_argument("hook", "address")], [_argument("", "bytes32")]),
    _function("initCodeHash", [_argument("parameters", "tuple", components=POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1)], [_argument("", "bytes32")]),
    _function("predict", [_argument("parameters", "tuple", components=POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1), _argument("salt", "bytes32")], [_argument("", "address")]),
    _function("validHookAddress", [_argument("hook", "address")], [_argument("", "bool")], "pure"),
    _function("deploy", [_argument("parameters", "tuple", components=POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1), _argument("salt", "bytes32")], [_argument("hook", "address")], "nonpayable"),
]
POOL_FEE_COLLECTOR_FACTORY_V1_ABI = [
    _function("collectorDeployer", [], [_argument("", "address")]),
    _function("decodeAndValidate", [_argument("registrar", "address"), _argument("token", "address"), _MARKET], [_argument("config", "tuple", components=V4_MARKET_CONFIG_COMPONENTS_V4)]),
    _function("poolBoundDeploymentMetadata", [_argument("registrar", "address"), _argument("token", "address"), _MARKET], POOL_BOUND_HOOK_DEPLOYMENT_COMPONENTS_V1),
    _function("poolBoundHookParameters", [_argument("registrar", "address"), _argument("token", "address"), _MARKET], [_argument("parameters", "tuple", components=POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1), _argument("salt", "bytes32")]),
]
LAUNCH_TOKEN_FACTORY_V1_ABI = [
    _function("core", [], [_argument("", "address")]),
    _function("predictToken", [_ID, _argument("config", "tuple", components=TOKEN_CONFIG_COMPONENTS_V1)], [_argument("", "address")]),
    _function("deployToken", [_ID, _argument("config", "tuple", components=TOKEN_CONFIG_COMPONENTS_V1)], [_argument("token", "address")], "nonpayable"),
    _function("createRewards", [_argument("token", "address"), _argument("hub", "address"), _argument("rewardAssets", "address[]")], [_argument("rewards", "address")], "nonpayable"),
]
LAUNCH_FEE_OWNER_REGISTRY_V2_ABI = [
    {"type": "constructor", "stateMutability": "nonpayable", "inputs": [_argument("protocolAdmin_", "address")]},
    *[{"type": "error", "name": name, "inputs": []} for name in ("ZeroAddress", "Unauthorized", "AlreadyConfigured", "InvalidLauncher", "InvalidLaunch", "AlreadyRegistered", "UnregisteredLaunch", "SameOwner", "InvalidSplitter", "AlreadyBound", "SplitterCannotOwnFees", "NoPendingTransfer")],
    *[_function(name, [], [_argument("", "address")]) for name in ("protocolAdmin", "launcher")],
    *[_function(name, [_argument("launch", "address")], [_argument("", "address")]) for name in ("feeOwner", "pendingFeeOwner", "feeSplitter")],
    _function("setLauncher", [_argument("launcher_", "address")], [], "nonpayable"),
    _function("registerLaunch", [_argument("launch", "address"), _argument("initialOwner", "address")], [], "nonpayable"),
    _function("bindSplitter", [_argument("launch", "address"), _argument("splitter", "address")], [], "nonpayable"),
    _function("transferFeeOwnership", [_argument("launches", "address[]"), _argument("newOwner", "address")], [], "nonpayable"),
    _function("acceptFeeOwnership", [_argument("launches", "address[]")], [], "nonpayable"),
    _function("overrideFeeOwner", [_argument("launches", "address[]"), _argument("newOwner", "address")], [], "nonpayable"),
    _event("LauncherConfigured", [_argument("launcher", "address", indexed=True)]),
    _event("LaunchRegistered", [_argument("launch", "address", indexed=True), _argument("feeOwner", "address", indexed=True)]),
    _event("FeeOwnershipTransferStarted", [_argument("launch", "address", indexed=True), _argument("currentOwner", "address", indexed=True), _argument("pendingOwner", "address", indexed=True)]),
    _event("FeeOwnershipTransferCancelled", [_argument("launch", "address", indexed=True), _argument("pendingOwner", "address", indexed=True)]),
    _event("FeeOwnershipTransferred", [_argument("launch", "address", indexed=True), _argument("previousOwner", "address", indexed=True), _argument("newOwner", "address", indexed=True)]),
    _event("FeeSplitterBound", [_argument("launch", "address", indexed=True), _argument("splitter", "address", indexed=True)]),
]
LAUNCH_FEE_HUB_V3_ABI = [
    {"type": "constructor", "stateMutability": "nonpayable", "inputs": [_argument("launchToken_", "address"), _argument("feeOwnerRegistry_", "address"), _argument("policies_", "tuple[]", components=FEE_ASSET_POLICY_COMPONENTS_V2), _argument("executorFeeBps_", "uint16"), _argument("configurator_", "address"), _argument("implementationRegistry_", "address")]},
    *[{"type": "error", "name": name, "inputs": []} for name in ("InvalidBinding", "InvalidPolicy", "InvalidExecutorFee", "InvalidSourceTerms", "InvalidAssetCount", "InvalidSourceCount", "UnsupportedAsset", "IncompatibleRewards", "InexactCollection", "InexactTransfer", "InexactBurn", "TransferFailed", "Unauthorized", "AlreadyConfigured", "NotConfigured", "InvalidRecipient", "NothingToClaim", "AuthorPayoutChanged", "NativeCurrencyUnsupported")],
    *[_function(name, [], [_argument("", "uint256")]) for name in ("BPS_DENOMINATOR", "MAX_ASSETS", "MAX_SOURCES")],
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
LAUNCH_FEE_HUB_V3_ABI += [
    _function("economicVersion", [], [_argument("", "uint16")]),
    _function("implementationRegistry", [], [_argument("", "address")]),
    _function("protocolMaximumDeveloperFeeBps", [], [_argument("", "uint16")]),
    _function("sourceTerms", [_argument("source", "address")], [_argument("", "tuple", components=SOURCE_TERMS_COMPONENTS_V3)]),
    _function("bindSourceTerms", [_argument("source", "address"), _argument("profileId", "bytes32"), _argument("termsDigest", "bytes32"), _argument("developerFeeBps", "uint16")], [], "nonpayable"),
    _function("claimableDeveloperFees", [_argument("authorId", "address"), _argument("asset", "address")], [_argument("", "uint256")]),
    _function("reservedDeveloperFees", [_argument("asset", "address")], [_argument("", "uint256")]),
    _function("claimDeveloperFees", [_argument("authorId", "address"), _argument("asset", "address")], [_argument("amount", "uint256")], "nonpayable"),
    _event("SourcesConfigured", [_argument("sources", "address[]", indexed=False), _argument("rewards", "address", indexed=True)]),
    _event("SourceTermsBound", [_argument("source", "address", indexed=True), _argument("profileId", "bytes32", indexed=True), _argument("beneficiary", "address", indexed=True), _argument("adapter", "address", indexed=False), _argument("termsDigest", "bytes32", indexed=False), _argument("maximumDeveloperFeeBps", "uint16", indexed=False), _argument("developerFeeBps", "uint16", indexed=False)]),
    _event("DeveloperFeesCredited", [_argument("authorId", "address", indexed=True), _argument("source", "address", indexed=True), _argument("asset", "address", indexed=True), _argument("amount", "uint256", indexed=False)]),
    _event("DeveloperFeesClaimed", [_argument("authorId", "address", indexed=True), _argument("asset", "address", indexed=True), _argument("payout", "address", indexed=True), _argument("amount", "uint256", indexed=False)]),
    _event("Distributed", [_argument("asset", "address", indexed=True), _argument("owner", "address", indexed=True), _argument("executor", "address", indexed=True), *[_argument(name, "uint256", indexed=False) for name in ("newlyCollected", "executorAmount", "ownerAmount", "developerAmount", "rewardsAmount", "burnAmount")]]),
]
LAUNCH_FEE_HUB_FACTORY_V3_ABI = [
    {"type": "constructor", "stateMutability": "nonpayable", "inputs": [_argument("registry_", "address"), _argument("core_", "address"), _argument("implementationRegistry_", "address")]},
    *[{"type": "error", "name": name, "inputs": []} for name in ("ZeroAddress", "InvalidRegistry", "InvalidImplementationRegistry", "InvalidConfigurator", "Unauthorized", "RegistryNotBound", "ExistingLaunchBinding", "InvalidPageLimit", "InvalidAssetList", "InsufficientPageGas")],
    *[_function(name, [], [_argument("", "uint256")]) for name in ("MAX_CLAIM_HUBS", "MAX_CLAIM_ASSETS", "DEVELOPER_CLAIM_GAS")],
    _function("createHub", [_argument("launchToken", "address"), _argument("initialOwner", "address"), _argument("policies", "tuple[]", components=FEE_ASSET_POLICY_COMPONENTS_V2), _argument("executorFeeBps", "uint16"), _argument("configurator", "address")], [_argument("hub", "address")], "nonpayable"),
    _event("HubCreated", [_argument("launchToken", "address", indexed=True), _argument("hub", "address", indexed=True), _argument("initialOwner", "address", indexed=True), _argument("configurator", "address", indexed=False), _argument("executorFeeBps", "uint16", indexed=False)]),
    _function("deploymentAuthority", [], [_argument("", "address")]),
    _function("implementationRegistry", [], [_argument("", "address")]),
    _function("registry", [], [_argument("", "address")]),
    _function("isHub", [_argument("hub", "address")], [_argument("", "bool")]),
    _function("claimDeveloperFeesPage", [_argument("authorId", "address"), _argument("offset", "uint256"), _argument("limit", "uint256"), _argument("assets", "address[]")], [_argument("results", "tuple[]", components=DEVELOPER_CLAIM_RESULT_COMPONENTS_V3), _argument("nextOffset", "uint256"), _argument("total", "uint256")], "nonpayable"),
    _event("DeveloperClaimResult", [_argument("authorId", "address", indexed=True), _argument("hub", "address", indexed=True), _argument("asset", "address", indexed=True), _argument("amount", "uint256", indexed=False), _argument("status", "uint8", indexed=False), _argument("errorSelector", "bytes4", indexed=False)]),
    _event("DeveloperClaimPage", [_argument("authorId", "address", indexed=True), _argument("offset", "uint256", indexed=False), _argument("nextOffset", "uint256", indexed=False), _argument("total", "uint256", indexed=False)]),
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
LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI += [
    {"type": "error", "name": name, "inputs": []}
    for name in ("Unauthorized", "InvalidRegistration", "InvalidAuthorization", "AlreadyRegistered", "IneligibleImplementation", "InvalidPage")
]
LAUNCH_FUNDING_ESCROW_V1_ABI += [
    {"type": "error", "name": name, "inputs": []}
    for name in ("Unauthorized", "InvalidFunding", "InexactTransfer", "InsufficientEscrow")
] + [{"type": "error", "name": "ConversionFailed", "inputs": [_argument("reason", "bytes")]}]



__all__ = [name for name in globals() if name.isupper() and not name.startswith("_")]
