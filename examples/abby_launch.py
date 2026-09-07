"""Reconstruct the successful Abby the Vampire Squid mainnet launch.

Historical example only: it never signs, simulates, or broadcasts a transaction.
Source: https://robinhoodchain.blockscout.com/tx/0xd0dcae27e9ec2f7fb6e2304d7b1d739fd2f45f91d1eb5e762cdfcf01819fb837
"""

from black_market_sdk import (
    ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
    AUCTION_SUPPLY,
    DUAL_DIVIDENDS_TEMPLATE_ID,
    AtomicInitialBuy,
    AtomicLaunchRequest,
    AtomicPoolConfig,
    AtomicTokenConfig,
    AbyssPoolProfile,
    FeeDisposition,
    TokenKind,
    build_atomic_launch_calldata,
    get_addresses,
)

ABBY_DEPLOYMENT = {
    "chain_id": 4663,
    "transaction_hash": "0xd0dcae27e9ec2f7fb6e2304d7b1d739fd2f45f91d1eb5e762cdfcf01819fb837",
    "block_number": 56_232_272,
    "creator": "0x73bD52A3848B9219C6FE7E0D81CecF85E0c6D38b",
    "launch_factory": "0xAf3FdC499b3717EBE8aD51B66bA78Cb083552351",
    "launch_fee": 500_000_000_000_000,
    "token": "0x450b50d216088e40cdd412da98b3b4c07bb4931f",
    "pool": "0x5304f1300384d129d7f18a2b657d9cc6ff2cc002",
    "paired_token": "0x15f3385625D7e364C5a6216FBbceadf10fa90e7d",
    "rewards": "0x26C8F60D2877AA9e7078f6701137c829A46eA882",
    "splitter": "0x43b4b4aC6965FbDB641394664AC6a4e4bE43E0C2",
    "fee_claimer": "0x17b6d871A96A683916129c27d63136AF7aDA842a",
    "token_id": 29,
    "initial_buy_launched_token_amount": 913_888_003_035_081_430_019,
}

ABBY_REQUEST = AtomicLaunchRequest(
    creator=ABBY_DEPLOYMENT["creator"],
    template_id=DUAL_DIVIDENDS_TEMPLATE_ID,
    template_version=1,
    token=AtomicTokenConfig(
        kind=TokenKind.HOLDER_DIVIDEND,
        name="Abby the Vampire Squid",
        symbol="ABBY",
        decimals=18,
        supply=AUCTION_SUPPLY,
    ),
    pool=AtomicPoolConfig(
        paired_token=ABBY_DEPLOYMENT["paired_token"],
        launched_token_is_quote=False,
        profile=AbyssPoolProfile.STANDARD_ORACLE,
        fee=10_000,
        oracle_config_id=ATOMIC_LAUNCH_ORACLE_CONFIG_ID,
        launch_tick=-800,
        liquidity=1_040_808_692_711_685_547_298_718_484,
        launched_token_amount_maximum=AUCTION_SUPPLY,
        paired_token_amount_maximum=0,
    ),
    initial_buy=AtomicInitialBuy(
        paired_token_amount_in=1_000 * 10**18,
        launched_token_amount_out_minimum=909_318_563_019_906_022_868,
        sqrt_price_limit_x96=75_931_191_248_180_498_334_338_978_414,
    ),
    launched_token_fees=FeeDisposition(owner_bps=0, rewards_bps=10_000, burn_bps=0),
    paired_token_fees=FeeDisposition(owner_bps=0, rewards_bps=10_000, burn_bps=0),
    deadline=1_788_724_575,
)

ABBY_CALLDATA = build_atomic_launch_calldata(ABBY_REQUEST)
assert get_addresses(4663).launch_factory == ABBY_DEPLOYMENT["launch_factory"]
assert ABBY_CALLDATA[:4].hex() == "e5ac002e"
assert len(ABBY_CALLDATA) == 1_060

print("Abby launch reconstructed:")
print("  transaction:", ABBY_DEPLOYMENT["transaction_hash"])
print("  token:      ", ABBY_DEPLOYMENT["token"])
print("  pool:       ", ABBY_DEPLOYMENT["pool"])
print(f"  calldata:    0x{ABBY_CALLDATA[:20].hex()}… ({len(ABBY_CALLDATA)} bytes)")
