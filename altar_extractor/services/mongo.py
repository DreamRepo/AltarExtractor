"""
MongoDB service functions for AltarExtractor.
"""

from typing import Dict, List, Optional
from urllib.parse import quote_plus
from bson import ObjectId
import pymongo

from ..config import MONGO_HOST, MONGO_PORT, MONGO_USERNAME, MONGO_PASSWORD, MONGO_AUTH_SOURCE, ALLOWED_DATABASES


def get_mongo_client() -> pymongo.MongoClient:
    """
    Create a MongoDB client using credentials from .env file.
    Uses MONGO_AUTH_SOURCE for authentication.
    """
    if MONGO_USERNAME and MONGO_PASSWORD:
        auth_source = MONGO_AUTH_SOURCE if MONGO_AUTH_SOURCE != "auto" else "admin"
        # URL-encode username and password to handle special characters
        encoded_user = quote_plus(MONGO_USERNAME)
        encoded_pass = quote_plus(MONGO_PASSWORD)
        uri = f"mongodb://{encoded_user}:{encoded_pass}@{MONGO_HOST}:{MONGO_PORT}/?authSource={auth_source}"
    else:
        uri = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/"
    return pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)


def get_mongo_client_for_db(database_name: str) -> pymongo.MongoClient:
    """
    Create a MongoDB client for a specific database.
    If MONGO_AUTH_SOURCE is 'auto', uses the database name as authSource.
    Otherwise uses the configured MONGO_AUTH_SOURCE.
    """
    if MONGO_USERNAME and MONGO_PASSWORD:
        auth_source = database_name if MONGO_AUTH_SOURCE == "auto" else MONGO_AUTH_SOURCE
        # URL-encode username and password to handle special characters
        encoded_user = quote_plus(MONGO_USERNAME)
        encoded_pass = quote_plus(MONGO_PASSWORD)
        uri = f"mongodb://{encoded_user}:{encoded_pass}@{MONGO_HOST}:{MONGO_PORT}/?authSource={auth_source}"
        print(f"[DEBUG] get_mongo_client_for_db: user={MONGO_USERNAME}, host={MONGO_HOST}:{MONGO_PORT}, authSource={auth_source}", flush=True)
    else:
        uri = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/"
        print(f"[DEBUG] get_mongo_client_for_db: no auth, host={MONGO_HOST}:{MONGO_PORT}", flush=True)
    return pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)


def list_available_databases() -> List[str]:
    """
    List all available databases from MongoDB.
    If ALLOWED_DATABASES is set in .env, return those directly (no connection needed).
    Otherwise try to connect and list databases.
    Excludes system databases (admin, local, config).
    """
    # If ALLOWED_DATABASES is explicitly set, return those without connecting
    # This is necessary when users are authenticated per-database
    if ALLOWED_DATABASES:
        print(f"[DEBUG] Using ALLOWED_DATABASES: {ALLOWED_DATABASES}", flush=True)
        return sorted(ALLOWED_DATABASES)
    
    system_dbs = {"admin", "local", "config"}
    
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        all_dbs = client.list_database_names()
        client.close()
        
        # Filter out system databases
        user_dbs = [db for db in all_dbs if db not in system_dbs]
        print(f"[DEBUG] Found databases: {user_dbs}")
        return sorted(user_dbs)
    except Exception as e:
        print(f"[DEBUG] Error listing databases: {e}")
        return []


def build_mongodb_uri(
    uri_from_user: Optional[str],
    host: Optional[str],
    port: Optional[str],
    username: Optional[str],
    password: Optional[str],
    database_name: Optional[str],
    auth_source: Optional[str] = None,
) -> str:
    """
    Build a MongoDB connection URI from either a full URI provided by the user,
    or individual connection fields.
    """
    if uri_from_user and uri_from_user.strip():
        return uri_from_user.strip()

    resolved_host = (host or "localhost").strip()
    resolved_port = (port or "27017").strip()
    resolved_username = (username or "").strip()
    resolved_password = (password or "").strip()
    resolved_db_name = (database_name or "").strip()
    resolved_auth_source = (auth_source or "").strip()

    # No auth case
    if not resolved_username:
        return f"mongodb://{resolved_host}:{resolved_port}/"

    # Auth case; include authSource using explicit value or the database name when provided
    effective_auth_source = resolved_auth_source or resolved_db_name
    if effective_auth_source:
        return (
            f"mongodb://{resolved_username}:{resolved_password}"
            f"@{resolved_host}:{resolved_port}/?authSource={effective_auth_source}"
        )
    return f"mongodb://{resolved_username}:{resolved_password}@{resolved_host}:{resolved_port}/"


