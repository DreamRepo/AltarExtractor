import os
import random
from typing import Dict, List, Tuple, Optional

import dash
from dash import Dash, html, dcc, dash_table, Input, Output, State, no_update, ALL
from dash.dependencies import ClientsideFunction
import dash_bootstrap_components as dbc
import pymongo
import json
from bson import ObjectId
import uuid
import pandas as pd
from flask import request, make_response
import io
import csv

# Simple in-memory cache to pass data to the pygwalker page
PYGWALKER_CACHE: Dict[str, List[Dict]] = {}


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
        # Fallback: return empty list if no runs collection present
        return []
    names = db["runs"].distinct("experiment.name")
    # Filter out empty/None and sort
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
    cursor = db["runs"].find({}, {"_id": 1, "experiment.name": 1, "config": 1, "info.metrics": 1, "info.result": 1}).limit(limit)
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
        runs.append({"run_id": run_id, "experiment": exp_name, "config": cfg, "metrics": metrics, "result": result})
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
        # Sort by name
        items.sort(key=lambda x: x.get("name", ""))
    except Exception:
        # On any error, return empty list to avoid breaking UI
        return []
    return items

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

def fetch_metrics_values_map(client: pymongo.MongoClient, database_name: str, id_strs: List[str]) -> Dict[str, Dict]:
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
            # skip invalid ObjectId strings
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

