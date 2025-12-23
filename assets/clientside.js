// Ensure global namespace
window.dash_clientside = Object.assign({}, window.dash_clientside, {
  pyg: {
    open: function(url) {
      if (!url) {
        return window.dash_clientside.no_update;
      }
      try {
        window.open(url, "_blank");
      } catch (e) {
        // swallow errors; user might have blocked popups
      }
      return "";
    }
  }
});

