from pymongo import MongoClient
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