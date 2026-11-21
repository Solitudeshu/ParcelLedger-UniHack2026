"""Known Solidity event schemas; RPC payloads never choose their own labels."""
from eth_abi import decode
from eth_utils import keccak

EVENTS = {
    "Funded": [("payer", "address", True), ("amount", "uint256", False)],
    "EvidenceCommitted": [("stage", "uint8", True), ("manifestHash", "bytes32", True), ("evidenceRoot", "bytes32", False), ("sequence", "uint256", False), ("actor", "address", False)],
    "Activated": [("termsHash", "bytes32", False)],
    "ReportAccepted": [("reportHash", "bytes32", True), ("version", "uint256", False), ("evidenceRoot", "bytes32", False), ("nonce", "uint256", False)],
    "DisputeOpened": [("actor", "address", True), ("evidenceHash", "bytes32", True)],
    "Resolved": [("merchantAmount", "uint256", False), ("buyerAmount", "uint256", False), ("commitment", "bytes32", True), ("arbitrated", "bool", False)],
    "Cancelled": [("refunded", "uint256", False)],
    "Withdrawn": [("beneficiary", "address", True), ("amount", "uint256", False)],
}
TOPICS = {"0x"+keccak(text=name+"("+",".join(field[1] for field in fields)+")").hex(): name
          for name, fields in EVENTS.items()}


def decode_log(log):
    if not log.get("topics") or log["topics"][0].lower() not in TOPICS:
        return None
    name = TOPICS[log["topics"][0].lower()]
    fields = EVENTS[name]
    nonindexed = iter(decode([field[1] for field in fields if not field[2]], bytes.fromhex(log["data"][2:])))
    topics = iter(log["topics"][1:])
    values = {}
    for key, kind, indexed in fields:
        value = decode([kind], bytes.fromhex(next(topics)[2:]))[0] if indexed else next(nonindexed)
        values[key] = "0x"+value.hex() if isinstance(value, bytes) else value if isinstance(value, bool) else str(value)
    return {"name": name, "args": values}