def build_table_from_runs(runs: List[Dict], selected_keys: List[str]) -> Tuple[List[Dict], List[Dict]]:
    """
    Build DataTable columns and rows based on selected configuration keys.
    Returns (columns, data_rows).
    """
    columns = [{"name": "run_id", "id": "run_id"}, {"name": "Experiment", "id": "experiment"}] + [
        {"name": key, "id": key} for key in selected_keys
    ]
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
        # Force connection attempt
        client.admin.command("ping")
    except Exception as exc:
        return (
            f"Connection failed: {exc}",
            {"color": "#b00020"},  # red
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
        style = {"color": "#1b5e20"}  # green
        rows = [{"experiment": name} for name in experiment_names]
        return status, style, rows
    except Exception as exc:
        return (
            f"Connected, but failed to query experiments: {exc}",
            {"color": "#b00020"},
            [],
        )


app = Dash(
    __name__,
    suppress_callback_exceptions=True,
    external_stylesheets=[
        dbc.themes.LUX,
        dbc.icons.BOOTSTRAP,  # Bootstrap Icons via dbc helper
        "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css",  # fallback
    ],
)
server = app.server

DEFAULT_DB_NAME = os.environ.get("SACRED_DB_NAME", "sacred")

app.layout = dbc.Container(
    [
        dcc.Store(id="creds-store", storage_type="local"),
        dcc.Store(id="ui-store", storage_type="local"),
        dcc.Store(id="db-history", storage_type="local"),
        dcc.Store(id="runs-cache", storage_type="memory"),
        dcc.Store(id="config-keys-store", storage_type="local"),
        dcc.Store(id="filters-store", storage_type="local"),
        dcc.Store(id="metrics-store", storage_type="memory"),
        dcc.Store(id="metrics-values-store", storage_type="memory"),
        dcc.Store(id="metrics-selected-store", storage_type="local"),
        dcc.Store(id="experiments-random-store", storage_type="local"),
        dcc.Store(id="experiments-page-size-store", storage_type="local"),
        dcc.Store(id="metrics-page-size-store", storage_type="local"),
        dcc.Store(id="metrics-show-keys-store", storage_type="local"),
        dcc.Store(id="metrics-layout-mode-store", storage_type="local"),
        dcc.Store(id="results-store", storage_type="memory"),
        dcc.Interval(id="init-tick", interval=0, n_intervals=0, max_intervals=1),

        dbc.Navbar(
            dbc.Container(
                [
                    dbc.NavbarBrand("AltarExtractor", class_name="mb-0 h4 text-dark"),
                    dbc.Button("Database credentials", id="toggle-connection", color="link", class_name="mb-0 h5 p-0"),
                ]
            ),
            color="light",
            sticky="top",
            class_name="mb-3",
        ),

        dbc.Collapse(
            id="connection-collapse",
            is_open=True,
            children=dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.P("Enter your MongoDB credentials or a MongoDB URI."),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                dbc.RadioItems(
                                                    options=[
                                                        {"label": "Use URI", "value": "uri"},
                                                        {"label": "Use Credentials", "value": "credentials"},
                                                    ],
                                                    value="credentials",
                                                    id="connection-mode-switch",
                                                    inline=True,
                                                ),
                                                md=12,
                                            ),
                                        ],
                                        class_name="mb-3",
                                    ),
                                    # URI mode fields
                                    html.Div(
                                        id="uri-mode-fields",
                                        children=[
                                            dbc.Row(
                                                [
                                                    dbc.Col(
                                                        dcc.Input(
                                                            id="uri-input",
                                                            placeholder="MongoDB URI (e.g. mongodb+srv://user:pass@cluster/db?authSource=admin)",
                                                            type="text",
                                                            value="",
                                                            style={"width": "100%"},
                                                        ),
                                                        width=12,
                                                    ),
                                                ],
                                                class_name="mb-2",
                                            ),
                                        ],
                                        style={"display": "none"},
                                    ),
                                    # Credentials mode fields
                                    html.Div(
                                        id="credentials-mode-fields",
                                        children=[
                                            dbc.Row(
                                                [
                                                    dbc.Col(dcc.Input(id="host-input", placeholder="Host (default: localhost)", type="text", value="", style={"width": "100%"}), md=6),
                                                    dbc.Col(dcc.Input(id="port-input", placeholder="Port (default: 27017)", type="text", value="", style={"width": "100%"}), md=6),
                                                ],
                                                class_name="mb-2",
                                            ),
                                            dbc.Row(
                                                [
                                                    dbc.Col(dcc.Input(id="username-input", placeholder="Username (optional)", type="text", value="", style={"width": "100%"}), md=6),
                                                    dbc.Col(dcc.Input(id="password-input", placeholder="Password (optional)", type="password", value="", style={"width": "100%"}), md=6),
                                                ],
                                                class_name="mb-2",
                                            ),
                                            dbc.Row(
                                                [
                                                    dbc.Col(dcc.Input(id="authsource-input", placeholder="Auth source (default: database name)", type="text", value="", style={"width": "100%"}), md=12),
                                                ],
                                                class_name="mb-2",
                                            ),
                                        ],
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                dbc.Checklist(
                                                    options=[
                                                        {"label": "Save credentials", "value": "save"},
                                                    ],
                                                    value=[],
                                                    id="save-options",
                                                    switch=True,
                                                ),
                                                md=12,
                                            ),
                                        ],
                                        class_name="mb-2",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(dbc.Button("Clear saved", id="clear-saved-button", color="link", n_clicks=0), width="auto"),
                                        ],
                                        class_name="g-2 align-items-center",
                                    ),
                                ]
                            ),
                            class_name="mb-3",
                        ),
                        md=6,
                    ),
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody([]),
                            class_name="mb-3",
                        ),
                        md=6,
                    ),
                ],
                class_name="g-2",
            ),
        ),

        # Top row: database name with autocomplete + Connect + status
        dbc.Row(
            [
                dbc.Col(
                    [
                        dbc.Label("Database"),
                        dbc.Input(id="db-name-input", placeholder=f"Database name (default: {DEFAULT_DB_NAME})", type="text", list="db-name-list"),
                        html.Datalist(id="db-name-list"),
                    ],
                    md=4,
                ),
                dbc.Col(
                    [
                        dbc.Label(" "),
                        dbc.Button("Connect", id="connect-button", color="primary", n_clicks=0, class_name="d-block"),
                    ],
                    md=2,
                ),
                dbc.Col(
                    [
                        dbc.Label(" "),
                        dbc.Alert(id="status-alert", is_open=False, color="light", class_name="mb-0"),
                    ],
                    md=6,
                ),
            ],
            class_name="g-2 align-items-end mb-3",
        ),

        # Config keys selector under a single collapsible card
        dbc.Card(
            [
                dbc.CardHeader(
                    html.Div(
                        [
                            html.Span("Select Keys"),
                            html.I(className="ms-auto bi bi-chevron-down"),
                        ],
                        id="toggle-select-keys",
                        n_clicks=0,
                        className="d-flex align-items-center",
                        style={"cursor": "pointer", "fontSize": "1.25rem", "fontWeight": "600"},
                    )
                ),
                dbc.Collapse(
                    dbc.CardBody(
                        dbc.Row(
                                [
                                    dbc.Col(
                                        dbc.Card(
                                            [
                                                dbc.CardHeader("Available config keys ([type] [distinct value counts])"),
                                            dbc.CardBody(
                                                [
                                                    dcc.Dropdown(
                                                        id="config-keys-select",
                                                        options=[],
                                                        value=[],
                                                        multi=True,
                                                        placeholder="Select config keys...",
                                                    ),
                                                    html.Div(id="config-keys-none-note", style={"color": "#666", "marginTop": "0.25rem", "marginBottom": "0.75rem"}),
                                                    dbc.Button("Check/Uncheck all", id="config-keys-toggle-all", size="sm", color="secondary", class_name="mb-2"),
                                                    dbc.ListGroup(id="available-keys", style={"display": "none"}),
                                                ]
                                            ),
                                            ]
                                        ),
                                        md=4,
                                    ),
                                    dbc.Col(
                                        dbc.Card(
                                            [
                                                dbc.CardHeader("Selected keys"),
                                                dbc.CardBody(dbc.ListGroup(id="selected-keys")),
                                            ]
                                        ),
                                        md=8,
                                    ),
                                ]
                        )
                    ),
                    id="select-keys-collapse",
                    is_open=True,
                ),
            ],
            class_name="mb-3",
        ),



        dbc.Card(
            [
                dbc.CardHeader(
                    html.Div(
                        [
                            html.Span("Experiments"),
                            html.I(className="ms-auto bi bi-chevron-down"),
                        ],
                        id="toggle-experiments",
                        n_clicks=0,
                        className="d-flex align-items-center",
                        style={"cursor": "pointer", "fontSize": "1.25rem", "fontWeight": "600"},
                    )
                ),
                dbc.Collapse(
                    dbc.CardBody(
                        [
                            html.Div(
                                [
                                    dbc.Row(
                                        [
                                            dbc.Col(dbc.Button("Check/Uncheck all results", id="results-toggle-all", size="sm", color="secondary"), width="auto"),
                                            dbc.Col(dcc.Dropdown(id="results-select", options=[], value=[], multi=True, placeholder="Select result keys...")),
                                        ],
                                        id="results-controls-row",
                                        class_name="g-2 mb-1 align-items-center",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Number of rows"),
                                                    dcc.Input(id="experiments-page-size-input", type="number", value=10, min=1, step=1, style={"width": "80px", "marginLeft": "8px"}),
                                                ],
                                                width="auto",
                                            ),
                                            dbc.Col(
                                                dbc.Checklist(
                                                    options=[{"label": "Random order", "value": "random"}],
                                                    value=[],
                                                    id="experiments-random-check",
                                                    switch=True,
                                                ),
                                                width="auto",
                                                class_name="ms-2",
                                            ),
                                        ],
                                        class_name="g-2 mb-1 align-items-center",
                                    ),
                                            html.Div(id="results-none-note", style={"color": "#666", "marginTop": "0.25rem", "marginBottom": "0.25rem"}),
                                ]
                            ),
                            dash_table.DataTable(
                                id="experiments-table",
                                columns=[{"name": "Experiment", "id": "experiment"}],
                                data=[],
                                page_size=20,
                                style_table={"overflowX": "auto", "width": "100%"},
                                style_cell={"textAlign": "left", "padding": "8px"},
                                style_header={"fontWeight": "bold"},
                            ),
                            html.Div(
                                [
                                    dbc.Button("Open in Pygwalker", id="open-pygwalker-exp-btn", color="primary", class_name="mt-2 me-2"),
                                    dbc.Button("Download dataset", id="download-exp-open", color="secondary", class_name="mt-2"),
                                ]
                            ),
                            dbc.Modal(
                                [
                                    dbc.ModalHeader("Open in Pygwalker"),
                                    dbc.ModalBody(
                                        html.Div(
                                            [
                                                html.P("Choose which columns to include:"),
                                                dbc.Button("All keys", id="open-exp-all-keys", color="primary", class_name="me-2"),
                                                dbc.Button("Only selected keys", id="open-exp-selected-keys", color="secondary"),
                                            ]
                                        )
                                    ),
                                ],
                                id="open-exp-modal",
                                is_open=False,
                            ),
                            dbc.Modal(
                                [
                                    dbc.ModalHeader("Download CSV"),
                                    dbc.ModalBody(
                                        dbc.Input(id="download-exp-filename", type="text", placeholder="experiments.csv", value="experiments.csv")
                                    ),
                                    dbc.ModalFooter(
                                        [
                                            dbc.Button("Cancel", id="download-exp-cancel", class_name="me-2"),
                                            dbc.Button("Download", id="download-exp-confirm", color="primary"),
                                        ]
                                    ),
                                ],
                                id="download-exp-modal",
                                is_open=False,
                            ),
                            dcc.Download(id="download-exp-csv"),
                        ]
                    ),
                    id="experiments-collapse",
                    is_open=True,
                ),
            ],
            class_name="mb-3",
        ),

        dbc.Card(
            [
                dbc.CardHeader(
                    html.Div(
                        [
                            html.Span("Metrics"),
                            html.I(className="ms-auto bi bi-chevron-down"),
                        ],
                        id="toggle-metrics",
                        n_clicks=0,
                        className="d-flex align-items-center",
                        style={"cursor": "pointer", "fontSize": "1.25rem", "fontWeight": "600"},
                    )
                ),
                dbc.Collapse(
                    dbc.CardBody(
                        [
                            html.Div(
                                [
                                    dbc.Row(
                                        [
                                            dbc.Col(dbc.Button("Check/Uncheck all metrics", id="metrics-toggle-all", size="sm", color="secondary"), width="auto"),
                                            dbc.Col(dcc.Dropdown(id="metrics-select", options=[], value=[], multi=True, placeholder="Select metrics...")),
                                        ],
                                        id="metrics-controls-row",
                                        class_name="g-2 align-items-center",
                                    ),
                                    html.Div(id="metrics-none-note", style={"color": "#666", "marginTop": "0.5rem"}),  
                                ]
                            ),
                            html.Div(
                                [
                                    html.Hr(),
                                    html.Div("Per-step metrics table", style={"fontWeight": "600", "marginBottom": "0.5rem"}),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Number of rows"),
                                                    dcc.Input(id="metrics-page-size-input", type="number", value=10, min=1, step=1, style={"width": "80px", "marginLeft": "8px"}),
                                                ],
                                                width="auto",
                                            ),
                                            dbc.Col(
                                                dbc.Checklist(
                                                    options=[{"label": "Show selected keys", "value": "show"}],
                                                    value=["show"],
                                                    id="metrics-show-keys-switch",
                                                    switch=True,
                                                ),
                                                width="auto",
                                                class_name="ms-3",
                                            ),
                                            dbc.Col(
                                                dbc.RadioItems(
                                                    options=[
                                                        {"label": "Steps as rows", "value": "rows"},
                                                        {"label": "Steps as columns", "value": "cols"},
                                                    ],
                                                    value="rows",
                                                    id="metrics-layout-mode",
                                                    inline=True,
                                                ),
                                                width="auto",
                                                class_name="ms-3",
                                            ),
                                        ],
                                        class_name="g-2 align-items-center mb-2",
                                    ),
                                    dash_table.DataTable(
                                        id="metrics-steps-table",
                                        columns=[{"name": "Experiment", "id": "experiment"}],
                                        data=[],
                                        page_size=20,
                                        style_table={"overflowX": "auto", "width": "100%"},
                                        style_cell={"textAlign": "left", "padding": "8px"},
                                        style_header={"fontWeight": "bold"},
                                    ),
                                    html.Div(
                                        [
                                            dbc.Button("Open in Pygwalker", id="open-pygwalker-btn", color="primary", class_name="mt-2 me-2"),
                                            dbc.Button("Download dataset", id="download-steps-open", color="secondary", class_name="mt-2"),
                                        ]
                                    ),
                                    dbc.Modal(
                                        [
                                            dbc.ModalHeader("Open in Pygwalker"),
                                            dbc.ModalBody(
                                                html.Div(
                                                    [
                                                        html.P("Choose which columns to include:"),
                                                        dbc.Button("All keys", id="open-steps-all-keys", color="primary", class_name="me-2"),
                                                        dbc.Button("Only selected keys", id="open-steps-selected-keys", color="secondary"),
                                                    ]
                                                )
                                            ),
                                        ],
                                        id="open-steps-modal",
                                        is_open=False,
                                    ),
                                    # Download modal
                                    dbc.Modal(
                                        [
                                            dbc.ModalHeader("Download CSV"),
                                            dbc.ModalBody(
                                                dbc.Input(id="download-steps-filename", type="text", placeholder="metrics_steps.csv", value="metrics_steps.csv")
                                            ),
                                            dbc.ModalFooter(
                                                [
                                                    dbc.Button("Cancel", id="download-steps-cancel", class_name="me-2"),
                                                    dbc.Button("Download", id="download-steps-confirm", color="primary"),
                                                ]
                                            ),
                                        ],
                                        id="download-steps-modal",
                                        is_open=False,
                                    ),
                                    dcc.Download(id="download-steps-csv"),
                                    dcc.Store(id="pygwalker-url"),
                                    html.Div(id="pygwalker-open-dummy", style={"display": "none"}),
                                ],
                                id="metrics-per-step-section",
                            ),
                        ]
                    ),
                    id="metrics-collapse",
                    is_open=True,
                ),
            ],
            class_name="mb-3",
        ),
    ],
    fluid=True,
)


