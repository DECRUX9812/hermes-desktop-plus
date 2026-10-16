# hermes-plus

The sidecar at the heart of Hermes Desktop Plus: it serves the desktop web UI to
any browser, multiplexes the gateway WebSocket, and fronts a loopback-only stock
`hermes dashboard`. Hermes stays stock and untouched, and nothing is ever
installed into Hermes's own virtualenv.

Full documentation, including the architecture and the compatibility matrix:
<https://github.com/DECRUX9812/hermes-desktop-plus>

```sh
uv tool install https://github.com/DECRUX9812/hermes-desktop-plus/releases/download/v0.1.0/hermes_plus-0.1.0-py3-none-any.whl
hermes-plus install     # copy the bridge plugin into the Hermes home, migrate our database
hermes-plus serve       # print a URL with a one-time token
hermes-plus doctor      # compatibility and health checks
```

MIT licensed; see the repository `LICENSE` and `NOTICE`.
