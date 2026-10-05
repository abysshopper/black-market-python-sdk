---
name: sdk-usage
description: Use the Abyss Python SDK to read protocol state, build unsigned launch plans, or integrate swaps and lending in a consumer application.
---

# Use the Python SDK

1. Read `README.md` and install `black-market-sdk` in the consumer environment.
2. Import public exports from `black_market_sdk`; source definitions are in
   `black_market_sdk/` and exported through `__init__.py`.
3. Select the intended chain explicitly. Resolve launch contracts with
   `get_launch_addresses(4663)` and other contracts with `get_addresses(4663)`.
4. Use a `Web3` client pointed at the intended RPC. Read launch offerings with
   `read_lifecycle_profiles(client, orchestrator=launch.orchestrator)`.
   Inspect returned admission and terms rather than inventing a profile configuration.
5. Use integer token units and explicit decimals. Reuse SDK ABIs and web3.py types.
6. Keep wallet submission separate from reads and unsigned planning.

Runnable read-only example: `python examples/quickstart.py` after installation.
Inspect existing swap/lending helpers and exported signatures before adding a wrapper.

For launch execution, read the sibling `token-launch/SKILL.md`. For API shapes and
recovery details, read `docs/launch-guide.md`. Validate repository edits with
`python -m pytest -q`; exercise the changed consumer path without writes unless
the user has explicitly authorized them.
