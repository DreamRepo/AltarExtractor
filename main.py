import os
import sys
import webbrowser
import threading
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


def is_frozen():
    """Check if running as a PyInstaller bundle."""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def open_browser(port: int):
    """Open the default browser after a short delay."""
    import time
    time.sleep(1.5)  # Wait for server to start
    webbrowser.open(f"http://localhost:{port}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8050"))
    debug_mode = os.environ.get("DEBUG", "false").lower() in ("true", "1", "yes")
    
    # When running as executable, disable debug and open browser
    if is_frozen():
        debug_mode = False
        print("\n" + "=" * 60)
        print("  AltarExtractor is starting...")
        print(f"  Open your browser at: http://localhost:{port}")
        print("  Press Ctrl+C to stop the server")
        print("=" * 60 + "\n")
        
        # Open browser automatically in a separate thread
        browser_thread = threading.Thread(target=open_browser, args=(port,), daemon=True)
        browser_thread.start()
    
    app = create_and_configure_app()
    app.run(host="0.0.0.0", port=port, debug=debug_mode)


