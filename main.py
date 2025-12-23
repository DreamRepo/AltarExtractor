import os
from dream_extractor import create_app
from dream_extractor.components.layout import build_layout
from dream_extractor.callbacks.ui import register_ui_callbacks
from dream_extractor.callbacks.connection import register_connection_callbacks
from dream_extractor.callbacks.filters import register_filters_callbacks
from dream_extractor.callbacks.experiments import register_experiments_callbacks
from dream_extractor.callbacks.metrics import register_metrics_callbacks
from dream_extractor.callbacks.pygwalker import register_pygwalker


def create_and_configure_app():
    app, server = create_app()
    app.layout = build_layout()
    register_ui_callbacks(app)
    register_connection_callbacks(app)
    register_filters_callbacks(app)
    register_experiments_callbacks(app)
    register_metrics_callbacks(app)
    register_pygwalker(app, server)
    return app


if __name__ == "__main__":
    app = create_and_configure_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8050")), debug=True)


