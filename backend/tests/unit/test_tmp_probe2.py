from tests.unit.test_transaction_edit import client, spend  # noqa
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import Transaction


def test_durability(client, session, engine, accounts):
    txn = spend(client, accounts, description="Tesco", merchant="TESCO")
    r = client.patch(f"/api/transactions/{txn['id']}", json={"description": "Renamed"})
    print("PATCH:", r.status_code, r.json()["description"])
    other = sessionmaker(bind=engine)()
    row = other.execute(
        select(Transaction.description).where(Transaction.id == txn["id"])
    ).scalar_one_or_none()
    print("SEEN BY ANOTHER SESSION:", row)
    other.close()


def test_long_merchant(client, accounts):
    txn = spend(client, accounts, description="Tesco", merchant="TESCO")
    r = client.patch(f"/api/transactions/{txn['id']}", json={"merchant": "X" * 300})
    print("LONG MERCHANT:", r.status_code, r.text[:200])
    after = client.get("/api/transactions").json()[0]
    print("AFTER:", after["merchant"][:20] if after["merchant"] else None, len(after["merchant"] or ""))
