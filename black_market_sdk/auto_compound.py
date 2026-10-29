"""Canonical first-party auto-compounding config tails for pool-bound V6 markets."""
from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

AUTO_COMPOUND_STATIC_V1_PROFILE_ID = "0xdd53c2c686f822b2d17536ccf4f9db954b4976d1be80404bc74571253179fae0"
AUTO_COMPOUND_DYNAMIC_V1_PROFILE_ID = "0x46b81bfe9316fbaa90e930c6f1d5ad5e6ecfced2c886fd5520ca2bae11b92744"
AUTO_COMPOUND_CONFIG_VERSION = "0x" + keccak(text="black-market.auto-compound.config.v1").hex()
AUTO_COMPOUND_TAIL_LENGTH = 64
AUTO_COMPOUND_CONFIG_ABI_TYPE = "(bytes32,uint16)"


@dataclass(frozen=True)
class AutoCompoundConfigV1:
    compound_share_bps: int


def is_auto_compound_profile(profile_id: bytes | str) -> bool:
    identity = "0x" + profile_id.hex() if isinstance(profile_id, bytes) else profile_id.lower()
    return identity in (AUTO_COMPOUND_STATIC_V1_PROFILE_ID, AUTO_COMPOUND_DYNAMIC_V1_PROFILE_ID)


def validate_auto_compound_config(config: AutoCompoundConfigV1) -> None:
    share = config.compound_share_bps
    if isinstance(share, bool) or not isinstance(share, int) or not 0 <= share <= 10_000:
        raise ValueError("compound_share_bps must be an integer in [0, 10000]")


def encode_auto_compound_tail(config: AutoCompoundConfigV1) -> bytes:
    """Return the exact two-word abi.encode(AutoCompoundConfigV1) suffix."""
    validate_auto_compound_config(config)
    return abi_encode([AUTO_COMPOUND_CONFIG_ABI_TYPE], [(
        bytes.fromhex(AUTO_COMPOUND_CONFIG_VERSION[2:]), config.compound_share_bps,
    )])


def decode_auto_compound_tail(data: bytes) -> AutoCompoundConfigV1:
    if len(data) != AUTO_COMPOUND_TAIL_LENGTH:
        raise ValueError("Auto-compound tail must be exactly 64 bytes")
    try:
        version, share = abi_decode([AUTO_COMPOUND_CONFIG_ABI_TYPE], data)[0]
    except DecodingError as error:
        raise ValueError("Auto-compound tail is not canonical") from error
    if "0x" + version.hex() != AUTO_COMPOUND_CONFIG_VERSION:
        raise ValueError("Auto-compound tail has an unsupported version")
    config = AutoCompoundConfigV1(share)
    if encode_auto_compound_tail(config) != data:
        raise ValueError("Auto-compound tail is not canonical")
    return config
