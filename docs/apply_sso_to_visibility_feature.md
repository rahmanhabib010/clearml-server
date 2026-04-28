# Apply Authentik SSO Changes To `visibility-feature`

Date: 2026-04-28

Source branch where SSO work was implemented:

```text
SSO-Integration
```

Target branch:

```text
visibility-feature
```

Example existing company ClearML URL:

```text
https://www.clearml.com
```

Replace `https://www.clearml.com` with the actual company ClearML URL.

## Purpose

This document explains how to take the Authentik SSO work from `SSO-Integration` and apply it cleanly to a new branch based on `visibility-feature`.

This document assumes:

```text
1. The company already has a working DNS/URL for ClearML.
2. The company already has Authentik available.
3. The company already has a customized ClearML dashboard image or frontend build process.
4. The goal is to add SSO routing and ClearML session creation into the visibility-feature branch.
```

This document does not explain how to create DNS records.

## Final Target Architecture

After applying these changes, the browser flow should be:

```text
Browser
  -> https://www.clearml.com
  -> clearml-gateway
  -> oauth2-proxy
  -> Authentik login
  -> oauth2-proxy callback
  -> sso-bridge
  -> company ClearML dashboard
```

The backend paths should be:

```text
https://www.clearml.com/api
https://www.clearml.com/files
```

The frontend should not call Docker-internal service names from the browser:

```text
Do not use http://apiserver:8008 in browser code.
Do not use http://fileserver:8081 in browser code.
Do not use http://webserver in browser code.
```

Those service names only work inside Docker containers.

## Important Clarification About The Frontend

The current SSO branch can show a ClearML dashboard even if the custom frontend repo is not running locally.

Reason:

```text
clearml/server:latest already contains a compiled ClearML web UI.
```

In the SSO branch, this service currently serves that bundled frontend:

```yaml
webserver:
  command:
    - webserver
  image: clearml/server:latest
```

For the company customized dashboard, replace that `webserver` image with the company dashboard image.

Example:

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

If the custom dashboard image listens on a port other than `80`, update:

```yaml
sso-bridge:
  environment:
    CLEARML_WEB_UPSTREAM: http://webserver:<port>
```

If the custom dashboard listens on `80`, keep:

```yaml
CLEARML_WEB_UPSTREAM: http://webserver
```

## Files To Bring From `SSO-Integration`

Copy or recreate these files in the target branch:

```text
.env.sso.example
docker/docker-compose.local-windows.yml
docker/sso/nginx.conf
docker/sso/oauth2-proxy.cfg
docker/sso/sso_bridge.py
```

Modify these existing files in the target branch:

```text
.gitignore
docker/docker-compose.yml
docker/compose.yaml
```

Do not copy or commit:

```text
.env.sso
apiserver/schema/services/_cache.json
```

`.env.sso` contains secrets and must remain local/private.

`apiserver/schema/services/_cache.json` is runtime-generated and should not be part of this SSO change unless intentionally regenerated for another reason.

## Step 1: Create A New Branch From `visibility-feature`

```powershell
git checkout visibility-feature
git pull
git checkout -b visibility-feature-sso
```

Use any branch name the team prefers.

## Step 2: Copy New SSO Files

From the `SSO-Integration` branch, bring these files into the new branch:

```text
.env.sso.example
docker/docker-compose.local-windows.yml
docker/sso/nginx.conf
docker/sso/oauth2-proxy.cfg
docker/sso/sso_bridge.py
```

If applying manually, create this folder:

```text
docker/sso
```

The folder should contain:

```text
nginx.conf
oauth2-proxy.cfg
sso_bridge.py
```

## Step 3: Update `.gitignore`

Add these entries:

```gitignore
.env
.env.*
!.env.example
!.env.*.example
```

Reason:

```text
The real `.env.sso` file contains Authentik client secrets and OAuth cookie secrets.
It must not be committed.
The example file should remain commit-safe.
```

