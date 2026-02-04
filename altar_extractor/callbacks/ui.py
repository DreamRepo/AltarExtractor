"""
UI toggle and display callbacks for AltarExtractor.
"""

from dash import Input, Output, State, no_update


def register_ui_callbacks(app):
    """Register UI-related callbacks for panel toggles and display."""

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
        Output("metrics-per-step-section", "style"),
        Input("metrics-select", "options"),
    )
    def toggle_metrics_section(options):
        has_metrics = isinstance(options, list) and len(options) > 0
        return {} if has_metrics else {"display": "none"}
