"""Read-only chain indexing; cursor and events commit atomically.

A changed cursor hash retains old logs as orphaned and replays from deployment.
No transactions are sent by this process.
"""
import logging
import time

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from services.api import chain_rpc
from services.api.chain_events import decode_log
from services.api.config import settings
from services.api.database import ChainCase, ChainLog, SessionLocal, Shipment, now

logger = logging.getLogger("kienchung.reconciler")


def reconcile_case(shipment_id):
    deployed = chain_rpc.deployment()
    if not deployed:
        return False
    with SessionLocal() as db, db.begin():
        case = db.scalar(select(ChainCase).where(ChainCase.shipment_id == shipment_id,
            ChainCase.chain_id == settings().chain_id).with_for_update(skip_locked=True))
        if case is None:
            return False
        latest, _, _ = chain_rpc.network_block()
        latest = int(latest, 16)
        start = int(deployed["start_block"])
        if case.indexed_block is not None:
            old_block = chain_rpc.rpc("eth_getBlockByNumber", [hex(case.indexed_block), False])
            if not old_block or old_block["hash"] != case.indexed_block_hash:
                db.execute(update(ChainLog).where(ChainLog.shipment_id == shipment_id).values(canonical=False))
            else:
                start = case.indexed_block+1
        # The public Fuji RPC accepts at most 100 blocks per log request.
        page_size = 100 if settings().chain_id == 43113 else 2000
        end = min(latest, start + page_size - 1)
        if start <= end:
            end_block = chain_rpc.rpc("eth_getBlockByNumber", [hex(end), False])
            logs = chain_rpc.rpc("eth_getLogs", [{"address": case.escrow_address, "fromBlock": hex(start), "toBlock": hex(end)}])
            for log in logs:
                decoded = decode_log(log)
                if decoded is None or log.get("removed"):
                    continue
                if log["address"].lower() != case.escrow_address.lower() or not start <= int(log["blockNumber"],16) <= end:
                    raise ValueError("RPC_LOG_SCOPE_INVALID")
                values = dict(shipment_id=shipment_id, chain_id=case.chain_id, block_number=int(log["blockNumber"],16),
                    block_hash=log["blockHash"], transaction_hash=log["transactionHash"], log_index=int(log["logIndex"],16),
                    kind=decoded["name"], arguments=decoded["args"], canonical=True)
                db.execute(insert(ChainLog).values(**values).on_conflict_do_update(
                    index_elements=["chain_id", "transaction_hash", "log_index"], set_=values))
            check = chain_rpc.rpc("eth_getBlockByNumber", [hex(end), False])
            if check["hash"] != end_block["hash"]:
                raise ValueError("CHAIN_MOVED_DURING_INDEX")
            case.indexed_block, case.indexed_block_hash = end, check["hash"]
        current = chain_rpc.snapshot(case.escrow_address)
        shipment = db.get(Shipment, shipment_id)
        if not shipment or current["terms"]["caseId"].lower() != shipment.case_id.lower():
            raise ValueError("CHAIN_CASE_IDENTITY_MISMATCH")
        case.snapshot, case.synced_at = current, now()
        if not case.snapshot["accounting_consistent"]:
            logger.error("Accounting mismatch for case %s", shipment_id)
        return True


def run_once():
    with SessionLocal() as db:
        identifiers = list(db.scalars(select(ChainCase.shipment_id).where(ChainCase.chain_id == settings().chain_id)))
    for identifier in identifiers:
        try:
            reconcile_case(identifier)
        except Exception:
            logger.warning("Chain sync unavailable for case %s; cursor not advanced", identifier)
    return len(identifiers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            run_once()
        except Exception:
            logger.warning("Reconciler dependencies unavailable")
        time.sleep(15)