## Step 4: Add `.env.sso.example`

Create:

```text
.env.sso.example
```

Content:

```env
OAUTH2_PROXY_CLIENT_ID=<authentik-client-id>
OAUTH2_PROXY_CLIENT_SECRET=<authentik-client-secret>
OAUTH2_PROXY_COOKIE_SECRET=<16-or-24-or-32-character-secret>

# For production HTTPS usage:
OAUTH2_PROXY_COOKIE_SECURE=true
```

The actual `.env.sso` file should be created locally by each developer or deployed as a secret in the server environment.

## Step 5: Update Authentik Application

In the Authentik provider/application for ClearML, set the allowed redirect URI to:

```text
https://www.clearml.com/oauth2/callback
```

Required scopes:

```text
openid
email
profile
```

The SSO bridge expects oauth2-proxy to forward the authenticated email in:

```text
X-Forwarded-Email
```

## Step 6: Update `docker/sso/oauth2-proxy.cfg`

Use this production-style configuration:

```toml
http_address = "0.0.0.0:4180"

provider = "oidc"
provider_display_name = "Authentik"
oidc_issuer_url = "https://authentik.company.com/application/o/clearml/"
redirect_url = "https://www.clearml.com/oauth2/callback"
scope = "openid email profile"

email_domains = [ "*" ]
upstreams = [ "http://sso-bridge:5000/" ]

reverse_proxy = true
skip_provider_button = true

set_xauthrequest = true
pass_user_headers = true
pass_authorization_header = false
pass_access_token = false
session_cookie_minimal = true
code_challenge_method = "S256"

cookie_name = "_clearml_oauth2_proxy"
cookie_secure = true
cookie_samesite = "lax"
cookie_refresh = "0"
cookie_expire = "8h"

insecure_oidc_allow_unverified_email = true
```

Replace:

```text
https://authentik.company.com/application/o/clearml/
```

with the actual Authentik issuer URL.

Replace:

```text
https://www.clearml.com/oauth2/callback
```

with the actual company ClearML callback URL.

For local development only, use:

```toml
redirect_url = "http://localhost:8080/oauth2/callback"
cookie_secure = false
```

For the real company URL, use:

```toml
redirect_url = "https://www.clearml.com/oauth2/callback"
cookie_secure = true
```

## Step 7: Add SSO Bridge

Create:

```text
docker/sso/sso_bridge.py
```

Purpose:

```text
1. Receive authenticated identity headers from oauth2-proxy.
2. Validate the user email.
3. Create or reuse a ClearML company/user.
4. Generate a ClearML JWT using ClearML AuthBLL.
5. Set ClearML cookie `clearml_token_basic`.
6. Proxy frontend requests to the dashboard container.
```

Important behavior:

```text
Default user role: user
Default workspace mode: company per email
Default session cookie: clearml_token_basic
Default frontend upstream: http://webserver
```

Important environment variables:

```text
CLEARML_WEB_UPSTREAM
CLEARML_SERVER_SUB_PATH
SSO_CLEARML_COMPANY_ID
SSO_CLEARML_COOKIE_DOMAIN
SSO_CLEARML_COOKIE_SECURE
SSO_CLEARML_COOKIE_HTTPONLY
SSO_CLEARML_COOKIE_SAMESITE
SSO_CLEARML_DEFAULT_ROLE
SSO_CLEARML_TOKEN_EXPIRATION_SEC
SSO_CLEARML_WORKSPACE_MODE
```

For production HTTPS, set:

```yaml
SSO_CLEARML_COOKIE_SECURE: "true"
```

If the app is served at the root URL:

```text
https://www.clearml.com/
```

keep:

```yaml
CLEARML_SERVER_SUB_PATH: ""
```

If the app is served under a subpath:

```text
https://www.clearml.com/clearml/
```

set:

```yaml
CLEARML_SERVER_SUB_PATH: "/clearml"
```

