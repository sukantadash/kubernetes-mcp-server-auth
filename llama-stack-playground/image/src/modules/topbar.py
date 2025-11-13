# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

import base64
import json
import os
from modules.api import llama_stack_api


def decode_jwt_token(token: str) -> dict | None:
    """Decode JWT token to extract payload information"""
    try:
        # JWT tokens have 3 parts separated by dots: header.payload.signature
        parts = token.split('.')
        if len(parts) != 3:
            return None
        
        # Decode the payload (second part)
        payload = parts[1]
        
        # Add padding if needed for base64 decoding
        padding = 4 - (len(payload) % 4)
        if padding != 4:
            payload += '=' * padding
        
        # Decode base64 URL
        decoded_bytes = base64.urlsafe_b64decode(payload)
        return json.loads(decoded_bytes)
    except Exception:
        return None


def get_user_info():
    """Extract user information from JWT token (stateless - no session caching)"""
    jwt_token = llama_stack_api._get_jwt_token()
    
    if not jwt_token:
        # Fallback: Return generic authenticated user
        return {
            "username": "Authenticated User",
            "email": "",
            "name": "User",
            "sub": "",
            "groups": [],
        }
    
    token_data = decode_jwt_token(jwt_token)
    if not token_data:
        # Fallback if decode fails
        return {
            "username": "Authenticated User",
            "email": "",
            "name": "User",
            "sub": "",
            "groups": [],
        }
    
    user_info = {
        "username": token_data.get("preferred_username", token_data.get("username", "User")),
        "email": token_data.get("email", ""),
        "name": token_data.get("name", "User"),
        "sub": token_data.get("sub", ""),
        "groups": token_data.get("groups", []),
    }
    
    # Stateless - no session caching
    return user_info


def clear_session():
    """No-op - stateless application, no session to clear"""
    pass


def get_logout_url():
    """
    Generate logout URL that:
    1. Uses oauth2-proxy's /oauth2/sign_out endpoint to clear cookies
    2. Redirects to Keycloak logout to clear Keycloak session
    3. Finally redirects back to the app
    
    The oauth2-proxy sign_out endpoint clears all oauth2-proxy cookies,
    then redirects to the specified URL (Keycloak logout).
    """
    from urllib.parse import quote, urlencode
    
    keycloak_url = os.environ.get("KEYCLOAK_URL", "")
    realm = os.environ.get("KEYCLOAK_REALM", "openshift")
    app_url = os.environ.get("APP_URL", "")
    
    # Build Keycloak logout URL with redirect back to app
    keycloak_logout_url = f"{keycloak_url}/realms/{realm}/protocol/openid-connect/logout"
    if app_url:
        # URL encode the redirect_uri parameter properly
        params = {"redirect_uri": app_url}
        keycloak_logout_url += "?" + urlencode(params)
    
    # Use oauth2-proxy's sign_out endpoint which clears cookies first
    # The 'rd' parameter tells oauth2-proxy where to redirect after clearing cookies
    # This ensures cookies are deleted before redirecting to Keycloak
    oauth2_proxy_signout = "/oauth2/sign_out"
    if keycloak_logout_url:
        # URL encode the entire Keycloak logout URL for the rd parameter
        keycloak_logout_encoded = quote(keycloak_logout_url, safe='')
        oauth2_proxy_signout += f"?rd={keycloak_logout_encoded}"
    
    return oauth2_proxy_signout



