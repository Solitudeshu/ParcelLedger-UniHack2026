from conftest import post
from eth_account import Account
from eth_account.messages import encode_typed_data


def test_wallet_binding_signature_replay_and_cross_user(clients):
    merchant, buyer, outsider = clients
    account, wrong = Account.create(), Account.create()
    response = post(merchant, "/v1/wallet/challenge", {"address": account.address})
    assert response.status_code == 200, response.text
    challenge = response.json()
    message = encode_typed_data(full_message=challenge["typed_data"])
    proof = {"challenge_id": challenge["id"], "signature": "0x"+account.sign_message(message).signature.hex()}
    bad = {**proof, "signature": "0x"+wrong.sign_message(message).signature.hex()}
    assert post(merchant, "/v1/wallet/verify", bad).status_code == 400
    assert post(outsider, "/v1/wallet/verify", proof).status_code == 403
    verified = post(merchant, "/v1/wallet/verify", proof)
    assert verified.status_code == 200, verified.text
    assert verified.json()["address"] == account.address.lower()
    assert post(merchant, "/v1/wallet/verify", proof).status_code == 409
    second = post(buyer, "/v1/wallet/challenge", {"address": account.address}).json()
    signature = "0x"+account.sign_message(encode_typed_data(full_message=second["typed_data"])).signature.hex()
    assert post(buyer, "/v1/wallet/verify", {"challenge_id": second["id"], "signature": signature}).status_code == 409
