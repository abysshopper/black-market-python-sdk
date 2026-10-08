"""Reviewed lifecycle ABIs and exact canonical wire component arrays.

The packaged arrays are generated from the matching Node SDK release, so both
languages expose identical methods, errors, events, constructors, and tuples.
"""

from __future__ import annotations

from .abis import _SDK_ABI_DATA

TOKEN_CONFIG_COMPONENTS_V1 = _SDK_ABI_DATA["lifecycleTokenComponents"]
ASSET_FUNDING_COMPONENTS_V1 = _SDK_ABI_DATA["lifecycleFundingComponents"]
FEE_ASSET_POLICY_COMPONENTS_V2 = _SDK_ABI_DATA["lifecycleFeePolicyComponents"]
MARKET_CONFIG_COMPONENTS_V1 = _SDK_ABI_DATA["lifecycleMarketComponents"]
INITIAL_BUY_COMPONENTS_V1 = _SDK_ABI_DATA["lifecycleBuyComponents"]
LAUNCH_PLAN_COMPONENTS_V1 = _SDK_ABI_DATA["launchPlanV1Components"]
MARKET_IDENTITY_COMPONENTS_V1 = _SDK_ABI_DATA["marketIdentityV1Components"]
PREPARED_MARKET_COMPONENTS_V1 = _SDK_ABI_DATA["preparedMarketV1Components"]
POSITION_IDENTITY_COMPONENTS_V1 = _SDK_ABI_DATA["positionIdentityV1Components"]
LAUNCH_PROGRESS_COMPONENTS_V1 = _SDK_ABI_DATA["launchProgressV1Components"]
LAUNCH_RECEIPT_COMPONENTS_V1 = _SDK_ABI_DATA["launchReceiptV1Components"]
EXECUTION_CONTEXT_COMPONENTS_V1 = _SDK_ABI_DATA["launchExecutionContextV1Components"]
ADAPTER_REGISTRATION_COMPONENTS_V1 = _SDK_ABI_DATA["adapterRegistrationV1Components"]
PROFILE_REGISTRATION_COMPONENTS_V1 = _SDK_ABI_DATA["profileRegistrationV1Components"]
PROFILE_TOPOLOGY_COMPONENTS_V1 = _SDK_ABI_DATA["profileTopologyV1Components"]
LAUNCH_BOUNDS_COMPONENTS_V2 = _SDK_ABI_DATA["launchBoundsV2Components"]
LAUNCH_GRAPH_COMPONENTS_V2 = _SDK_ABI_DATA["launchGraphV2Components"]
LAUNCH_ENVELOPE_COMPONENTS_V2 = _SDK_ABI_DATA["launchEnvelopeV2Components"]
SOURCE_TERMS_COMPONENTS_V3 = _SDK_ABI_DATA["sourceTermsV3Components"]
DEVELOPER_CLAIM_RESULT_COMPONENTS_V3 = _SDK_ABI_DATA["developerClaimResultV3Components"]
MARKET_LIVE_STATE_COMPONENTS_V1 = _SDK_ABI_DATA["marketLiveStateV1Components"]
V4_POSITION_CONFIG_COMPONENTS_V1 = _SDK_ABI_DATA["v4LifecyclePositionComponents"]
V4_MARKET_CONFIG_COMPONENTS_V4 = _SDK_ABI_DATA["v4LifecycleMarketComponents"]
V4_MARKET_CONFIG_COMPONENTS_V5 = _SDK_ABI_DATA["poolBoundV4LifecycleMarketComponentsV5"]
V4_MARKET_CONFIG_COMPONENTS_V6 = _SDK_ABI_DATA["poolBoundV4LifecycleMarketComponentsV6"]
POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V1 = _SDK_ABI_DATA["poolBoundHookParametersV1Components"]
POOL_BOUND_HOOK_PARAMETERS_COMPONENTS_V2 = _SDK_ABI_DATA["poolBoundHookParametersV2Components"]
ABYSS_POSITION_CONFIG_COMPONENTS_V1 = _SDK_ABI_DATA["abyssLifecyclePositionComponents"]
ABYSS_MARKET_CONFIG_COMPONENTS_V1 = _SDK_ABI_DATA["abyssLifecycleMarketComponents"]
V4_POOL_KEY_COMPONENTS = _SDK_ABI_DATA["lifecycleV4PoolKeyComponents"]
V4_HOOK_POOL_CONFIG_COMPONENTS = _SDK_ABI_DATA["lifecycleV4HookPoolConfigComponents"]

