"""
Connection-related callbacks for AltarExtractor.
Credentials are loaded from .env file, user only selects the database.
"""

from dash import Input, Output, State, no_update
import dash

from ..config import DEFAULT_DB_NAME
from ..services.mongo import (
    list_available_databases,
    get_mongo_client_for_db,
    fetch_config_keys,
    fetch_runs_docs,
    fetch_metrics_values_map,
)
from ..services.data import collect_metric_ids_from_runs


def register_connection_callbacks(app):
    """Register all connection-related callbacks."""

    @app.callback(
        Output("db-selector", "options"),
        Output("db-selector", "value"),
        Output("available-dbs-store", "data"),
        Input("init-tick", "n_intervals"),
        Input("refresh-dbs-button", "n_clicks"),
        State("db-selector", "value"),
        prevent_initial_call=False,
    )
    def load_available_databases(n_intervals, refresh_clicks, current_value):
        """Load available databases from MongoDB on startup or refresh."""
        print(f"[CALLBACK] load_available_databases triggered: n_intervals={n_intervals}, refresh_clicks={refresh_clicks}", flush=True)
        databases = list_available_databases()
        print(f"[CALLBACK] load_available_databases: {databases}", flush=True)
        options = [{"label": db, "value": db} for db in databases]
        print(f"[CALLBACK] options: {options}", flush=True)
        
        # Determine which value to select
        if current_value and current_value in databases:
            selected = current_value
        elif DEFAULT_DB_NAME in databases:
            selected = DEFAULT_DB_NAME
        elif databases:
            selected = databases[0]
        else:
            selected = None
        
        return options, selected, databases

    @app.callback(
        Output("status-alert", "children"),
        Output("status-alert", "color"),
        Output("status-alert", "is_open"),
        Output("runs-cache", "data"),
        Output("config-keys-store", "data"),
        Output("metrics-store", "data"),
        Output("metrics-values-store", "data"),
        Output("results-store", "data"),
        Output("current-db-store", "data"),  # Store the connected database name
        Input("connect-button", "n_clicks"),
        State("db-selector", "value"),
        State("config-keys-store", "data"),
        prevent_initial_call=True,
    )
    def on_connect_click(n_clicks, selected_db, existing_config_store):
        """Connect to the selected database using credentials from .env."""
        if not selected_db:
            return "Please select a database", "warning", True, no_update, no_update, no_update, no_update, no_update, no_update

        # Connect using .env credentials with authSource = database name
        try:
            client = get_mongo_client_for_db(selected_db)
            # Ping the selected database, not admin (user may only have access to this db)
            client[selected_db].command("ping")
            print(f"[CALLBACK] Connected successfully to '{selected_db}'", flush=True)
        except Exception as exc:
            print(f"[CALLBACK] Connection failed: {exc}", flush=True)
            return f"Connection failed: {exc}", "danger", True, no_update, no_update, no_update, no_update, no_update, no_update

        # Fetch data
        try:
            import time
            t0 = time.time()
            print(f"[CALLBACK] Fetching config keys...", flush=True)
            keys = fetch_config_keys(client, selected_db)
            print(f"[CALLBACK] Got {len(keys)} config keys in {time.time()-t0:.2f}s", flush=True)
            
            t0 = time.time()
            print(f"[CALLBACK] Fetching runs...", flush=True)
            runs = fetch_runs_docs(client, selected_db)
            print(f"[CALLBACK] Got {len(runs)} runs in {time.time()-t0:.2f}s", flush=True)

            # Compute metric names
            t0 = time.time()
            print(f"[CALLBACK] Computing metrics...", flush=True)
            metric_names = set()
            for r in runs:
                m = r.get("metrics", None)
                if isinstance(m, dict):
                    for k in m.keys():
                        if isinstance(k, str) and k.strip():
                            metric_names.add(k)
                elif isinstance(m, list):
                    for item in m:
                        if isinstance(item, dict):
                            nm = item.get("name")
                            if isinstance(nm, str) and nm.strip():
                                metric_names.add(nm)

            metrics = sorted(metric_names)
            print(f"[CALLBACK] Got {len(metrics)} metrics in {time.time()-t0:.2f}s", flush=True)
            
            # Don't fetch metric values at connection time - it's too slow for large datasets
            # They will be fetched on-demand when user selects a metric
            metric_ids = collect_metric_ids_from_runs(runs)
            print(f"[CALLBACK] Found {len(metric_ids)} metric IDs (will load on demand)", flush=True)
            metrics_values_map = {}  # Empty - will be populated on demand

            # Result keys
            t0 = time.time()
            print(f"[CALLBACK] Computing result keys...", flush=True)
            result_keys = set()
            for r in runs:
                res = r.get("result", None)
                if isinstance(res, dict):
                    for k in res.keys():
                        if isinstance(k, str) and k.strip():
                            result_keys.add(k)
            results_keys_sorted = sorted(result_keys)
            print(f"[CALLBACK] Got {len(results_keys_sorted)} result keys in {time.time()-t0:.2f}s", flush=True)

            count = len(runs)
            status_text = f"Connected to '{selected_db}' - {count} run(s) found."

            # Preserve selected keys
            existing_selected = []
            if existing_config_store and isinstance(existing_config_store, dict):
                existing_selected = list(existing_config_store.get("selected", []) or [])
            merged_selected = [k for k in existing_selected if k in set(keys)]
            config_store = {"available": keys, "selected": merged_selected}

            client.close()
            return status_text, "success", True, runs, config_store, metrics, metrics_values_map, results_keys_sorted, selected_db
        except Exception as exc:
            return f"Connected, but failed to query data: {exc}", "danger", True, no_update, no_update, no_update, no_update, no_update, no_update