def fetch_sacred_experiment_names(
    client: pymongo.MongoClient, database_name: str
) -> List[str]:
    """
    Return a sorted list of experiment names stored by Sacred.
    Sacred's MongoObserver stores runs in the 'runs' collection with the field 'experiment.name'.
    """
    db = client[database_name]
    if "runs" not in db.list_collection_names():
        return []
    names = db["runs"].distinct("experiment.name")
    cleaned = sorted([n for n in names if isinstance(n, str) and n.strip()])
    return cleaned


def fetch_config_keys(client: pymongo.MongoClient, database_name: str) -> List[str]:
    """
    Return sorted list of distinct top-level keys found in the 'config' field of Sacred runs.
    """
    db = client[database_name]
    if "runs" not in db.list_collection_names():
        return []
    pipeline = [
        {"$match": {"config": {"$type": "object"}}},
        {"$project": {"cfg": {"$objectToArray": "$config"}}},
        {"$unwind": "$cfg"},
        {"$group": {"_id": "$cfg.k"}},
        {"$project": {"_id": 0, "k": "$_id"}},
        {"$sort": {"k": 1}},
    ]
    keys = [doc["k"] for doc in db["runs"].aggregate(pipeline)]
    return keys


def fetch_runs_docs(client: pymongo.MongoClient, database_name: str, limit: int = 500) -> List[Dict]:
    """
    Fetch a subset of runs with experiment name and config for table rendering.
    """
    db = client[database_name]
    if "runs" not in db.list_collection_names():
        return []
    cursor = db["runs"].find(
        {},
        {"_id": 1, "experiment.name": 1, "config": 1, "info.metrics": 1, "info.result": 1}
    ).limit(limit)
    runs: List[Dict] = []
    for doc in cursor:
        run_id = str(doc.get("_id"))
        exp_name = None
        exp = doc.get("experiment")
        if isinstance(exp, dict):
            exp_name = exp.get("name")
        if not isinstance(exp_name, str):
            exp_name = ""
        cfg = doc.get("config")
        cfg = cfg if isinstance(cfg, dict) else {}
        info = doc.get("info") if isinstance(doc.get("info", {}), dict) else {}
        metrics = (info or {}).get("metrics", None)
        result = (info or {}).get("result", None)
        runs.append({
            "run_id": run_id,
            "experiment": exp_name,
            "config": cfg,
            "metrics": metrics,
            "result": result
        })
    return runs


def fetch_metrics_list(client: pymongo.MongoClient, database_name: str, limit: int = 1000) -> List[Dict]:
    """
    Fetch available metrics from the 'metrics' collection.
    Returns a list of dicts with at least {'id': str, 'name': str}.
    """
    db = client[database_name]
    if "metrics" not in db.list_collection_names():
        return []
    items: List[Dict] = []
    try:
        cursor = db["metrics"].find({}, {"_id": 1, "name": 1, "title": 1}).limit(limit)
        for doc in cursor:
            _id = str(doc.get("_id"))
            name = doc.get("name") or doc.get("title") or _id
            if not isinstance(name, str):
                name = str(name)
            items.append({"id": _id, "name": name})
        items.sort(key=lambda x: x.get("name", ""))
    except Exception:
        return []
    return items


def fetch_metrics_values_map(client: pymongo.MongoClient, database_name: str, id_strs: List[str]) -> Dict[str, Dict]:
    """
    Fetch metric values and steps for a list of metric IDs.
    """
    if not id_strs:
        return {}
    db = client[database_name]
    if "metrics" not in db.list_collection_names():
        return {}
    object_ids = []
    for s in id_strs:
        try:
            object_ids.append(ObjectId(s))
        except Exception:
            continue
    if not object_ids:
        return {}
    values_by_id: Dict[str, Dict] = {}
    for doc in db["metrics"].find({"_id": {"$in": object_ids}}, {"values": 1, "steps": 1}):
        values_by_id[str(doc.get("_id"))] = {
            "values": doc.get("values", []),
            "steps": doc.get("steps", []),
        }
    return values_by_id

