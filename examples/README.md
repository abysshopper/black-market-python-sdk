# Python launch examples

Start with `launch_erc20_abyss.py` or `launch_erc20_v4.py`: one market, one position,
one opening buy. The [eleven-case table](../README.md#examples) lists the fixed
wrappers; each imports `launch_examples.py` and `_launch_support.py`.
For SDK integrations, follow the [launch guide](../docs/launch-guide.md).

## Configure once

From an editable checkout (`python -m pip install -e .`):

```sh
cp -n examples/.env.example .env
chmod 600 .env
```

Set `LAUNCH_API_URL=https://api.abyss.trading` and exactly one key source:
`PRIVATE_KEY`, or an owner-only V3 JSON keystore at `EXAMPLE_KEYSTORE_PATH`.
`EXAMPLE_KEYSTORE_PASSWORD` defaults to empty. Process environment values override
`.env`. Remote RPC/API endpoints require HTTPS; owned loopback services can use HTTP.

A dry run derives the creator address from that key and needs enough ETH for the
buy and gas, plus working RPC/API access. It does not create a signer, sign, upload,
or publish. For wallet-free discovery, run `quickstart.py` or `launch_matrix.py`.

## Dry run, then broadcast

```sh
python examples/launch_erc20_abyss.py
python examples/launch_erc20_abyss.py --broadcast
```

The first command prints `DRY RUN — no transactions signed or broadcast.`, discovers
profiles, derives prices/ranges, builds funding, and assesses the protected plan.
Review the full supply, aligned FDV, fees, buy output/share/minimum, and gas.
Artifacts are saved in a printed `launch-results/<timestamp-case-random>/` directory.
A successful dry run exits 0 with execution `dry-run`, chain `planned`, API `not-run`.
Planning failure exits nonzero and retains diagnostics.

`--broadcast` authorizes spending and metadata publication without a confirmation
prompt. It builds a **fresh plan**, not the dry-run commitment. It stages metadata,
executes, verifies canonical chain state, then publishes the activation hash.
V4 wrappers select the fixed-fee profile; they do not switch to dynamic or HMM.

Planning uses deterministic capacity budgets and pinned inputs, with no launch
simulation or aggregate simulation window, including conversion routes.
Multi-market cases remain staged. Single-market conversions can be atomic when
their known budget plus poster allocation fits, including an empirical 2M compute
reserve per Swap before padding. Review the returned mode and `assessment.ready`;
an unavailable or oversized activation budget refuses with an actionable reason
rather than executing a fallback.

## Continue the original run

```sh
python examples/launch_erc20_abyss.py --resume "launch-results/<original-run>"
```

Use the original **broadcast** directory after any partial execution. Resume keeps
its plan identity, session, and receipts, and can spend/publish remaining work.
An Active launch resumes verification/publication, not deployment.
Keep private recovery files, keys, capabilities, and signed bytes out of commits
and shared output. A dry-run directory has no issued publication session.

Only no arguments, `--broadcast`, or `--resume <dir>` are accepted. Omitting the
argument after `--resume`, unknown/duplicate flags, mixed modes, or extra arguments
exit **2** before configuration/network access. Supplying a nonexistent directory
(or one without its recovery file) reaches **`MISSING_RECOVERY`**, exit **1**.
For a reorg or uncertain signed hash, reconcile read-only first; see
[recovery and cancellation](../docs/launch-guide.md#recover-or-cancel).

## Fund the opening buys

| Quote / source | Funding | Required balance |
| --- | --- | --- |
| WETH from native ETH | `NATIVE_WRAP`: escrow wraps ETH; no Kyber route | Buy ETH plus gas |
| Other quote, such as USDG, from ETH | `SWAP`: admitted committed Kyber conversion | Native input plus gas |
| Existing quote ERC20, custom SDK plan | `ERC20`: approve the funding escrow | Exact ERC20 input plus gas ETH |

The default buy is **0.00001 ETH per ordered buy**, not per launch. Quote conversions
have a **now + 300 s** deadline (maximum 1200 s); the plan has **now + 3600 s**.
A stalled staged run can outlive the conversion. Use the saved plan's cancellation
path if funding is expired; a fresh broadcast cannot continue its identity.

## Change the fixed case

Copy a wrapper next to both helpers, or copy all three together and install
`black-market-sdk==0.16.1`. In `CASE`, `tokenKind` is 0 ERC20 / 1 ERC404;
`rewardMode` is 0 none / 1 staking / 2 dividends; `mode` is `atomic` / `staged`.
`markets` selects `venue` (`v4` / `abyss`), `quote` (`WETH` / `USDG`), and position
count; `buysPerMarket` controls ordered buys. `distributionPreset` accepts the SDK
range presets. For supply, fee policies, budgets, or ranges, adapt `construct_plan`,
`fee_policies`, and `derive_market_pricing` in `launch_examples.py`.
The typed helper uses `venue="uniswap-v4"`, not runner `"v4"`.

## Environment variables

See [.env.example](.env.example). Amounts are exact decimal strings.

| Variable | Meaning / default |
| --- | --- |
| `PRIVATE_KEY` | Creator key; configure this or the keystore, not both |
| `EXAMPLE_KEYSTORE_PATH` / `EXAMPLE_KEYSTORE_PASSWORD` | Owner-only regular V3 JSON keystore / password (empty by default) |
| `LAUNCH_API_URL` | Required API origin: `https://api.abyss.trading` |
| `RPC_URL` | Official Robinhood mainnet RPC by default |
| `EXAMPLE_KYBER_API_URL` | Conversion-build API; defaults to `LAUNCH_API_URL`; unused for ETH→WETH |
| `EXAMPLE_BUY_AMOUNT_WEI` | Positive native input **per buy**; `10000000000000` (0.00001 ETH) |
| `EXAMPLE_TARGET_MARKET_CAP_USD` | Full minted-supply target; `5000`, up to 18 decimal places |
| `EXAMPLE_TOKEN_NAME` / `EXAMPLE_TOKEN_SYMBOL` | Optional identity overrides; ≤100/32 codepoints and ≤128/32 UTF-8 bytes |
| `EXAMPLE_TOKEN_DESCRIPTION` | Optional description, ≤2000 codepoints |
| `EXAMPLE_IMAGE_PATH` | Optional PNG/JPEG/WebP ≤5 MiB; dry-run binds descriptor, broadcast uploads/verifies |
| `NFT_BASE_URI` | Hosted ERC404 metadata base URI; empty by default, ignored for ERC20 |
| `LAUNCH_CHAIN_GAS_CAP` / `LAUNCH_RPC_GAS_CAP` / `LAUNCH_ACCOUNT_GAS_CAP` | Optional positive per-transaction gas-policy caps |
| `LAUNCH_CALLDATA_CAP` | Optional positive calldata byte cap |

## UFG treasury launches (fork only)

`launch_ufg_treasury.py` uses a disposable local Anvil fork and its first unlocked
account. It refuses remote/non-Anvil RPCs. Default `UFG_FORK_RPC_URL` is
`http://127.0.0.1:8562`; set it in the process environment, not the fixed runner's `.env`.

```sh
anvil --fork-url https://rpc.mainnet.chain.robinhood.com/ --chain-id 4663 \
  --port 8562 --gas-limit 100000000
# Another terminal, from this checkout:
PYTHONPATH=. python examples/launch_ufg_treasury.py
```

It exercises Abyss+UFG fixed V4 atomically with ERC20 proxy approvals, then
Abyss+UFG dynamic V4 in stages with NativeWrap. It checks each command and reports
Active state, treasury addresses, and config hashes. Stop Anvil afterward.
The example uses no real key or metadata API and does not prove native-Nitro gas.
For production use [typed UFG planning](../docs/launch-guide.md#ufg-treasury-launches).
`launch_recipe.py` cannot encode UFG/HMM tails; `launch_submit.py` has no UFG option.

## Inspect HMM V2 configuration

```sh
python examples/hmm_launch_config.py --max-bid-quote 10000000000000000 --reference-deviation 0
```

This prints settings and the 512-byte V2 tail using explicit illustration inputs:
0.01 WETH bid cap and zero reference deviation. Choose both for your market.
`--live` adds read-only admission checks; it does not launch or sign.
The fixed funded cases remain non-HMM. See [HMM configuration](../docs/launch-guide.md#hmm-launch-configuration)
and [API test-service setup](../docs/reference.md#test-against-an-api-service).