Only use a subpath if the company dashboard and gateway are designed for it.

## Step 8: Add Gateway Nginx Config

Create:

```text
docker/sso/nginx.conf
```

The gateway must route:

```text
/healthz  -> local gateway health response
/api      -> apiserver:8008
/files    -> fileserver:8081
/oauth2/  -> oauth2-proxy:4180
/         -> oauth2-proxy:4180
```

The important production logic is:

```nginx
location /api {
    proxy_pass http://clearml_api;
    rewrite ^/api/?(.*)$ /$1 break;
}

location /files {
    proxy_pass http://clearml_files;
    rewrite ^/files/?(.*)$ /$1 break;
}

location /oauth2/ {
    proxy_pass http://$oauth2_proxy;
}

location / {
    proxy_pass http://$oauth2_proxy;
}
```

Keep the larger proxy buffers from the SSO branch:

```nginx
large_client_header_buffers 8 32k;
proxy_buffer_size 128k;
proxy_buffers 8 128k;
proxy_busy_buffers_size 256k;
```

Reason:

```text
OIDC login and callback headers can be large.
Without larger buffers, nginx can return 502 on callback.
```

## Step 9: Update `docker/docker-compose.yml`

Remove the old Compose version line if present:

```yaml
version: "3.6"
```

Add healthcheck to `apiserver`:

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -fsS http://localhost:8008/debug.ping >/dev/null || exit 1"]
  interval: 10s
  timeout: 5s
  retries: 30
  start_period: 60s
```

Add healthcheck to `elasticsearch`:

```yaml
healthcheck:
  test: ["CMD-SHELL", "bash -c ': > /dev/tcp/127.0.0.1/9200'"]
  interval: 10s
  timeout: 5s
  retries: 30
  start_period: 30s
```

Update apiserver dependencies:

```yaml
depends_on:
  redis:
    condition: service_started
  mongo:
    condition: service_started
  elasticsearch:
    condition: service_healthy
  fileserver:
    condition: service_started
```

Add `webserver`:

```yaml
webserver:
  command:
    - webserver
  container_name: clearml-webserver
  image: clearml/server:latest
  restart: unless-stopped
  depends_on:
    apiserver:
      condition: service_healthy
  networks:
    - backend
    - frontend
```

For company customized dashboard, replace the image:

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

Add `sso-bridge`:

```yaml
sso-bridge:
  entrypoint:
    - python3
    - /opt/clearml/sso/sso_bridge.py
  container_name: clearml-sso-bridge
  image: clearml/server:latest
  restart: unless-stopped
  depends_on:
    apiserver:
      condition: service_healthy
    mongo:
      condition: service_started
    redis:
      condition: service_started
    elasticsearch:
      condition: service_healthy
    webserver:
      condition: service_started
  environment:
    CLEARML_ELASTIC_SERVICE_HOST: elasticsearch
    CLEARML_ELASTIC_SERVICE_PORT: 9200
    CLEARML_MONGODB_SERVICE_HOST: mongo
    CLEARML_MONGODB_SERVICE_PORT: 27017
    CLEARML_REDIS_SERVICE_HOST: redis
    CLEARML_REDIS_SERVICE_PORT: 6379
    CLEARML_SERVER_DEPLOYMENT_TYPE: linux
    CLEARML_WEB_UPSTREAM: http://webserver
    PYTHONPATH: /opt/clearml
    SSO_CLEARML_COMPANY_ID: ${SSO_CLEARML_COMPANY_ID:-}
    SSO_CLEARML_COOKIE_DOMAIN: ${SSO_CLEARML_COOKIE_DOMAIN:-}
    SSO_CLEARML_COOKIE_SECURE: ${SSO_CLEARML_COOKIE_SECURE:-true}
    SSO_CLEARML_DEFAULT_ROLE: ${SSO_CLEARML_DEFAULT_ROLE:-user}
    SSO_CLEARML_TOKEN_EXPIRATION_SEC: ${SSO_CLEARML_TOKEN_EXPIRATION_SEC:-2592000}
    SSO_CLEARML_WORKSPACE_MODE: ${SSO_CLEARML_WORKSPACE_MODE:-company}
  volumes:
    - /opt/clearml/logs:/var/log/clearml
    - /opt/clearml/config:/opt/clearml/config
    - ../apiserver:/opt/clearml/apiserver
    - ./sso/sso_bridge.py:/opt/clearml/sso/sso_bridge.py:ro
  networks:
    - backend
    - frontend
