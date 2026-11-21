import {mkdirSync, readFileSync, writeFileSync} from "node:fs";
mkdirSync("packages/chain/src", {recursive: true});
for (const name of ["ShipmentEscrow", "EscrowFactory", "DemoToken"]) {
  const artifact = JSON.parse(readFileSync(`contracts/out/${name}.sol/${name}.json`, "utf8"));
  writeFileSync(`packages/chain/src/${name}.ts`, `// Generated from compiled Solidity; run scripts/generate-chain-abi.mjs.\nexport const ${name}Abi = ${JSON.stringify(artifact.abi, null, 2)} as const;\n`);
}
console.log("Generated three contract ABIs.");
