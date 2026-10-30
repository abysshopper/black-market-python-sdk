import { createHash } from "node:crypto";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Import a built, reviewed Node 0.16.0 checkout. No RPC, fork, signing or deployment.
const nodeRoot = process.argv[2];
if (!nodeRoot) throw new Error("Usage: node tools/ci/import_raeto_release.mjs /path/to/node-sdk");
const source = resolve(nodeRoot);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const sdk = await import(pathToFileURL(resolve(source, "dist/lifecycle/index.js")).href);
const { deriveKnownPoolBoundHookDeployment } = await import(pathToFileURL(resolve(source, "dist/lifecycle/calibration.js")).href);
const { makeRaetoPlan, keys, testToken } = await import(pathToFileURL(resolve(source, "test/fixtures/raeto-plan.mjs")).href);
const { LIFECYCLE_GAS_PROFILE_VERSION } = await import(pathToFileURL(resolve(source, "dist/lifecycle/gas-profile.js")).href);
const version = JSON.parse(readFileSync(resolve(source, "package.json"), "utf8")).version;
if (version !== "0.16.0" || LIFECYCLE_GAS_PROFILE_VERSION !== "robinhood-4663-capacity-v4") {
  throw new Error("Raeto import requires the reviewed Node 0.16.0 capacity-v4 release");
}
const read = (name) => JSON.parse(readFileSync(resolve(source, "test/fixtures", name), "utf8"));
const write = (name, value) => writeFileSync(resolve(root, name), `${JSON.stringify(value, (_, item) => typeof item === "bigint" ? item.toString() : item, 2)}\n`);
const sha256 = (encoded) => createHash("sha256").update(Buffer.from(encoded.slice(2), "hex")).digest("hex");
const evidence = read("raeto-registry-readback.json");
const identity = { chainId: BigInt(evidence.chainId), orchestrator: evidence.orchestrator };
const release = Object.fromEntries(["chainId", "registry", "orchestrator", "blockNumber", "blockHash"].map((key) => [key, evidence[key]]));
release.adapters = [];
release.profiles = [];
release.provenance = { sourceCommit: evidence.sourceCommit, goLiveRecordCommit: evidence.goLiveRecordCommit, nodeVersion: version };
for (const key of keys) {
  const { profile, boundHook } = sdk.getKnownLifecycleProfile({ ...identity, key });
  const recorded = evidence.profiles.find(({ profileId }) => profileId === profile.id);
  const envelopeBytes = sdk.encodeLaunchEnvelope(profile.envelope);
  if (envelopeBytes !== recorded.raw.profileEnvelope || envelopeBytes !== recorded.admission.envelopeBytes) {
    throw new Error("Node deployment differs from the preserved Raeto admission");
  }
  release.adapters.push({ id: profile.registration.adapterId, address: profile.adapter.implementation,
    codehash: profile.adapter.codeHash, capabilities: Number(profile.adapter.capabilities), configVersion: profile.adapter.configVersion });
  release.profiles.push({ ...profile.registration, capabilities: Number(profile.registration.capabilities),
    key, id: profile.id, envelopeBytes, creationCode: boundHook.creationCode, topology: profile.topology });
  release.provenance[key] = { transactionHash: recorded.admission.transactionHash,
    blockNumber: recorded.admission.blockNumber, blockHash: recorded.admission.blockHash };
}
write("black_market_sdk/_raeto_release_20261029.json", release);
const abiPath = resolve(root, "black_market_sdk/_sdk_abi_data.json");
const abiData = JSON.parse(readFileSync(abiPath, "utf8"));
abiData.raetoFeeCollectorV1Abi = sdk.raetoFeeCollectorV1Abi;
write("black_market_sdk/_sdk_abi_data.json", abiData);
for (const name of ["raeto-registry-readback.json", "template-gas-matrix.json"]) {
  copyFileSync(resolve(source, "test/fixtures", name), resolve(root, "tests/fixtures", name));
}
const vectors = { generator: "Node SDK 0.16.0 / viem; offline", cases: [] };
for (const key of keys) for (const quote of sdk.RAETO_QUOTE_ASSETS) for (const shareBps of sdk.RAETO_SHARE_BPS_OPTIONS) {
  const { plan, config } = makeRaetoPlan(key, { quote, shareBps });
  const { deployment, parameters } = deriveKnownPoolBoundHookDeployment({ plan, marketIndex: 0, token: testToken });
  vectors.cases.push({ key, quote, shareBps, predictedToken: testToken,
    plan: JSON.parse(sdk.serializeLaunchPlan(plan)), tail: sdk.encodeRaetoLaunchTail(config.raetoLaunchConfig),
    planHash: sdk.hashLaunchPlan(plan), planEncodingSha256: sha256(sdk.encodeLaunchPlan(plan)),
    marketCommitment: parameters.marketCommitment, deployment });
}
write("tests/fixtures/raeto-launch-vectors.json", vectors);
const matrix = read("template-gas-matrix.json");
const parity = { generator: "Node SDK 0.16.0 capacity-v4; offline (no matrix re-execution)",
  matrixSha256: createHash("sha256").update(readFileSync(resolve(source, "test/fixtures/template-gas-matrix.json"))).digest("hex"), cases: [] };
for (const row of matrix.cases) {
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
write("tests/fixtures/template-gas-node-parity.json", parity);
console.log(`Imported ${release.profiles.length} Raeto presets, ${vectors.cases.length} encoding vectors, and ${parity.cases.length} measured-case parity vectors; no RPC used`);
