"""Execute UFG mixed-venue launches ONLY on a disposable local Robinhood Anvil fork.

Start the fork separately (no keys or mainnet writes):
  anvil --fork-url https://rpc.mainnet.chain.robinhood.com/ --chain-id 4663 --port 8562
Then, from the checkout with the SDK installed:
  python examples/launch_ufg_treasury.py

Uses Anvil's first unlocked default account, never a private key. Fixed is atomic
with ERC20 proxy approvals; dynamic is staged with native-wrap funding. Every
unsigned SDK command is eth_call-simulated from that principal before sending.
The $2,000/WETH input is a fork-only arithmetic fixture, not a live price quote.
Native Nitro admission remains a separate production-RPC proof: a generic Anvil
fork cannot certify ArbOS compute/poster limits. No metadata API is contacted.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from dataclasses import replace
from urllib.parse import urlsplit

from eth_utils import keccak
from web3 import Web3

from black_market_sdk import (
    ROBINHOOD_WETH, LAUNCH_ORACLE_CONFIG_ID, WETH_ABI,
    UFG_LAUNCH_PROXY_ADDRESS, UFG_TREASURY_FACTORY_ADDRESS,
    UFG_LAUNCH_PROXY_V1_ABI, UFG_TREASURY_FACTORY_V1_ABI, UfgLaunchConfig,
    KnownLifecycleProfile, LaunchPlanV1, LifecycleTokenConfig, LifecycleAssetFunding,
    LifecycleMarketConfig, LifecycleInitialBuy, LifecycleAbyssMarketConfig,
    LifecycleAbyssPosition, LifecyclePoolBoundV4MarketConfigV6, LifecycleV4Position,
    get_launch_addresses, get_known_lifecycle_profile, ufg_launch_nonce,
    predict_launch_token, derive_launch_pool_recipe, derive_launch_buy_sqrt_price_limit_x96,
    build_ufg_fee_asset_policies, validate_ufg_launch_plan, encode_ufg_launch_config,
    hash_ufg_launch_config, encode_lifecycle_abyss_market_config,
    encode_lifecycle_pool_bound_v4_market_config, decode_lifecycle_pool_bound_v4_market_config,
    read_pool_bound_hook_deployment, mine_pool_bound_hook_salt, build_ufg_approvals,
    build_ufg_launch_transactions, read_ufg_treasury, read_launch_progress, launch_id_of,
)

ZERO = "0x" + "00" * 20
HASH = bytes(32)
ANVIL_PRINCIPAL = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def submit(client: Web3, transaction: dict) -> dict:
    envelope = {key: value for key, value in transaction.items() if key != "chainId"}
    client.eth.call(envelope)  # No signature or persisted state; sender is the principal.
    envelope["gas"] = 45_000_000
    receipt = client.eth.wait_for_transaction_receipt(client.eth.send_transaction(envelope))
    if receipt["status"] != 1:
        raise RuntimeError("Fork transaction reverted")
    return {"transactionHash": receipt["transactionHash"].hex(), "gasUsed": receipt["gasUsed"]}


async def construct_plan(client: Web3, *, dynamic: bool) -> tuple[LaunchPlanV1, UfgLaunchConfig, str]:
    infrastructure = get_launch_addresses(4663)
    supply, budget = 1_000_000 * 10**18, 500_000 * 10**18
    sequence = secrets.randbits(96)
    config = UfgLaunchConfig(10000, 100, (ROBINHOOD_WETH,))
    draft = LaunchPlanV1(4663, infrastructure.orchestrator, UFG_LAUNCH_PROXY_ADDRESS,
        ufg_launch_nonce(ANVIL_PRINCIPAL, sequence),
        LifecycleTokenConfig(0, 0, "UFG FORK ONLY treasury", "UFGFORK", supply, 0, "",
            keccak(text=f"Python UFG fork {dynamic} {sequence}"), ANVIL_PRINCIPAL, True),
        (), (), (), (), client.eth.get_block("latest")["timestamp"] + 86400, 0)
    token = predict_launch_token(client, draft)
    token0 = int(token, 16) < int(ROBINHOOD_WETH, 16)
    recipe = derive_launch_pool_recipe(paired_token_decimals=18, paired_token_usd_price_x18=2000 * 10**18,
        target_market_cap_usd_x18=5000 * 10**18, launched_token_is_quote=token0,
        fee=3000, tick_spacing=60, supply=supply, token_budget=budget)
    native = get_known_lifecycle_profile(chain_id=4663, orchestrator=infrastructure.orchestrator, key=KnownLifecycleProfile.ABYSS_2)
    key = KnownLifecycleProfile.V4_UFG_DYNAMIC_POOL if dynamic else KnownLifecycleProfile.V4_UFG_FIXED_POOL
    bound = get_known_lifecycle_profile(chain_id=4663, orchestrator=infrastructure.orchestrator, key=key)
    envelope = bound.profile.envelope
    native_config = LifecycleAbyssMarketConfig(2, 3000, HASH, recipe.launch_sqrt_price_x96,
        (LifecycleAbyssPosition(recipe.tick_lower, recipe.tick_upper, recipe.liquidity, budget),))
    bound_config = LifecyclePoolBoundV4MarketConfigV6(6, 0, 60, recipe.launch_sqrt_price_x96,
        10000, 1000 if dynamic else 10000, 7654321 if dynamic else 0, 1,
        envelope.protocol_fee_denominator, envelope.protocol_treasury, True, LAUNCH_ORACLE_CONFIG_ID,
        HASH, bound.profile.id, envelope.terms_digest, envelope.beneficiary, 0,
        (LifecycleV4Position(recipe.tick_lower, recipe.tick_upper, recipe.liquidity, HASH, budget),),
        ufg_launch_config=config)
    markets = (
        LifecycleMarketConfig(native.profile.registration.adapter_id, native.profile.id, ROBINHOOD_WETH,
            budget, 1, encode_lifecycle_abyss_market_config(native_config)),
        LifecycleMarketConfig(bound.profile.registration.adapter_id, bound.profile.id, ROBINHOOD_WETH,
            budget, 6, encode_lifecycle_pool_bound_v4_market_config(bound_config)),
    )
    buy_amount = 10**13
    limit = derive_launch_buy_sqrt_price_limit_x96(launch_sqrt_price_x96=recipe.launch_sqrt_price_x96,
        launched_token_is_quote=token0, slippage_bps=5000)
    plan = replace(draft, markets=markets, fee_assets=build_ufg_fee_asset_policies(token, config.reserve_assets),
        funding=(LifecycleAssetFunding(ROBINHOOD_WETH, 2 * buy_amount, 1 if dynamic else 0,
            ROBINHOOD_WETH, 2 * buy_amount, ZERO, b""),),
        buys=tuple(LifecycleInitialBuy(index, buy_amount, 1, ANVIL_PRINCIPAL, limit) for index in range(2)))
    deployment = read_pool_bound_hook_deployment(client, plan, market_index=1)
    mined = await mine_pool_bound_hook_salt(deployer=deployment.deployer, init_code_hash=deployment.init_code_hash)
    final_config = replace(bound_config, hook_salt=mined.salt)
    plan = replace(plan, markets=(markets[0], replace(markets[1], config=encode_lifecycle_pool_bound_v4_market_config(final_config))))
    validate_ufg_launch_plan(plan, config, token)
    return plan, config, token


async def run_case(client: Web3, *, dynamic: bool) -> dict:
    plan, config, token = await construct_plan(client, dynamic=dynamic)
    transactions = []
    if not dynamic:
        weth = client.eth.contract(address=ROBINHOOD_WETH, abi=WETH_ABI)
        transactions.append(submit(client, {"from": ANVIL_PRINCIPAL, "to": ROBINHOOD_WETH,
            "data": weth.encode_abi("deposit"), "value": plan.funding[0].amount}))
    for approval in build_ufg_approvals(plan):
        transactions.append(submit(client, approval.as_transaction()))
    # Independent Solidity canonical-prefix/tail validation before any launch write.
    factory = client.eth.contract(address=UFG_TREASURY_FACTORY_ADDRESS, abi=UFG_TREASURY_FACTORY_V1_ABI)
    encoded = plan.markets[1].config
    tail = encode_ufg_launch_config(config)
    accepted = factory.functions.validateMarketLaunchConfig(encoded, encoded[:-len(tail)], token, ROBINHOOD_WETH).call()
    assert accepted[0:2] == (10000, 100)
    mode = "staged" if dynamic else "atomic"
    for transaction in build_ufg_launch_transactions(plan, config=config, mode=mode,
            preparation_batches=[1, 1] if dynamic else None):
        transactions.append({"step": transaction.kind, **submit(client, transaction.as_transaction())})
    treasury = read_ufg_treasury(client, token)
    if treasury is None:
        raise RuntimeError("UFG launch did not create its treasury")
    proxy = client.eth.contract(address=UFG_LAUNCH_PROXY_ADDRESS, abi=UFG_LAUNCH_PROXY_V1_ABI)
    bound_hash = "0x" + proxy.functions.launchConfigHash(bytes.fromhex(launch_id_of(plan)[2:])).call().hex()
    if bound_hash != hash_ufg_launch_config(config):
        raise RuntimeError("Proxy launch config hash differs from the SDK commitment")
    progress = read_launch_progress(client, plan, mode=mode, ufg=config)
    if progress.phase.name != "ACTIVE" or progress.prepared_markets != 2:
        raise RuntimeError("Fork launch is not canonically active with both markets")
    decoded = decode_lifecycle_pool_bound_v4_market_config(plan.markets[1].config)
    assert decoded.ufg_launch_config == config
    return {"profile": "dynamic" if dynamic else "fixed", "mode": mode, "token": token,
        "treasury": treasury, "launchConfigHash": bound_hash, "configHashMatches": True,
        "phase": progress.phase.name, "transactions": transactions}


async def main() -> None:
    rpc_url = os.environ.get("UFG_FORK_RPC_URL", "http://127.0.0.1:8562")
    if urlsplit(rpc_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("This example can write ONLY to a loopback Anvil fork")
    client = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 120}))
    if "anvil" not in client.client_version.lower() or client.eth.chain_id != 4663:
        raise RuntimeError("Expected a disposable Anvil Robinhood fork (chain 4663)")
    if client.eth.accounts[0].lower() != ANVIL_PRINCIPAL.lower():
        raise RuntimeError("Expected Anvil's first unlocked default account; never supply real keys")
    results = [await run_case(client, dynamic=False), await run_case(client, dynamic=True)]
    print(json.dumps({"forkRpc": rpc_url, "principal": ANVIL_PRINCIPAL, "launches": results}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
