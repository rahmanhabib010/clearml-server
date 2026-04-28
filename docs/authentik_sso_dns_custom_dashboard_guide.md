# ClearML Authentik SSO DNS And Custom Dashboard Deployment Guide

Date: 2026-04-28

Example DNS used in this document:

```text
https://www.clearml.company.com
```

Replace this example with the real company DNS name before deploying.

## Purpose

This document explains how to deploy the ClearML Authentik SSO setup with a real DNS hostname instead of `localhost`, and how to connect the company customized ClearML dashboard.

This guide assumes the SSO architecture has already been added:

```text
clearml-gateway
clearml-oauth2-proxy
clearml-sso-bridge
clearml-webserver
clearml-apiserver
clearml-fileserver
clearml-mongo
clearml-redis
clearml-elastic
```

The current browser entrypoint is:

```text
http://localhost:8080
```

The DNS deployment target should become:

```text
https://www.clearml.company.com
```

## Current Local Flow

For local testing, the request path is:

```text
Browser
  -> http://localhost:8080
  -> clearml-gateway
  -> oauth2-proxy
  -> Authentik
  -> oauth2-proxy callback
  -> sso-bridge
  -> clearml-webserver
  -> ClearML dashboard
```

The backend API path is:

```text
Browser or ClearML SDK
  -> http://localhost:8080/api
  -> clearml-gateway
  -> clearml-apiserver:8008
```

The files path is:

```text
Browser or ClearML SDK
  -> http://localhost:8080/files
  -> clearml-gateway
  -> clearml-fileserver:8081
```

## DNS Production Flow

For real DNS, the intended request path should be:

```text
Browser
  -> https://www.clearml.company.com
  -> company DNS
  -> company load balancer / reverse proxy / firewall
  -> clearml-gateway
  -> oauth2-proxy
  -> Authentik
  -> sso-bridge
  -> company customized dashboard
```

The public API URL should become:

```text
https://www.clearml.company.com/api
```

The public files URL should become:

```text
https://www.clearml.company.com/files
```

Do not expose internal Docker service names to users:

```text
Do not use http://apiserver:8008 in browser/client config.
Do not use http://fileserver:8081 in browser/client config.
Do not use http://webserver inside frontend runtime config.
```

Those names only work inside Docker.

## DNS Requirements

Create a DNS record for the ClearML hostname.

Example:

```text
www.clearml.company.com
```

Point it to the server or load balancer that reaches the Docker host.

Typical DNS records:

```text
www.clearml.company.com   A      <public-or-private-server-ip>
www.clearml.company.com   CNAME  <company-load-balancer-hostname>
```

The selected DNS name must be used consistently in:

```text
1. Authentik redirect URI.
2. oauth2-proxy redirect_url.
3. Browser URL.
4. ClearML SDK configuration.
5. Any company reverse proxy or load balancer rule.
```

## Authentik Changes

In Authentik, update the ClearML OAuth/OIDC provider/application.

Set the allowed redirect URI to:

```text
https://www.clearml.company.com/oauth2/callback
```

If Authentik has a launch URL or application URL field, set it to:

```text
https://www.clearml.company.com
```

Required scopes:

```text
openid
email
profile
```

The SSO bridge expects oauth2-proxy to pass the authenticated user email through headers. The most important header is:

```text
X-Forwarded-Email
```

## Required `.env.sso` Changes

The file `.env.sso` is local/private and must not be committed.

For real DNS, use:

```env
OAUTH2_PROXY_CLIENT_ID=<authentik-client-id>
OAUTH2_PROXY_CLIENT_SECRET=<authentik-client-secret>
OAUTH2_PROXY_COOKIE_SECRET=<16-or-24-or-32-character-secret>
```

For HTTPS DNS deployment, the cookie must be secure:

```env
OAUTH2_PROXY_COOKIE_SECURE=true
```

If you decide to make `oauth2-proxy.cfg` read redirect values from environment variables in the future, use:

