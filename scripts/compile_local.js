/**
 * scripts/compile_local.js
 * Uses the bundled solc npm package (0.8.26) to compile all contracts
 * and write ABI + bytecode to artifacts/.  Used in environments where
 * Hardhat cannot download a compiler (no outbound network).
 *
 * Usage:  node scripts/compile_local.js
 */
const solc    = require("solc");
const fs      = require("fs");
const path    = require("path");

const CONTRACTS_DIR = path.join(__dirname, "..", "contracts");
const ARTIFACTS_DIR = path.join(__dirname, "..", "artifacts", "contracts");
const OZ_PATH       = path.join(__dirname, "..", "node_modules", "@openzeppelin", "contracts");

// ── Read all .sol files ───────────────────────────────────────────────────
const sources = {};
for (const file of fs.readdirSync(CONTRACTS_DIR).filter(f => f.endsWith(".sol"))) {
  sources[file] = { content: fs.readFileSync(path.join(CONTRACTS_DIR, file), "utf8") };
}

// ── Import resolver (handles @openzeppelin/... imports) ───────────────────
function findImports(importPath) {
  // @openzeppelin/contracts/...
  if (importPath.startsWith("@openzeppelin/contracts/")) {
    const rel = importPath.replace("@openzeppelin/contracts/", "");
    const full = path.join(OZ_PATH, rel);
    if (fs.existsSync(full)) return { contents: fs.readFileSync(full, "utf8") };
  }
  // Relative imports within contracts/
  const full = path.join(CONTRACTS_DIR, importPath);
  if (fs.existsSync(full)) return { contents: fs.readFileSync(full, "utf8") };
  return { error: `Import not found: ${importPath}` };
}

// ── Compile ───────────────────────────────────────────────────────────────
const input = {
  language: "Solidity",
  sources,
  settings: {
    optimizer: { enabled: true, runs: 200 },
    outputSelection: {
      "*": { "*": ["abi", "evm.bytecode", "evm.deployedBytecode"] },
    },
  },
};

console.log(`\nCompiling with solc ${solc.version()}...\n`);
const output = JSON.parse(solc.compile(JSON.stringify(input), { import: findImports }));

// ── Report errors / warnings ──────────────────────────────────────────────
let hasError = false;
if (output.errors) {
  for (const err of output.errors) {
    const prefix = err.severity === "error" ? "✗ ERROR" : "⚠ WARN ";
    console.log(`  ${prefix}  ${err.formattedMessage.trim()}`);
    if (err.severity === "error") hasError = true;
  }
}

if (hasError) {
  console.error("\nCompilation FAILED — see errors above.");
  process.exit(1);
}

// ── Write artifacts ───────────────────────────────────────────────────────
fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });

let compiled = 0;
for (const [file, fileOutput] of Object.entries(output.contracts || {})) {
  for (const [contractName, contractOutput] of Object.entries(fileOutput)) {
    const dir = path.join(ARTIFACTS_DIR, `${file}`);
    fs.mkdirSync(dir, { recursive: true });
    const artifact = {
      contractName,
      abi:       contractOutput.abi,
      bytecode:  "0x" + contractOutput.evm.bytecode.object,
      deployedBytecode: "0x" + contractOutput.evm.deployedBytecode.object,
    };
    fs.writeFileSync(path.join(dir, `${contractName}.json`), JSON.stringify(artifact, null, 2));
    const abiMethods = artifact.abi.filter(x => x.type === "function").map(x => x.name);
    console.log(`  ✓ ${contractName}  (${abiMethods.length} functions: ${abiMethods.join(", ")})`);
    compiled++;
  }
}

console.log(`\nCompilation SUCCEEDED — ${compiled} contracts written to artifacts/contracts/\n`);
