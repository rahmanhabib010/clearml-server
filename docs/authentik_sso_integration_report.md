# Authentik SSO Integration Report

Date: 2026-04-28

Branch used for this work: `SSO-Integration`

Baseline branch: `visibility-feature`

## Executive Summary

This work added an Authentik/OIDC login path in front of ClearML while keeping the ClearML backend services running in Docker Compose.

The dashboard currently visible at `http://localhost:8080/dashboard` is the ClearML web UI that is already bundled inside the Docker image `clearml/server:latest`. We are not yet serving the custom frontend from `https://github.com/rahmanhabib010/clearml_webapp_customized`.

The current browser flow is:

```text
Browser
  -> http://localhost:8080
  -> clearml-gateway nginx
  -> oauth2-proxy
  -> Authentik OIDC login
  -> sso-bridge
  -> clearml-webserver
  -> ClearML dashboard
```

The current API/files flow is:

```text
ClearML frontend or SDK
  -> http://localhost:8080/api
  -> clearml-gateway nginx
  -> clearml-apiserver:8008

ClearML files
  -> http://localhost:8080/files
  -> clearml-gateway nginx
  -> clearml-fileserver:8081
```

## What Changed From `visibility-feature`

The `SSO-Integration` branch currently has the SSO work as local working-tree changes. At the time of this report, there are no committed branch-history differences from `visibility-feature`; the implementation exists as modified and new files.

Intentional files changed or added:

```text
.gitignore
.env.sso.example
docker/docker-compose.yml
docker/compose.yaml
docker/docker-compose.local-windows.yml
docker/sso/nginx.conf
docker/sso/oauth2-proxy.cfg
docker/sso/sso_bridge.py
docs/authentik_sso_integration_report.md
```

Runtime-generated or not-intended-for-review file:

```text
apiserver/schema/services/_cache.json
```

That cache file was modified by ClearML runtime activity and should normally not be treated as part of the SSO implementation unless there is a separate reason to regenerate schema cache.

## Frontend Clarification

The frontend being displayed right now is not coming from a locally running React/Angular/Vite dev server and is not coming from the GitHub repo `clearml_webapp_customized`.

It is coming from this Compose service:

```yaml
webserver:
  command:
    - webserver
  image: clearml/server:latest
```

That image already contains ClearML's compiled web application. When the browser reaches `/dashboard`, it means Authentik login succeeded, the SSO bridge issued a ClearML session cookie, and the bundled ClearML frontend booted successfully.

To use the custom frontend repo later, Docker Compose must be changed so the `webserver` service uses an image built from that custom frontend, or a separate frontend container must be added and wired behind the same gateway.

## New Services Added

### `clearml-webserver`

This serves the bundled ClearML UI from `clearml/server:latest`.

It does not expose host port `8080` directly anymore. Instead, traffic goes through the gateway.

### `clearml-sso-bridge`

This is a Flask bridge at `docker/sso/sso_bridge.py`.

Responsibilities:

```text
1. Receive authenticated user headers from oauth2-proxy.
2. Extract and validate the user email.
3. Create or reuse a ClearML company/user for that email.
4. Generate a ClearML JWT using ClearML's own AuthBLL.
5. Set the ClearML session cookie `clearml_token_basic`.
6. Proxy the frontend request to `clearml-webserver`.
```

Important behavior:

```text
X-Forwarded-Email is treated as the primary identity.
Each SSO email gets its own company by default.
The default role is `user`.
The ClearML session cookie is HTTP-only and SameSite=Lax.
```

Configurable environment variables:

```text
SSO_CLEARML_COMPANY_ID
SSO_CLEARML_COOKIE_DOMAIN
SSO_CLEARML_COOKIE_SECURE
SSO_CLEARML_DEFAULT_ROLE
SSO_CLEARML_TOKEN_EXPIRATION_SEC
SSO_CLEARML_WORKSPACE_MODE
CLEARML_WEB_UPSTREAM
CLEARML_SERVER_SUB_PATH
```

### `clearml-oauth2-proxy`

This service handles OIDC with Authentik.

Config file:

```text
docker/sso/oauth2-proxy.cfg
```

Important settings:

```text
provider = "oidc"
oidc_issuer_url = "https://authentik.ccixgtestbed.org/application/o/clearml/"
redirect_url = "http://localhost:8080/oauth2/callback"
scope = "openid email profile"
upstreams = [ "http://sso-bridge:5000/" ]
code_challenge_method = "S256"
session_cookie_minimal = true
cookie_refresh = "0"
```

The client ID, client secret, and cookie secret are loaded from `.env.sso`.

### `clearml-gateway`

This is the public entrypoint on host port `8080`.

Config file:

```text
docker/sso/nginx.conf
```

Routing:

```text
/healthz  -> local nginx health response
/api      -> clearml-apiserver:8008
/files    -> clearml-fileserver:8081
/oauth2/  -> oauth2-proxy:4180
/         -> oauth2-proxy:4180 -> sso-bridge -> clearml-webserver
```