```env
OAUTH2_PROXY_REDIRECT_URL=https://www.clearml.company.com/oauth2/callback
OAUTH2_PROXY_OIDC_ISSUER_URL=https://authentik.company.com/application/o/clearml/
```

Important: the current `docker/sso/oauth2-proxy.cfg` has hardcoded `redirect_url` and `oidc_issuer_url`, so editing `.env.sso` alone is not enough unless the config file is also changed to use environment-driven settings or the hardcoded values are updated.

## Required oauth2-proxy Config Changes

File:

```text
docker/sso/oauth2-proxy.cfg
```

Change this local value:

```toml
redirect_url = "http://localhost:8080/oauth2/callback"
```

To the DNS value:

```toml
redirect_url = "https://www.clearml.company.com/oauth2/callback"
```

For HTTPS, change:

```toml
cookie_secure = false
```

To:

```toml
cookie_secure = true
```

Keep this unless Authentik issuer changes:

```toml
oidc_issuer_url = "https://authentik.ccixgtestbed.org/application/o/clearml/"
```

If company Authentik uses a different issuer URL, change it to:

```toml
oidc_issuer_url = "https://authentik.company.com/application/o/clearml/"
```

Optional cookie domain setting:

If the ClearML app is only served on one hostname, omit `cookie_domains`.

If the company wants the auth cookie scoped across subdomains, add:

```toml
cookie_domains = [ ".company.com" ]
```

Use the company root domain, not the full app hostname, only if that is intentional.

## Gateway Deployment Options

There are two clean ways to expose `www.clearml.company.com`.

## Option A: Company Reverse Proxy Terminates HTTPS

Use this if the company already has Nginx, Apache, Traefik, HAProxy, F5, Cloudflare, or another load balancer handling TLS.

Public request:

```text
https://www.clearml.company.com
```

Company proxy forwards to Docker host:

```text
http://<docker-host>:8080
```

Keep Docker Compose gateway port as:

```yaml
gateway:
  ports:
    - "8080:80"
```

Company reverse proxy must forward these headers:

```text
Host: www.clearml.company.com
X-Forwarded-Host: www.clearml.company.com
X-Forwarded-Proto: https
X-Forwarded-For: <client-ip>
X-Real-IP: <client-ip>
```

Example company Nginx proxy:

```nginx
server {
    listen 443 ssl;
    server_name www.clearml.company.com;

    ssl_certificate /etc/ssl/company/www.clearml.company.com.crt;
    ssl_certificate_key /etc/ssl/company/www.clearml.company.com.key;

    client_max_body_size 0;

    location / {
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_pass http://<docker-host>:8080;
    }
}
```

This is the recommended enterprise setup because TLS certificates remain in the company-managed proxy layer.

## Option B: Docker Gateway Terminates HTTPS Directly

Use this only if the Docker host itself will serve HTTPS publicly.

Change gateway ports in `docker/docker-compose.yml`:

```yaml
gateway:
  ports:
    - "80:80"
    - "443:443"
```

Mount certificates into the gateway:

```yaml
gateway:
  volumes:
    - ./sso/nginx.conf:/opt/clearml/sso/nginx.conf:ro
    - /path/to/certs:/opt/clearml/certs:ro
```

Update `docker/sso/nginx.conf` to add HTTPS:

```nginx
server {
    listen 80;
    server_name www.clearml.company.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name www.clearml.company.com;

    ssl_certificate /opt/clearml/certs/fullchain.pem;
    ssl_certificate_key /opt/clearml/certs/privkey.pem;

    proxy_http_version 1.1;
    client_max_body_size 0;
    proxy_buffering off;
    proxy_buffer_size 128k;
    proxy_buffers 8 128k;
    proxy_busy_buffers_size 256k;

    location = /healthz {
        add_header Content-Type text/plain;
        return 200 "ok\n";
    }

    location /api {
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Host $host;
        proxy_set_header Connection "";
        proxy_pass http://clearml_api;
        rewrite ^/api/?(.*)$ /$1 break;
    }

    location /files {
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Host $host;
        proxy_pass http://clearml_files;
        rewrite ^/files/?(.*)$ /$1 break;
    }

    location /oauth2/ {
        set $oauth2_proxy "oauth2-proxy:4180";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Uri $request_uri;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_pass http://$oauth2_proxy;
    }

    location / {
        set $oauth2_proxy "oauth2-proxy:4180";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Uri $request_uri;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_pass http://$oauth2_proxy;
    }
}
```

