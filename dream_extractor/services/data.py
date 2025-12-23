from typing import Dict, List, Tuple
from bson import ObjectId


def collect_metric_ids_from_runs(runs: List[Dict]) -> List[str]:
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
    columns = [{"name": "run_id", "id": "run_id"}, {"name": "Experiment", "id": "experiment"}] + [{"name": key, "id": key} for key in selected_keys]
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


