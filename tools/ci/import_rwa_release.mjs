import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Import a built, reviewed Node 0.17.0 checkout. No RPC, fork, signing or deployment.
const nodeRoot = process.argv[2];
if (!nodeRoot) throw new Error("Usage: node tools/ci/import_rwa_release.mjs /path/to/node-sdk");
const source = resolve(nodeRoot);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const sdk = await import(pathToFileURL(resolve(source, "dist/lifecycle/index.js")).href);
const { deriveKnownPoolBoundHookDeployment } = await import(pathToFileURL(resolve(source, "dist/lifecycle/calibration.js")).href);
const { LIFECYCLE_GAS_PROFILE_VERSION } = await import(pathToFileURL(resolve(source, "dist/lifecycle/gas-profile.js")).href);
const { makeRwaPlan, keys, testToken, listedStocks, rwaTerms } = await import(pathToFileURL(resolve(source, "test/fixtures/rwa-plan.mjs")).href);
const { encodeAbiParameters, toHex } = createRequire(resolve(source, "package.json"))("viem");
const version = JSON.parse(readFileSync(resolve(source, "package.json"), "utf8")).version;
if (version !== "0.17.0" || LIFECYCLE_GAS_PROFILE_VERSION !== "robinhood-4663-capacity-v6") {
  throw new Error("RWA import requires the reviewed Node 0.17.0 capacity-v6 release");
}
const read = (name) => JSON.parse(readFileSync(resolve(source, "test/fixtures", name), "utf8"));
const write = (name, value) => writeFileSync(resolve(root, name), `${JSON.stringify(value, (_, item) => typeof item === "bigint" ? item.toString() : item, 2)}\n`);
const digest = (data) => createHash("sha256").update(data).digest("hex");
const sha256 = (encoded) => digest(Buffer.from(encoded.slice(2), "hex"));
const evidence = read("rwa-registry-readback.json");
const identity = { chainId: BigInt(evidence.chainId), orchestrator: evidence.orchestrator };
const release = Object.fromEntries(["chainId", "registry", "orchestrator", "blockNumber", "blockHash"].map((key) => [key, evidence[key]]));
release.adapters = [];
release.profiles = [];
release.provenance = { sourceCommit: evidence.sourceCommit, goLiveRecordCommit: evidence.goLiveRecordCommit, nodeVersion: version };
for (const key of keys) {
  const { profile, boundHook } = sdk.getKnownLifecycleProfile({ ...identity, key });
  const recorded = evidence.profiles.find(({ profileId }) => profileId === profile.id);
  const envelopeBytes = sdk.encodeLaunchEnvelope(profile.envelope);
  assert.equal(envelopeBytes, recorded.raw.profileEnvelope, "Node envelope differs from registry readback");
  assert.equal(envelopeBytes, recorded.admission.envelopeBytes, "Node envelope differs from preserved RWA admission");
  release.adapters.push({ id: profile.registration.adapterId, address: profile.adapter.implementation,
    codehash: profile.adapter.codeHash, capabilities: Number(profile.adapter.capabilities), configVersion: profile.adapter.configVersion });
  release.profiles.push({ ...profile.registration, capabilities: Number(profile.registration.capabilities),
    key, id: profile.id, envelopeBytes, creationCode: boundHook.creationCode, topology: profile.topology });
  release.provenance[key] = { transactionHash: recorded.admission.transactionHash,
    blockNumber: recorded.admission.blockNumber, blockHash: recorded.admission.blockHash };
}
write("black_market_sdk/_rwa_release_20261113.json", release);
for (const name of ["rwa-registry-readback.json", "template-gas-rwa-matrix.json", "template-gas-rwa-smoke.json"]) {
  copyFileSync(resolve(source, "test/fixtures", name), resolve(root, "tests/fixtures", name));
}
const vectors = { generator: "Node SDK 0.17.0 / viem; offline", cases: [], termChecks: [] };
const describe = (plan) => {
  const { deployment, parameters } = deriveKnownPoolBoundHookDeployment({ plan, marketIndex: 0, token: testToken });
  return { planHash: sdk.hashLaunchPlan(plan), planEncodingSha256: sha256(sdk.encodeLaunchPlan(plan)),
    marketCommitment: parameters.marketCommitment, deployment };
};
for (const key of keys) for (let size = 1; size <= sdk.RWA_MAX_BASKET; size += 1) {
  const { plan, config } = makeRwaPlan(key, { terms: rwaTerms(size) });
  const variants = [{ rwaLaunchConfig: { ...config.rwaLaunchConfig, shareBps: 10000, premiumBps: 100,
    maxPriceAge: sdk.RWA_ANY_PRICE_AGE, weightsBps: config.rwaLaunchConfig.basket.map((_, i) => i === size - 1 ? 10000 : 0) } },
    { hookSalt: toHex(1234n, { size: 32 }) }].map((changes) => {
    const changed = { ...plan, markets: [{ ...plan.markets[0], config: sdk.encodePoolBoundV4LifecycleMarketConfig({ ...config, ...changes }) }] };
    return { changes, ...describe(changed) };
  });
  vectors.cases.push({ key, size, predictedToken: testToken, config,
    plan: JSON.parse(sdk.serializeLaunchPlan(plan)), tail: sdk.encodeRwaLaunchTail(config.rwaLaunchConfig),
    ...describe(plan), hookCommitmentVariants: variants });
}
const termsComponents = [{ name: "shareBps", type: "uint16" }, { name: "premiumBps", type: "uint16" },
  { name: "maxPriceAge", type: "uint32" }, { name: "basket", type: "address[]" }, { name: "weightsBps", type: "uint16[]" }];
