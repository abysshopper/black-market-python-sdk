---
name: sdk-usage
description: Integrate the Abyss Python SDK using the correct deployment graph, construction presets, public APIs and live execution authority. Covers protocol reads, unsigned lifecycle planning, swaps and lending.
---

# Use the Python SDK

## Read the right contract

- Read `README.md`, `docs/launch-guide.md` and the installed package's exports.
  Use Python 3.10+ in a virtual environment and integer financial base units.
- Consumer imports use `black_market_sdk`; public exports are in `__init__.py`.
  Resolve launch infrastructure with `get_launch_addresses(chain_id)` and other
  infrastructure with `get_addresses(chain_id)`, not copied deployment addresses.
- Use `Web3(Web3.HTTPProvider(rpc_url))` for the documented synchronous reads and
  verify `client.eth.chain_id`. Select the intended chain explicitly; local chains
  need their own graph rather than mainnet defaults.
- Compare the plan's core, registry, funding escrow and selected profiles with the
  intended release. An old wheel, vendored package, environment override or saved
  plan can target an older immutable stack. Version/name alone is not graph identity.

## Construct locally; assess pinned inputs

1. Use `get_known_lifecycle_deployment`, `get_known_lifecycle_profile` and
   `list_known_lifecycle_profiles` for shipped construction metadata. Pure preset
   selection avoids remote enumeration; it does not grant live admission.
2. Use `read_lifecycle_profiles(client, orchestrator=...)` for explicit discovery
   or an unlisted offering. Check `admitted`, refusal `reason`, schema/version,
   topology, bounds, dependency graph and author terms.
3. Build the complete `LaunchPlanV1`: creator/core/nonce, token behavior, fee
   policies, funding, all markets/positions and ordered buys. Read the sibling
   `token-launch/SKILL.md` and `launch-pricing/SKILL.md` before choosing economics.
4. `build_launch_transactions(plan, mode=...)` compiles unsigned calldata only.
   It does not prove balances, fees, gas capacity, quote freshness or execution.
5. Await `prepare_and_plan_lifecycle_launch(client, plan, account=..., mode=...)`
   for unified bound-hook preparation and deterministic capacity/input assessment.
   Call synchronous `plan_launch` for an already-finalized plan. Review the final
   plan/hash, returned mode, `assessment.ready` and any actionable refusal reason.
6. Use `read_launch_progress` and `build_next_transaction` for canonical recovery
   and fresh assessment of the next command. Preserve the original plan, mode,
   confirmation policy and actual included/replacement hashes.

Use synchronous Web3, not AsyncWeb3. `read_lifecycle_profiles`, `read_launch_progress`,
`plan_launch` and `build_next_transaction` are synchronous;
`prepare_and_plan_lifecycle_launch` and `mine_pool_bound_hook_salt` are asynchronous.
Do not translate Node calls mechanically or wrap unified preparation in another
mining/planning loop. Release presets own profile/deployment wiring. Launch commands
use the fixed preset native ceiling (32M total gas on 4663), not profile-sized gas,
poster additions or held-gas substitutions. Multi-market or conversion funding is
always staged. Unknown capacity refuses without an execution fallback. Planning
never simulates calldata; build-next constructs only the immediate command.
Chain identity is observed once per client session; the wallet assigns EOA nonces.
Launch/domain nonces and receipt-confirmation policy remain unchanged.

Review the selected atomic/staged mode before execution. A refusal does not
authorize smaller budgets, fewer positions, dropped buys, a different hook or
automatic wallet-write replay. Staging partitions preparation, not final activation.
Native Nitro execution is not generic local EVM evidence. Use the release preset's
native ceiling, not a template estimate or fixture-derived chain limit.

## Representation and integration boundaries

- Semantic models are immutable dataclasses with snake_case attributes. A decoded
  `LaunchReceiptV1` is not a dict: use `receipt.token_out` / `receipt.quote_spent`.
  Portable wire JSON retains Solidity camelCase and decimal-string wide quantities;
  use `serialize_launch_plan` / `parse_launch_plan`.
- Keep token amounts, liquidity, USD-X18, fee pips and basis points in named integer
  units. Do not introduce floats during parsing, pricing or ABI conversion.
- A profile selects an implementation/configuration contract, not arbitrary hook
  calldata. Config version and topology do not identify fee behavior.
- Quote/fee assets and direct ERC20/native-wrap funding are not a curated-token
  allowlist. Conversion inputs and target/spender roles have separate admission;
  inspect the pricing skill before integrating Kyber.
- Separate planning assessments and mathematical quotes from wallet submission,
  canonical activation, actual receipt outputs and API publication.
- Reuse exported AMM/lending ABIs/helpers. Indexed candidates are not executable
  calldata. Launch/oracle setup does not grant lending admission: mature history,
  liquidity, risk policy and governance remain separate. A lending-feed cutoff is
  not automatically a launch USD valuation requirement.
- Normalize/checksum addresses through web3.py/SDK helpers. Never use case-sensitive
  address strings to distinguish deployments or quote assets.

## Verification and safety

`python examples/quickstart.py` is a read-only discovery smoke after installation.
Code changes use `python -m pytest -q`; opt-in HTTP/chain paths require documented
disposable services. Documentation changes should exercise pure/read-only consumer
paths, not funded launch runners. Keep package and `__version__` versions consistent.

Reading, pricing, mining and compilation do not authorize signing, broadcasting,
uploads or publication. Planning never executes launch transactions on any
backend. Preserve unrelated worktree changes.

Historical decisions and known regressions are in
`../token-launch/references/audit-history.md`; current exports and deployed evidence
override superseded conversations.
