# Hermes Desktop Plus Docker Deployment

Hermes Desktop Plus runs alongside stock Hermes Agent in an isolated container environment.

## 1. Running Locally

Copy the example environment configuration:

```bash
cp docker/.env.example docker/.env
```

Edit `docker/.env` to configure your `HERMES_PLUS_PASSWORD`. Then start the service:

```bash
docker compose -f docker/compose.yaml up -d
```

The desktop web application will be accessible at:
`http://127.0.0.1:9129`

All data, profiles, and state persist in the `hermes-data` volume mounted at `/data/hermes`.

## 2. Remote Access via Cloudflare Tunnel

To expose Hermes Desktop Plus securely without opening router ports:

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

## 3. Upgrading Stock Hermes Agent

The Docker image installs stock Hermes Agent from GitHub pinned to `HERMES_REF`. To upgrade to a newer stock Hermes release, rebuild the image with the updated tag:

```bash
docker compose -f docker/compose.yaml build --build-arg HERMES_REF=v0.22.2
docker compose -f docker/compose.yaml up -d
```
