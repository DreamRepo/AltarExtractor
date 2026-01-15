"""
Data processing utilities for AltarExtractor.
"""

from typing import Dict, List, Tuple
from bson import ObjectId
import pymongo

from .mongo import fetch_sacred_experiment_names


def collect_metric_ids_from_runs(runs: List[Dict]) -> List[str]:
    """
    Extract all metric IDs referenced in runs.
    """
    ids = set()
    for r in runs or []:
        m = r.get("metrics", None)
        if isinstance(m, dict):
            for val in m.values():
                if isinstance(val, dict) and val.get("id") is not None:
                    ids.add(str(val.get("id")))
                elif isinstance(val, (str, ObjectId)):
                    ids.add(str(val))
        elif isinstance(m, list):
            for item in m:
                if not isinstance(item, dict):
                    continue
                mid = item.get("id") or item.get("_id")
                if mid is not None:
                    ids.add(str(mid))
    return sorted(ids)


def build_table_from_runs(runs: List[Dict], selected_keys: List[str]) -> Tuple[List[Dict], List[Dict]]:
    """
    Build DataTable columns and rows based on selected configuration keys.
    Returns (columns, data_rows).
    """
    columns = [
        {"name": "run_id", "id": "run_id"},
        {"name": "Experiment", "id": "experiment"}
    ] + [{"name": key, "id": key} for key in selected_keys]

    rows: List[Dict] = []
    for run in runs:
        row = {"run_id": run.get("run_id", ""), "experiment": run.get("experiment", "")}
        cfg = run.get("config", {}) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        for key in selected_keys:
            row[key] = cfg.get(key)
        rows.append(row)
    return columns, rows


def attempt_connect_and_list(
    uri: str, database_name: str
) -> Tuple[str, Dict, List[Dict]]:
    """
    Try to connect to MongoDB using the provided URI and list Sacred experiments.
    Returns: (status_text, style_dict, table_rows)
    """
    try:
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
    except Exception as exc:
        return (
            f"Connection failed: {exc}",
            {"color": "#b00020"},
            [],
        )

    try:
        experiment_names = fetch_sacred_experiment_names(client, database_name)
        count = len(experiment_names)
        status = (
            f"Connected. Database '{database_name}' contains {count} Sacred experiment(s)."
            if count > 0
            else f"Connected. Database '{database_name}' contains no Sacred experiments."
        )
        style = {"color": "#1b5e20"}
        rows = [{"experiment": name} for name in experiment_names]
        return status, style, rows
    except Exception as exc:
        return (
            f"Connected, but failed to query experiments: {exc}",
            {"color": "#b00020"},
            [],
        )