# Open pygwalker URL in a new tab (client-side via ClientsideFunction)
app.clientside_callback(
    ClientsideFunction(namespace="pyg", function_name="open"),
    Output("pygwalker-open-dummy", "children"),
    Input("pygwalker-url", "data"),
)

# Pygwalker page route
@server.route("/pygwalker")
def pygwalker_route():
    try:
        key = request.args.get("id", "").strip()
        data = PYGWALKER_CACHE.get(key, [])
        df = pd.DataFrame(data or [])
        try:
            from pygwalker.api.html import to_html
            html_str = to_html(df, title="AltarExtractor — Pygwalker")
        except Exception as exc:
            html_str = f"""
<!DOCTYPE html>
<html>
  <head><meta charset="utf-8"><title>Pygwalker unavailable</title></head>
  <body>
    <h2>Pygwalker is not available</h2>
    <p>Install it with: <code>pip install pygwalker</code></p>
    <h3>Preview DataFrame (first 100 rows)</h3>
    <pre>{df.head(100).to_string(index=False)}</pre>
  </body>
</html>
""".strip()
        resp = make_response(html_str)
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        return resp
    except Exception as exc:
        resp = make_response(f"Failed to render pygwalker page: {exc}")
        resp.headers["Content-Type"] = "text/plain; charset=utf-8"
        return resp, 500

