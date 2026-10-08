"""Encode complete explicit reviewed shared4, bound5 or bound6 config JSON offline.

Use Solidity camelCase field names. Large exact quantities may be canonical
integer strings; no template, author, fee or protocol defaults are supplied.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from black_market_sdk import (
    LifecyclePoolBoundV4MarketConfigV5, LifecyclePoolBoundV4MarketConfigV6, LifecycleV4MarketConfig, LifecycleV4Position,
    V4_MARKET_CONFIG_COMPONENTS_V4, V4_MARKET_CONFIG_COMPONENTS_V5, V4_MARKET_CONFIG_COMPONENTS_V6,
    encode_lifecycle_pool_bound_v4_market_config, encode_lifecycle_v4_market_config,
)
from black_market_sdk.lifecycle import _parse_json_tuple


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    value = json.loads(args.config.read_text())
    version = value["version"]
    if version in (4, "4") and not isinstance(version, bool):
        components, kind, encode = V4_MARKET_CONFIG_COMPONENTS_V4, LifecycleV4MarketConfig, encode_lifecycle_v4_market_config
    elif version in (5, "5") and not isinstance(version, bool):
        components, kind, encode = V4_MARKET_CONFIG_COMPONENTS_V5, LifecyclePoolBoundV4MarketConfigV5, encode_lifecycle_pool_bound_v4_market_config
    elif version in (6, "6") and not isinstance(version, bool):
        components, kind, encode = V4_MARKET_CONFIG_COMPONENTS_V6, LifecyclePoolBoundV4MarketConfigV6, encode_lifecycle_pool_bound_v4_market_config
    else:
        raise ValueError("config version must be explicit shared4, bound5 or bound6")
    values = _parse_json_tuple(components, value)
    config = kind(*values[:-1], tuple(LifecycleV4Position(*position) for position in values[-1]))
    print(json.dumps({"version": config.version, "config": "0x" + encode(config).hex(),
        "note": "Exact config encoding only; registry admission and execution remain unproved"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
