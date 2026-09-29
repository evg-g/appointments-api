# Building behind a corporate TLS-inspecting proxy

Why this exists: some networks put a proxy in the middle that re-signs HTTPS with a private CA. On such
a network, `docker build` and package installs fail with certificate errors, because the base image
does not trust that CA. This is a **local build** concern only — GitHub-hosted runners have a clean TLS
path, so CI never needs any of this.

## The build argument

The `Dockerfile` accepts an optional `EXTRA_CA_CERT` build argument. When set, the runtime stage writes
it into the system trust store (`update-ca-certificates`) and points Python's TLS at the system bundle
(`REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`). When empty (the default, and always in CI), nothing changes.

```bash
# Build with your corporate root CA trusted inside the image:
docker build \
  --build-arg EXTRA_CA_CERT="$(cat /path/to/corp-root-ca.pem)" \
  -t appointments-api:local .
```

## Don't commit the certificate

The CA is passed at build time and never checked into the repo. It is not baked into published images
(CI builds with `EXTRA_CA_CERT` empty), so released images trust only the standard public CAs.

## uv / pip behind the proxy

If dependency resolution fails during the build, export the proxy and CA for the build tools:

```bash
export HTTPS_PROXY=http://proxy.corp:8080
export SSL_CERT_FILE=/path/to/corp-root-ca.pem
```

These belong in your shell, not in the repo or the image.

### uv also needs system certs enabled

`uv` uses rustls, which does **not** read `SSL_CERT_FILE` on its own. On a TLS-inspecting proxy a plain
`make setup` (`uv sync`) fails with `invalid peer certificate: UnknownIssuer`. Point uv at the OS trust
store (where the corporate CA already lives) as well:

```bash
export UV_SYSTEM_CERTS=1        # older uv: UV_NATIVE_TLS=1 (now deprecated)
make setup
```

Verified: with `UV_SYSTEM_CERTS=1` a clean clone of this repo bootstraps and the no-Docker gate
(`make test` + `make contract-check`) runs green. As with everything here, this is a local concern —
GitHub-hosted CI has a clean TLS path and needs none of it.