The gateway also includes larger header buffers. This was required because OIDC callback cookies/headers were large enough to cause `502 Bad Gateway` before the buffer increase.

## Compose Changes

The main Compose files were updated:

```text
docker/docker-compose.yml
docker/compose.yaml
```

Major changes:

```text
1. Removed obsolete Compose `version` from docker/docker-compose.yml.
2. Added healthchecks for Elasticsearch and apiserver.
3. Changed dependent services to wait for healthy Elasticsearch/apiserver.
4. Added webserver, sso-bridge, oauth2-proxy, and gateway services.
5. Kept apiserver on port 8008 and fileserver on port 8081.
6. Made gateway the browser entrypoint on port 8080.
7. Loaded oauth2-proxy secrets from ../.env.sso.
```

Windows-specific override:

```text
docker/docker-compose.local-windows.yml
```

This override moves Elasticsearch data/logs into Docker named volumes:

```yaml
services:
  elasticsearch:
    volumes:
      - clearml_elastic_data:/usr/share/elasticsearch/data
      - clearml_elastic_logs:/usr/share/elasticsearch/logs
```

This was added because Elasticsearch on Windows/WSL hit `node.lock` permission failures when using the original `/opt/clearml/data/elastic_7` bind mount.

## Environment And Secrets

Added:

```text
.env.sso.example
```

Local-only file:

```text
.env.sso
```

`.env.sso` is ignored by Git and must not be committed. It contains the real Authentik client secret and OAuth cookie secret.

Required values:

```env
OAUTH2_PROXY_CLIENT_ID=<authentik-client-id>
OAUTH2_PROXY_CLIENT_SECRET=<authentik-client-secret>
OAUTH2_PROXY_COOKIE_SECRET=<16-or-24-or-32-character-secret>
```

For localhost testing:

```env
OAUTH2_PROXY_REDIRECT_URL=http://localhost:8080/oauth2/callback
OAUTH2_PROXY_COOKIE_SECURE=false
```

For HTTPS production or shared server testing:

```env
OAUTH2_PROXY_REDIRECT_URL=https://<clearml-host>/oauth2/callback
OAUTH2_PROXY_COOKIE_SECURE=true
```

The same redirect URL must be registered in Authentik.

## Problems Found And Fixes Applied

### Elasticsearch Failed To Start

Symptom:

```text
failed to obtain node locks
AccessDeniedException: /usr/share/elasticsearch/data/node.lock
```

Cause:

Windows/WSL bind-mounted Elasticsearch data directory had permission/lock behavior Elasticsearch did not accept.

Fix:

Use `docker/docker-compose.local-windows.yml` with Docker named volumes for Elasticsearch data/logs.

### Apiserver Could Not Connect To Elasticsearch

Symptom:

```text
Connection refused: HTTPConnection(host='elasticsearch', port=9200)
```

Cause:

Apiserver started before Elasticsearch was actually ready.

Fix:

Added Elasticsearch and apiserver healthchecks and changed dependencies to wait for health.

### oauth2-proxy Missing Cookie Secret

Symptom:

```text
missing setting: cookie-secret
```

Cause:

`.env.sso` was missing or did not contain `OAUTH2_PROXY_COOKIE_SECRET`.

Fix:

Added `.env.sso.example`, added `.env.sso` locally, and ignored real `.env*` files in `.gitignore`.

### Authentik Discovery Failed

Symptom:

oauth2-proxy could not complete OIDC discovery.

Cause:

A hardcoded `extra_hosts` mapping pointed the Authentik hostname at the wrong internal IP.

Fix:

Removed the hardcoded host mapping and allowed normal DNS resolution.

### Callback Returned 502

Symptom:

After Authentik login, browser showed:

```text
502 Bad Gateway
```

Cause:

OIDC callback headers/cookies exceeded default nginx proxy buffer sizes.

Fix:

Increased nginx client/proxy header buffers in `docker/sso/nginx.conf`.

### Browser Went Blank After Login

Symptoms:

```text
HTTP 200 responses, but blank/white page in browser.
```

Causes found:

```text
1. The bridge forwarded `Content-Encoding: gzip` after Python requests had already decompressed the body.
2. ClearML webserver served env.js with literal `${CLEARML_SERVER_SUB_PATH}`.
```

Fixes:

```text
1. sso_bridge.py now strips hop-by-hop/body-specific headers including Content-Encoding and Content-Length.
2. sso_bridge.py now serves a clean /env.js with `window.__env.subPath = ""`.
3. Root page and env/config responses now receive no-store cache headers.
```

## Verification Completed

Commands used during verification:

