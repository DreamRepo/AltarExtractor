from typing import Dict, List
from dash import Input, Output, State, no_update, dcc
import io
import csv
import json
from bson import ObjectId


def register_metrics_callbacks(app):
    @app.callback(
        Output("metrics-steps-table", "columns"),
        Output("metrics-steps-table", "data"),
        Input("runs-cache", "data"),
        Input("config-keys-store", "data"),
        Input("filters-store", "data"),
        Input("metrics-select", "value"),
        Input("metrics-values-store", "data"),
    )
    def refresh_metrics_steps_table(runs_cache, config_store, filters_store, selected_metrics_names, metrics_values_map):
        runs = runs_cache or []
        selected = (config_store or {}).get("selected", [])
        metrics_values_map = metrics_values_map or {}
        selected_metrics = [m for m in (selected_metrics_names or []) if isinstance(m, str) and m.strip()]

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

        columns = [{"name": "run_id", "id": "run_id"}, {"name": "Experiment", "id": "experiment"}] + [{"name": key, "id": key} for key in selected]
        columns.append({"name": "Step", "id": "step"})
        for mname in selected_metrics:
            columns.append({"name": mname, "id": f"metric:{mname}"})

        def extract_metric_id_for_run(run_metrics, metric_name):
            if isinstance(run_metrics, dict):
                v = run_metrics.get(metric_name, None)
                if isinstance(v, dict) and v.get("id") is not None:
                    return str(v.get("id"))
                if isinstance(v, (str, ObjectId)):
                    return str(v)
                return None
            if isinstance(run_metrics, list):
                for item in run_metrics:
                    if isinstance(item, dict) and item.get("name") == metric_name:
                        mid = item.get("id") or item.get("_id")
                        if mid is not None:
                            return str(mid)
                        return None
            return None

        rows: List[Dict] = []
        for run in filtered_runs:
            base = {"run_id": run.get("run_id", ""), "experiment": run.get("experiment", "")}
            cfg = run.get("config", {}) or {}
            for key in selected:
                base[key] = cfg.get(key)

            run_metrics = run.get("metrics", None)
            step_grid = None
            metric_series: Dict[str, List] = {}
            for mname in selected_metrics:
                mid = extract_metric_id_for_run(run_metrics, mname)
                payload = metrics_values_map.get(str(mid), {}) if mid else {}
                values = payload.get("values") or []
                steps = payload.get("steps") or list(range(len(values)))
                metric_series[mname] = values
                if steps and (step_grid is None or len(steps) > len(step_grid)):
                    step_grid = steps
            if step_grid is None:
                continue

            for idx, step in enumerate(step_grid):
                row = dict(base)
                row["step"] = step
                for mname in selected_metrics:
                    series = metric_series.get(mname, [])
                    row[f"metric:{mname}"] = series[idx] if idx < len(series) else ""
                rows.append(row)

        return columns, rows

    @app.callback(
        Output("download-steps-modal", "is_open"),
        Input("download-steps-open", "n_clicks"),
        Input("download-steps-cancel", "n_clicks"),
        Input("download-steps-confirm", "n_clicks"),
        State("download-steps-modal", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_download_steps_modal(open_clicks, cancel_clicks, confirm_clicks, is_open):
        return not is_open

    @app.callback(
        Output("download-steps-csv", "data"),
        Input("download-steps-confirm", "n_clicks"),
        State("download-steps-filename", "value"),
        State("metrics-steps-table", "columns"),
        State("metrics-steps-table", "data"),
        prevent_initial_call=True,
    )
    def download_steps_csv(n_clicks, filename, columns, data_rows):
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
        safe_name = (filename or "").strip() or "metrics_steps.csv"
        if not safe_name.lower().endswith(".csv"):
            safe_name += ".csv"
        return dcc.send_string(csv_str, safe_name)

    @app.callback(
        Output("metrics-select", "options"),
        Output("metrics-controls-row", "style"),
        Output("metrics-none-note", "children"),
        Input("metrics-store", "data"),
        State("metrics-select", "value"),
    )
    def populate_metrics_checklist(metrics_store, current_selected):
        names = metrics_store or []
        if not isinstance(names, list):
            names = []
        names = sorted([n for n in names if isinstance(n, str) and n.strip()])
        options = [{"label": n, "value": n} for n in names]
        if len(options) == 0:
            return [], {"display": "none"}, "No metrics found"
        return options, {}, ""

    @app.callback(
        Output("metrics-select", "value"),
        Input("metrics-toggle-all", "n_clicks"),
        State("metrics-select", "options"),
        State("metrics-select", "value"),
        prevent_initial_call=True,
    )
    def toggle_all_metrics(n_clicks, options, current_values):
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

    @app.callback(
        Output("metrics-selected-store", "data", allow_duplicate=True),
        Input("metrics-select", "value"),
        prevent_initial_call=True,
    )
    def persist_selected_metrics(selected_values):
        return list(selected_values or [])

    @app.callback(
        Output("metrics-select", "value", allow_duplicate=True),
        Input("metrics-selected-store", "data"),
        Input("metrics-select", "options"),
        prevent_initial_call=True,
    )
    def restore_selected_metrics(saved_values, options):
        available = set([opt.get("value") for opt in (options or []) if isinstance(opt, dict)])
        desired = [v for v in (saved_values or []) if v in available]
        return desired

    @app.callback(
        Output("metrics-per-step-section", "style"),
        Input("metrics-select", "options"),
    )
    def toggle_metrics_section(options):
        has_metrics = isinstance(options, list) and len(options) > 0
        return {} if has_metrics else {"display": "none"}

    @app.callback(
        Output("metrics-steps-table", "page_size"),
        Output("metrics-page-size-store", "data", allow_duplicate=True),
        Input("metrics-page-size-input", "value"),
        prevent_initial_call=True,
    )
    def set_metrics_page_size(value):
        try:
            v = int(value)
            v = v if v and v > 0 else 10
            return v, v
        except Exception:
            return 10, 10

    @app.callback(
        Output("metrics-page-size-input", "value", allow_duplicate=True),
        Input("metrics-page-size-store", "data"),
        prevent_initial_call=True,
    )
    def restore_metrics_page_size(saved):
        try:
            v = int(saved)
            return v if v and v > 0 else 10
        except Exception:
            return 10


