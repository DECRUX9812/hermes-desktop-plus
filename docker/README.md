# Hermes Desktop Plus Docker Deployment

The image is a thin layer on top of the official Hermes Agent image
(`nousresearch/hermes-agent`): stock Hermes stays untouched, and our sidecar
runs beside it from its own virtualenv under `/opt/hermes-plus/venv`.

What the image adds:

| Path | Content |
| --- | --- |
| `/opt/hermes-plus/venv` | the `hermes-plus` sidecar (its own venv; Hermes's `/opt/hermes/.venv` is never written) |
| `/opt/hermes-plus/venv/lib/.../hermes_plus/ui_dist` | the built web UI (no Node needed at runtime) |
| `/opt/hermes-plus/entrypoint.sh` | container command: `hermes-plus install`, then `hermes-plus serve --host 0.0.0.0` |

The base image keeps ownership of PID 1 (s6-overlay), the `hermes` user
(UID 10000, remapped by `HERMES_UID`/`HERMES_GID`) and `HERMES_HOME=/opt/data`.
The sidecar's own stock dashboard child binds to loopback inside the container;
only the sidecar is published.

## 1. Running locally

```bash
cp docker/.env.example docker/.env      # set HERMES_PLUS_PASSWORD
docker compose -f docker/compose.yaml up -d --build
```

The desktop web application is then at `http://127.0.0.1:9129`.

`HERMES_PLUS_PASSWORD` is mandatory in `password` mode. The container refuses to
start — exit 1, before it writes anything to the volume — when it is empty or
still the `change-me-to-a-secure-password` placeholder from `.env.example`, and
says which file to fix. Otherwise a skipped `docker/.env` would install, exit 1
and restart-loop under `restart: unless-stopped`, or serve the published port to
anyone with the documented placeholder. A mounted secret also works: give the
service `HERMES_PLUS_PASSWORD_FILE=/run/secrets/hermes-plus-password`.

### Signing in, and staying signed in

In `password` mode a signed-in browser stays signed in across a container
restart (and a host reboot or image upgrade): the session cookie is signed with a
key at `<HERMES_HOME>/hermes-plus/session.key` (mode 0600, created at the first
login) inside the `hermes-data` volume. Changing `HERMES_PLUS_PASSWORD` refuses
every session minted with the old password; deleting `session.key` logs everyone
out too.

In `token` mode the cookie holds the process token, so a restart ends every
browser session unless `HERMES_PLUS_TOKEN` is pinned. That is unchanged.

All state (Hermes config and profiles, `hermes-plus/plus.db`, uploads, the
installed bridge plugin) lives in the `hermes-data` volume mounted at `/opt/data`.

To keep a host Hermes home instead, replace the volume with a bind mount:

```yaml
    volumes:
      - ~/.hermes:/opt/data
```

## 2. Remote access via Cloudflare Tunnel

1. Create a Cloudflare Tunnel in the Cloudflare Zero Trust dashboard.
2. Route a public hostname (e.g. `desktop.example.com`) to the container service origin:
   `http://hermes-plus:9129`
3. Put the tunnel token into `docker/.env`:
   ```env
   TUNNEL_TOKEN=eyJh...
   HERMES_PLUS_TRUST_PROXY_HEADERS=1
   ```
4. Start both Hermes Plus and the Cloudflare Tunnel sidecar:
   ```bash
   docker compose -f docker/compose.yaml --profile tunnel up -d
   ```

### Recommended Security: Cloudflare Access
Pair Cloudflare Tunnel with Cloudflare Access (Zero Trust SSO / MFA) in front of your public hostname. With `HERMES_PLUS_TRUST_PROXY_HEADERS=1`, authenticated identity headers are preserved.

## 3. Changing the Hermes version

`HERMES_TAG` selects the official base image tag and is what the sidecar is
tested against:

```bash
docker compose -f docker/compose.yaml build --build-arg HERMES_TAG=v0.22.2
docker compose -f docker/compose.yaml up -d
```

Or set `HERMES_TAG` in `docker/.env` and rebuild. Tag names are upstream's
release tags (`vX.Y.Z`); `latest` tracks the newest stable release.

Inside the container `hermes update` refuses to run: a published image is
image-managed, so the upgrade path is pulling/rebuilding the image above.

## 4. Inspecting a running container

```bash
docker compose -f docker/compose.yaml exec hermes-plus hermes-plus doctor --json
docker compose -f docker/compose.yaml logs -f hermes-plus
```

`default_port` is a pre-flight check (is 9129 free to bind?), so against a
running container it says "Port 9129 is already in use" — the sidecar itself
holds it — and `doctor` exits 1. That is the check working, not a broken
install; every other check reports on the container as it stands. For a clean
all-ok run, ask about a port nothing holds:

```bash
docker compose -f docker/compose.yaml exec hermes-plus hermes-plus doctor --port 9177
```