This option requires certificate renewal planning.

## ClearML SDK Configuration For Users

For users connecting with the ClearML Python SDK, the API and files URLs should use the DNS hostname.

Expected values:

```text
api_server: https://www.clearml.company.com/api
web_server: https://www.clearml.company.com
files_server: https://www.clearml.company.com/files
```

During `clearml-init`, users should enter:

```text
Web App: https://www.clearml.company.com
API: https://www.clearml.company.com/api
File Store: https://www.clearml.company.com/files
```

The ClearML SDK should not use:

```text
http://localhost:8080
http://apiserver:8008
http://fileserver:8081
```

Those are local/internal addresses.

## Company Customized Dashboard

The company customized frontend repository is:

```text
https://github.com/rahmanhabib010/clearml_webapp_customized
```

The current Docker setup does not use that repository yet.

Right now, Compose uses the built-in ClearML frontend:

```yaml
webserver:
  command:
    - webserver
  image: clearml/server:latest
```

To use the company dashboard, replace the `webserver` service with a Docker image built from the customized dashboard.

## Custom Dashboard Option 1: Use A Published Image

If the company has already pushed a dashboard image to a registry, update `docker/docker-compose.yml`:

```yaml
webserver:
  container_name: clearml-webserver
  image: ghcr.io/company/clearml-webapp-customized:<tag>
  restart: unless-stopped
  depends_on:
    apiserver:
      condition: service_healthy
  networks:
    - backend
    - frontend
```

If the custom image already serves the frontend on port `80`, no bridge change is needed.

If the custom image serves on a different port, for example `8080`, update the SSO bridge environment:

```yaml
sso-bridge:
  environment:
    CLEARML_WEB_UPSTREAM: http://webserver:8080
```

## Custom Dashboard Option 2: Build Locally From GitHub

If the custom repo has a working Dockerfile:

```powershell
git clone https://github.com/rahmanhabib010/clearml_webapp_customized
cd clearml_webapp_customized
docker build -t clearml-webapp-customized:local .
```

Then update `docker/docker-compose.yml`:

```yaml
webserver:
  container_name: clearml-webserver
  image: clearml-webapp-customized:local
  restart: unless-stopped
  depends_on:
    apiserver:
      condition: service_healthy
  networks:
    - backend
    - frontend
```

Start the stack:

```powershell
docker compose -f docker\docker-compose.yml -f docker\docker-compose.local-windows.yml up -d --force-recreate webserver sso-bridge oauth2-proxy gateway
```

## Custom Dashboard Runtime Requirements

The customized dashboard must be compatible with this gateway layout:

```text
Frontend root: /
API base path: /api
Files base path: /files
OAuth callback path: /oauth2/callback
```

The browser should call:

```text
https://www.clearml.company.com/api
https://www.clearml.company.com/files
```

The browser should not call:

```text
http://apiserver:8008
http://fileserver:8081
http://localhost:8008
```

If the customized dashboard has an environment file such as `env.js`, `.env`, `config.js`, or runtime JSON, set these values:

```text
API URL: /api
Files URL: /files
Web root/subpath: /
Public URL: https://www.clearml.company.com
```

Current SSO bridge behavior:

```text
docker/sso/sso_bridge.py serves /env.js directly with window.__env.subPath = "".
```

If the company dashboard needs additional values in `env.js`, update `env_js_response()` in `docker/sso/sso_bridge.py` to include them.

Example:

