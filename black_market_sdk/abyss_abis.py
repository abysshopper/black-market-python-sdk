"""Black Market protocol ABIs, auto-ported from the canonical TypeScript SDK (@black-market/sdk).

Each ABI is a list of ABI entry dicts suitable for ``web3.eth.contract(abi=...)``.
"""

from __future__ import annotations


ABYSS_FACTORY_ABI = [
  {
   "type": "function",
   "name": "feeAmountTickSpacing",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "fee",
     "type": "uint24",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "int24",
    },
   ],
  },
  {
   "type": "function",
   "name": "oracleConfigs",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "id",
     "type": "bytes32",
    },
   ],
   "outputs": [
    {
     "name": "maxAbsTickMove",
     "type": "uint24",
    },
    {
     "name": "cardinality",
     "type": "uint16",
    },
   ],
  },
  {
   "type": "function",
   "name": "getPool",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "poolId",
     "type": "bytes32",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "isPool",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "pool",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "computePoolId",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "key",
     "type": "tuple",
     "components": [
      {
       "name": "token0",
       "type": "address",
      },
      {
       "name": "token1",
       "type": "address",
      },
      {
       "name": "profile",
       "type": "uint8",
      },
      {
       "name": "fee",
       "type": "uint24",
      },
      {
       "name": "quoteIsToken0",
       "type": "bool",
      },
      {
       "name": "oracleConfigId",
       "type": "bytes32",
      },
     ],
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "computePoolAddress",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "key",
     "type": "tuple",
     "components": [
      {
       "name": "token0",
       "type": "address",
      },
      {
       "name": "token1",
       "type": "address",
      },
      {
       "name": "profile",
       "type": "uint8",
      },
      {
       "name": "fee",
       "type": "uint24",
      },
      {
       "name": "quoteIsToken0",
       "type": "bool",
      },
      {
       "name": "oracleConfigId",
       "type": "bytes32",
      },
     ],
    },
   ],
   "outputs": [
    {
     "name": "predicted",
     "type": "address",
    },
   ],
  },
  {
   "type": "event",
   "name": "PoolCreated",
   "inputs": [
    {
     "name": "token0",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "token1",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "fee",
     "type": "uint24",
     "indexed": True,
    },
    {
     "name": "profile",
     "type": "uint8",
     "indexed": False,
    },
    {
     "name": "quoteIsToken0",
     "type": "bool",
     "indexed": False,
    },
    {
     "name": "oracleConfigId",
     "type": "bytes32",
     "indexed": False,
    },
    {
     "name": "pool",
     "type": "address",
     "indexed": False,
    },
   ],
  },
 ]

ABYSS_POOL_ABI = [
  {
   "type": "function",
   "name": "factory",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "token0",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "token1",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "fee",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint24",
    },
   ],
  },
  {
   "type": "function",
   "name": "tickSpacing",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "int24",
    },
   ],
  },
  {
   "type": "function",
   "name": "liquidity",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint128",
    },
   ],
  },
  {
   "type": "function",
   "name": "slot0",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "sqrtPriceX96",
     "type": "uint160",
    },
    {
     "name": "tick",
     "type": "int24",
    },
    {
     "name": "observationIndex",
     "type": "uint16",
    },
    {
     "name": "observationCardinality",
     "type": "uint16",
    },
    {
     "name": "observationCardinalityNext",
     "type": "uint16",
    },
    {
     "name": "feeProtocol",
     "type": "uint8",
    },
    {
     "name": "unlocked",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "observeTruncated",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "secondsAgos",
     "type": "uint32[]",
    },
   ],
   "outputs": [
    {
     "name": "tickCumulatives",
     "type": "int56[]",
    },
    {
     "name": "secondsPerLiquidityCumulativeX128s",
     "type": "uint160[]",
    },
   ],
  },
  {
   "type": "function",
   "name": "quoteIsToken0",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
 ]

ABYSS_POSITION_MANAGER_ABI = [
  {
   "type": "function",
   "name": "factory",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "nextTokenId",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "positions",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "tokenId",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "account",
     "type": "address",
    },
    {
     "name": "pool",
     "type": "address",
    },
    {
     "name": "tickLower",
     "type": "int24",
    },
    {
     "name": "tickUpper",
     "type": "int24",
    },
    {
     "name": "liquidity",
     "type": "uint128",
    },
   ],
  },
  {
   "type": "function",
   "name": "accountFor",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "tokenId",
     "type": "uint256",
    },
    {
     "name": "pool",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "account",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "createAndInitializePoolIfNecessary",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "key",
     "type": "tuple",
     "components": [
      {
       "name": "token0",
       "type": "address",
      },
      {
       "name": "token1",
       "type": "address",
      },
      {
       "name": "profile",
       "type": "uint8",
      },
      {
       "name": "fee",
       "type": "uint24",
      },
      {
       "name": "quoteIsToken0",
       "type": "bool",
      },
      {
       "name": "oracleConfigId",
       "type": "bytes32",
      },
     ],
    },
    {
     "name": "sqrtPriceX96",
     "type": "uint160",
    },
    {
     "name": "existingPriceMinimumX96",
     "type": "uint160",
    },
    {
     "name": "existingPriceMaximumX96",
     "type": "uint160",
    },
   ],
   "outputs": [
    {
     "name": "pool",
     "type": "address",
    },
    {
     "name": "created",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "mint",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "pool",
     "type": "address",
    },
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "tickLower",
     "type": "int24",
    },
    {
     "name": "tickUpper",
     "type": "int24",
    },
    {
     "name": "liquidity",
     "type": "uint128",
    },
    {
     "name": "amount0Maximum",
     "type": "uint256",
    },
    {
     "name": "amount1Maximum",
     "type": "uint256",
    },
    {
     "name": "deadline",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "tokenId",
     "type": "uint256",
    },
    {
     "name": "amount0",
     "type": "uint256",
    },
    {
     "name": "amount1",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "multicall",
   "stateMutability": "payable",
   "inputs": [
    {
     "name": "data",
     "type": "bytes[]",
    },
   ],
   "outputs": [
    {
     "name": "results",
     "type": "bytes[]",
    },
   ],
  },
  {
   "type": "function",
   "name": "safeTransferFrom",
   "stateMutability": "payable",
   "inputs": [
    {
     "name": "from",
     "type": "address",
    },
    {
     "name": "to",
     "type": "address",
    },
    {
     "name": "id",
     "type": "uint256",
    },
   ],
   "outputs": [],
  },
  {
   "type": "function",
   "name": "safeTransferFrom",
   "stateMutability": "payable",
   "inputs": [
    {
     "name": "from",
     "type": "address",
    },
    {
     "name": "to",
     "type": "address",
    },
    {
     "name": "id",
     "type": "uint256",
    },
    {
     "name": "data",
     "type": "bytes",
    },
   ],
   "outputs": [],
  },
 ]

