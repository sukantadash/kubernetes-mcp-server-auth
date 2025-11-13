# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

from flask import Flask, render_template, redirect, url_for
from modules.topbar import get_user_info
import os
import json

app = Flask(__name__)
# No session cookies - application is stateless
# OAuth-proxy cookies handle authentication
# Chat history stored client-side (localStorage)
app.config['SESSION_COOKIE_ENABLED'] = False  # Disable Flask session cookies
# Set a minimal secret key (still required even if sessions disabled)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'fallback-key-for-dev-only')

# Configure logging
import logging

# Use logs directory if it exists, otherwise current directory
log_dir = os.environ.get('LOG_DIR', '/app/logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'app.log')

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file)
    ]
)
logger = logging.getLogger(__name__)
logger.info("Flask app starting...")

# Add tojson filter for templates (Jinja2 doesn't have this by default in all versions)
@app.template_filter('tojson')
def tojson_filter(obj):
    return json.dumps(obj, indent=2)

# Register blueprints
from routes.playground import playground_bp
from routes.evaluations import evaluations_bp
from routes.distribution import distribution_bp
from routes.profile import profile_bp

app.register_blueprint(playground_bp)
app.register_blueprint(evaluations_bp)
app.register_blueprint(distribution_bp)
app.register_blueprint(profile_bp)


@app.route('/')
def index():
    """Redirect to default playground page (Chat)"""
    return redirect(url_for('playground.chat'))


@app.route('/health')
def health():
    """Health check endpoint - no authentication required"""
    return "OK", 200


@app.route('/_stcore/<path:path>')
def handle_stcore(path):
    """Handle legacy Streamlit paths - return 404 or redirect"""
    # Legacy Streamlit paths - return empty response to avoid errors
    return "", 404


@app.route('/debug/auth')
def debug_auth():
    """Debug endpoint to check authentication headers (remove in production)"""
    from flask import request
    auth_headers = {
        k: v[:100] + "..." if len(v) > 100 else v 
        for k, v in request.headers.items() 
        if 'auth' in k.lower() or 'token' in k.lower() or 'x-' in k.lower()
    }
    # No session - tokens always from headers
    return json.dumps({
        "auth_headers": auth_headers,
        "jwt_token_in_session": "N/A - No Flask session cookies (stateless)",
        "user_info": get_user_info()
    }, indent=2), 200, {'Content-Type': 'application/json'}


@app.context_processor
def inject_user_info():
    """Make user_info and logout_url available to all templates"""
    from modules.topbar import get_logout_url
    return dict(user_info=get_user_info(), logout_url=get_logout_url())


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8501)
