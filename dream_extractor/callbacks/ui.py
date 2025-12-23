from dash import html, no_update
from dash import Input, Output, State
import dash


def register_ui_callbacks(app):
    @app.callback(
        Output("connection-collapse", "is_open"),
        Output("ui-store", "data"),
        Input("toggle-connection", "n_clicks"),
        State("connection-collapse", "is_open"),
        State("ui-store", "data"),
    )
    def toggle_connection_panel(n_clicks, is_open, ui_data):
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
        return ([db_name] + history)[:20]

    @app.callback(
        Output("db-name-list", "children"),
        Input("db-history", "data"),
    )
    def render_db_datalist(db_history):
        options = db_history or []
        return [html.Option(value=opt) for opt in options]

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
        Output("db-name-input", "value"),
        Input("db-history", "data"),
        Input("creds-store", "data"),
        State("db-name-input", "value"),
        prevent_initial_call=False,
    )
    def set_db_name_from_history(db_history, creds_data, current_value):
        if current_value:
            return dash.no_update
        saved_db = (creds_data or {}).get("db_name") if isinstance(creds_data, dict) else None
        if isinstance(saved_db, str) and saved_db.strip():
            return saved_db
        if db_history and isinstance(db_history, list) and len(db_history) > 0:
            return db_history[0]
        return dash.no_update


