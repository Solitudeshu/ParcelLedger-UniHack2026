"""Bounded read-only JSON-RPC to an operator-configured test network."""
import json
from pathlib import Path

import httpx
from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from services.api.config import settings
from services.api.errors import DomainError

ZERO32 = "0x" + "00"*32
ZERO_ADDRESS = "0x" + "00"*20


def deployment():
    path = Path(settings().chain_deployment_path)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("status") != "DEPLOYED" or value.get("chain_id") != settings().chain_id:
        return None
    return value


def rpc(method, params):
    if settings().chain_id not in {31337, 43113}:
        raise DomainError("TESTNET_ONLY", "Môi trường chỉ hỗ trợ quỹ thử nghiệm.", 503)
    try:
        with httpx.Client(timeout=httpx.Timeout(12., connect=4.)) as client:
            response = client.post(settings().chain_rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            response.raise_for_status()
            value = response.json()
        if "result" not in value or "error" in value:
            raise ValueError("RPC_ERROR")
        return value["result"]
    except (ValueError, httpx.HTTPError) as exc:
        raise DomainError("CHAIN_UNAVAILABLE", "Chưa đọc được mạng blockchain. Hãy thử lại.", 503) from exc


def call(address, signature, output_types, input_types=(), args=(), block="latest"):
    data = "0x"+(keccak(text=signature)[:4]+encode(input_types, args)).hex()
    raw = rpc("eth_call", [{"to": address, "data": data}, block])
    try:
        if not isinstance(raw, str) or not raw.startswith("0x") or raw == "0x":
            raise ValueError("EMPTY_OR_INVALID_CONTRACT_RETURN")
        values = decode(output_types, bytes.fromhex(raw[2:]))
        return values[0] if len(values) == 1 else values
    except (ValueError, OverflowError, DecodingError) as exc:
        raise DomainError("CHAIN_DATA_INVALID", "Dữ liệu hợp đồng không khớp cấu hình.", 503) from exc


def network_block():
    if int(rpc("eth_chainId", []), 16) != settings().chain_id:
        raise DomainError("CHAIN_ID_MISMATCH", "RPC không đúng mạng đã cấu hình.", 503)
    block = rpc("eth_getBlockByNumber", ["latest", False])
    return block["number"], int(block["timestamp"], 16), block["hash"]


TERM_FIELDS = ["caseId", "merchant", "buyer", "token", "reserveAmount", "acceptBy", "reviewDeadline", "arbitrationSafe", "termsHash"]
TERM_TYPES = ["bytes32", "address", "address", "address", "uint256", "uint64", "uint64", "address", "bytes32"]


def snapshot(address):
    block, timestamp, block_hash = network_block()
    terms = call(address, "terms()", TERM_TYPES, block=block)
    result = {"block_number": int(block, 16), "block_hash": block_hash, "chain_timestamp": timestamp}
    result["terms"] = {name: "0x"+value.hex() if isinstance(value, bytes) else str(value)
                       for name, value in zip(TERM_FIELDS, terms, strict=True)}
    for name in ("state", "evidenceSequence", "acceptedReportVersion", "reportNonce", "settlementNonce", "disputeNonce", "totalFunded", "totalWithdrawn", "unallocated"):
        result[name] = str(call(address, name+"()", ["uint256"], block=block))
    for name in ("dispatchHash", "receiptHash", "evidenceRoot", "acceptedReportHash"):
        result[name] = "0x"+call(address, name+"()", ["bytes32"], block=block).hex()
    for party in ("merchant", "buyer"):
        result[party+"Claimable"] = str(call(address, "claimable(address)", ["uint256"], ["address"], [result["terms"][party]], block=block))
    result["tokenBalance"] = str(call(result["terms"]["token"], "balanceOf(address)", ["uint256"], ["address"], [address], block=block))
    liabilities = int(result["merchantClaimable"])+int(result["buyerClaimable"])+int(result["unallocated"])
    result["accounting_consistent"] = int(result["totalFunded"]) == int(result["totalWithdrawn"])+liabilities and int(result["tokenBalance"]) >= liabilities
    return result
