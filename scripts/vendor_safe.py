"""Vendor audited release bytecode plus complete matching Solidity sources/license.

No Safe JavaScript signing code is used; deploys use viem. This avoids bringing
the release's historical ethers peer dependency into the application runtime.
"""
import hashlib
import json
import shutil
from pathlib import Path


def main():
    source = Path("node_modules/@safe-global/safe-contracts")
    target = Path("contracts/vendor/safe-1.4.1")
    metadata = json.loads((source/"package.json").read_text())
    assert metadata["version"] == "1.4.1-2"
    target.mkdir(parents=True, exist_ok=True)
    for path in (source/"contracts").rglob("*.sol"):
        dest = target / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    shutil.copyfile(source/"LICENSE", target/"LICENSE")
    for name, relative in {
        "Safe": "Safe.sol/Safe.json", "SafeProxyFactory": "proxies/SafeProxyFactory.sol/SafeProxyFactory.json",
        "CompatibilityFallbackHandler": "handler/CompatibilityFallbackHandler.sol/CompatibilityFallbackHandler.json",
    }.items():
        artifact = json.loads((source/"build/artifacts/contracts"/relative).read_text())
        (target/f"{name}.json").write_text(json.dumps({key: artifact[key] for key in
            ("contractName", "sourceName", "abi", "bytecode", "deployedBytecode")}, indent=2), encoding="utf-8")
    hashes = {str(path.relative_to(target)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in target.rglob("*") if path.is_file() and path.name != "provenance.json"}
    (target/"provenance.json").write_text(json.dumps({"package": "@safe-global/safe-contracts",
        "package_version": metadata["version"], "upstream": "https://github.com/safe-global/safe-smart-account/tree/v1.4.1",
        "license": "LGPL-3.0-only", "modifications": "None to source or bytecode; artifact JSON retains ABI and bytecode fields.",
        "sha256": hashes}, indent=2), encoding="utf-8")
    print(json.dumps({"vendored_files": len(hashes), "folder": str(target)}))


if __name__ == "__main__":
    main()