@app.callback(
    Output("status-alert", "children"),
    Output("status-alert", "color"),
    Output("status-alert", "is_open"),
    Output("runs-cache", "data"),
    Output("config-keys-store", "data"),
    Output("metrics-store", "data"),
    Output("metrics-values-store", "data"),
    Output("results-store", "data"),
    Input("connect-button", "n_clicks"),
    Input("init-tick", "n_intervals"),
    State("uri-input", "value"),
    State("host-input", "value"),
    State("port-input", "value"),
    State("username-input", "value"),
    State("password-input", "value"),
    State("authsource-input", "value"),
    State("db-name-input", "value"),
    State("creds-store", "data"),
    State("db-history", "data"),
    State("config-keys-store", "data"),
    State("connection-mode-switch", "value"),
    prevent_initial_call=False,
)
def on_connect_click(
    n_clicks: int,
    n_intervals: int,
    uri_value: str,
    host_value: str,
    port_value: str,
    username_value: str,
    password_value: str,
    auth_source_value: str,
    db_name_value: str,
    saved_creds,
    db_history,
    existing_config_store,
    connection_mode: str,
):
    ctx = dash.callback_context  # type: ignore
    triggered = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else None

    # If nothing triggered yet, show a neutral message
    if triggered is None:
        initial_text = "Connecting..."
        return initial_text, "light", True, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

    auto_triggered = (triggered == "init-tick")

    # Resolve DB name: prefer current input, then saved creds, then history, then default
    resolved_db_name = (db_name_value or "").strip()
    if not resolved_db_name:
        saved_db_name = ""
        try:
            saved_db_name = ((saved_creds or {}).get("db_name") or "").strip() if isinstance(saved_creds, dict) else ""
        except Exception:
            saved_db_name = ""
        if saved_db_name:
            resolved_db_name = saved_db_name
        elif db_history and isinstance(db_history, list) and len(db_history) > 0:
            resolved_db_name = db_history[0]
        else:
            resolved_db_name = DEFAULT_DB_NAME

    # Determine connection mode
    resolved_mode = connection_mode or "credentials"
    if auto_triggered and saved_creds and isinstance(saved_creds, dict):
        resolved_mode = saved_creds.get("connection_mode", resolved_mode)

    # Resolve credentials based on mode
    if auto_triggered and saved_creds:
        uri_from_user = (saved_creds or {}).get("uri") or uri_value
        host = (saved_creds or {}).get("host") or host_value
        port = (saved_creds or {}).get("port") or port_value
        username = (saved_creds or {}).get("username") or username_value
        password = (saved_creds or {}).get("password") or password_value
        auth_source = (saved_creds or {}).get("authSource") or auth_source_value
    else:
        uri_from_user = uri_value
        host = host_value
        port = port_value
        username = username_value
        password = password_value
        auth_source = auth_source_value

    # Apply connection mode: only use URI when in URI mode, only use credentials when in credentials mode
    if resolved_mode == "uri":
        # In URI mode, ignore individual credentials
        host = None
        port = None
        username = None
        password = None
        auth_source = None
    else:
        # In credentials mode, ignore URI
        uri_from_user = None

    # Build URI and attempt connection
    uri = build_mongodb_uri(
        uri_from_user=uri_from_user,
        host=host,
        port=port,
        username=username,
        password=password,
        database_name=resolved_db_name,
        auth_source=auth_source,
    )
    try:
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
    except Exception as exc:
        status_text = f"Connection failed: {exc}"
        return status_text, "danger", True, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

    # Connected - fetch keys and runs
    try:
        keys = fetch_config_keys(client, resolved_db_name)
        runs = fetch_runs_docs(client, resolved_db_name)

        # Compute distinct metric names from runs' info.metrics
        metric_names = set()
        for r in runs:
            m = r.get("metrics", None)
            if isinstance(m, dict):
                # dict: use keys as names
                for k in m.keys():
                    if isinstance(k, str) and k.strip():
                        metric_names.add(k)
            elif isinstance(m, list):
                # list: expect dicts with 'name'
                for item in m:
                    if isinstance(item, dict):
                        nm = item.get("name")
                        if isinstance(nm, str) and nm.strip():
                            metric_names.add(nm)

        metrics = sorted(metric_names)
        # Collect referenced metric ids and fetch their values arrays
        metric_ids = collect_metric_ids_from_runs(runs)
        metrics_values_map = fetch_metrics_values_map(client, resolved_db_name, metric_ids)

        # Distinct result keys
        result_keys = set()
        for r in runs:
            res = r.get("result", None)
            if isinstance(res, dict):
                for k in res.keys():
                    if isinstance(k, str) and k.strip():
                        result_keys.add(k)
        results_keys_sorted = sorted(result_keys)
        count = len(runs)
        status_text = f"Connected. Database '{resolved_db_name}' has {count} run(s)."

        # Preserve previously selected keys (intersect with available)
        existing_selected = []
        if existing_config_store and isinstance(existing_config_store, dict):
            existing_selected = list(existing_config_store.get("selected", []) or [])
        merged_selected = [k for k in existing_selected if k in set(keys)]
        config_store = {"available": keys, "selected": merged_selected}
        return status_text, "success", True, runs, config_store, metrics, metrics_values_map, results_keys_sorted
    except Exception as exc:
        status_text = f"Connected, but failed to query runs/config keys: {exc}"
        return status_text, "danger", True, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update


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
    # legacy version without random; we'll add random flag read below if store exists
    runs = runs_cache or []
    selected = (config_store or {}).get("selected", [])

    # Apply filters to runs before building table
    active_filters = filters_store or {}

    def row_passes_filters(run_cfg: Dict) -> bool:
        for key in selected:
            f = active_filters.get(key) if isinstance(active_filters, dict) else None
            if not f:
                continue
            value = run_cfg.get(key, None) if isinstance(run_cfg, dict) else None

            # Boolean filter
            mode = f.get("mode") if isinstance(f, dict) else None
            if mode in ("true", "false"):
                if not isinstance(value, bool):
                    return False
                desired = (mode == "true")
                if value != desired:
                    return False

            # Numeric filter
            has_min = "min" in f and f.get("min") is not None
            has_max = "max" in f and f.get("max") is not None
            if has_min or has_max:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    return False
                if has_min and value < f.get("min"):
                    return False
                if has_max and value > f.get("max"):
                    return False

            # String filter (multi-select)
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

    # Sort by run_id unless random is enabled
    try:
        random_enabled = bool(random_store)
    except Exception:
        random_enabled = False
    if random_enabled and len(filtered_runs) > 1:
        random.shuffle(filtered_runs)
    else:
        def run_id_sort_key(run: Dict):
            rid = str(run.get("run_id", ""))
            # Try decimal number
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

    # Apply random ordering if enabled
    try:
        random_enabled = bool(random_store)
    except Exception:
        random_enabled = False
    if random_enabled and len(filtered_runs) > 1:
        random.shuffle(filtered_runs)

    columns, rows = build_table_from_runs(filtered_runs, selected)

    # Optionally include selected results columns from info.result
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
    Output("metrics-steps-table", "columns"),
    Output("metrics-steps-table", "data"),
    Input("runs-cache", "data"),
    Input("config-keys-store", "data"),
    Input("filters-store", "data"),
    Input("metrics-select", "value"),
    Input("metrics-values-store", "data"),
    Input("metrics-show-keys-switch", "value"),
    Input("metrics-layout-mode", "value"),
)
def refresh_metrics_steps_table(runs_cache, config_store, filters_store, selected_metrics_names, metrics_values_map, show_keys_switch, layout_mode):
    runs = runs_cache or []
    selected = (config_store or {}).get("selected", [])
    metrics_values_map = metrics_values_map or {}
    selected_metrics = [m for m in (selected_metrics_names or []) if isinstance(m, str) and m.strip()]
    show_selected_keys = bool(show_keys_switch and "show" in show_keys_switch)
    steps_as_columns = (layout_mode == "cols")

    # Apply same filters as experiments table
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

    # Helper: get metric id for run and metric name
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

    # Collect step grids and metric series for all runs first
    run_data = []
    all_step_values = set()
    for run in filtered_runs:
        base = {"run_id": run.get("run_id", ""), "experiment": run.get("experiment", "")}
        cfg = run.get("config", {}) or {}
        if show_selected_keys:
            for key in selected:
                base[key] = cfg.get(key)

        run_metrics = run.get("metrics", None)
        step_grid = None
        metric_series: Dict[str, List] = {}
        metric_steps: Dict[str, List] = {}
        for mname in selected_metrics:
            mid = extract_metric_id_for_run(run_metrics, mname)
            payload = metrics_values_map.get(str(mid), {}) if mid else {}
            values = payload.get("values") or []
            steps = payload.get("steps") or list(range(len(values)))
            metric_series[mname] = values
            metric_steps[mname] = steps
            for s in steps:
                all_step_values.add(s)
            if steps and (step_grid is None or len(steps) > len(step_grid)):
                step_grid = steps
        if step_grid is None:
            continue
        run_data.append({"base": base, "step_grid": step_grid, "metric_series": metric_series, "metric_steps": metric_steps})

    # Sort step values for consistent column order
    sorted_steps = sorted(all_step_values)

    rows: List[Dict] = []

    if steps_as_columns:
        # Mode: Steps as columns, one row per metric per run
        # Columns: run_id, Experiment, [config keys], Metric, <actual step values>
        columns = [{"name": "run_id", "id": "run_id"}, {"name": "Experiment", "id": "experiment"}]
        if show_selected_keys:
            columns += [{"name": key, "id": key} for key in selected]
        columns.append({"name": "Metric", "id": "metric_name"})
        for step_val in sorted_steps:
            columns.append({"name": str(step_val), "id": f"step:{step_val}"})

        for rd in run_data:
            base = rd["base"]
            metric_series = rd["metric_series"]
            metric_steps = rd["metric_steps"]
            for mname in selected_metrics:
                row = dict(base)
                row["metric_name"] = mname
                series = metric_series.get(mname, [])
                steps = metric_steps.get(mname, [])
                # Build a mapping from step value to metric value
                step_to_value = {s: v for s, v in zip(steps, series)}
                for step_val in sorted_steps:
                    row[f"step:{step_val}"] = step_to_value.get(step_val, "")
                rows.append(row)
    else:
        # Mode: Steps as rows (current behavior)
        # Columns: run_id, Experiment, [config keys], Step, metric1, metric2, ...
        columns = [{"name": "run_id", "id": "run_id"}, {"name": "Experiment", "id": "experiment"}]
        if show_selected_keys:
            columns += [{"name": key, "id": key} for key in selected]
        columns.append({"name": "Step", "id": "step"})
        for mname in selected_metrics:
            columns.append({"name": mname, "id": f"metric:{mname}"})

        for rd in run_data:
            base = rd["base"]
            step_grid = rd["step_grid"]
            metric_series = rd["metric_series"]
            for idx, step in enumerate(step_grid):
                row = dict(base)
                row["step"] = step
                for mname in selected_metrics:
                    series = metric_series.get(mname, [])
                    row[f"metric:{mname}"] = series[idx] if idx < len(series) else ""
                rows.append(row)

    return columns, rows