```

Add `oauth2-proxy`:

```yaml
oauth2-proxy:
  command:
    - --config=/etc/oauth2-proxy.cfg
  container_name: clearml-oauth2-proxy
  image: quay.io/oauth2-proxy/oauth2-proxy:v7.6.0
  restart: unless-stopped
  depends_on:
    - sso-bridge
  env_file:
    - path: ../.env.sso
      required: false
  volumes:
    - ./sso/oauth2-proxy.cfg:/etc/oauth2-proxy.cfg:ro
  networks:
    - frontend
```

Add `gateway`:

```yaml
gateway:
  command:
    - -c
    - /opt/clearml/sso/nginx.conf
    - -g
    - "daemon off;"
  container_name: clearml-gateway
  entrypoint:
    - /usr/sbin/nginx
  image: clearml/server:latest
  restart: unless-stopped
  depends_on:
    apiserver:
      condition: service_healthy
    fileserver:
      condition: service_started
    oauth2-proxy:
      condition: service_started
  ports:
    - "8080:80"
  volumes:
    - ./sso/nginx.conf:/opt/clearml/sso/nginx.conf:ro
  networks:
    - backend
    - frontend
```

If the company reverse proxy already forwards `https://www.clearml.com` to this Docker host, keep:

```yaml
ports:
  - "8080:80"
```

If this Docker gateway itself must listen publicly on port 80, use:

```yaml
ports:
  - "80:80"
```

If this Docker gateway itself must terminate HTTPS, add `443:443` and mount certificates. Use that only if the team wants TLS inside this Compose stack.

## Step 10: Update `docker/compose.yaml`

Apply the same logical changes from `docker/docker-compose.yml` to:

```text
docker/compose.yaml
```

Important difference:

In `docker/docker-compose.yml`, the SSO bridge mounts:

```yaml
- ../apiserver:/opt/clearml/apiserver
```

In `docker/compose.yaml`, keep the structure consistent with that file's existing path layout. If it does not mount `../apiserver`, do not add it unless the compose file needs source-mounted apiserver code for that environment.

## Step 11: Add Windows Elasticsearch Override If Needed

Create:

```text
docker/docker-compose.local-windows.yml
```

Content:

```yaml
services:
  elasticsearch:
    volumes:
      - clearml_elastic_data:/usr/share/elasticsearch/data
      - clearml_elastic_logs:/usr/share/elasticsearch/logs

volumes:
  clearml_elastic_data:
  clearml_elastic_logs:
```

Use this file on Windows/WSL if Elasticsearch has permission or `node.lock` issues with bind mounts.

For Linux deployments where the original bind mount works, this override may not be necessary.

## Step 12: Local Secret File

Create:

```text
.env.sso
```

Example:

```env
OAUTH2_PROXY_CLIENT_ID=<real-client-id>
OAUTH2_PROXY_CLIENT_SECRET=<real-client-secret>
OAUTH2_PROXY_COOKIE_SECRET=<real-32-character-secret>
OAUTH2_PROXY_COOKIE_SECURE=true
```

Do not commit `.env.sso`.

## Step 13: Start Or Recreate Services

For Windows local testing:

```powershell
docker compose -f docker\docker-compose.yml -f docker\docker-compose.local-windows.yml up -d --force-recreate webserver sso-bridge oauth2-proxy gateway
```