```powershell
docker compose -f docker\docker-compose.yml -f docker\docker-compose.local-windows.yml config --quiet
docker compose -f docker\docker-compose.yml -f docker\docker-compose.local-windows.yml ps -a
curl.exe -i http://localhost:8080/healthz
curl.exe -i http://localhost:8080/api/debug.ping
docker exec clearml-gateway curl -sS http://sso-bridge:5000/env.js
```

Verified results:

```text
Gateway health endpoint returns 200 ok.
API debug ping returns 200.
Authentik redirects and login work.
oauth2-proxy starts successfully.
sso-bridge starts successfully.
SSO-created ClearML token is accepted by users.get_current_user.
Browser reaches ClearML dashboard after login.
```

## Team Impact

Existing running containers on teammates' machines are not changed just because these files exist in this branch. Docker containers only change after they pull/apply the branch and run Compose commands that recreate services.

If these changes are merged or a teammate switches to this branch and runs Compose, they will be affected in these ways:

```text
1. Browser access on localhost:8080 goes through Authentik SSO.
2. They need a valid `.env.sso` file for oauth2-proxy to start correctly.
3. Without `.env.sso`, oauth2-proxy may fail and the gateway browser path will not work.
4. The apiserver direct port 8008 remains exposed.
5. The fileserver direct port 8081 remains exposed.
6. On Windows, using docker-compose.local-windows.yml creates separate Docker named volumes for Elasticsearch.
7. Existing Elasticsearch data stored in the old bind mount will not automatically appear in the new named volume.
```

Important teammate note:

If teammates are not ready to use SSO, do not merge these Compose changes into a shared default branch as-is. Instead, keep this as an SSO-specific branch or move the SSO services into a separate override file such as `docker-compose.sso.yml`.

## Fresh Branch Implementation Instructions

Starting from `visibility-feature`:

```powershell
git checkout visibility-feature
git checkout -b SSO-Integration
```

Apply or copy these files from this branch:

```text
.gitignore
.env.sso.example
docker/docker-compose.yml
docker/compose.yaml
docker/docker-compose.local-windows.yml
docker/sso/nginx.conf
docker/sso/oauth2-proxy.cfg
docker/sso/sso_bridge.py
```

Create the local secret file:

```powershell
Copy-Item .env.sso.example .env.sso
```

Edit `.env.sso` with real values:

```env
OAUTH2_PROXY_CLIENT_ID=<authentik-client-id>
OAUTH2_PROXY_CLIENT_SECRET=<authentik-client-secret>
OAUTH2_PROXY_COOKIE_SECRET=<16-or-24-or-32-character-secret>
```

For local Windows/WSL testing, start with:

```powershell
docker compose -f docker\docker-compose.yml -f docker\docker-compose.local-windows.yml up -d
```

For Linux/macOS where the original Elasticsearch bind mount works:

```powershell
docker compose -f docker\docker-compose.yml up -d
```

Check services:

```powershell
docker compose -f docker\docker-compose.yml -f docker\docker-compose.local-windows.yml ps -a
curl.exe -i http://localhost:8080/healthz
curl.exe -i http://localhost:8080/api/debug.ping
```

Open:

```text
http://localhost:8080
```

Expected result:

```text
1. Browser redirects to Authentik.
2. User logs in.
3. Browser returns to /oauth2/callback.
4. oauth2-proxy forwards to sso-bridge.
5. sso-bridge creates/reuses a ClearML user.
6. Browser lands on /dashboard.
```

## Authentik Setup Requirements

Authentik application/provider must allow this redirect URI for local testing:

```text
http://localhost:8080/oauth2/callback
```

For a real hosted deployment, replace it with:

```text
https://<clearml-host>/oauth2/callback
```

Required scopes:

```text
openid
email
profile
```

The bridge expects oauth2-proxy to forward an email header. The primary header used is:

```text
X-Forwarded-Email
```

## Custom Frontend Next Step

The customized frontend repo is:

```text
https://github.com/rahmanhabib010/clearml_webapp_customized
```

That repo is not currently wired into this Docker Compose setup.

To use it, build or publish a Docker image for that frontend and change the `webserver` service from:

```yaml
image: clearml/server:latest
command:
  - webserver
```

to something like:

```yaml
image: <custom-clearml-webapp-image>:<tag>
```

The custom frontend must still call the backend through the gateway:

```text
/api
/files
```

Do not point the browser frontend directly at internal Docker service names like `apiserver:8008`; those names only resolve inside Docker.

## Recommended Cleanup Before Commit

Before committing this branch, review:

```text
apiserver/schema/services/_cache.json
```

Unless intentionally regenerated, leave it out of the SSO commit.

Also verify `.env.sso` is not staged:

```powershell
git status --short
```

Safe files to stage for the SSO implementation:

```powershell
git add .gitignore .env.sso.example docker/docker-compose.yml docker/compose.yaml docker/docker-compose.local-windows.yml docker/sso docs/authentik_sso_integration_report.md
```

Do not stage:

```powershell
git add .env.sso
git add apiserver/schema/services/_cache.json
```