LAUNCH_LIFECYCLE_V1_ABI = _SDK_ABI_DATA["launchLifecycleAbi"]
LAUNCH_IMPLEMENTATION_REGISTRY_V2_ABI = _SDK_ABI_DATA["lifecycleRegistryAbi"]
LAUNCH_DIRECTORY_V1_ABI = _SDK_ABI_DATA["lifecycleDirectoryAbi"]
LAUNCH_MARKET_ADAPTER_V1_ABI = _SDK_ABI_DATA["lifecycleAdapterAbi"]
POOL_MARKET_ADAPTER_V1_ABI = _SDK_ABI_DATA["poolMarketAdapterV1Abi"]
ABYSS_MARKET_ADAPTER_V1_ABI = _SDK_ABI_DATA["abyssLifecycleAdapterAbi"]
V4_FEE_LIQUIDITY_LOCKER_V2_ABI = _SDK_ABI_DATA["lifecycleV4LockerAbi"]
SHARED_MARKET_ADAPTER_V1_ABI = _SDK_ABI_DATA["sharedMarketAdapterV1Abi"]
LIFECYCLE_V4_HOOK_ABI = _SDK_ABI_DATA["lifecycleV4HookAbi"]
FIXED_FEE_POOL_HOOK_V1_ABI = _SDK_ABI_DATA["fixedFeePoolHookV1Abi"]
FIXED_FEE_POOL_HOOK_CONFIG_V6_ABI = _SDK_ABI_DATA["fixedFeePoolHookConfigV6Abi"]
POOL_HOOK_DEPLOYER_V1_ABI = _SDK_ABI_DATA["poolHookDeployerV1Abi"]
POOL_HOOK_DEPLOYER_CONFIG_V6_ABI = _SDK_ABI_DATA["poolHookDeployerConfigV6Abi"]
SHARED_HOOK_DEPLOYER_V1_ABI = _SDK_ABI_DATA["sharedHookDeployerV1Abi"]
POOL_FEE_COLLECTOR_FACTORY_V1_ABI = _SDK_ABI_DATA["poolFeeCollectorFactoryV1Abi"]
POOL_FEE_COLLECTOR_FACTORY_CONFIG_V6_ABI = _SDK_ABI_DATA["poolFeeCollectorFactoryConfigV6Abi"]
LAUNCH_TOKEN_FACTORY_V1_ABI = _SDK_ABI_DATA["lifecycleTokenFactoryAbi"]
LAUNCH_TOKEN_CONTEXT_V1_ABI = _SDK_ABI_DATA["lifecycleTokenContextAbi"]
LAUNCH_ERC20_V1_ABI = _SDK_ABI_DATA["lifecycleErc20Abi"]
LAUNCH_ERC404_V1_ABI = _SDK_ABI_DATA["lifecycleErc404Abi"]
LAUNCH_ERC404_MIRROR_V1_ABI = _SDK_ABI_DATA["lifecycleErc404MirrorAbi"]
LAUNCH_FEE_OWNER_REGISTRY_V2_ABI = _SDK_ABI_DATA["launchFeeOwnerRegistryAbi"]
LAUNCH_FEE_HUB_V3_ABI = _SDK_ABI_DATA["lifecycleFeeHubAbi"]
LAUNCH_FEE_HUB_FACTORY_V3_ABI = _SDK_ABI_DATA["lifecycleFeeHubFactoryAbi"]
MULTI_ASSET_REWARDS_V1_ABI = _SDK_ABI_DATA["lifecycleRewardsAbi"]
LIFECYCLE_DIVIDEND_V1_ABI = _SDK_ABI_DATA["lifecycleDividendAbi"]
LAUNCH_FUNDING_ESCROW_V1_ABI = _SDK_ABI_DATA["lifecycleFundingEscrowAbi"]
LIFECYCLE_ORACLE_FACTORY_ABI = _SDK_ABI_DATA["lifecycleOracleFactoryAbi"]


def _argument(name, abi_type, *, components=None):
    value = {"name": name, "type": abi_type}
    if components is not None:
        value["components"] = components
    return value


def _function(name, inputs=(), outputs=(), state_mutability="view"):
    return {"type": "function", "name": name, "stateMutability": state_mutability, "inputs": list(inputs), "outputs": list(outputs)}


# Official Nitro precompile and virtual NodeInterface methods are private backend
# evidence, not a guessed generic-EVM substitute for native economic admission.
NITRO_ARB_SYS_ABI = [_function("arbOSVersion", [], [_argument("", "uint256")])]
NITRO_ARB_GAS_INFO_ABI = [
    _function("getMaxTxGasLimit", [], [_argument("", "uint256")]),
    _function("getMaxBlockGasLimit", [], [_argument("", "uint64")]),
]
NITRO_NODE_INTERFACE_ABI = [
    _function("gasEstimateL1Component", [
        _argument("to", "address"), _argument("contractCreation", "bool"), _argument("data", "bytes"),
    ], [
        _argument("gasEstimateForL1", "uint64"), _argument("baseFee", "uint256"), _argument("l1BaseFeeEstimate", "uint256"),
    ], "payable"),
]
DEVELOPER_TERMS_COMPONENTS_V3 = [
    _argument("adapter", "address"), _argument("beneficiary", "address"),
    _argument("maximumDeveloperFeeBps", "uint16"), _argument("termsDigest", "bytes32"), _argument("enabled", "bool"),
]
POOL_BOUND_HOOK_DEPLOYMENT_COMPONENTS_V1 = [
    _argument("deployer", "address"), _argument("initCodeHash", "bytes32"),
    _argument("salt", "bytes32"), _argument("predictedHook", "address"),
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
    _function("progress", [_argument("launchId", "bytes32")], [_argument("progress", "tuple", components=LAUNCH_PROGRESS_COMPONENTS_V1)]),
    _function("launchPage", [_argument("offset", "uint256"), _argument("limit", "uint256")], [_argument("", "tuple[]", components=LAUNCH_PROGRESS_COMPONENTS_V1)]),
    _function("liveMarket", [_argument("launchId", "bytes32"), _argument("marketIndex", "uint32")], [_argument("adapter", "address"), _argument("identity", "tuple", components=PREPARED_MARKET_COMPONENTS_V1), _argument("state", "tuple", components=MARKET_LIVE_STATE_COMPONENTS_V1)]),
    _function("livePositions", [_argument("launchId", "bytes32"), _argument("marketIndex", "uint32"), _argument("offset", "uint256"), _argument("limit", "uint256")], [_argument("", "tuple[]", components=LIVE_POSITION_COMPONENTS_V1)]),
    _function("feeRoute", [_argument("token", "address")], [_argument("", "tuple", components=FEE_ROUTE_COMPONENTS_V1)]),
]

__all__ = [name for name in globals() if name.isupper() and not name.startswith("_")]