For Linux server deployment:

```powershell
docker compose -f docker\docker-compose.yml up -d --force-recreate webserver sso-bridge oauth2-proxy gateway
```

If starting the whole stack:

```powershell
docker compose -f docker\docker-compose.yml up -d
```

For Windows local testing with the override:

```powershell
docker compose -f docker\docker-compose.yml -f docker\docker-compose.local-windows.yml up -d
```

## Step 14: Verify

Health check:

```powershell
curl.exe -i https://www.clearml.com/healthz
```

API check:

```powershell
curl.exe -i https://www.clearml.com/api/debug.ping
```

OAuth redirect check:

```powershell
curl.exe -I https://www.clearml.com/oauth2/start
```

Expected browser behavior:

```text
1. User opens https://www.clearml.com.
2. Browser redirects to Authentik.
3. User logs in.
4. Browser returns to https://www.clearml.com/oauth2/callback.
5. User lands on the ClearML dashboard.
```

Expected SDK configuration:

```text
api_server: https://www.clearml.com/api
web_server: https://www.clearml.com
files_server: https://www.clearml.com/files
```

## Step 15: What To Commit

Commit:

```text
.gitignore
.env.sso.example
docker/docker-compose.yml
docker/compose.yaml
docker/docker-compose.local-windows.yml
docker/sso/nginx.conf
docker/sso/oauth2-proxy.cfg
docker/sso/sso_bridge.py
docs/apply_sso_to_visibility_feature.md
```

Do not commit:

```text
.env.sso
```

Usually do not commit:

```text
apiserver/schema/services/_cache.json
```

## Minimal Review Checklist

Before opening a PR, verify:

```text
1. `.env.sso` is not staged.
2. `apiserver/schema/services/_cache.json` is not staged unless intentionally needed.
3. Authentik redirect URI exactly matches `https://www.clearml.com/oauth2/callback`.
4. `oauth2-proxy.cfg` has `cookie_secure = true` for HTTPS.
5. `sso-bridge` has `SSO_CLEARML_COOKIE_SECURE: true` for HTTPS.
6. Gateway routes `/api` and `/files` without forcing SSO.
7. Gateway routes `/` and `/oauth2/` through oauth2-proxy.
8. Custom dashboard uses `/api` and `/files` public paths.
9. Browser login reaches the dashboard.
10. ClearML SDK can reach `/api/debug.ping`.
```

## Main Files And Their Roles

```text
docker/sso/nginx.conf
```

Public gateway routing for `/`, `/oauth2/`, `/api`, and `/files`.

```text
docker/sso/oauth2-proxy.cfg
```

Authentik OIDC client configuration.

```text
docker/sso/sso_bridge.py
```

Turns Authentik-authenticated users into ClearML-authenticated sessions.

```text
docker/docker-compose.yml
```

Main Docker services and dependency wiring.

```text
docker/docker-compose.local-windows.yml
```

Optional Windows/WSL Elasticsearch volume override.

```text
.env.sso.example
```

Commit-safe template for required SSO secrets.

```text
.env.sso
```

Local secret file. Do not commit.

## Summary

To apply the SSO work to `visibility-feature`, bring over the SSO gateway, oauth2-proxy config, SSO bridge, Compose service additions, `.env.sso.example`, and `.gitignore` updates.

For a real existing company URL such as:

```text
https://www.clearml.com
```

the key production values are:

```text
Authentik redirect URI: https://www.clearml.com/oauth2/callback
oauth2-proxy redirect_url: https://www.clearml.com/oauth2/callback
oauth2-proxy cookie_secure: true
sso-bridge SSO_CLEARML_COOKIE_SECURE: true
ClearML SDK API URL: https://www.clearml.com/api
ClearML SDK files URL: https://www.clearml.com/files
```

For the company customized dashboard, replace the `webserver` image with the company dashboard image and make sure the browser-side app uses:

```text
/api
/files
```

