import sys, os, json
from pyteal import *
from pathlib import Path

codepath = (Path( os.curdir ) / "shared").resolve()
sys.path.append(str(codepath))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import algosdk
from algosdk import transaction
from algosdk import account, mnemonic
from algosdk.v2client import algod
from algosdk.transaction import AssetOptInTxn, AssetTransferTxn
import algosdk.error
import base64
import logging

# loading credential utility
from algo_util import load_credentials 

# load credentials from storage
credentials = load_credentials(file_name="credentials_temp")
usiv_account = credentials['usiv']

# FastAPI setup
app = FastAPI()
asset_id = 684625427  

# Initialize the algod client (Testnet or Mainnet)
algod_client = algod.AlgodClient(
    algod_token='', 
    algod_address=credentials['algod_test'], 
    headers=credentials['purestake_token']
)

# Setup logging
logging.basicConfig(level=logging.INFO)

# Models for request bodies
class OptInRequest(BaseModel):
    address: str
    private_key: str

class ReceiveCoinRequest(BaseModel):
    address: str
    private_key: str

class VoteRequest(BaseModel):
    address: str
    private_key: str
    vote: str

# Define smart contract functions
def smart_contract_opt_in(address, private_key, asset_id):
    sp = algod_client.suggested_params()
    txn = AssetOptInTxn(address, sp, asset_id)
    signed_txn = txn.sign(private_key)
    txid = algod_client.send_transaction(signed_txn)
    return txid

def smart_contract_receive_coin(address, private_key, asset_id):
    sp = algod_client.suggested_params()
    transfer_txn = AssetTransferTxn(sender=usiv_account["public"],
                                    sp=sp,
                                    receiver=address,
                                    index=asset_id,
                                    amt=1)  # Adjust the amount as necessary
    signed_txn = transfer_txn.sign(usiv_account["private"])
    txid = algod_client.send_transaction(signed_txn)
    return txid


from algosdk.transaction import calculate_group_id, AssetTransferTxn, ApplicationNoOpTxn

def smart_contract_vote(address, private_key, vote):
    """
    Cast a vote in the smart contract application.
    
    """
    app_id = 684636235  # The application ID of the voting smart contract
    idx_coin = 684625427  # The asset ID for the coin to be transferred

    # Ensure the vote is valid
    valid_votes = ["In favor", "Against", "Abstained"]
    if vote not in valid_votes:
        raise ValueError(f"Invalid vote option. Choose from {valid_votes}.")
    
    # Prepare the transactions
    sp = algod_client.suggested_params()

    # 1st transaction -> Asset transfer to make sure the user can vote
    amt_1 = int(1)
    txn_1 = AssetTransferTxn(
        sender=address, 
        sp=sp, 
        receiver=usiv_account['public'], 
        amt=amt_1,
        index=idx_coin)

    # 2nd transaction -> Vote
    txn_2 = ApplicationNoOpTxn(
        sender=address, 
        sp=sp, 
        index=app_id, 
        app_args=[vote]
    )

    # Create group transaction by assigning the group_id to the transaction group
    gid = calculate_group_id([txn_1, txn_2])
    txn_1.group = gid
    txn_2.group = gid

    # Sign each transaction individually
    stxn_1 = txn_1.sign(private_key)
    stxn_2 = txn_2.sign(private_key)

    # Send the transactions
    txid = algod_client.send_transactions([stxn_1, stxn_2])

    # Wait for confirmation
    wait_for_confirmation(algod_client, txid)

    return txid

def wait_for_confirmation(client, txid):
    """
    Utility function to wait until the transaction is confirmed before proceeding.

    """
    last_round = client.status().get('last-round')
    while True:
        tx_info = client.pending_transaction_info(txid)
        if tx_info.get('confirmed-round') and tx_info.get('confirmed-round') > 0:
            print(f"Transaction {txid} confirmed in round {tx_info.get('confirmed-round')}.")
            break
        else:
            print("Waiting for confirmation...")
            last_round += 1
            client.status_after_block(last_round)


def fetch_voting_results(algod_client=algod_client, app_id=684636235):
    """
    Fetch the voting results from the Algorand blockchain.
    """
    try:
        # Fetch the global state of the application
        app_info = algod_client.application_info(app_id)
        global_state = app_info['params']['global-state']

        # Initialize counters
        in_favor = 0
        against = 0
        abstained = 0

        # Sum the values for each key
        for state in global_state:
            key = base64.b64decode(state['key']).decode('utf-8')
            value = state['value']['uint']
            if key == "In favor":
                in_favor += value
            elif key == "Against":
                against += value
            elif key == "Abstained":
                abstained += value

        # Determine the verdict
        if in_favor > against:
            verdict = "Approved"
        elif against > in_favor:
            verdict = "Rejected"
        else:
            verdict = "Tie"

        # Return the result
        return {
            "In favor": in_favor,
            "Against": against,
            "Abstained": abstained,
            "Verdict": verdict
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/optin")
async def opt_in(request: OptInRequest):
    try:
        txid = smart_contract_opt_in(request.address, request.private_key, asset_id)
        return {"message": "Opt-in successful", "txid": txid}
    except Exception as e:
        logging.error(f"Error during opt-in: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/receive-coin")
async def receive_coin(request: ReceiveCoinRequest):
    try:
        txid = smart_contract_receive_coin(request.address, request.private_key, asset_id)
        return {"message": "Coin received successfully", "txid": txid}
    except Exception as e:
        logging.error(f"Error during receive coin: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/vote")
async def vote(request: VoteRequest):
    try:
        txid = smart_contract_vote(request.address, request.private_key, request.vote)
        return {"message": "Vote cast successfully", "txid": txid}
    except ValueError as ve:
        logging.error(f"Invalid vote option: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logging.error(f"Error during vote: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/results")
async def results():
    try:
        results = fetch_voting_results()
        return results
    except Exception as e:
        logging.error(f"Error fetching results: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
