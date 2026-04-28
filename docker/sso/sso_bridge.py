#!/usr/bin/env python3
import logging
import os
import re
import sys
import json
from datetime import datetime
from hashlib import sha256
from http.cookies import SimpleCookie

from flask import Flask, Response, abort, request
import requests


CLEARML_ROOT = os.getenv("CLEARML_ROOT", "/opt/clearml")
if CLEARML_ROOT not in sys.path:
    sys.path.insert(0, CLEARML_ROOT)


LOG_LEVEL = os.getenv("SSO_BRIDGE_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(asctime)s] [%(levelname)s] [sso_bridge] %(message)s",
)
log = logging.getLogger("sso_bridge")

app = Flask(__name__)

_db_initialized = False

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default=None):
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


COOKIE_NAME = os.getenv("CLEARML_SESSION_COOKIE_NAME", "clearml_token_basic")
COOKIE_SECURE = env_bool("SSO_CLEARML_COOKIE_SECURE", False)
COOKIE_HTTPONLY = env_bool("SSO_CLEARML_COOKIE_HTTPONLY", True)
COOKIE_DOMAIN = os.getenv("SSO_CLEARML_COOKIE_DOMAIN") or None
COOKIE_SAMESITE = os.getenv("SSO_CLEARML_COOKIE_SAMESITE", "Lax")
TOKEN_EXPIRATION_SEC = env_int("SSO_CLEARML_TOKEN_EXPIRATION_SEC")

CLEARML_WEB_UPSTREAM = os.getenv("CLEARML_WEB_UPSTREAM", "http://webserver").rstrip("/")
CLEARML_SERVER_SUB_PATH = os.getenv("CLEARML_SERVER_SUB_PATH", "")
DEFAULT_ROLE = os.getenv("SSO_CLEARML_DEFAULT_ROLE", "user")
DEFAULT_COMPANY_ID = os.getenv("SSO_CLEARML_COMPANY_ID")
WORKSPACE_MODE = os.getenv("SSO_CLEARML_WORKSPACE_MODE", "company").strip().lower()


def init_db():
    global _db_initialized
    if _db_initialized:
        return

    os.makedirs("/var/log/clearml", exist_ok=True)

    from apiserver.database import db

    db.initialize()
    _db_initialized = True
    log.info("Connected to ClearML MongoDB")


def first_header(*names):
    for name in names:
        value = request.headers.get(name)
        if value:
            return value.split(",")[0].strip()
    return ""


def normalize_email(value):
    email = (value or "").strip().lower()
    if not EMAIL_RE.match(email):
        return ""
    return email


def display_name_from_headers(email):
    value = first_header(
        "X-Forwarded-Name",
        "X-Auth-Request-Preferred-Username",
        "X-Forwarded-Preferred-Username",
        "X-Auth-Request-User",
        "X-Forwarded-User",
    )
    return value or email


def company_id_for_email(email):
    if DEFAULT_COMPANY_ID:
        return DEFAULT_COMPANY_ID
    if WORKSPACE_MODE in {"shared", "default"}:
        from apiserver.config_repo import config

        return config.get("apiserver.default_company")
    return sha256(f"clearml-sso:{email}".encode("utf-8")).hexdigest()[:32]


def ensure_company(company_id, email):
    from apiserver.bll.queue import QueueBLL
    from apiserver.database.model.company import Company
    from apiserver.database.model.queue import Queue

    company_name = f"sso-{email}"[:120]
    if not Company.objects(id=company_id).only("id").first():
        Company(id=company_id, name=company_name).save()
        log.info("Created ClearML company %s for SSO email %s", company_id, email)

    if not Queue.objects(company=company_id, system_tags="default").only("id").first():
        QueueBLL.create(company_id, name="default", system_tags=["default"])


def ensure_clearml_user(email):
    init_db()

    from apiserver.apimodels.auth import CreateUserRequest
    from apiserver.bll.auth import AuthBLL
    from apiserver.database.model.auth import User

    user = User.objects(email=email).first()
    now = datetime.utcnow()

    if user:
        User.objects(id=user.id).update_one(set__validated=now)
        return User.objects(id=user.id).first()

    company_id = company_id_for_email(email)
    ensure_company(company_id, email)

    name = display_name_from_headers(email)
    given_name = first_header("X-Forwarded-Given-Name", "X-Auth-Request-Given-Name")
    family_name = first_header("X-Forwarded-Family-Name", "X-Auth-Request-Family-Name")

    request_model = CreateUserRequest(
        name=name,
        company=company_id,
        role=DEFAULT_ROLE,
        email=email,
        given_name=given_name or None,
        family_name=family_name or None,
    )
    user_id = AuthBLL.create_user(request_model)
    User.objects(id=user_id).update_one(set__autocreated=True, set__validated=now)
    log.info("Created ClearML user %s for SSO email %s", user_id, email)
    return User.objects(id=user_id).first()


