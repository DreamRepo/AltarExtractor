from dash import Input, Output, ClientsideFunction, no_update
from flask import request, make_response
import pandas as pd
import uuid
from ..state.cache import PYGWALKER_CACHE


def register_pygwalker(app, server):
    # Open pygwalker URL in a new tab (client-side via ClientsideFunction)
    app.clientside_callback(
        ClientsideFunction(namespace="pyg", function_name="open"),
        Output("pygwalker-open-dummy", "children"),
        Input("pygwalker-url", "data"),
    )

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
        Output("pygwalker-url", "data", allow_duplicate=True),
        Input("open-pygwalker-btn", "n_clicks"),
        State("metrics-steps-table", "data"),
        prevent_initial_call=True,
    )
    def open_pygwalker_page(n_clicks, table_data):
        if not n_clicks:
            return no_update
        data = table_data or []
        key = str(uuid.uuid4())
        try:
            PYGWALKER_CACHE[key] = data
        except Exception:
            return no_update
        return f"/pygwalker?id={key}"

    @app.callback(
        Output("pygwalker-url", "data", allow_duplicate=True),
        Input("open-pygwalker-exp-btn", "n_clicks"),
        State("experiments-table", "data"),
        prevent_initial_call=True,
    )
    def open_pygwalker_exp_page(n_clicks, table_data):
        if not n_clicks:
            return no_update
        data = table_data or []
        key = str(uuid.uuid4())
        try:
            PYGWALKER_CACHE[key] = data
        except Exception:
            return no_update
        return f"/pygwalker?id={key}"