const checkTerms = (name, terms) => {
  let accepted = true, code, tail;
  try { tail = sdk.encodeRwaLaunchTail(terms); } catch (error) { accepted = false; code = error.code; }
  return { name, terms, accepted, code, tail };
};
for (const profile of evidence.profiles) for (const check of profile.termChecks.checks) {
  const computed = checkTerms(check.name, check.terms);
  assert.equal(computed.accepted, check.accepted || check.name === "unlisted stock");
  if (computed.accepted) assert.equal(computed.tail, check.tail);
  vectors.termChecks.push({ profileId: profile.profileId, ...computed, tail: check.tail, deployedAccepted: check.accepted });
}
const [a, b] = listedStocks;
for (const [name, changes] of Object.entries({
  "share above 10000": { shareBps: 10001 }, "negative share": { shareBps: -1 }, "fractional share": { shareBps: 0.5 },
  "premium above 100": { premiumBps: 101 }, "age above uint32": { maxPriceAge: 2 ** 32 }, "negative age": { maxPriceAge: -1 },
  "empty basket": { basket: [], weightsBps: [] },
  "seven stocks": { basket: listedStocks.slice(0, 7), weightsBps: [4000, 1000, 1000, 1000, 1000, 1000, 1000] },
  "descending basket": { basket: [b, a], weightsBps: [5000, 5000] }, "duplicate stock": { basket: [a, a], weightsBps: [5000, 5000] },
  "USDG stock": { basket: [sdk.RWA_QUOTE_ASSET], weightsBps: [10000] },
  "zero address": { basket: ["0x0000000000000000000000000000000000000000"], weightsBps: [10000] },
  "missing weight": { basket: [a, b], weightsBps: [10000] }, "weights below 10000": { basket: [a, b], weightsBps: [5000, 4999] },
  "weights above 10000": { basket: [a, b], weightsBps: [5000, 5001] }, "weight above uint16": { basket: [a, b], weightsBps: [70000, -60000] },
})) {
  const check = checkTerms(name, { ...rwaTerms(), ...changes });
  assert.equal(check.accepted, false);
  assert.equal(check.code, "INVALID_RWA_CONFIG");
  vectors.termChecks.push(check);
}
// ABI tuple encoding is independently recorded, including the leading offset word.
vectors.anyAgeTails = Array.from({ length: sdk.RWA_MAX_BASKET }, (_, i) => {
  const terms = { ...rwaTerms(i + 1), maxPriceAge: sdk.RWA_ANY_PRICE_AGE };
  const tail = encodeAbiParameters([{ type: "tuple", components: termsComponents }], [terms]);
  assert.equal(sdk.encodeRwaLaunchTail(terms), tail);
  return { terms, tail };
});
write("tests/fixtures/rwa-launch-vectors.json", vectors);
for (const [matrixName, parityName] of [["template-gas-matrix.json", "template-gas-node-parity.json"],
  ["template-gas-rwa-matrix.json", "template-gas-rwa-node-parity.json"]]) {
  const matrixBytes = readFileSync(resolve(source, "test/fixtures", matrixName));
  assert.equal(digest(readFileSync(resolve(root, "tests/fixtures", matrixName))), digest(matrixBytes), "Preserved matrix differs from Node evidence");
  const parity = { generator: "Node SDK 0.17.0 capacity-v6; offline (no matrix re-execution)",
    matrixSha256: digest(matrixBytes), cases: [] };
  for (const row of JSON.parse(matrixBytes).cases) {
    const plan = sdk.parseLaunchPlan(JSON.stringify(row.plan));
    const commands = sdk.buildLaunchTransactions({ plan, mode: row.scenario.mode,
      ...(row.scenario.mode === "staged" ? { preparationBatches: row.transactions.filter(({ kind }) => kind === "prepare").map(({ marketCount }) => marketCount) } : {}) });
    parity.cases.push({ planHash: sdk.hashLaunchPlan(plan), planEncodingSha256: sha256(sdk.encodeLaunchPlan(plan)),
      bounds: [0, 1500, 5000].map((headroomBps) => ({ headroomBps,
        ...sdk.computeLifecycleLaunchGasBounds({ plan, predictedToken: row.predictedToken, headroomBps }) })),
      initialBuys: sdk.computeLifecycleOpeningBuys({ plan, predictedToken: row.predictedToken }),
      transactions: commands.map((command) => ({ kind: command.kind, calldataSha256: sha256(command.data),
        ...(command.kind === "prepare" ? { preparationGas: sdk.preparationGasEstimate(plan, command.marketStart, command.marketCount) } : {}) })) });
  }
  write(`tests/fixtures/${parityName}`, parity);
}
console.log(`Imported ${release.profiles.length} RWA presets, ${vectors.cases.length} launch vectors and capacity-v6 parity for both matrices; no RPC used`);
