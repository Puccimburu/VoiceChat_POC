module.exports = function (app) {
  // Security headers required for SharedArrayBuffer (VAD worklet uses it)
  app.use((req, res, next) => {
    res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
    res.setHeader('Cross-Origin-Embedder-Policy', 'credentialless');
    next();
  });
  // CRA's webpack-dev-server already handles historyApiFallback (serves
  // index.html for /admin etc. on hard refresh) — no custom SPA middleware needed.
};