ABYSS_POSITION_LOCKER_ABI = [
  {
   "type": "function",
   "name": "positionManager",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "locks",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "tokenId",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "owner",
     "type": "address",
    },
    {
     "name": "claimAuthority",
     "type": "address",
    },
    {
     "name": "feeRecipient",
     "type": "address",
    },
    {
     "name": "unlockTime",
     "type": "uint64",
    },
    {
     "name": "permissionlessClaim",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "claim",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "tokenId",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "amount0",
     "type": "uint128",
    },
    {
     "name": "amount1",
     "type": "uint128",
    },
   ],
  },
 ]

ABYSS_ROUTER_ABI = [
  {
   "type": "function",
   "name": "factory",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "exactInputSingle",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "key",
     "type": "tuple",
     "components": [
      {
       "name": "token0",
       "type": "address",
      },
      {
       "name": "token1",
       "type": "address",
      },
      {
       "name": "profile",
       "type": "uint8",
      },
      {
       "name": "fee",
       "type": "uint24",
      },
      {
       "name": "quoteIsToken0",
       "type": "bool",
      },
      {
       "name": "oracleConfigId",
       "type": "bytes32",
      },
     ],
    },
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "zeroForOne",
     "type": "bool",
    },
    {
     "name": "amountIn",
     "type": "uint256",
    },
    {
     "name": "amountOutMinimum",
     "type": "uint256",
    },
    {
     "name": "sqrtPriceLimitX96",
     "type": "uint160",
    },
    {
     "name": "deadline",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "amountOut",
     "type": "uint256",
    },
   ],
  },
 ]

ABYSS_FIXED_SUPPLY_TOKEN_ABI = [
  {
   "type": "constructor",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "name_",
     "type": "string",
    },
    {
     "name": "symbol_",
     "type": "string",
    },
    {
     "name": "decimals_",
     "type": "uint8",
    },
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "supply",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "name",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "string",
    },
   ],
  },
  {
   "type": "function",
   "name": "symbol",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "string",
    },
   ],
  },
  {
   "type": "function",
   "name": "decimals",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "totalSupply",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "balanceOf",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "allowance",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "owner",
     "type": "address",
    },
    {
     "name": "spender",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "approve",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "spender",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "transfer",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "transferFrom",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "sender",
     "type": "address",
    },
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
 ]

BURNABLE_FIXED_SUPPLY_TOKEN_ABI = [
  {
   "type": "constructor",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "name_",
     "type": "string",
    },
    {
     "name": "symbol_",
     "type": "string",
    },
    {
     "name": "decimals_",
     "type": "uint8",
    },
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "supply",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "name",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "string",
    },
   ],
  },
  {
   "type": "function",
   "name": "symbol",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "string",
    },
   ],
  },
  {
   "type": "function",
   "name": "decimals",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "totalSupply",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "balanceOf",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "allowance",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "owner",
     "type": "address",
    },
    {
     "name": "spender",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "approve",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "spender",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "transfer",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "transferFrom",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "sender",
     "type": "address",
    },
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "burn",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [],
  },
  {
   "type": "function",
   "name": "burnFrom",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [],
  },
 ]

