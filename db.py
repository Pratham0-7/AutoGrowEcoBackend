from pymongo import MongoClient, ASCENDING
from pymongo.errors import OperationFailure
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("MONGO_URI is not set")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client["age_db"]

usersCollection = db["users"]
compCollection = db["comp"]
leadCollection = db["leads"]
campCollection = db["campaigns"]
msgCollection = db["messages"]
stepCollection = db["steps"]
bookingsCollection = db["bookings"]
waMessagesCollection = db["wa_messages"]


def ensure_indexes():
    """
    Create useful indexes.
    Fails gracefully if duplicates already exist in the DB.
    """
    errors = []

    try:
        usersCollection.create_index(
            [("clerk_user_id", ASCENDING)],
            unique=True,
            sparse=True,
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
            sparse=True,
            name="company_created_by_unique",
        )
        print("[DB] Index OK: comp.created_by (unique)", flush=True)
    except OperationFailure as e:
        errors.append(f"comp.created_by: {e.details.get('errmsg', str(e))}")

    try:
        compCollection.create_index(
            [("meta_phone_number_id", ASCENDING)],
            sparse=True,
            name="company_meta_phone_number_id_idx",
        )
        print("[DB] Index OK: comp.meta_phone_number_id", flush=True)
    except OperationFailure as e:
        errors.append(f"comp.meta_phone_number_id: {e.details.get('errmsg', str(e))}")

    # Booking indexes
    try:
        bookingsCollection.create_index(
            [("start_at", ASCENDING)],
            name="booking_start_at_idx",
        )
        print("[DB] Index OK: bookings.start_at", flush=True)
    except OperationFailure as e:
        errors.append(f"bookings.start_at: {e.details.get('errmsg', str(e))}")

    try:
        bookingsCollection.create_index(
            [("date", ASCENDING), ("time", ASCENDING), ("status", ASCENDING)],
            name="booking_slot_status_idx",
        )
        print("[DB] Index OK: bookings.date_time_status", flush=True)
    except OperationFailure as e:
        errors.append(f"bookings.date_time_status: {e.details.get('errmsg', str(e))}")

    # WhatsApp Meta message indexes
    try:
        waMessagesCollection.create_index(
            [("company_id", ASCENDING), ("meta_message_id", ASCENDING)],
            sparse=True,
            name="wa_messages_company_meta_message_idx",
        )
        print("[DB] Index OK: wa_messages.company_meta_message", flush=True)
    except OperationFailure as e:
        errors.append(f"wa_messages.company_meta_message: {e.details.get('errmsg', str(e))}")

    try:
        waMessagesCollection.create_index(
            [("company_id", ASCENDING), ("contact_phone", ASCENDING), ("created_at", ASCENDING)],
            name="wa_messages_conversation_idx",
        )
        print("[DB] Index OK: wa_messages.conversation", flush=True)
    except OperationFailure as e:
        errors.append(f"wa_messages.conversation: {e.details.get('errmsg', str(e))}")

    if errors:
        print("[DB] Some indexes could not be created:", flush=True)
        for err in errors:
            print(f"  - {err}", flush=True)

    return errors


# Try on startup
ensure_indexes()