@app.callback(
    Output("open-steps-modal", "is_open", allow_duplicate=True),
    Input("open-pygwalker-btn", "n_clicks"),
    State("open-steps-modal", "is_open"),
    prevent_initial_call=True,
)
def open_steps_modal(n_clicks, is_open):
    return True


@app.callback(
    Output("open-exp-modal", "is_open", allow_duplicate=True),
    Input("open-pygwalker-exp-btn", "n_clicks"),
    State("open-exp-modal", "is_open"),
    prevent_initial_call=True,
)
def open_exp_modal(n_clicks, is_open):
    return True


@app.callback(
    Output("download-steps-modal", "is_open"),
    Input("download-steps-open", "n_clicks"),
    Input("download-steps-cancel", "n_clicks"),
    Input("download-steps-confirm", "n_clicks"),
    State("download-steps-modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_download_steps_modal(open_clicks, cancel_clicks, confirm_clicks, is_open):
    # Toggle on any of the three buttons
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

    # Determine header and order
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
    Output("pygwalker-url", "data", allow_duplicate=True),
    Output("open-exp-modal", "is_open", allow_duplicate=True),
    Input("open-exp-all-keys", "n_clicks"),
    Input("open-exp-selected-keys", "n_clicks"),
    State("runs-cache", "data"),
    State("config-keys-store", "data"),
    State("filters-store", "data"),
    State("results-select", "value"),
    State("experiments-table", "data"),
    prevent_initial_call=True,
)
def open_pygwalker_exp_choice(click_all, click_sel, runs_cache, config_store, filters_store, selected_result_keys, table_data):
    ctx = dash.callback_context  # type: ignore
    if not ctx.triggered:
        return no_update, no_update
    which = ctx.triggered[0]["prop_id"].split(".")[0]

    # Build filtered runs
    runs = runs_cache or []
    selected = (config_store or {}).get("selected", [])
    available = (config_store or {}).get("available", [])
    all_keys = list(dict.fromkeys(list(available) + list(selected)))
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

    # Compose dataset
    result_keys = [k for k in (selected_result_keys or []) if isinstance(k, str) and k.strip()]
    data = []
    if which == "open-exp-selected-keys":
        # Use current table rows (already filtered and limited to selected keys)
        data = table_data or []
    else:
        # All keys: build rows with all config keys
        for run in filtered_runs:
            row = {"run_id": run.get("run_id", ""), "experiment": run.get("experiment", "")}
            cfg = run.get("config", {}) or {}
            if not isinstance(cfg, dict):
                cfg = {}
            for key in all_keys:
                row[key] = cfg.get(key)
            # Include currently selected result keys if any
            r = run.get("result", None)
            if isinstance(r, dict):
                for key in result_keys:
                    row[f"result:{key}"] = r.get(key, "")
            data.append(row)

    key = str(uuid.uuid4())
    try:
        PYGWALKER_CACHE[key] = data
    except Exception:
        return no_update, False
    return f"/pygwalker?id={key}", False

@app.callback(
    Output("pygwalker-url", "data", allow_duplicate=True),
    Output("open-steps-modal", "is_open", allow_duplicate=True),
    Input("open-steps-all-keys", "n_clicks"),
    Input("open-steps-selected-keys", "n_clicks"),
    State("runs-cache", "data"),
    State("config-keys-store", "data"),
    State("filters-store", "data"),
    State("metrics-select", "value"),
    State("metrics-values-store", "data"),
    State("metrics-steps-table", "data"),
    prevent_initial_call=True,
)
def open_pygwalker_steps_choice(click_all, click_sel, runs_cache, config_store, filters_store, selected_metrics_names, metrics_values_map, table_data):
    ctx = dash.callback_context  # type: ignore
    if not ctx.triggered:
        return no_update, no_update
    which = ctx.triggered[0]["prop_id"].split(".")[0]

    if which == "open-steps-selected-keys":
        data = table_data or []
        key = str(uuid.uuid4())
        try:
            PYGWALKER_CACHE[key] = data
        except Exception:
            return no_update, False
        return f"/pygwalker?id={key}", False

    # Build per-step with all config keys
    runs = runs_cache or []
    selected = (config_store or {}).get("selected", [])
    available = (config_store or {}).get("available", [])
    all_keys = list(dict.fromkeys(list(available) + list(selected)))
    active_filters = filters_store or {}
    metrics_values_map = metrics_values_map or {}
    selected_metrics = [m for m in (selected_metrics_names or []) if isinstance(m, str) and m.strip()]

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
        for key in all_keys:
            base[key] = cfg.get(key)

        run_metrics = run.get("metrics", None)
        step_grid = None
        metric_series: Dict[str, List] = {}
        for mname in selected_metrics:
            mid = extract_metric_id_for_run(run_metrics, mname)
            payload = (metrics_values_map or {}).get(str(mid), {}) if mid else {}
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

    key = str(uuid.uuid4())
    try:
        PYGWALKER_CACHE[key] = rows
    except Exception:
        return no_update, False
    return f"/pygwalker?id={key}", False

@app.callback(
    Output("config-keys-select", "options"),
    Output("config-keys-select", "value"),
    Output("config-keys-select", "style"),
    Output("config-keys-none-note", "children"),
    Input("config-keys-store", "data"),
    Input("runs-cache", "data"),
    prevent_initial_call=False,
)
def populate_config_keys_dropdown(store, runs_cache):
    data = store or {"available": [], "selected": []}
    available = data.get("available", []) or []
    selected = data.get("selected", []) or []
    all_keys = sorted(set(list(available) + list(selected)))

    runs = runs_cache or []
    def type_name_for_value(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "dict"
        return "unknown"

    key_to_type: Dict[str, str] = {}
    for key in all_keys:
        seen_types = set()
        for run in runs:
            cfg = run.get("config", {}) or {}
            if isinstance(cfg, dict) and key in cfg:
                t = type_name_for_value(cfg.get(key))
                if t is not None:
                    seen_types.add(t)
            if len(seen_types) > 1:
                break
        if len(seen_types) == 0:
            key_to_type[key] = "unknown"
        elif len(seen_types) == 1:
            key_to_type[key] = next(iter(seen_types))
        else:
            key_to_type[key] = "mixed"

    def encode_for_set(value):
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

    key_to_value_count: Dict[str, int] = {}
    for key in all_keys:
        distinct = set()
        for run in runs:
            cfg = run.get("config", {}) or {}
            if not isinstance(cfg, dict):
                continue
            v = cfg.get(key, None)
            if v is None:
                continue
            distinct.add(encode_for_set(v))
        key_to_value_count[key] = len(distinct)

    options = [{"label": f"{k} ({key_to_type.get(k, 'unknown')} {key_to_value_count.get(k, 0)})", "value": k} for k in all_keys]
    if len(options) == 0:
        return [], [], {"display": "none"}, "No config keys found"
    selected_clean = [k for k in selected if k in set(all_keys)]
    note = "All keys selected" if (len(selected_clean) == len(all_keys) and len(all_keys) > 0) else ""
    return options, selected_clean, {}, note


@app.callback(
    Output("config-keys-store", "data", allow_duplicate=True),
    Input("config-keys-select", "value"),
    State("config-keys-store", "data"),
    prevent_initial_call=True,
)
def on_config_keys_select_change(selected_values, store):
    store = store or {"available": [], "selected": []}
    available_old = list(store.get("available", []) or [])
    selected_old = list(store.get("selected", []) or [])
    all_keys = list(dict.fromkeys(list(available_old) + list(selected_old)))
    selected_set = set(k for k in (selected_values or []) if k in set(all_keys))
    available = [k for k in all_keys if k not in selected_set]
    selected = [k for k in all_keys if k in selected_set]
    return {"available": available, "selected": selected}


@app.callback(
    Output("available-keys", "children"),
    Output("selected-keys", "children"),
    Input("config-keys-store", "data"),
    Input("runs-cache", "data"),
    Input("filters-store", "data"),
)
def render_key_lists(config_store, runs_cache, filters_store):
    store = config_store or {"available": [], "selected": []}
    available = store.get("available", []) or []
    selected = store.get("selected", []) or []

    # Determine type per key by scanning cached runs
    runs = runs_cache or []
    keys_to_check = set(list(available) + list(selected))

    def type_name_for_value(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "dict"
        return "unknown"

    key_to_type: Dict[str, str] = {}
    for key in keys_to_check:
        seen_types = set()
        for run in runs:
            cfg = run.get("config", {}) or {}
            if isinstance(cfg, dict) and key in cfg:
                t = type_name_for_value(cfg.get(key))
                if t is not None:
                    seen_types.add(t)
            if len(seen_types) > 1:
                break
        if len(seen_types) == 0:
            key_to_type[key] = "unknown"
        elif len(seen_types) == 1:
            key_to_type[key] = next(iter(seen_types))
        else:
            key_to_type[key] = "mixed"

    # Collect unique string values for string filters
    key_to_str_values: Dict[str, List[str]] = {}
    # Collect distinct value counts (ignore None) for all keys
    key_to_value_count: Dict[str, int] = {}

    def encode_for_set(value):
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

    for key in selected:
        values = set()
        for run in runs:
            cfg = run.get("config", {}) or {}
            if isinstance(cfg, dict):
                v = cfg.get(key)
                if isinstance(v, str):
                    values.add(v)
        if values:
            key_to_str_values[key] = sorted(values)

    for key in keys_to_check:
        distinct = set()
        for run in runs:
            cfg = run.get("config", {}) or {}
            if not isinstance(cfg, dict):
                continue
            v = cfg.get(key, None)
            if v is None:
                continue
            distinct.add(encode_for_set(v))
        key_to_value_count[key] = len(distinct)

    available_children = [
        dbc.ListGroupItem(
            f"{key} ({key_to_type.get(key, 'unknown')} {key_to_value_count.get(key, 0)})",
            id={"type": "available-key", "key": key},
            action=True,
            n_clicks=0,
        )
        for key in available
    ]

    # Existing filters (to set current values in controls)
    existing_filters = filters_store or {}

    selected_children = []
    for key in selected:
        ktype = key_to_type.get(key, "unknown")
        current = existing_filters.get(key, {}) if isinstance(existing_filters, dict) else {}

        cnt = key_to_value_count.get(key, 0)
        label = html.Div(f"{key} ({cnt})")

        # Inline controls by type
        if ktype == "boolean":
            control = dbc.RadioItems(
                options=[
                    {"label": "All", "value": "all"},
                    {"label": "True", "value": "true"},
                    {"label": "False", "value": "false"},
                ],
                value=current.get("mode", "all"),
                id={"type": "filter-bool", "key": key},
                inline=True,
            )
        elif ktype == "number":
            control = dbc.Row(
                [
                    dbc.Col(dcc.Input(id={"type": "filter-number-min", "key": key}, type="number", placeholder="min", value=current.get("min", None), style={"width": "100%"}), md=6),
                    dbc.Col(dcc.Input(id={"type": "filter-number-max", "key": key}, type="number", placeholder="max", value=current.get("max", None), style={"width": "100%"}), md=6),
                ],
                class_name="g-2",
            )
        elif ktype == "string":
            control = dcc.Dropdown(
                id={"type": "filter-string", "key": key},
                options=[{"label": v, "value": v} for v in key_to_str_values.get(key, [])],
                value=current.get("values", []),
                multi=True,
                placeholder="Select values...",
                style={"width": "100%"},
            )
        else:
            control = html.Div("No filters", style={"color": "#666"})

        # Reorder and remove buttons
        buttons = html.Div(
            [
                dbc.Button(
                    html.I(className="bi bi-chevron-up", style={"fontSize": "1.15rem", "lineHeight": "1"}),
                    id={"type": "move-up", "key": key},
                    size="sm",
                    color="link",
                    class_name="px-1 py-0 lh-1",
                    style={"height": "1.25rem"},
                ),
                dbc.Button(
                    html.I(className="bi bi-chevron-down", style={"fontSize": "1.15rem", "lineHeight": "1"}),
                    id={"type": "move-down", "key": key},
                    size="sm",
                    color="link",
                    class_name="px-1 py-0 lh-1",
                    style={"height": "1.25rem"},
                ),
                dbc.Button(
                    html.I(className="bi bi-x-lg", style={"fontSize": "1.15rem", "lineHeight": "1"}),
                    id={"type": "remove-selected-key", "key": key},
                    size="sm",
                    color="link",
                    class_name="px-1 py-0 lh-1 text-danger",
                    style={"height": "1.25rem"},
                ),
            ],
            style={"whiteSpace": "nowrap"},
        )

        selected_children.append(
            dbc.ListGroupItem(
                dbc.Row(
                    [
                        dbc.Col(label, md=6),
                        dbc.Col(control, md=5),
                        dbc.Col(buttons, md=1, class_name="text-end"),
                    ],
                    class_name="g-2 align-items-center",
                ),
                id={"type": "selected-key", "key": key},
                action=False,
                color="primary",
            )
        )

    return available_children, selected_children


@app.callback(
    Output("filters-store", "data", allow_duplicate=True),
    Input({"type": "filter-bool", "key": ALL}, "value"),
    Input({"type": "filter-number-min", "key": ALL}, "value"),
    Input({"type": "filter-number-max", "key": ALL}, "value"),
    Input({"type": "filter-string", "key": ALL}, "value"),
    State({"type": "filter-bool", "key": ALL}, "id"),
    State({"type": "filter-number-min", "key": ALL}, "id"),
    State({"type": "filter-number-max", "key": ALL}, "id"),
    State({"type": "filter-string", "key": ALL}, "id"),
    State("config-keys-store", "data"),
    prevent_initial_call=True,
)
def update_filters_store(bool_values, min_values, max_values, string_values, bool_ids, min_ids, max_ids, string_ids, config_store):
    # Build maps from ids to values
    filters: Dict[str, Dict] = {}
    selected = (config_store or {}).get("selected", []) or []

    # Boolean modes
    for idx, id_obj in enumerate(bool_ids or []):
        key = id_obj.get("key")
        if key not in selected:
            continue
        val = (bool_values or [None])[idx] if idx < len(bool_values or []) else None
        if val in ("true", "false", "all"):
            filters.setdefault(key, {})["mode"] = val

    # Number mins
    for idx, id_obj in enumerate(min_ids or []):
        key = id_obj.get("key")
        if key not in selected:
            continue
        val = (min_values or [None])[idx] if idx < len(min_values or []) else None
        if val is not None and val != "":
            try:
                filters.setdefault(key, {})["min"] = float(val)
            except Exception:
                pass
        else:
            # Ensure explicit None clears when present
            filters.setdefault(key, {})["min"] = None

    # Number maxs
    for idx, id_obj in enumerate(max_ids or []):
        key = id_obj.get("key")
        if key not in selected:
            continue
        val = (max_values or [None])[idx] if idx < len(max_values or []) else None
        if val is not None and val != "":
            try:
                filters.setdefault(key, {})["max"] = float(val)
            except Exception:
                pass
        else:
            filters.setdefault(key, {})["max"] = None

    # String values
    for idx, id_obj in enumerate(string_ids or []):
        key = id_obj.get("key")
        if key not in selected:
            continue
        vals = (string_values or [None])[idx] if idx < len(string_values or []) else None
        if isinstance(vals, list):
            filters.setdefault(key, {})["values"] = vals

    # Keep only selected keys
    filtered_out = {k: v for k, v in filters.items() if k in selected}
    return filtered_out
@app.callback(
    Output("config-keys-store", "data", allow_duplicate=True),
    Input({"type": "available-key", "key": ALL}, "n_clicks"),
    Input({"type": "remove-selected-key", "key": ALL}, "n_clicks"),
    Input({"type": "move-up", "key": ALL}, "n_clicks"),
    Input({"type": "move-down", "key": ALL}, "n_clicks"),
    State({"type": "available-key", "key": ALL}, "id"),
    State({"type": "remove-selected-key", "key": ALL}, "id"),
    State({"type": "move-up", "key": ALL}, "id"),
    State({"type": "move-down", "key": ALL}, "id"),
    State("config-keys-store", "data"),
    prevent_initial_call=True,
)
def move_keys(available_clicks, remove_clicks, up_clicks, down_clicks, available_ids, remove_ids, up_ids, down_ids, store):
    ctx = dash.callback_context  # type: ignore
    if not ctx.triggered or store is None:
        return dash.no_update

    store = store or {"available": [], "selected": []}
    available = list(store.get("available", []) or [])
    selected = list(store.get("selected", []) or [])

    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    try:
        trigger_id = json.loads(triggered)
    except Exception:
        return dash.no_update

    # Ignore spurious triggers caused by component remounts (value becomes 0 on mount)
    try:
        triggered_value = ctx.triggered[0].get("value", None)
    except Exception:
        triggered_value = None
    if trigger_id.get("type") in {"available-key", "remove-selected-key", "move-up", "move-down"}:
        if not isinstance(triggered_value, int) or triggered_value <= 0:
            return dash.no_update

    if trigger_id.get("type") == "available-key":
        key = trigger_id.get("key")
        if key in available:
            available.remove(key)
        if key not in selected:
            selected.append(key)
    elif trigger_id.get("type") == "remove-selected-key":
        key = trigger_id.get("key")
        if key in selected:
            selected.remove(key)
        if key not in available:
            available.append(key)
            available.sort()
    elif trigger_id.get("type") == "move-up":
        key = trigger_id.get("key")
        if key in selected:
            idx = selected.index(key)
            if idx > 0:
                selected[idx - 1], selected[idx] = selected[idx], selected[idx - 1]
    elif trigger_id.get("type") == "move-down":
        key = trigger_id.get("key")
        if key in selected:
            idx = selected.index(key)
            if idx < len(selected) - 1:
                selected[idx + 1], selected[idx] = selected[idx], selected[idx + 1]

    return {"available": available, "selected": selected}


@app.callback(
    Output("config-keys-store", "data", allow_duplicate=True),
    Input("config-keys-toggle-all", "n_clicks"),
    State("config-keys-store", "data"),
    prevent_initial_call=True,
)
def toggle_all_config_keys(n_clicks, store):
    if not n_clicks:
        return no_update
    store = store or {"available": [], "selected": []}
    available = list(store.get("available", []) or [])
    selected = list(store.get("selected", []) or [])
    union_keys = list(available) + list(selected)
    if len(union_keys) == 0:
        return no_update
    if len(available) == 0 and len(selected) == len(union_keys):
        return {"available": sorted(union_keys), "selected": []}
    return {"available": [], "selected": union_keys}

@app.callback(
    Output("creds-store", "data"),
    Input("connect-button", "n_clicks"),
    Input("clear-saved-button", "n_clicks"),
    State("uri-input", "value"),
    State("host-input", "value"),
    State("port-input", "value"),
    State("username-input", "value"),
    State("password-input", "value"),
    State("authsource-input", "value"),
    State("db-name-input", "value"),
    State("save-options", "value"),
    State("connection-mode-switch", "value"),
    prevent_initial_call=True,
)
def update_saved_credentials(
    connect_clicks,
    clear_clicks,
    uri_value,
    host_value,
    port_value,
    username_value,
    password_value,
    auth_source_value,
    db_name_value,
    save_options,
    connection_mode,
):
    # Determine which button triggered
    ctx = dash.callback_context  # type: ignore
    if not ctx.triggered:
        return no_update
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "clear-saved-button":
        return {}

    # Only save on connect if "Save credentials" is enabled
    save_enabled = "save" in (save_options or [])
    if not save_enabled:
        return no_update

    data = {
        "uri": (uri_value or ""),
        "host": (host_value or ""),
        "port": (port_value or ""),
        "username": (username_value or ""),
        "db_name": (db_name_value or ""),
        "connection_mode": (connection_mode or "credentials"),
    }
    # Always store password if saving is enabled
    data["password"] = (password_value or "")
    data["authSource"] = (auth_source_value or "")
    return data


@app.callback(
    Output("uri-input", "value"),
    Output("host-input", "value"),
    Output("port-input", "value"),
    Output("username-input", "value"),
    Output("password-input", "value"),
    Output("authsource-input", "value"),
    Output("save-options", "value"),
    Input("creds-store", "data"),
)
def populate_inputs_from_saved(data):
    if not data:
        # Do not override user's current typing
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update

    save_values = ["save"] if any([data.get("uri"), data.get("host"), data.get("port"), data.get("username"), data.get("db_name"), data.get("password")]) else []

    return (
        data.get("uri", ""),
        data.get("host", ""),
        data.get("port", ""),
        data.get("username", ""),
        data.get("password", ""),
        data.get("authSource", ""),
        save_values,
    )


@app.callback(
    Output("connection-collapse", "is_open"),
    Output("ui-store", "data"),
    Input("toggle-connection", "n_clicks"),
    State("connection-collapse", "is_open"),
    State("ui-store", "data"),
)
def toggle_connection_panel(n_clicks, is_open, ui_data):
    # Default to open if no stored state
    stored_open = True if not ui_data else bool(ui_data.get("connection_open", True))
    if n_clicks is None:
        return stored_open, {"connection_open": stored_open}
    new_state = not is_open
    return new_state, {"connection_open": new_state}


@app.callback(
    Output("connection-collapse", "is_open", allow_duplicate=True),
    Input("ui-store", "data"),
    prevent_initial_call=True,
)
def apply_saved_ui_state(ui_data):
    if not ui_data:
        return no_update
    return bool(ui_data.get("connection_open", True))

# --- Database name history for autocomplete ---
@app.callback(
    Output("db-history", "data"),
    Input("connect-button", "n_clicks"),
    State("db-name-input", "value"),
    State("db-history", "data"),
    prevent_initial_call=True,
)
def update_db_history(n_clicks, db_name, db_history):
    if not db_name:
        return no_update
    history = db_history or []
    if db_name in history:
        return history
    # Prepend newest, keep up to 20
    return ([db_name] + history)[:20]


@app.callback(
    Output("db-name-list", "children"),
    Input("db-history", "data"),
)
def render_db_datalist(db_history):
    options = db_history or []
    return [html.Option(value=opt) for opt in options]


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

@app.callback(
    Output("metrics-show-keys-store", "data", allow_duplicate=True),
    Input("metrics-show-keys-switch", "value"),
    prevent_initial_call=True,
)
def persist_metrics_show_keys(value):
    return bool(value and "show" in value)

@app.callback(
    Output("metrics-show-keys-switch", "value", allow_duplicate=True),
    Input("metrics-show-keys-store", "data"),
    prevent_initial_call=True,
)
def restore_metrics_show_keys(saved):
    return ["show"] if saved else []

@app.callback(
    Output("metrics-layout-mode-store", "data", allow_duplicate=True),
    Input("metrics-layout-mode", "value"),
    prevent_initial_call=True,
)
def persist_metrics_layout_mode(value):
    return value or "rows"

@app.callback(
    Output("metrics-layout-mode", "value", allow_duplicate=True),
    Input("metrics-layout-mode-store", "data"),
    prevent_initial_call=True,
)
def restore_metrics_layout_mode(saved):
    return saved if saved in ("rows", "cols") else "rows"

@app.callback(
    Output("metrics-per-step-section", "style"),
    Input("metrics-select", "options"),
)
def toggle_metrics_section(options):
    has_metrics = isinstance(options, list) and len(options) > 0
    return {} if has_metrics else {"display": "none"}

@app.callback(
    Output("select-keys-collapse", "is_open"),
    Input("toggle-select-keys", "n_clicks"),
    State("select-keys-collapse", "is_open"),
    prevent_initial_call=True,
)
def toggle_select_keys(n_clicks, is_open):
    if not n_clicks:
        return no_update
    return not is_open


@app.callback(
    Output("experiments-collapse", "is_open"),
    Input("toggle-experiments", "n_clicks"),
    State("experiments-collapse", "is_open"),
    prevent_initial_call=True,
)
def toggle_experiments(n_clicks, is_open):
    if not n_clicks:
        return no_update
    return not is_open


@app.callback(
    Output("metrics-collapse", "is_open"),
    Input("toggle-metrics", "n_clicks"),
    State("metrics-collapse", "is_open"),
    prevent_initial_call=True,
)
def toggle_metrics(n_clicks, is_open):
    if not n_clicks:
        return no_update
    return not is_open


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
    # If already all selected, clear; otherwise select all
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
    Output("db-name-input", "value"),
    Input("db-history", "data"),
    Input("creds-store", "data"),
    State("db-name-input", "value"),
    prevent_initial_call=False,
)
def set_db_name_from_history(db_history, creds_data, current_value):
    # On first load or when history/creds update, if input is empty, set it to saved db name or most recent history
    if current_value:
        return dash.no_update
    # Prefer saved creds db_name if available
    saved_db = (creds_data or {}).get("db_name") if isinstance(creds_data, dict) else None
    if isinstance(saved_db, str) and saved_db.strip():
        return saved_db
    # Otherwise use history
    if db_history and isinstance(db_history, list) and len(db_history) > 0:
        return db_history[0]
    return dash.no_update


# --- Connection mode toggle (URI vs Credentials) ---
@app.callback(
    Output("uri-mode-fields", "style"),
    Output("credentials-mode-fields", "style"),
    Input("connection-mode-switch", "value"),
)
def toggle_connection_mode_fields(mode):
    if mode == "uri":
        return {}, {"display": "none"}
    else:
        return {"display": "none"}, {}


@app.callback(
    Output("connection-mode-switch", "value"),
    Input("creds-store", "data"),
    prevent_initial_call=False,
)
def restore_connection_mode(creds_data):
    if not creds_data or not isinstance(creds_data, dict):
        return "credentials"
    saved_mode = creds_data.get("connection_mode", "credentials")
    return saved_mode if saved_mode in ("uri", "credentials") else "credentials"


if __name__ == "__main__":
    debug_mode = os.environ.get("DEBUG", "false").lower() in ("true", "1", "yes")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8050")), debug=debug_mode)