LAUNCH_TOKEN_BURN_SINK_ABI = [
  {
   "type": "constructor",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "token_",
     "type": "address",
    },
   ],
  },
  {
   "type": "error",
   "name": "BurnAccountingMismatch",
   "inputs": [
    {
     "name": "balanceBefore",
     "type": "uint256",
    },
    {
     "name": "balanceAfter",
     "type": "uint256",
    },
    {
     "name": "supplyBefore",
     "type": "uint256",
    },
    {
     "name": "supplyAfter",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "error",
   "name": "BurnFailed",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidToken",
   "inputs": [
    {
     "name": "token",
     "type": "address",
    },
   ],
  },
  {
   "type": "error",
   "name": "Reentrancy",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "ZeroBalance",
   "inputs": [],
  },
  {
   "type": "event",
   "name": "Burned",
   "inputs": [
    {
     "name": "caller",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "function",
   "name": "burn",
   "stateMutability": "nonpayable",
   "inputs": [],
   "outputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "token",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
 ]

LAUNCH_TOKEN_FACTORY_ABI = [
  {
   "type": "function",
   "name": "configurator",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "launcher",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "DEPLOYMENT_SALT_DOMAIN",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "deploymentNonce",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "setLauncher",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "launcher_",
     "type": "address",
    },
   ],
   "outputs": [],
  },
  {
   "type": "function",
   "name": "deploy",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "creator",
     "type": "address",
    },
    {
     "name": "kind",
     "type": "uint8",
    },
    {
     "name": "name",
     "type": "string",
    },
    {
     "name": "symbol",
     "type": "string",
    },
    {
     "name": "decimals",
     "type": "uint8",
    },
    {
     "name": "supply",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "token",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "computeDeploymentSalt",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "creator",
     "type": "address",
    },
    {
     "name": "nonce",
     "type": "uint256",
    },
    {
     "name": "prevrandao",
     "type": "uint256",
    },
    {
     "name": "kind",
     "type": "uint8",
    },
    {
     "name": "name",
     "type": "string",
    },
    {
     "name": "symbol",
     "type": "string",
    },
    {
     "name": "decimals",
     "type": "uint8",
    },
    {
     "name": "supply",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "predictTokenAddress",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "creator",
     "type": "address",
    },
    {
     "name": "nonce",
     "type": "uint256",
    },
    {
     "name": "prevrandao",
     "type": "uint256",
    },
    {
     "name": "kind",
     "type": "uint8",
    },
    {
     "name": "name",
     "type": "string",
    },
    {
     "name": "symbol",
     "type": "string",
    },
    {
     "name": "decimals",
     "type": "uint8",
    },
    {
     "name": "supply",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "token",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "launchAuthority",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "token",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "authority",
     "type": "address",
    },
   ],
  },
  {
   "type": "event",
   "name": "TokenDeployed",
   "inputs": [
    {
     "name": "token",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "creator",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "salt",
     "type": "bytes32",
     "indexed": True,
    },
    {
     "name": "kind",
     "type": "uint8",
     "indexed": False,
    },
    {
     "name": "nonce",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "prevrandao",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "event",
   "name": "LauncherConfigured",
   "inputs": [
    {
     "name": "launcher",
     "type": "address",
     "indexed": True,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "error",
   "name": "EmptyName",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "EmptySymbol",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "ZeroSupply",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "ZeroCreator",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "Unauthorized",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "AlreadyConfigured",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidLauncher",
   "inputs": [],
  },
 ]

ABYSS_LAUNCH_COORDINATOR_ABI = [
  {
   "type": "constructor",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "factory_",
     "type": "address",
    },
    {
     "name": "positionManager_",
     "type": "address",
    },
    {
     "name": "positionLocker_",
     "type": "address",
    },
    {
     "name": "tokenFactory_",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "factory",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "positionManager",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "positionLocker",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "tokenFactory",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "launches",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "launchedToken",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "result",
     "type": "tuple",
     "components": [
      {
       "name": "creator",
       "type": "address",
      },
      {
       "name": "feeRecipient",
       "type": "address",
      },
      {
       "name": "pool",
       "type": "address",
      },
      {
       "name": "positionAccount",
       "type": "address",
      },
      {
       "name": "tokenId",
       "type": "uint256",
      },
      {
       "name": "amount0",
       "type": "uint256",
      },
      {
       "name": "amount1",
       "type": "uint256",
      },
      {
       "name": "created",
       "type": "bool",
      },
     ],
    },
   ],
  },
  {
   "type": "function",
   "name": "launch",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "params",
     "type": "tuple",
     "components": [
      {
       "name": "launchedToken",
       "type": "address",
      },
      {
       "name": "feeRecipient",
       "type": "address",
      },
      {
       "name": "creator",
       "type": "address",
      },
      {
       "name": "key",
       "type": "tuple",
       "components": [
        {
         "name": "token0",
         "type": "address",
        },
        {
         "name": "token1",
         "type": "address",
        },
        {
         "name": "profile",
         "type": "uint8",
        },
        {
         "name": "fee",
         "type": "uint24",
        },
        {
         "name": "quoteIsToken0",
         "type": "bool",
        },
        {
         "name": "oracleConfigId",
         "type": "bytes32",
        },
       ],
      },
      {
       "name": "sqrtPriceX96",
       "type": "uint160",
      },
      {
       "name": "existingPriceMinimumX96",
       "type": "uint160",
      },
      {
       "name": "existingPriceMaximumX96",
       "type": "uint160",
      },
      {
       "name": "tickLower",
       "type": "int24",
      },
      {
       "name": "tickUpper",
       "type": "int24",
      },
      {
       "name": "liquidity",
       "type": "uint128",
      },
      {
       "name": "amount0Maximum",
       "type": "uint256",
      },
      {
       "name": "amount1Maximum",
       "type": "uint256",
      },
      {
       "name": "deadline",
       "type": "uint256",
      },
     ],
    },
   ],
   "outputs": [
    {
     "name": "result",
     "type": "tuple",
     "components": [
      {
       "name": "creator",
       "type": "address",
      },
      {
       "name": "feeRecipient",
       "type": "address",
      },
      {
       "name": "pool",
       "type": "address",
      },
      {
       "name": "positionAccount",
       "type": "address",
      },
      {
       "name": "tokenId",
       "type": "uint256",
      },
      {
       "name": "amount0",
       "type": "uint256",
      },
      {
       "name": "amount1",
       "type": "uint256",
      },
      {
       "name": "created",
       "type": "bool",
      },
     ],
    },
   ],
  },
  {
   "type": "event",
   "name": "LaunchCompleted",
   "inputs": [
    {
     "name": "launchedToken",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "creator",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "pool",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "tokenId",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "positionAccount",
     "type": "address",
     "indexed": False,
    },
    {
     "name": "feeRecipient",
     "type": "address",
     "indexed": False,
    },
    {
     "name": "amount0",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "amount1",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "created",
     "type": "bool",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
 ]

DISPOSITION_COMPONENTS = [
  {
   "name": "ownerBps",
   "type": "uint16",
  },
  {
   "name": "rewardsBps",
   "type": "uint16",
  },
  {
   "name": "burnBps",
   "type": "uint16",
  },
 ]

LAUNCH_TEMPLATE_COMPONENTS = [
  {
   "name": "tokenKindMask",
   "type": "uint8",
  },
  {
   "name": "poolProfileMask",
   "type": "uint8",
  },
  {
   "name": "rewardMode",
   "type": "uint8",
  },
  {
   "name": "feeAssetMode",
   "type": "uint8",
  },
  {
   "name": "rewardDuration",
   "type": "uint32",
  },
  {
   "name": "launchedTokenIsQuote",
   "type": "bool",
  },
  {
   "name": "launchedTokenDestinations",
   "type": "uint8",
  },
  {
   "name": "pairedTokenDestinations",
   "type": "uint8",
  },
 ]

LAUNCH_TEMPLATE_REGISTRY_ABI = [
  {
   "type": "function",
   "name": "STANDARD_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "QUOTE_STAKING_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "QUOTE_DIVIDENDS_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "DUAL_STAKING_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "DUAL_DIVIDENDS_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "FEE_BURN_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "INITIAL_VERSION",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint32",
    },
   ],
  },
  {
   "type": "function",
   "name": "REWARD_DURATION",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint32",
    },
   ],
  },
  {
   "type": "function",
   "name": "DESTINATION_OWNER",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "DESTINATION_REWARDS",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "DESTINATION_BURN",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "REWARD_MODE_NONE",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "REWARD_MODE_STAKING",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "REWARD_MODE_DIVIDENDS",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "FEE_ASSET_MODE_PROFILE",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "FEE_ASSET_MODE_PAIRED_ONLY",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "FEE_ASSET_MODE_BOTH",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "FEE_ASSET_MODE_LAUNCHED_ONLY",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "registrar",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "registerTemplate",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "templateId",
     "type": "bytes32",
    },
    {
     "name": "version",
     "type": "uint32",
    },
    {
     "name": "config",
     "type": "tuple",
     "components": [
      {
       "name": "tokenKindMask",
       "type": "uint8",
      },
      {
       "name": "poolProfileMask",
       "type": "uint8",
      },
      {
       "name": "rewardMode",
       "type": "uint8",
      },
      {
       "name": "feeAssetMode",
       "type": "uint8",
      },
      {
       "name": "rewardDuration",
       "type": "uint32",
      },
      {
       "name": "launchedTokenIsQuote",
       "type": "bool",
      },
      {
       "name": "launchedTokenDestinations",
       "type": "uint8",
      },
      {
       "name": "pairedTokenDestinations",
       "type": "uint8",
      },
     ],
    },
   ],
   "outputs": [],
  },
  {
   "type": "function",
   "name": "template",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "templateId",
     "type": "bytes32",
    },
    {
     "name": "version",
     "type": "uint32",
    },
   ],
   "outputs": [
    {
     "name": "config",
     "type": "tuple",
     "components": [
      {
       "name": "tokenKindMask",
       "type": "uint8",
      },
      {
       "name": "poolProfileMask",
       "type": "uint8",
      },
      {
       "name": "rewardMode",
       "type": "uint8",
      },
      {
       "name": "feeAssetMode",
       "type": "uint8",
      },
      {
       "name": "rewardDuration",
       "type": "uint32",
      },
      {
       "name": "launchedTokenIsQuote",
       "type": "bool",
      },
      {
       "name": "launchedTokenDestinations",
       "type": "uint8",
      },
      {
       "name": "pairedTokenDestinations",
       "type": "uint8",
      },
     ],
    },
   ],
  },
  {
   "type": "function",
   "name": "isRegistered",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "templateId",
     "type": "bytes32",
    },
    {
     "name": "version",
     "type": "uint32",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "templateKey",
   "stateMutability": "pure",
   "inputs": [
    {
     "name": "templateId",
     "type": "bytes32",
    },
    {
     "name": "version",
     "type": "uint32",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "supportsTokenKind",
   "stateMutability": "pure",
   "inputs": [
    {
     "name": "config",
     "type": "tuple",
     "components": [
      {
       "name": "tokenKindMask",
       "type": "uint8",
      },
      {
       "name": "poolProfileMask",
       "type": "uint8",
      },
      {
       "name": "rewardMode",
       "type": "uint8",
      },
      {
       "name": "feeAssetMode",
       "type": "uint8",
      },
      {
       "name": "rewardDuration",
       "type": "uint32",
      },
      {
       "name": "launchedTokenIsQuote",
       "type": "bool",
      },
      {
       "name": "launchedTokenDestinations",
       "type": "uint8",
      },
      {
       "name": "pairedTokenDestinations",
       "type": "uint8",
      },
     ],
    },
    {
     "name": "kind",
     "type": "uint8",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "supportsPoolProfile",
   "stateMutability": "pure",
   "inputs": [
    {
     "name": "config",
     "type": "tuple",
     "components": [
      {
       "name": "tokenKindMask",
       "type": "uint8",
      },
      {
       "name": "poolProfileMask",
       "type": "uint8",
      },
      {
       "name": "rewardMode",
       "type": "uint8",
      },
      {
       "name": "feeAssetMode",
       "type": "uint8",
      },
      {
       "name": "rewardDuration",
       "type": "uint32",
      },
      {
       "name": "launchedTokenIsQuote",
       "type": "bool",
      },
      {
       "name": "launchedTokenDestinations",
       "type": "uint8",
      },
      {
       "name": "pairedTokenDestinations",
       "type": "uint8",
      },
     ],
    },
    {
     "name": "profile",
     "type": "uint8",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "supportsDispositions",
   "stateMutability": "pure",
   "inputs": [
    {
     "name": "config",
     "type": "tuple",
     "components": [
      {
       "name": "tokenKindMask",
       "type": "uint8",
      },
      {
       "name": "poolProfileMask",
       "type": "uint8",
      },
      {
       "name": "rewardMode",
       "type": "uint8",
      },
      {
       "name": "feeAssetMode",
       "type": "uint8",
      },
      {
       "name": "rewardDuration",
       "type": "uint32",
      },
      {
       "name": "launchedTokenIsQuote",
       "type": "bool",
      },
      {
       "name": "launchedTokenDestinations",
       "type": "uint8",
      },
      {
       "name": "pairedTokenDestinations",
       "type": "uint8",
      },
     ],
    },
    {
     "name": "profile",
     "type": "uint8",
    },
    {
     "name": "launchedTokenFees",
     "type": "tuple",
     "components": [
      {
       "name": "ownerBps",
       "type": "uint16",
      },
      {
       "name": "rewardsBps",
       "type": "uint16",
      },
      {
       "name": "burnBps",
       "type": "uint16",
      },
     ],
    },
    {
     "name": "pairedTokenFees",
     "type": "tuple",
     "components": [
      {
       "name": "ownerBps",
       "type": "uint16",
      },
      {
       "name": "rewardsBps",
       "type": "uint16",
      },
      {
       "name": "burnBps",
       "type": "uint16",
      },
     ],
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "event",
   "name": "TemplateRegistered",
   "inputs": [
    {
     "name": "templateId",
     "type": "bytes32",
     "indexed": True,
    },
    {
     "name": "version",
     "type": "uint32",
     "indexed": True,
    },
    {
     "name": "config",
     "type": "tuple",
     "components": [
      {
       "name": "tokenKindMask",
       "type": "uint8",
      },
      {
       "name": "poolProfileMask",
       "type": "uint8",
      },
      {
       "name": "rewardMode",
       "type": "uint8",
      },
      {
       "name": "feeAssetMode",
       "type": "uint8",
      },
      {
       "name": "rewardDuration",
       "type": "uint32",
      },
      {
       "name": "launchedTokenIsQuote",
       "type": "bool",
      },
      {
       "name": "launchedTokenDestinations",
       "type": "uint8",
      },
      {
       "name": "pairedTokenDestinations",
       "type": "uint8",
      },
     ],
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "error",
   "name": "InvalidTemplate",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "TemplateAlreadyRegistered",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "Unauthorized",
   "inputs": [],
  },
 ]

ATOMIC_TOKEN_CONFIG_COMPONENTS = [
  {
   "name": "kind",
   "type": "uint8",
  },
  {
   "name": "name",
   "type": "string",
  },
  {
   "name": "symbol",
   "type": "string",
  },
  {
   "name": "decimals",
   "type": "uint8",
  },
  {
   "name": "supply",
   "type": "uint256",
  },
 ]

ATOMIC_POOL_CONFIG_COMPONENTS = [
  {
   "name": "pairedToken",
   "type": "address",
  },
  {
   "name": "launchedTokenIsQuote",
   "type": "bool",
  },
  {
   "name": "profile",
   "type": "uint8",
  },
  {
   "name": "fee",
   "type": "uint24",
  },
  {
   "name": "oracleConfigId",
   "type": "bytes32",
  },
  {
   "name": "launchTick",
   "type": "int24",
  },
  {
   "name": "liquidity",
   "type": "uint128",
  },
  {
   "name": "launchedTokenAmountMaximum",
   "type": "uint256",
  },
  {
   "name": "pairedTokenAmountMaximum",
   "type": "uint256",
  },
 ]

ATOMIC_INITIAL_BUY_COMPONENTS = [
  {
   "name": "pairedTokenAmountIn",
   "type": "uint256",
  },
  {
   "name": "launchedTokenAmountOutMinimum",
   "type": "uint256",
  },
  {
   "name": "sqrtPriceLimitX96",
   "type": "uint160",
  },
 ]

ATOMIC_LAUNCH_REQUEST_COMPONENTS = [
  {
   "name": "creator",
   "type": "address",
  },
  {
   "name": "templateId",
   "type": "bytes32",
  },
  {
   "name": "templateVersion",
   "type": "uint32",
  },
  {
   "name": "token",
   "type": "tuple",
   "components": [
    {
     "name": "kind",
     "type": "uint8",
    },
    {
     "name": "name",
     "type": "string",
    },
    {
     "name": "symbol",
     "type": "string",
    },
    {
     "name": "decimals",
     "type": "uint8",
    },
    {
     "name": "supply",
     "type": "uint256",
    },
   ],
  },
  {
   "name": "pool",
   "type": "tuple",
   "components": [
    {
     "name": "pairedToken",
     "type": "address",
    },
    {
     "name": "launchedTokenIsQuote",
     "type": "bool",
    },
    {
     "name": "profile",
     "type": "uint8",
    },
    {
     "name": "fee",
     "type": "uint24",
    },
    {
     "name": "oracleConfigId",
     "type": "bytes32",
    },
    {
     "name": "launchTick",
     "type": "int24",
    },
    {
     "name": "liquidity",
     "type": "uint128",
    },
    {
     "name": "launchedTokenAmountMaximum",
     "type": "uint256",
    },
    {
     "name": "pairedTokenAmountMaximum",
     "type": "uint256",
    },
   ],
  },
  {
   "name": "initialBuy",
   "type": "tuple",
   "components": [
    {
     "name": "pairedTokenAmountIn",
     "type": "uint256",
    },
    {
     "name": "launchedTokenAmountOutMinimum",
     "type": "uint256",
    },
    {
     "name": "sqrtPriceLimitX96",
     "type": "uint160",
    },
   ],
  },
  {
   "name": "launchedTokenFees",
   "type": "tuple",
   "components": [
    {
     "name": "ownerBps",
     "type": "uint16",
    },
    {
     "name": "rewardsBps",
     "type": "uint16",
    },
    {
     "name": "burnBps",
     "type": "uint16",
    },
   ],
  },
  {
   "name": "pairedTokenFees",
   "type": "tuple",
   "components": [
    {
     "name": "ownerBps",
     "type": "uint16",
    },
    {
     "name": "rewardsBps",
     "type": "uint16",
    },
    {
     "name": "burnBps",
     "type": "uint16",
    },
   ],
  },
  {
   "name": "deadline",
   "type": "uint256",
  },
 ]

ATOMIC_LAUNCH_RECEIPT_COMPONENTS = [
  {
   "name": "token",
   "type": "address",
  },
  {
   "name": "pool",
   "type": "address",
  },
  {
   "name": "tokenId",
   "type": "uint256",
  },
  {
   "name": "liquidityLaunchedTokenAmount",
   "type": "uint256",
  },
  {
   "name": "liquidityPairedTokenAmount",
   "type": "uint256",
  },
  {
   "name": "initialBuyPairedTokenAmount",
   "type": "uint256",
  },
  {
   "name": "initialBuyLaunchedTokenAmount",
   "type": "uint256",
  },
  {
   "name": "rewards",
   "type": "address",
  },
  {
   "name": "splitter",
   "type": "address",
  },
  {
   "name": "feeClaimer",
   "type": "address",
  },
 ]

ATOMIC_LAUNCH_FACTORY_ABI = [
  {
   "type": "function",
   "name": "STANDARD_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "QUOTE_STAKING_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "QUOTE_DIVIDENDS_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "DUAL_STAKING_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "DUAL_DIVIDENDS_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "FEE_BURN_TEMPLATE_ID",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bytes32",
    },
   ],
  },
  {
   "type": "function",
   "name": "INITIAL_TEMPLATE_VERSION",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint32",
    },
   ],
  },
  {
   "type": "function",
   "name": "abyssFactory",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "coordinator",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "feeOwnerRegistry",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "launchFee",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "launchFeeRecipient",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "moduleFactory",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "positionLocker",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "router",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "templateRegistry",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "tokenFactory",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "wrappedNative",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "deployAndLaunch",
   "stateMutability": "payable",
   "inputs": [
    {
     "name": "request",
     "type": "tuple",
     "components": [
      {
       "name": "creator",
       "type": "address",
      },
      {
       "name": "templateId",
       "type": "bytes32",
      },
      {
       "name": "templateVersion",
       "type": "uint32",
      },
      {
       "name": "token",
       "type": "tuple",
       "components": [
        {
         "name": "kind",
         "type": "uint8",
        },
        {
         "name": "name",
         "type": "string",
        },
        {
         "name": "symbol",
         "type": "string",
        },
        {
         "name": "decimals",
         "type": "uint8",
        },
        {
         "name": "supply",
         "type": "uint256",
        },
       ],
      },
      {
       "name": "pool",
       "type": "tuple",
       "components": [
        {
         "name": "pairedToken",
         "type": "address",
        },
        {
         "name": "launchedTokenIsQuote",
         "type": "bool",
        },
        {
         "name": "profile",
         "type": "uint8",
        },
        {
         "name": "fee",
         "type": "uint24",
        },
        {
         "name": "oracleConfigId",
         "type": "bytes32",
        },
        {
         "name": "launchTick",
         "type": "int24",
        },
        {
         "name": "liquidity",
         "type": "uint128",
        },
        {
         "name": "launchedTokenAmountMaximum",
         "type": "uint256",
        },
        {
         "name": "pairedTokenAmountMaximum",
         "type": "uint256",
        },
       ],
      },
      {
       "name": "initialBuy",
       "type": "tuple",
       "components": [
        {
         "name": "pairedTokenAmountIn",
         "type": "uint256",
        },
        {
         "name": "launchedTokenAmountOutMinimum",
         "type": "uint256",
        },
        {
         "name": "sqrtPriceLimitX96",
         "type": "uint160",
        },
       ],
      },
      {
       "name": "launchedTokenFees",
       "type": "tuple",
       "components": [
        {
         "name": "ownerBps",
         "type": "uint16",
        },
        {
         "name": "rewardsBps",
         "type": "uint16",
        },
        {
         "name": "burnBps",
         "type": "uint16",
        },
       ],
      },
      {
       "name": "pairedTokenFees",
       "type": "tuple",
       "components": [
        {
         "name": "ownerBps",
         "type": "uint16",
        },
        {
         "name": "rewardsBps",
         "type": "uint16",
        },
        {
         "name": "burnBps",
         "type": "uint16",
        },
       ],
      },
      {
       "name": "deadline",
       "type": "uint256",
      },
     ],
    },
   ],
   "outputs": [
    {
     "name": "receipt",
     "type": "tuple",
     "components": [
      {
       "name": "token",
       "type": "address",
      },
      {
       "name": "pool",
       "type": "address",
      },
      {
       "name": "tokenId",
       "type": "uint256",
      },
      {
       "name": "liquidityLaunchedTokenAmount",
       "type": "uint256",
      },
      {
       "name": "liquidityPairedTokenAmount",
       "type": "uint256",
      },
      {
       "name": "initialBuyPairedTokenAmount",
       "type": "uint256",
      },
      {
       "name": "initialBuyLaunchedTokenAmount",
       "type": "uint256",
      },
      {
       "name": "rewards",
       "type": "address",
      },
      {
       "name": "splitter",
       "type": "address",
      },
      {
       "name": "feeClaimer",
       "type": "address",
      },
     ],
    },
   ],
  },
  {
   "type": "event",
   "name": "AtomicLaunchCompleted",
   "inputs": [
    {
     "name": "token",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "creator",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "pool",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "templateId",
     "type": "bytes32",
     "indexed": False,
    },
    {
     "name": "templateVersion",
     "type": "uint32",
     "indexed": False,
    },
    {
     "name": "tokenId",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "liquidityLaunchedTokenAmount",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "liquidityPairedTokenAmount",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "initialBuyPairedTokenAmount",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "initialBuyLaunchedTokenAmount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "event",
   "name": "LaunchFeePaid",
   "inputs": [
    {
     "name": "token",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "payer",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "recipient",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "event",
   "name": "LaunchModulesDeployed",
   "inputs": [
    {
     "name": "token",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "rewards",
     "type": "address",
     "indexed": False,
    },
    {
     "name": "splitter",
     "type": "address",
     "indexed": False,
    },
    {
     "name": "feeClaimer",
     "type": "address",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "error",
   "name": "ApprovalMismatch",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "ApproveFailed",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "ExistingPool",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InexactTransfer",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidBinding",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidConfiguration",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidSwapResult",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "Reentrancy",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "TransferFailed",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "TransferFromFailed",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "UnsupportedTemplate",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "IncorrectLaunchFee",
   "inputs": [
    {
     "name": "provided",
     "type": "uint256",
    },
    {
     "name": "required",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "error",
   "name": "LaunchFeePaymentFailed",
   "inputs": [],
  },
 ]

STAKING_REWARD_VAULT_ABI = [
  {
   "type": "function",
   "name": "REWARDS_DURATION",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "claim",
   "stateMutability": "nonpayable",
   "inputs": [],
   "outputs": [
    {
     "name": "amount0",
     "type": "uint256",
    },
    {
     "name": "amount1",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "claimFor",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "beneficiary",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "amount0",
     "type": "uint256",
    },
    {
     "name": "amount1",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "earned",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "amount0",
     "type": "uint256",
    },
    {
     "name": "amount1",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "earned",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
    {
     "name": "rewardToken",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "lastTimeRewardApplicable",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardData",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "periodFinish",
     "type": "uint256",
    },
    {
     "name": "rewardRate",
     "type": "uint256",
    },
    {
     "name": "lastUpdateTime",
     "type": "uint256",
    },
    {
     "name": "rewardPerTokenStored",
     "type": "uint256",
    },
    {
     "name": "rewardPerTokenRemainder",
     "type": "uint256",
    },
    {
     "name": "remainingRewards",
     "type": "uint256",
    },
    {
     "name": "queuedRewards",
     "type": "uint256",
    },
    {
     "name": "accountedBalance",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardPerToken",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardToken0",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardToken1",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardsDistributor",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "event",
   "name": "RewardNotified",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "totalScheduled",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "event",
   "name": "RewardQueued",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "event",
   "name": "RewardPaid",
   "inputs": [
    {
     "name": "beneficiary",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "rewardToken",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "error",
   "name": "InexactTransfer",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidRewardTokens",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "NothingToClaim",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "RewardBalanceDeficit",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "Unauthorized",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "UnsupportedRewardToken",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "ZeroAddress",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "ZeroAmount",
   "inputs": [],
  },
  {
   "type": "function",
   "name": "balanceOf",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "stake",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [],
  },
  {
   "type": "function",
   "name": "stakingToken",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "totalSupply",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "withdraw",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [],
  },
  {
   "type": "event",
   "name": "Staked",
   "inputs": [
    {
     "name": "account",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "event",
   "name": "Withdrawn",
   "inputs": [
    {
     "name": "account",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "error",
   "name": "InsufficientStake",
   "inputs": [],
  },
 ]

HOLDER_DIVIDEND_TRACKER_ABI = [
  {
   "type": "function",
   "name": "REWARDS_DURATION",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "claim",
   "stateMutability": "nonpayable",
   "inputs": [],
   "outputs": [
    {
     "name": "amount0",
     "type": "uint256",
    },
    {
     "name": "amount1",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "claimFor",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "beneficiary",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "amount0",
     "type": "uint256",
    },
    {
     "name": "amount1",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "earned",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "amount0",
     "type": "uint256",
    },
    {
     "name": "amount1",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "earned",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
    {
     "name": "rewardToken",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "lastTimeRewardApplicable",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardData",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "periodFinish",
     "type": "uint256",
    },
    {
     "name": "rewardRate",
     "type": "uint256",
    },
    {
     "name": "lastUpdateTime",
     "type": "uint256",
    },
    {
     "name": "rewardPerTokenStored",
     "type": "uint256",
    },
    {
     "name": "rewardPerTokenRemainder",
     "type": "uint256",
    },
    {
     "name": "remainingRewards",
     "type": "uint256",
    },
    {
     "name": "queuedRewards",
     "type": "uint256",
    },
    {
     "name": "accountedBalance",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardPerToken",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardToken0",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardToken1",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewardsDistributor",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "event",
   "name": "RewardNotified",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "totalScheduled",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "event",
   "name": "RewardQueued",
   "inputs": [
    {
     "name": "rewardToken",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "event",
   "name": "RewardPaid",
   "inputs": [
    {
     "name": "beneficiary",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "rewardToken",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "error",
   "name": "InexactTransfer",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidRewardTokens",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "NothingToClaim",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "RewardBalanceDeficit",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "Unauthorized",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "UnsupportedRewardToken",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "ZeroAddress",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "ZeroAmount",
   "inputs": [],
  },
  {
   "type": "function",
   "name": "eligibleBalanceOf",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "eligibleSupply",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "isExcluded",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "excluded",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "protocolAdmin",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "trackedToken",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "event",
   "name": "AccountExcluded",
   "inputs": [
    {
     "name": "account",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "initial",
     "type": "bool",
     "indexed": True,
    },
   ],
   "anonymous": False,
  },
 ]

HOLDER_DIVIDEND_TOKEN_ABI = [
  {
   "type": "constructor",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "name_",
     "type": "string",
    },
    {
     "name": "symbol_",
     "type": "string",
    },
    {
     "name": "decimals_",
     "type": "uint8",
    },
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "supply",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "name",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "string",
    },
   ],
  },
  {
   "type": "function",
   "name": "symbol",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "string",
    },
   ],
  },
  {
   "type": "function",
   "name": "decimals",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint8",
    },
   ],
  },
  {
   "type": "function",
   "name": "totalSupply",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "balanceOf",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "allowance",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "owner",
     "type": "address",
    },
    {
     "name": "spender",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "approve",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "spender",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "transfer",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "transferFrom",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "sender",
     "type": "address",
    },
    {
     "name": "recipient",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "burn",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [],
  },
  {
   "type": "function",
   "name": "burnFrom",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "account",
     "type": "address",
    },
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
   "outputs": [],
  },
  {
   "type": "function",
   "name": "rewardTracker",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "event",
   "name": "RewardTrackerConfigured",
   "inputs": [
    {
     "name": "tracker",
     "type": "address",
     "indexed": True,
    },
   ],
   "anonymous": False,
  },
 ]

LAUNCH_FEE_SPLITTER_ABI = [
  {
   "type": "function",
   "name": "claimableOwnerFees",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "owner",
     "type": "address",
    },
    {
     "name": "token",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "claimer",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "claimOwnerFees",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "token",
     "type": "address",
    },
    {
     "name": "recipient",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "amount",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "distribute",
   "stateMutability": "nonpayable",
   "inputs": [
    {
     "name": "token",
     "type": "address",
    },
   ],
   "outputs": [],
  },
  {
   "type": "function",
   "name": "launchToken",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "requiresRewards",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "bool",
    },
   ],
  },
  {
   "type": "function",
   "name": "rewards",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "token0",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "token0Policy",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "tuple",
     "components": [
      {
       "name": "ownerBps",
       "type": "uint16",
      },
      {
       "name": "rewardsBps",
       "type": "uint16",
      },
      {
       "name": "burnBps",
       "type": "uint16",
      },
     ],
    },
   ],
  },
  {
   "type": "function",
   "name": "token1",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "token1Policy",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "tuple",
     "components": [
      {
       "name": "ownerBps",
       "type": "uint16",
      },
      {
       "name": "rewardsBps",
       "type": "uint16",
      },
      {
       "name": "burnBps",
       "type": "uint16",
      },
     ],
    },
   ],
  },
  {
   "type": "event",
   "name": "Distributed",
   "inputs": [
    {
     "name": "token",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "owner",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "ownerAmount",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "rewardsAmount",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "burnAmount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "event",
   "name": "OwnerFeesClaimed",
   "inputs": [
    {
     "name": "owner",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "token",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "recipient",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "error",
   "name": "InactivePolicy",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InexactTransfer",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidBinding",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidRecipient",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "NothingToClaim",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "Unauthorized",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "UnsupportedToken",
   "inputs": [],
  },
 ]

LOCKED_FEE_CLAIMER_ABI = [
  {
   "type": "function",
   "name": "claimAndDistribute",
   "stateMutability": "nonpayable",
   "inputs": [],
   "outputs": [
    {
     "name": "amount0",
     "type": "uint256",
    },
    {
     "name": "amount1",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "function",
   "name": "positionLocker",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "splitter",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "token0",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "token1",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "tokenId",
   "stateMutability": "view",
   "inputs": [],
   "outputs": [
    {
     "name": "",
     "type": "uint256",
    },
   ],
  },
  {
   "type": "event",
   "name": "ClaimedAndDistributed",
   "inputs": [
    {
     "name": "caller",
     "type": "address",
     "indexed": True,
    },
    {
     "name": "amount0",
     "type": "uint256",
     "indexed": False,
    },
    {
     "name": "amount1",
     "type": "uint256",
     "indexed": False,
    },
   ],
   "anonymous": False,
  },
  {
   "type": "error",
   "name": "ClaimMismatch",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InexactTransfer",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidBinding",
   "inputs": [],
  },
  {
   "type": "error",
   "name": "InvalidLock",
   "inputs": [],
  },
 ]

LAUNCH_FEE_OWNER_REGISTRY_ABI = [
  {
   "type": "function",
   "name": "feeOwner",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "launch",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "owner",
     "type": "address",
    },
   ],
  },
  {
   "type": "function",
   "name": "feeSplitter",
   "stateMutability": "view",
   "inputs": [
    {
     "name": "launch",
     "type": "address",
    },
   ],
   "outputs": [
    {
     "name": "splitter",
     "type": "address",
    },
   ],
  },
 ]
