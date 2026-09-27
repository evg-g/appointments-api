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
