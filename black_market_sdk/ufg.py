"""Opt-in, principal-funded UFG treasury launches on the canonical Robinhood graph."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import is_address, keccak, to_checksum_address

from .addresses import ROBINHOOD_LAUNCH_INFRASTRUCTURE, ROBINHOOD_MAINNET_CHAIN_ID

if TYPE_CHECKING:
    from web3 import Web3
    from .lifecycle import LaunchPlanV1, LifecycleFeeAssetPolicy, LifecycleTransaction, LaunchExecutionMode
    from .lifecycle_rpc import LaunchBlock

UFG_LAUNCH_PROXY_ADDRESS = "0xf6673d859DE03eB74A0b01696Edb97f941dFD0CA"
UFG_TREASURY_FACTORY_ADDRESS = "0x26Aa5D52134c8E6c4C01E9fb53A7568c0C64901F"
UFG_UPSTREAM_FACTORY_ADDRESS = "0xA170b5A58C111334A7237F2Ea2c428ec5022f68E"
UFG_FIXED_PROFILE_ID = "0x5c6045af480814e469f3dba7c120d264047c9320b869c63a064536041a2dbae6"
UFG_DYNAMIC_PROFILE_ID = "0x03de8d5e38f2957d80ecb9f2e59d555601ff091dfb17bce84178956a2e8d9b41"
UFG_FIXED_ADAPTER_ID = "0x073a4d46af70a3d4f69641e4f9dcdc9d8c279cd443daff960bbdf65a3b5fc758"
UFG_DYNAMIC_ADAPTER_ID = "0xc0906e17625b48240a87a3afc6ee921b653bdacd72906a16b359d0776d938518"
UFG_LAUNCH_CONFIG_ABI_TYPE = "(uint16,uint16,address[])"


@dataclass(frozen=True)
class UfgLaunchConfig:
    treasury_share_bps: int
    exit_fee_bps: int
    reserve_assets: tuple[str, ...]


def is_ufg_profile(profile_id: bytes | str) -> bool:
    identity = "0x" + profile_id.hex() if isinstance(profile_id, bytes) else profile_id.lower()
    return identity in (UFG_FIXED_PROFILE_ID, UFG_DYNAMIC_PROFILE_ID)


def _address(value: str) -> str:
    if not isinstance(value, str) or not is_address(value) or int(value, 16) == 0:
        raise ValueError("UFG assets and principal must be nonzero Ethereum addresses")
    return to_checksum_address(value)


def _reserves(assets: Sequence[str]) -> tuple[str, ...]:
    if not 1 <= len(assets) <= 7:
        raise ValueError("UFG launches require one through seven reserves")
    result = tuple(_address(asset) for asset in assets)
    if any(int(first, 16) >= int(second, 16) for first, second in zip(result, result[1:])):
        raise ValueError("UFG reserves must be strictly ascending")
    return result


def validate_ufg_launch_config(config: UfgLaunchConfig) -> None:
    if isinstance(config.treasury_share_bps, bool) or not isinstance(config.treasury_share_bps, int) or config.treasury_share_bps != 10000:
        raise ValueError("UFG treasury_share_bps must be 10000")
    if isinstance(config.exit_fee_bps, bool) or not isinstance(config.exit_fee_bps, int) or not 0 <= config.exit_fee_bps <= 2000:
        raise ValueError("UFG exit_fee_bps must be an integer between zero and 2000")
    _reserves(config.reserve_assets)


def encode_ufg_launch_config(config: UfgLaunchConfig) -> bytes:
    """Return abi.encode(UFGLaunchConfigV1), including the dynamic tuple offset."""
    validate_ufg_launch_config(config)
    return abi_encode([UFG_LAUNCH_CONFIG_ABI_TYPE], [(config.treasury_share_bps, config.exit_fee_bps, config.reserve_assets)])


def decode_ufg_launch_config(data: bytes | str) -> UfgLaunchConfig:
    from .lifecycle_rpc import hex_bytes
    raw = bytes.fromhex(hex_bytes(data)[2:])
    share, exit_fee, assets = abi_decode([UFG_LAUNCH_CONFIG_ABI_TYPE], raw)[0]
    config = UfgLaunchConfig(share, exit_fee, tuple(to_checksum_address(asset) for asset in assets))
    if encode_ufg_launch_config(config) != raw:
        raise ValueError("UFG launch config is not canonical")
    return config


def hash_ufg_launch_config(config: UfgLaunchConfig) -> str:
    return "0x" + keccak(encode_ufg_launch_config(config)).hex()


def ufg_launch_nonce(principal: str, nonce: int) -> int:
    if isinstance(nonce, bool) or not isinstance(nonce, int) or not 0 <= nonce < (1 << 96):
        raise ValueError("UFG nonce must fit uint96")
    return (int(_address(principal), 16) << 96) | nonce


def _ufg_principal(plan: LaunchPlanV1) -> str:
    if isinstance(plan.nonce, bool) or not isinstance(plan.nonce, int) or not 0 <= plan.nonce < (1 << 256):
        raise ValueError("UFG plan nonce must fit uint256")
    return _address("0x" + (plan.nonce >> 96).to_bytes(20, "big").hex())


def build_ufg_fee_asset_policies(token: str, reserve_assets: Sequence[str]) -> tuple[LifecycleFeeAssetPolicy, ...]:
    from .lifecycle import LifecycleFeeAssetPolicy
    token = _address(token)
    reserves = _reserves(reserve_assets)
    if token.lower() in {asset.lower() for asset in reserves}:
        raise ValueError("UFG reserves cannot include the launch token")
    return tuple(LifecycleFeeAssetPolicy(asset, 0 if asset == token else 10000, 0, 10000 if asset == token else 0)
                 for asset in sorted((token, *reserves), key=lambda asset: int(asset, 16)))


def _validate_ufg_identity(plan: LaunchPlanV1) -> None:
    from .lifecycle import LifecyclePlanningError
    if (plan.chain_id != ROBINHOOD_MAINNET_CHAIN_ID or isinstance(plan.chain_id, bool)
            or plan.orchestrator.lower() != ROBINHOOD_LAUNCH_INFRASTRUCTURE.orchestrator.lower()
            or plan.creator.lower() != UFG_LAUNCH_PROXY_ADDRESS.lower() or plan.token.kind != 0
            or plan.token.reward_mode != 0 or plan.executor_fee_bps != 0 or not plan.markets
            or plan.token.inventory_recipient.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower()
            or any(buy.recipient.lower() == UFG_LAUNCH_PROXY_ADDRESS.lower() for buy in plan.buys)):
        raise LifecyclePlanningError("INVALID_UFG_PLAN", "UFG requires the canonical proxy/core/chain, ERC20 without rewards or executor fees, markets and external recipients")
    _ufg_principal(plan)


def _validate_ufg_markets(plan: LaunchPlanV1, config: UfgLaunchConfig) -> None:
    from .lifecycle import (decode_lifecycle_abyss_market_config, encode_lifecycle_abyss_market_config,
        decode_lifecycle_pool_bound_v4_market_config)
    from .lifecycle_rpc import hex_bytes
    from .lifecycle_presets import KnownLifecycleProfile, get_known_lifecycle_profile
    expected = encode_ufg_launch_config(config)
    if {market.quote_asset.lower() for market in plan.markets} != {asset.lower() for asset in config.reserve_assets}:
        raise ValueError("Every UFG reserve must be quoted and every market quote must be a reserve")
    abyss_id = "0x" + keccak(text="black-market.adapter.abyss-lifecycle.v1").hex()
    adapters = {UFG_FIXED_PROFILE_ID: UFG_FIXED_ADAPTER_ID, UFG_DYNAMIC_PROFILE_ID: UFG_DYNAMIC_ADAPTER_ID}
    for market in plan.markets:
        if market.config_version == 1:
            native = decode_lifecycle_abyss_market_config(market.config)
            if hex_bytes(market.adapter_id) != abyss_id or native.profile not in (2, 3) or encode_lifecycle_abyss_market_config(native) != bytes.fromhex(hex_bytes(market.config)[2:]):
                raise ValueError("UFG Abyss markets require canonical untailored QUOTE/QUOTE_ORACLE config1")
            key = KnownLifecycleProfile.ABYSS_2 if native.profile == 2 else KnownLifecycleProfile.ABYSS_3
            preset = get_known_lifecycle_profile(chain_id=plan.chain_id, orchestrator=plan.orchestrator, key=key)
            if preset is None or hex_bytes(market.profile_id) != preset.profile.id.lower():
                raise ValueError("UFG Abyss profile ID must match the encoded canonical profile")
        elif market.config_version == 6:
            identity = hex_bytes(market.profile_id)
            bound = decode_lifecycle_pool_bound_v4_market_config(market.config, expected_version=6)
            if (identity not in adapters or hex_bytes(market.adapter_id) != adapters[identity]
                    or hex_bytes(bound.profile_id) != identity or bound.fee_mode != 1 or bound.lp_fee_pips != 0
                    or bound.ufg_launch_config is None or encode_ufg_launch_config(bound.ufg_launch_config) != expected):
                raise ValueError("UFG V4 markets require an admitted UFG profile, QuoteOnly, zero LP fee and the identical launch config")
        else:
            raise ValueError("UFG markets support only config1 and config6")


def validate_ufg_launch_plan(plan: LaunchPlanV1, config: UfgLaunchConfig, predicted_token: str) -> None:
    """Validate proxy-specific intent offline; on-chain execution remains authoritative."""
    from .lifecycle import LifecyclePlanningError, to_launch_plan_tuple
    try:
        to_launch_plan_tuple(plan)
        _validate_ufg_identity(plan)
        validate_ufg_launch_config(config)
        policies = build_ufg_fee_asset_policies(predicted_token, config.reserve_assets)
        actual = tuple((policy.asset.lower(), policy.owner_bps, policy.rewards_bps, policy.burn_bps) for policy in plan.fee_assets)
        if actual != tuple((policy.asset.lower(), policy.owner_bps, policy.rewards_bps, policy.burn_bps) for policy in policies):
            raise ValueError("UFG fee policies must burn all launch-token fees and send all reserve fees to the owner")
        _validate_ufg_markets(plan, config)
    except (ValueError, TypeError, AttributeError, DecodingError) as error:
        raise LifecyclePlanningError("INVALID_UFG_PLAN", str(error)) from error


def _build_ufg_calldata(plan: LaunchPlanV1, command: str, config: UfgLaunchConfig | None = None, *, first_market: int = 0, count: int = 0) -> str:
    from .lifecycle import _encode_function, build_lifecycle_calldata, to_launch_plan_tuple
    from .ufg_abis import UFG_LAUNCH_PROXY_V1_ABI
    if command not in {"launchAtomic", "beginLaunch"}:
        return build_lifecycle_calldata(plan, command, first_market=first_market, count=count)
    if config is None:
        raise ValueError("UFG launch commands require an explicit treasury config")
    validate_ufg_launch_config(config)
    args = [to_launch_plan_tuple(plan)]
    if command == "beginLaunch":
        args.append(1)
    args.append((config.treasury_share_bps, config.exit_fee_bps, config.reserve_assets))
    return _encode_function(UFG_LAUNCH_PROXY_V1_ABI, command, args)


def build_ufg_launch_transactions(plan: LaunchPlanV1, *, config: UfgLaunchConfig, mode: LaunchExecutionMode, preparation_batches: Sequence[int] | None = None) -> tuple[LifecycleTransaction, ...]:
    from .lifecycle import build_launch_transactions
    _validate_ufg_identity(plan)
    _validate_ufg_markets(plan, config)
    commands = {"atomic": "launchAtomic", "begin": "beginLaunch", "prepare": "prepareMarkets", "activate": "activateLaunch"}
    return tuple(replace(tx, from_address=_ufg_principal(plan), to=UFG_LAUNCH_PROXY_ADDRESS,
        data=_build_ufg_calldata(plan, commands[tx.kind], config, first_market=tx.first_market or 0, count=tx.market_count or 0))
        for tx in build_launch_transactions(plan, mode=mode, preparation_batches=preparation_batches))


def build_ufg_cancel_transaction(plan: LaunchPlanV1) -> LifecycleTransaction:
    from .lifecycle import LifecycleTransaction
    _validate_ufg_identity(plan)
    return LifecycleTransaction("cancel", "cancel", plan.chain_id, _ufg_principal(plan), UFG_LAUNCH_PROXY_ADDRESS, _build_ufg_calldata(plan, "cancelLaunch"), 0, (), ())


def build_ufg_approvals(plan: LaunchPlanV1) -> tuple[LifecycleTransaction, ...]:
    """Approve exactly the proxy's summed ERC20 pulls; native inputs need no approval."""
    from .lifecycle import LifecycleTransaction, LifecycleAllowancePostcondition, _encode_function, _ERC20_READ_ABI
    _validate_ufg_identity(plan)
    totals: dict[str, int] = {}
    for funding in plan.funding:
        if funding.kind == 1 or (funding.kind == 2 and int(funding.input_asset, 16) == 0):
            continue
        asset = _address(funding.asset if funding.kind == 0 else funding.input_asset)
        amount = funding.amount if funding.kind == 0 else funding.input_amount
        totals[asset] = totals.get(asset, 0) + amount
    return tuple(LifecycleTransaction(f"approve:{asset}", "approve", plan.chain_id, _ufg_principal(plan), asset, _encode_function(_ERC20_READ_ABI, "approve", [UFG_LAUNCH_PROXY_ADDRESS, amount]), 0, (), (LifecycleAllowancePostcondition(asset, UFG_LAUNCH_PROXY_ADDRESS, amount),))
        for asset, amount in sorted(totals.items(), key=lambda item: int(item[0], 16)))


def read_ufg_treasury(client: Web3, token: str, *, block: LaunchBlock | None = None) -> str | None:
    from .lifecycle import _call
    from .lifecycle_rpc import assert_canonical, assert_chain, read_block
    from .ufg_abis import UFG_TREASURY_FACTORY_V1_ABI
    assert_chain(client, ROBINHOOD_MAINNET_CHAIN_ID)
    pinned = block or read_block(client)
    treasury = _call(client, UFG_TREASURY_FACTORY_ADDRESS, UFG_TREASURY_FACTORY_V1_ABI, "treasuryOf", [_address(token)], pinned)
    assert_canonical(client, pinned)
    assert_chain(client, ROBINHOOD_MAINNET_CHAIN_ID)
    return to_checksum_address(treasury) if int(treasury, 16) else None
