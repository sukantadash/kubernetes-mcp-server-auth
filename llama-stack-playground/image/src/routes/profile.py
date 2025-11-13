# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

from flask import Blueprint, render_template, request, redirect, url_for
from modules.api import llama_stack_api
from modules.topbar import decode_jwt_token, clear_session, get_logout_url
import os
from urllib.parse import quote

profile_bp = Blueprint('profile', __name__, url_prefix='/profile')


@profile_bp.route('/', methods=['GET'])
def index():
    """User profile page"""
    jwt_token = llama_stack_api._get_jwt_token()
    
    token_data = None
    if jwt_token:
        token_data = decode_jwt_token(jwt_token)
    
    # Get request headers for debugging
    headers = {}
    if hasattr(request, 'headers'):
        headers = dict(request.headers)
    
    logout_url = get_logout_url()
    endpoint = os.environ.get('LLAMA_STACK_ENDPOINT', 'http://localhost:8321')
    
    return render_template('profile/index.html',
                         jwt_token=jwt_token,
                         token_data=token_data,
                         headers=headers,
                         logout_url=logout_url,
                         endpoint=endpoint)


@profile_bp.route('/logout', methods=['POST', 'GET'])
def logout():
    """
    Handle logout by redirecting to oauth2-proxy's sign_out endpoint.
    This endpoint will:
    1. Clear all oauth2-proxy cookies
    2. Redirect to Keycloak logout (which clears Keycloak session)
    3. Keycloak will then redirect back to the app
    """
    clear_session()
    logout_url = get_logout_url()
    # Use absolute URL for redirect to ensure oauth2-proxy can handle it
    return redirect(logout_url)