def existing_token_for_email(email):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    try:
        init_db()
        from apiserver.database.model.auth import User
        from apiserver.service_repo.auth import Token

        identity = Token.from_encoded_token(token).identity
        user = User.objects(id=identity.user).only("email").first()
        if user and (user.email or "").lower() == email:
            return token
    except Exception as ex:
        log.debug("Ignoring invalid ClearML session cookie: %s", ex)

    return None


def create_clearml_token(user):
    from apiserver.bll.auth import AuthBLL

    return AuthBLL.get_token_for_user(
        user_id=user.id,
        company_id=user.company,
        expiration_sec=TOKEN_EXPIRATION_SEC,
    ).token


def target_url():
    path = request.path or "/"
    query = request.query_string.decode("utf-8", errors="ignore")
    url = f"{CLEARML_WEB_UPSTREAM}{path}"
    if query:
        url = f"{url}?{query}"
    return url


def proxied_request_headers(token):
    headers = {}
    for key, value in request.headers.items():
        lower = key.lower()
        if lower in HOP_BY_HOP_HEADERS or lower == "host":
            continue
        if lower == "accept-encoding":
            continue
        headers[key] = value

    cookie = SimpleCookie()
    for key, value in request.cookies.items():
        cookie[key] = value
    cookie[COOKIE_NAME] = token
    headers["Cookie"] = "; ".join(f"{m.key}={m.value}" for m in cookie.values())
    headers["Host"] = os.getenv("CLEARML_WEB_HOST_HEADER", "webserver")
    return headers


def no_store(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def env_js_response():
    body = "\n".join(
        [
            "(function (window) {",
            "  window.__env = window.__env || {};",
            f"  window.__env.subPath = {json.dumps(CLEARML_SERVER_SUB_PATH)};",
            "}(this));",
            "",
        ]
    )
    return no_store(Response(body, mimetype="application/javascript"))


def proxy_to_clearml_web(token):
    if request.method in {"GET", "HEAD"} and request.path == "/env.js":
        return env_js_response()

    upstream_response = requests.request(
        method=request.method,
        url=target_url(),
        headers=proxied_request_headers(token),
        data=request.get_data(),
        allow_redirects=False,
        stream=True,
        timeout=(5, 300),
    )

    response = Response(
        upstream_response.iter_content(chunk_size=64 * 1024),
        status=upstream_response.status_code,
    )
    for key, value in upstream_response.headers.items():
        if key.lower() not in HOP_BY_HOP_HEADERS:
            response.headers[key] = value
    if request.path in {"/", "/env.js"} or request.path.startswith("/config/"):
        no_store(response)
    return response


def set_clearml_cookie(response, token):
    kwargs = {
        "httponly": COOKIE_HTTPONLY,
        "secure": COOKIE_SECURE,
        "samesite": COOKIE_SAMESITE,
        "path": "/",
    }
    if COOKIE_DOMAIN:
        kwargs["domain"] = COOKIE_DOMAIN
    if TOKEN_EXPIRATION_SEC:
        kwargs["max_age"] = TOKEN_EXPIRATION_SEC
    response.set_cookie(COOKIE_NAME, token, **kwargs)


@app.get("/__sso/health")
def health():
    return "ok\n"


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def bridge(path):
    email = normalize_email(
        first_header(
            "X-Forwarded-Email",
            "X-Auth-Request-Email",
            "X-Forwarded-User",
            "X-Auth-Request-User",
        )
    )
    if not email:
        abort(401, "missing authenticated email header from oauth2-proxy")

    token = existing_token_for_email(email)
    new_token = False
    if not token:
        user = ensure_clearml_user(email)
        token = create_clearml_token(user)
        new_token = True

    response = proxy_to_clearml_web(token)
    if new_token:
        set_clearml_cookie(response, token)
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
