from typing import Dict, List
import random
from dash import Input, Output, State, no_update, dcc
import io
import csv
import json
from ..services.data import build_table_from_runs


def register_experiments_callbacks(app):
    @app.callback(
        Output("experiments-table", "columns"),
        Output("experiments-table", "data"),
        Input("runs-cache", "data"),
        Input("config-keys-store", "data"),
        Input("filters-store", "data"),
        Input("results-select", "value"),
        Input("experiments-random-store", "data"),
    )
    def refresh_table(runs_cache, config_store, filters_store, selected_result_keys, random_store):
        runs = runs_cache or []
        selected = (config_store or {}).get("selected", [])

        active_filters = filters_store or {}

        def row_passes_filters(run_cfg: Dict) -> bool:
            for key in selected:
                f = active_filters.get(key) if isinstance(active_filters, dict) else None
                if not f:
                    continue
                value = run_cfg.get(key, None) if isinstance(run_cfg, dict) else None

                mode = f.get("mode") if isinstance(f, dict) else None
                if mode in ("true", "false"):
                    if not isinstance(value, bool):
                        return False
                    desired = (mode == "true")
                    if value != desired:
                        return False

                has_min = "min" in f and f.get("min") is not None
                has_max = "max" in f and f.get("max") is not None
                if has_min or has_max:
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        return False
                    if has_min and value < f.get("min"):
                        return False
                    if has_max and value > f.get("max"):
                        return False

                values = f.get("values") if isinstance(f, dict) else None
                if isinstance(values, list) and len(values) > 0:
                    if not isinstance(value, str):
                        return False
                    if value not in values:
                        return False
            return True

        filtered_runs = []
        for run in runs:
            cfg = run.get("config", {}) or {}
            if row_passes_filters(cfg):
                filtered_runs.append(run)

        # Apply random ordering if enabled
        try:
            random_enabled = bool(random_store)
        except Exception:
            random_enabled = False
        if random_enabled and len(filtered_runs) > 1:
            random.shuffle(filtered_runs)

        # Sort by run_id unless random ordering is enabled
        if not random_enabled:
            def run_id_sort_key(run: Dict):
                rid = str(run.get("run_id", ""))
                # Try decimal int
                try:
                    return int(rid)
                except Exception:
                    pass
                # Try hex ObjectId
                try:
                    if len(rid) == 24:
                        return int(rid, 16)
                except Exception:
                    pass
                return rid
            try:
                filtered_runs.sort(key=run_id_sort_key)
            except Exception:
                pass

        columns, rows = build_table_from_runs(filtered_runs, selected)

        result_keys = [k for k in (selected_result_keys or []) if isinstance(k, str) and k.strip()]
        if len(result_keys) > 0:
            for key in result_keys:
                columns.append({"name": key, "id": f"result:{key}"})
            for idx, run in enumerate(filtered_runs):
                if idx >= len(rows):
                    continue
                r = run.get("result", None)
                if not isinstance(r, dict) or len(r) == 0:
                    for key in result_keys:
                        rows[idx][f"result:{key}"] = ""
                else:
                    for key in result_keys:
                        val = r.get(key, None)
                        if val is None:
                            rows[idx][f"result:{key}"] = ""
                        else:
                            try:
                                rows[idx][f"result:{key}"] = json.dumps(val, ensure_ascii=False, default=str) if isinstance(val, (list, dict)) else val
                            except Exception:
                                rows[idx][f"result:{key}"] = str(val)

        return columns, rows

    @app.callback(
        Output("download-exp-modal", "is_open"),
        Input("download-exp-open", "n_clicks"),
        Input("download-exp-cancel", "n_clicks"),
        Input("download-exp-confirm", "n_clicks"),
        State("download-exp-modal", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_download_exp_modal(open_clicks, cancel_clicks, confirm_clicks, is_open):
        return not is_open

    @app.callback(
        Output("experiments-table", "page_size"),
        Output("experiments-page-size-store", "data", allow_duplicate=True),
        Input("experiments-page-size-input", "value"),
        prevent_initial_call=True,
    )
    def set_experiments_page_size(value):
        try:
            v = int(value)
            v = v if v and v > 0 else 10
            return v, v
        except Exception:
            return 10, 10

    @app.callback(
        Output("experiments-page-size-input", "value", allow_duplicate=True),
        Input("experiments-page-size-store", "data"),
        prevent_initial_call=True,
    )
    def restore_experiments_page_size(saved):
        try:
            v = int(saved)
            return v if v and v > 0 else 10
        except Exception:
            return 10

    @app.callback(
        Output("experiments-random-store", "data", allow_duplicate=True),
        Input("experiments-random-check", "value"),
        prevent_initial_call=True,
    )
    def persist_random_order(value):
        return bool(value and "random" in value)

    @app.callback(
        Output("experiments-random-check", "value", allow_duplicate=True),
        Input("experiments-random-store", "data"),
        prevent_initial_call=True,
    )
    def restore_random_order(saved):
        return ["random"] if bool(saved) else []

    @app.callback(
        Output("download-exp-csv", "data"),
        Input("download-exp-confirm", "n_clicks"),
        State("download-exp-filename", "value"),
        State("experiments-table", "columns"),
        State("experiments-table", "data"),
        prevent_initial_call=True,
    )
    def download_exp_csv(n_clicks, filename, columns, data_rows):
        if not n_clicks:
            return no_update
        rows = data_rows or []
        cols = columns or []
        if len(rows) == 0 or len(cols) == 0:
            return no_update

        col_ids = [c.get("id") for c in cols if isinstance(c, dict) and c.get("id")]
        col_names = [c.get("name", c.get("id")) for c in cols]

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(col_names)

        def stringify(v):
            if isinstance(v, (list, dict, tuple)):
                try:
                    return json.dumps(v, ensure_ascii=False, default=str)
                except Exception:
                    return str(v)
            return v

        for row in rows:
            writer.writerow([stringify(row.get(cid, "")) for cid in col_ids])

        csv_str = buf.getvalue()
        buf.close()
        safe_name = (filename or "").strip() or "experiments.csv"
        if not safe_name.lower().endswith(".csv"):
            safe_name += ".csv"
        return dcc.send_string(csv_str, safe_name)

    @app.callback(
        Output("results-select", "options"),
        Output("results-controls-row", "style"),
        Output("results-none-note", "children"),
        Input("results-store", "data"),
    )
    def populate_results_checklist(result_keys):
        keys = result_keys or []
        if not isinstance(keys, list):
            keys = []
        keys = sorted([k for k in keys if isinstance(k, str) and k.strip()])
        if len(keys) == 0:
            return [], {"display": "none"}, ""
        return [{"label": k, "value": k} for k in keys], {}, ""

    @app.callback(
        Output("results-select", "value"),
        Input("results-toggle-all", "n_clicks"),
        State("results-select", "options"),
        State("results-select", "value"),
        prevent_initial_call=True,
    )
    def toggle_all_results(n_clicks, options, current_values):
        if not n_clicks:
            return no_update
        all_values = [opt.get("value") for opt in (options or []) if isinstance(opt, dict)]
        if not all_values:
            return no_update
        current_set = set(current_values or [])
        all_set = set(all_values)
        if current_set == all_set:
            return []
        return all_values


