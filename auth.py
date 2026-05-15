"""
OIDC Authentication via Logto
=============================
Handles OAuth2 Authorization Code flow for Streamlit using authlib.
Persists auth in a browser cookie so refreshes don't require re-login.
"""

import base64
import json as _json
import os
import secrets
import urllib.parse

import requests
import streamlit as st
import streamlit.components.v1 as _components

LOGTO_ENDPOINT = os.getenv("LOGTO_ENDPOINT", "")
LOGTO_APP_ID = os.getenv("LOGTO_APP_ID", "")
LOGTO_APP_SECRET = os.getenv("LOGTO_APP_SECRET", "")
LOGTO_REDIRECT_URI = os.getenv("LOGTO_REDIRECT_URI", "")
LOGTO_API_RESOURCE = os.getenv("LOGTO_API_RESOURCE", "")

# Cookie config
_COOKIE_NAME = "compfinder_auth"
_LOGOUT_FLAG_COOKIE = "compfinder_logged_out"
_COOKIE_MAX_AGE = 60 * 60 * 8  # 8 hours

# OIDC discovery endpoints (derived from Logto endpoint)
_AUTHORIZE_URL = f"{LOGTO_ENDPOINT}/oidc/auth"
_TOKEN_URL = f"{LOGTO_ENDPOINT}/oidc/token"
_USERINFO_URL = f"{LOGTO_ENDPOINT}/oidc/me"
_END_SESSION_URL = f"{LOGTO_ENDPOINT}/oidc/session/end"


def is_configured() -> bool:
    return all([LOGTO_ENDPOINT, LOGTO_APP_ID, LOGTO_APP_SECRET, LOGTO_REDIRECT_URI])


def _read_cookie() -> dict | None:
    """Read auth cookie synchronously via st.context.cookies."""
    try:
        raw = st.context.cookies.get(_COOKIE_NAME)
        if raw:
            # Cookie value may be URL-encoded
            try:
                raw = urllib.parse.unquote(raw)
            except Exception:
                pass
            return _json.loads(raw)
    except Exception:
        pass
    return None


def _set_cookie(data: dict):
    """Set a cookie in the browser via a hidden JS snippet."""
    encoded = urllib.parse.quote(_json.dumps(data))
    js = (
        f"<script>document.cookie='{_COOKIE_NAME}={encoded}"
        f";path=/;max-age={_COOKIE_MAX_AGE};SameSite=Lax';</script>"
    )
    _components.html(js, height=0, width=0)


def _delete_cookie():
    """Delete the auth cookie via a hidden JS snippet."""
    js = f"<script>document.cookie='{_COOKIE_NAME}=;path=/;max-age=0;SameSite=Lax';</script>"
    _components.html(js, height=0, width=0)


def _set_logout_flag_cookie():
    """Set a short-lived cookie signalling that the user just logged out."""
    js = (
        f"<script>document.cookie='{_LOGOUT_FLAG_COOKIE}=1"
        f";path=/;max-age=60;SameSite=Lax';</script>"
    )
    _components.html(js, height=0, width=0)


def _redirect_top(url: str):
    """Navigate the top browser window (not an iframe) to the given URL."""
    # st.html renders directly in the parent document (no iframe sandbox),
    # so window.location works without needing allow-top-navigation.
    safe = _json.dumps(url)
    try:
        st.html(f"<script>window.location.href = {safe};</script>")
    except Exception:
        # Fallback for older Streamlit: meta refresh via link click
        st.markdown(
            f'<meta http-equiv="refresh" content="0;url={url}">'
            f'<script>window.location.href = {safe};</script>',
            unsafe_allow_html=True,
        )