```python
def env_js_response():
    body = "\n".join(
        [
            "(function (window) {",
            "  window.__env = window.__env || {};",
            "  window.__env.subPath = \"\";",
            "  window.__env.apiBaseUrl = \"/api\";",
            "  window.__env.filesBaseUrl = \"/files\";",
            "}(this));",
            "",
        ]
    )
    return no_store(Response(body, mimetype="application/javascript"))
```

If the company dashboard must serve its own `/env.js`, remove the special `/env.js` handling from `proxy_to_clearml_web()` and make sure the dashboard container returns a valid root deployment config.

## Recommended Production Compose Values

For DNS deployment behind a company HTTPS proxy, use:

```yaml
gateway:
  ports:
    - "8080:80"
```

For DNS deployment where Docker directly exposes HTTP/HTTPS, use:

```yaml
gateway:
  ports:
    - "80:80"
    - "443:443"
```

For SSO cookies over HTTPS:

```toml
cookie_secure = true
```

For OAuth callback:

```toml
redirect_url = "https://www.clearml.company.com/oauth2/callback"
```

For ClearML bridge cookie:

```yaml
sso-bridge:
  environment:
    SSO_CLEARML_COOKIE_SECURE: "true"
```

If the ClearML hostname is exactly `www.clearml.company.com`, cookie domain can be omitted.

If the company wants the ClearML cookie shared across subdomains, set:

```yaml
sso-bridge:
  environment:
    SSO_CLEARML_COOKIE_DOMAIN: ".company.com"
```

Use a shared cookie domain only when required.

## Fresh DNS Deployment Checklist

1. Pick the final hostname.

Example:

```text
www.clearml.company.com
```

2. Create DNS record pointing to the company proxy or Docker host.

3. Add Authentik redirect URI:

```text
https://www.clearml.company.com/oauth2/callback
```

4. Update `docker/sso/oauth2-proxy.cfg`:

```toml
redirect_url = "https://www.clearml.company.com/oauth2/callback"
cookie_secure = true
```

5. Update issuer if Authentik hostname changes:

```toml
oidc_issuer_url = "https://authentik.company.com/application/o/clearml/"
```

6. Update `.env.sso` with production client secret and cookie secret.

7. Decide HTTPS termination:

```text
Company proxy terminates HTTPS and forwards to Docker port 8080.
Docker gateway terminates HTTPS directly on ports 80/443.
```

8. If using company customized dashboard, replace the `webserver` image.

Example:

```yaml
webserver:
  image: ghcr.io/company/clearml-webapp-customized:<tag>
```

9. Recreate the affected services:

```powershell
docker compose -f docker\docker-compose.yml -f docker\docker-compose.local-windows.yml up -d --force-recreate webserver sso-bridge oauth2-proxy gateway
```

10. Verify health:

```powershell
curl.exe -i https://www.clearml.company.com/healthz
curl.exe -i https://www.clearml.company.com/api/debug.ping
```

11. Open the browser:

```text
https://www.clearml.company.com
```

Expected result:

```text
Browser redirects to Authentik.
User logs in.
Browser returns to https://www.clearml.company.com/oauth2/callback.
User lands on the customized ClearML dashboard.
API calls go through /api.
File calls go through /files.
```

## Files To Review Before Merge

Review and intentionally commit:

```text
.gitignore
.env.sso.example
docker/docker-compose.yml
docker/compose.yaml
docker/docker-compose.local-windows.yml
docker/sso/nginx.conf
docker/sso/oauth2-proxy.cfg
docker/sso/sso_bridge.py
docs/authentik_sso_dns_custom_dashboard_guide.md
```

Do not commit:

```text
.env.sso
```

Usually do not commit unless intentionally regenerated:

```text
apiserver/schema/services/_cache.json
```

## Final Notes

For localhost testing, use:

```text
http://localhost:8080
```

For real team/company usage, use:

```text
https://www.clearml.company.com
```

For the company customized dashboard, the key requirement is that the dashboard image serves the frontend and uses public browser paths:

```text
/api
/files
```

The SSO bridge will handle identity, ClearML user creation, and ClearML session cookie creation before passing the browser into the dashboard.

