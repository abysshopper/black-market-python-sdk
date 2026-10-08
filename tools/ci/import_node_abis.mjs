import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Import a built, reviewed SDK release: no RPC, signing, or deployment occurs.
const modulePath = process.argv[2];
if (!modulePath) throw new Error("Usage: node tools/ci/import_node_abis.mjs /path/to/node-sdk/dist/index.js");
const sdk = await import(pathToFileURL(resolve(modulePath)).href);
const data = Object.fromEntries(Object.entries(sdk).filter(([name, value]) =>
  Array.isArray(value) && /(?:Abi|Components(?:V[0-9]+)?)$/.test(name)
));
const target = resolve(dirname(fileURLToPath(import.meta.url)), "../../black_market_sdk/_sdk_abi_data.json");
writeFileSync(target, `${JSON.stringify(data, null, 2)}\n`);
console.log(`Generated ${target} (${Object.keys(data).length} canonical ABI/component arrays)`);