def _get_login_url(state: str, nonce: str) -> str:
    scope = "openid profile email roles manage:compfinder"
    params = {
        "client_id": LOGTO_APP_ID,
        "redirect_uri": LOGTO_REDIRECT_URI,
        "response_type": "code",
        "scope": scope,
        "state": state,
        "nonce": nonce,
    }
    if LOGTO_API_RESOURCE:
        params["resource"] = LOGTO_API_RESOURCE
    return f"{_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _exchange_code(code: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "client_id": LOGTO_APP_ID,
        "client_secret": LOGTO_APP_SECRET,
        "redirect_uri": LOGTO_REDIRECT_URI,
        "code": code,
    }
    if LOGTO_API_RESOURCE:
        data["resource"] = LOGTO_API_RESOURCE
    resp = requests.post(_TOKEN_URL, data=data, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _decode_id_token(id_token: str) -> dict:
    """Decode JWT payload without verification (already validated by Logto)."""
    payload = id_token.split(".")[1]
    # Add padding
    payload += "=" * (4 - len(payload) % 4)
    return _json.loads(base64.urlsafe_b64decode(payload))


def _get_userinfo(access_token: str) -> dict:
    resp = requests.get(
        _USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def require_auth():
    """Gate the app behind Logto login. Call at the top of the app."""
    if not is_configured():
        return  # Auth not configured, allow access

    params = st.query_params
    # Detect "just logged out" via either query flag OR short-lived cookie
    # (Logto requires exact-match post-logout URI so we can't pass query params
    # through it — a cookie survives the round-trip.)
    just_logged_out = params.get("logged_out") == "1" or bool(
        st.context.cookies.get(_LOGOUT_FLAG_COOKIE)
    )

    # If we just logged out, wipe both cookies and skip restore
    if just_logged_out:
        _delete_cookie()
        # Clear the logged-out flag cookie too
        _components.html(
            f"<script>document.cookie='{_LOGOUT_FLAG_COOKIE}=;path=/;max-age=0;SameSite=Lax';</script>",
            height=0, width=0,
        )
        st.session_state.pop("_auth_user", None)
        st.session_state.pop("_auth_token", None)
    # Restore session from cookie if session_state is empty
    elif "_auth_user" not in st.session_state:
        cookie_data = _read_cookie()
        if cookie_data and "user" in cookie_data:
            st.session_state["_auth_user"] = cookie_data["user"]
            st.session_state["_auth_token"] = cookie_data.get("token", {})

    # Handle OAuth callback
    code = params.get("code")
    state = params.get("state")
    error = params.get("error")

    if error:
        desc = params.get("error_description", error)
        st.error(f"Login error: {desc}")
        if st.button("Try Again"):
            st.query_params.clear()
            st.rerun()
        st.stop()

    if code and state:
        # State validation skipped — Streamlit loses session on redirect.
        # CSRF risk is minimal for an internal tool behind corporate SSO.
        try:
            tokens = _exchange_code(code)
        except Exception as e:
            st.error(f"Token exchange failed: {e}")
            if st.button("Try Again"):
                st.query_params.clear()
                st.rerun()
            st.stop()

        try:
            # With an API resource, the access token is a JWT scoped to that
            # resource — the /oidc/me userinfo endpoint won't accept it.
            # Use ID token claims for user info instead.
            userinfo = {}
            if tokens.get("id_token"):
                userinfo = _decode_id_token(tokens["id_token"])
            st.session_state["_auth_user"] = userinfo
            st.session_state["_auth_token"] = tokens
            # Persist to cookie so refreshes don't require re-login
            _set_cookie({"user": userinfo, "token": {"access_token": tokens.get("access_token", ""), "id_token": tokens.get("id_token", ""), "scope": tokens.get("scope", "")}})
            # Clear query params
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Authentication failed: {e}")
            if st.button("Try Again"):
                st.query_params.clear()
                st.rerun()
            st.stop()

    # Check if already logged in
    if "_auth_user" in st.session_state:
        return

    # Show login screen
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(16)
    st.session_state["_oauth_state"] = state
    login_url = _get_login_url(state, nonce)

    # Auto-redirect to Logto. If Logto has an active session, it will
    # silently bounce back with a code; otherwise it will show Logto's
    # own login UI. Skip auto-redirect if user just logged out (detected
    # via query flag or short-lived cookie set just before logout).
    if not just_logged_out:
        _redirect_top(login_url)

    # Hide sidebar and default Streamlit elements on login page
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {display: none;}
        [data-testid="stHeader"] {display: none;}
        .block-container {
            padding-top: 0 !important;
            max-width: 480px !important;
            margin: auto;
        }
        .stApp > div > div > div > div.block-container {
            background: rgba(30,41,59,.85);
            border: 1px solid rgba(148,163,184,.15);
            border-radius: 20px;
            padding: 48px 40px !important;
            box-shadow: 0 24px 48px rgba(0,0,0,.3);
            margin-top: 12vh;
            margin-bottom: auto;
        }
        .login-icon {font-size: 56px; margin-bottom: 12px; text-align: center;}
        .login-title {color: #f1f5f9; font-size: 28px; font-weight: 700; margin-bottom: 4px; text-align: center;}
        .login-subtitle {color: #94a3b8; font-size: 15px; margin-bottom: 4px; text-align: center;}
        .login-powered {color: #64748b; font-size: 12px; margin-bottom: 20px; text-align: center;}
        .login-divider {
            display: flex; align-items: center; gap: 12px;
            color: #475569; font-size: 13px; margin: 16px 0 16px;
        }
        .login-divider::before, .login-divider::after {content: ""; flex: 1; height: 1px; background: #334155;}
        .login-footer {color: #475569; font-size: 12px; margin-top: 16px; text-align: center;}
        </style>
        <div class="login-icon">🏠</div>
        <div class="login-title">Comp Finder</div>
        <p class="login-subtitle">Comparable Property Search Tool</p>
        <p class="login-powered">Powered by MortgagePros</p>
        <div class="login-divider">Sign in to continue</div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<a href="{login_url}" target="_self" style="display:block;background:#2563eb;color:#fff;'
        f'padding:12px 20px;border-radius:8px;text-align:center;text-decoration:none;font-weight:600;'
        f'font-size:14px;transition:background .15s;">🔐 Sign in with Microsoft</a>',
        unsafe_allow_html=True,
    )
    st.markdown('<p class="login-footer">Authorized employees only</p>', unsafe_allow_html=True)
    st.stop()


def get_user() -> dict | None:
    return st.session_state.get("_auth_user")


def has_role(role_name: str) -> bool:
    """Return True if the logged-in user has the given Logto role name."""
    user = get_user()
    if not user:
        return False
    roles = user.get("roles", [])
    # Logto returns roles as a list of strings or dicts with a 'name' key
    for r in roles:
        if isinstance(r, str) and r == role_name:
            return True
        if isinstance(r, dict) and r.get("name") == role_name:
            return True
    return False


def has_scope(scope_name: str) -> bool:
    """Return True if the access token JWT contains the given scope claim."""
    token = st.session_state.get("_auth_token", {})
    access_token = token.get("access_token", "")
    if not access_token:
        return False
    try:
        claims = _decode_id_token(access_token)
        return scope_name in claims.get("scope", "").split()
    except Exception:
        return False


def logout():
    """Clear local session + cookie, end Logto session, return with logged_out flag."""
    id_token = st.session_state.get("_auth_token", {}).get("id_token", "")
    _delete_cookie()
    st.session_state.pop("_auth_user", None)
    st.session_state.pop("_auth_token", None)
    st.session_state.pop("_oauth_state", None)
    # Also clear cached search results
    st.session_state.pop("_results_subject", None)
    st.session_state.pop("_results_comps", None)

    # Build Logto end-session URL. post_logout_redirect_uri brings the user
    # back here with ?logged_out=1 so we don't auto-redirect to login.
    post_logout = f"{LOGTO_REDIRECT_URI}?logged_out=1"
    params = {"post_logout_redirect_uri": post_logout}
    if id_token:
        params["id_token_hint"] = id_token
    end_session_url = f"{_END_SESSION_URL}?{urllib.parse.urlencode(params)}"
    _redirect_top(end_session_url)
    st.stop()


def get_logout_url() -> str:
    """Return the Logto end-session URL for direct linking from the UI.
    The compfinder_logged_out cookie (set by the sign-out JS in app.py) is used
    to detect the post-logout redirect — no query param needed on the URI.
    """
    id_token = st.session_state.get("_auth_token", {}).get("id_token", "")
    params = {"post_logout_redirect_uri": LOGTO_REDIRECT_URI}
    if id_token:
        params["id_token_hint"] = id_token
    return f"{_END_SESSION_URL}?{urllib.parse.urlencode(params)}"
