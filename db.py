from pymongo import MongoClient, ASCENDING
from pymongo.errors import OperationFailure
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client["age_db"]

usersCollection = db["users"]
compCollection = db["comp"]
leadCollection = db["leads"]
campCollection = db["campaigns"]
msgCollection = db["messages"]


def ensure_indexes():
    """
    Create unique indexes on critical fields.
    Fails gracefully if duplicates already exist in the DB — run
    POST /admin/cleanup_duplicates first, then this will succeed.
    """
    errors = []

    try:
        usersCollection.create_index(
            [("clerk_user_id", ASCENDING)],
            unique=True,
            sparse=True,   # ignores docs where clerk_user_id is missing
            name="clerk_user_id_unique",
        )
        print("[DB] Index OK: users.clerk_user_id (unique)", flush=True)
    except OperationFailure as e:
        errors.append(f"users.clerk_user_id: {e.details.get('errmsg', str(e))}")

    try:
        compCollection.create_index(
            [("name", ASCENDING)],
            unique=True,
            sparse=True,
            name="company_name_unique",
        )
        print("[DB] Index OK: comp.name (unique)", flush=True)
    except OperationFailure as e:
        errors.append(f"comp.name: {e.details.get('errmsg', str(e))}")

    try:
        compCollection.create_index(
            [("created_by", ASCENDING)],
            unique=True,
            sparse=True,   # old auth.py records lack created_by — excluded from index
            name="company_created_by_unique",
        )
        print("[DB] Index OK: comp.created_by (unique)", flush=True)
    except OperationFailure as e:
        errors.append(f"comp.created_by: {e.details.get('errmsg', str(e))}")

    if errors:
        print("[DB] Some indexes could not be created (duplicates exist):", flush=True)
        for err in errors:
            print(f"  - {err}", flush=True)
        print("[DB] Run POST /admin/cleanup_duplicates then restart to apply indexes.", flush=True)

    return errors


# Try on startup — will log warnings if duplicates exist but won't crash the server
ensure_indexes()
