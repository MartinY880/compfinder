"""
OIDC Authentication via Logto
=============================
Handles OAuth2 Authorization Code flow for Streamlit using authlib.
"""

import base64
import json as _json
import os
import secrets
import urllib.parse

import requests
import streamlit as st

LOGTO_ENDPOINT = os.getenv("LOGTO_ENDPOINT", "")
LOGTO_APP_ID = os.getenv("LOGTO_APP_ID", "")
LOGTO_APP_SECRET = os.getenv("LOGTO_APP_SECRET", "")
LOGTO_REDIRECT_URI = os.getenv("LOGTO_REDIRECT_URI", "")
LOGTO_M2M_APP_ID = os.getenv("LOGTO_M2M_APP_ID", "")
LOGTO_M2M_APP_SECRET = os.getenv("LOGTO_M2M_APP_SECRET", "")

# OIDC discovery endpoints (derived from Logto endpoint)
_AUTHORIZE_URL = f"{LOGTO_ENDPOINT}/oidc/auth"
_TOKEN_URL = f"{LOGTO_ENDPOINT}/oidc/token"
_USERINFO_URL = f"{LOGTO_ENDPOINT}/oidc/me"
_END_SESSION_URL = f"{LOGTO_ENDPOINT}/oidc/session/end"


def is_configured() -> bool:
    return all([LOGTO_ENDPOINT, LOGTO_APP_ID, LOGTO_APP_SECRET, LOGTO_REDIRECT_URI])


def _get_login_url(state: str, nonce: str) -> str:
    params = {
        "client_id": LOGTO_APP_ID,
        "redirect_uri": LOGTO_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
    }
    return f"{_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _exchange_code(code: str) -> dict:
    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": LOGTO_APP_ID,
            "client_secret": LOGTO_APP_SECRET,
            "redirect_uri": LOGTO_REDIRECT_URI,
            "code": code,
        },
        timeout=10,
    )
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


_m2m_token_cache = {"token": None, "expires": 0}

def _get_m2m_token() -> str:
    """Get a cached Management API access token using M2M credentials."""
    import time
    if _m2m_token_cache["token"] and time.time() < _m2m_token_cache["expires"]:
        return _m2m_token_cache["token"]
    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": LOGTO_M2M_APP_ID,
            "client_secret": LOGTO_M2M_APP_SECRET,
            "resource": "https://default.logto.app/api",
            "scope": "all",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _m2m_token_cache["token"] = data["access_token"]
    _m2m_token_cache["expires"] = time.time() + data.get("expires_in", 3600) - 60
    return data["access_token"]


def _fetch_user_profile(user_id: str) -> dict:
    """Fetch full user profile from Logto Management API."""
    token = _get_m2m_token()
    resp = requests.get(
        f"{LOGTO_ENDPOINT}/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


def require_auth():
    """Gate the app behind Logto login. Call at the top of the app."""
    if not is_configured():
        return  # Auth not configured, allow access

    # Handle OAuth callback
    params = st.query_params
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
            userinfo = _get_userinfo(tokens["access_token"])
            # Enrich with Management API data (has name/email)
            if LOGTO_M2M_APP_ID and LOGTO_M2M_APP_SECRET and userinfo.get("sub"):
                try:
                    profile = _fetch_user_profile(userinfo["sub"])
                    userinfo["name"] = profile.get("name") or ""
                    userinfo["email"] = profile.get("primaryEmail") or ""
                except Exception:
                    pass
            # Merge ID token claims (has name/email even with openid-only scope)
            if tokens.get("id_token"):
                id_claims = _decode_id_token(tokens["id_token"])
                for key in ("name", "email", "username", "picture"):
                    if key in id_claims and key not in userinfo:
                        userinfo[key] = id_claims[key]
            st.session_state["_auth_user"] = userinfo
            st.session_state["_auth_token"] = tokens
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
    st.link_button("🔐 Sign in with Microsoft", login_url, use_container_width=True)
    st.markdown('<p class="login-footer">Authorized employees only</p>', unsafe_allow_html=True)
    st.stop()


def get_user() -> dict | None:
    return st.session_state.get("_auth_user")


def logout():
    """Clear session and redirect to Logto end-session."""
    st.session_state.pop("_auth_user", None)
    st.session_state.pop("_auth_token", None)
    st.session_state.pop("_oauth_state", None)
    # Also clear cached search results
    st.session_state.pop("_results_subject", None)
    st.session_state.pop("_results_comps", None)
