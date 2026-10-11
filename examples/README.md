# Python launch examples

The eleven `launch_erc20_*.py` / `launch_erc404_*.py` cases share
`launch_examples.py` and `_launch_support.py`. Their token, market, position, buy,
and atomic/staged choices remain fixed. See the [case table](../README.md#examples)
and [lifecycle guide](../docs/launch-guide.md#standalone-token-launch-examples).

## HMM presets and launch tails

`python examples/hmm_launch_config.py` prints calibrated WETH HMM defaults and
the exact launch tail for HMM Fixed/HMM Dynamic. Add `--live` for read-only
discovery that requires both registered profiles to be admitted; no key, signing,
funding or metadata writes. See the [HMM configuration guide](../docs/launch-guide.md#hmm-launch-configuration)
to bind `hmm_launch_config` to a reviewed config6 before encoding a market.
The existing funded launch cases retain the fixed-fee offering; they never
silently switch to HMM.

## Configure once

Install the SDK (`python -m pip install -e .` from this checkout, or install
`black-market-sdk` when copying a case and both helpers). Copy `.env.example` into
`.env` in the directory where you run the command, protect it with owner-only
permissions, and configure `LAUNCH_API_URL` plus exactly one key source.
Existing process environment values override `.env`; never commit keys,
keystores, passwords, or private launch artifacts.

Both modes derive the public creator address from the configured key. Keystore
decryption is allowed for that purpose. Dry-run does not construct the local
wallet signer or sign anything. Planning still contacts the configured RPC/API
for discovery, prices, funding builds, and read-only native simulation, and still
requires successful SDK admission and sufficient funding.

## 1. Dry run (default)

Choose any fixed case; no mode flag means dry run:

```sh
python examples/launch_erc20_v4.py
```

The first output line is:

```text
DRY RUN — no transactions signed or broadcast.
```

The runner performs discovery, selects admitted profiles, derives economics,
freezes the optional image descriptor, builds funding, and prepares/proves the
complete protected plan. It prints the same full economic review used for
execution and saves `run.json`, finalized `plan.json`, `admission.json`,
`economic-review.json`, `events.jsonl`, empty `receipts.json`, and `result.json`
in `launch-results/<timestamp-case-random>/`.

On the known chain/core, V4 cases explicitly retain the admitted
`KnownLifecycleProfile.V4_FIXED_FEE_POOL` offering even when Dynamic Fee Hook is
also admitted. Missing/disabled fixed profiles refuse rather than switch hooks;
unknown deployments still require one unambiguous supported V4 offering.

A successful dry-run exits 0 with `execution: "dry-run"`, chain status `planned`,
and API status `not-run`. It does not sign attribution, send transactions,
create an upload session, upload an image, or publish. Refused/failed planning
exits nonzero with retained diagnostics. The last output line is the exact case
command with `--broadcast`, using the currently running Python interpreter and
shell-safe quoting.

## 2. Broadcast (explicit execution)

```sh
python examples/launch_erc20_v4.py --broadcast
```

**Choosing `--broadcast` authorizes execution.** It intentionally skips interactive
review admission: there is no hash to type or environment hash to supply. The
review hash is still computed, printed, and saved as provenance. Broadcast-only
`economic-admission.json` records `authorization: "--broadcast"`, the computed
`reviewHash`, and the timestamp; it is evidence, not a hash input gate.

Broadcast prepares a fresh current-state plan, then runs local signing,
broadcast, canonical chain verification, optional image upload/verification, and
API publication. The previous dry-run plan is not a saved execution commitment.
All other safety checks remain: complete SDK planner admission, unchanged bound
economics at each signature, exact immediate-command preflight, canonical/reorg
barriers, original image identity/readiness, and deadlines. No refused case is
silently reduced, retried, or relaunched.

## Continue an existing launch

After a partial broadcast, resume its original recovery directory instead of
creating a fresh token:

```sh
python examples/launch_erc20_v4.py --resume launch-results/<original-run>
```

`--resume <dir>` keeps the original publication/continuation path, committed plan,
session, and receipt evidence. It is not a dry-run or a fresh launch. Preserve the
private recovery files and reconcile reorgs with the read-only `launch_submit.py`
workflow in the lifecycle guide. Never share private files or signed raw bytes.

Only three argument shapes are accepted: no arguments, `--broadcast`, or
`--resume <dir>`. Unknown flags, duplicate flags, missing directories, extra
arguments, and mixed modes fail immediately with usage text and exit status 2,
before configuration or network calls.

## Environment variables

All fixed-case runner variables are shown in [.env.example](.env.example):

| Variable | Meaning / default |
| --- | --- |
| `PRIVATE_KEY` | Local creator private key. Required unless `EXAMPLE_KEYSTORE_PATH` is set; never configure both. Dry-run only derives the public address. |
| `EXAMPLE_KEYSTORE_PATH` | Alternative owner-only regular V3 JSON keystore; symlinks/non-regular or unsafe-permission files are rejected. |
| `EXAMPLE_KEYSTORE_PASSWORD` | Keystore password; defaults to the empty string. Never logged. |
| `LAUNCH_API_URL` | Required Abyss API origin for USD pricing and broadcast upload sessions/publication. No guessed API default. |
| `RPC_URL` | Optional RPC endpoint; defaults to the SDK's official Robinhood chain4663 mainnet RPC. |
| `EXAMPLE_KYBER_API_URL` | Optional separate Abyss API for native-input conversion builds; defaults to `LAUNCH_API_URL`. WETH funding uses native wrapping; other quotes use admitted committed conversions. |
| `EXAMPLE_BUY_AMOUNT_WEI` | Native ETH input per ordered buy, a positive canonical decimal uint256; defaults to `10000000000000` (0.00001 ETH). Gas is additional. |
| `EXAMPLE_TARGET_MARKET_CAP_USD` | Opening USD valuation target based on the full minted supply; defaults to `5000`. Positive exact decimal, at most 18 fractional places. |
| `EXAMPLE_TOKEN_NAME` | Optional NFC-normalized/trimmed case name override; nonempty, at most 100 codepoints and 128 UTF-8 bytes. |
| `EXAMPLE_TOKEN_SYMBOL` | Optional NFC-normalized/trimmed symbol override; nonempty, at most 32 codepoints and 32 UTF-8 bytes. |
| `EXAMPLE_TOKEN_DESCRIPTION` | Optional NFC-normalized/trimmed description override; at most 2000 codepoints. |
| `EXAMPLE_IMAGE_PATH` | Optional PNG/JPEG/WebP file, at most 5 MiB. Dry-run reviews its SHA-256/type/length only; broadcast signs, uploads, verifies, and checks the public image. Unset means metadata-only. |
| `NFT_BASE_URI` | Optional hosted ERC404 NFT metadata base URI; defaults to empty. Ignored by ERC20 cases. |
| `LAUNCH_CHAIN_GAS_CAP` | Optional positive integer tightening policy for chain transaction gas. No invented ceiling by default. |
| `LAUNCH_RPC_GAS_CAP` | Optional positive integer tightening policy for RPC transaction gas. |
| `LAUNCH_ACCOUNT_GAS_CAP` | Optional positive integer tightening policy for account transaction gas. |
| `LAUNCH_CALLDATA_CAP` | Optional positive integer tightening policy for calldata byte length. |

Remote RPC/API endpoints require HTTPS; explicit loopback HTTP is accepted for
owned local services. API credentials, query strings, and fragments in URLs are
rejected. Public artifacts redact secrets and RPC credentials; private artifacts
are owner-only. None of these variables substitutes for explicit `--broadcast`
or an explicit original-launch `--resume` continuation.
