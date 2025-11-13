# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

import os
import logging
import json
from flask import request
from llama_stack_client import LlamaStackClient

logger = logging.getLogger(__name__)


class LlamaStackApi:
    def __init__(self):
        self.base_url = os.environ.get("LLAMA_STACK_ENDPOINT", "http://localhost:8321")
        self._mcp_endpoints_cache = None  # Cache for MCP endpoints (refreshed per request)
    
    def _get_jwt_token(self, raise_if_invalid: bool = False) -> str | None:
        """Extract JWT token from OAuth proxy headers (stateless - no session caching)
        
        No validation is performed - token is passed through as-is.
        Let oauth2-proxy and the backend handle token validation.
        
        Args:
            raise_if_invalid: Not used - kept for API compatibility
        
        Returns:
            JWT token string if found, None if not found
        """
        # Stateless approach: Always read from headers (oauth-proxy provides fresh token on each request)
        # No validation - just extract and return the token
        
        try:
            headers = request.headers if hasattr(request, 'headers') else {}
            
            # Log all headers that might contain tokens (for debugging)
            auth_headers = {}
            for k, v in headers.items():
                k_lower = k.lower()
                if any(keyword in k_lower for keyword in ['auth', 'token', 'access', 'bearer', 'x-auth', 'x-forwarded']):
                    auth_headers[k] = v[:100] + "..." if len(v) > 100 else v
            
            if auth_headers:
                logger.info(f"Auth-related headers received: {list(auth_headers.keys())}")
            
            # Try X-Forwarded-Access-Token header (primary - set by oauth2-proxy with --pass-access-token)
            token = (headers.get("X-Forwarded-Access-Token") or 
                    headers.get("x-forwarded-access-token"))
            if token:
                logger.info(f"JWT token found in X-Forwarded-Access-Token header (token length: {len(token)})")
                logger.info(f"JWT token (complete): {token}")
                return token
            
            # Try X-Auth-Request-Access-Token header (alternative - set by --set-xauthrequest)
            token = (headers.get("X-Auth-Request-Access-Token") or 
                    headers.get("x-auth-request-access-token"))
            if token:
                logger.info(f"JWT token found in X-Auth-Request-Access-Token header (token length: {len(token)})")
                logger.info(f"JWT token (complete): {token}")
                return token
            
            # Try Authorization header (set by oauth2-proxy with --set-authorization-header)
            auth_header = (headers.get("Authorization") or 
                          headers.get("authorization") or 
                          headers.get("X-Forwarded-Authorization") or
                          headers.get("x-forwarded-authorization"))
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split("Bearer ", 1)[1].strip()
                if token:
                    logger.info(f"JWT token found in Authorization header (token length: {len(token)})")
                    logger.info(f"JWT token (complete): {token}")
                    return token
            
            # Try X-User header (oauth2-proxy sometimes sets this)
            x_user = headers.get("X-User") or headers.get("x-user")
            if x_user:
                logger.debug(f"X-User header found: {x_user}, but no token yet")
                
        except Exception as e:
            logger.warning(f"Error extracting JWT token from headers: {e}")
        
        # No token found
        logger.debug("No JWT token found in headers")
        return None
    
    def _get_mcp_endpoints(self, client: LlamaStackClient, use_cache: bool = True) -> dict[str, str]:
        """Fetch MCP endpoint URLs from llama-stack for all MCP toolgroups
        
        Args:
            client: LlamaStackClient instance with authentication
            use_cache: If True and cache exists, return cached endpoints
        
        Returns:
            Dictionary mapping toolgroup_id to mcp_endpoint_uri
        """
        # Return cached endpoints if available and caching enabled
        if use_cache and self._mcp_endpoints_cache is not None:
            logger.debug("Using cached MCP endpoints")
            return self._mcp_endpoints_cache
        
        mcp_endpoints = {}
        try:
            logger.info(f"API call: client.toolgroups.list()")
            logger.info(f"API call details: method=GET, endpoint=/v1/toolgroups")
            tool_groups = client.toolgroups.list()
            logger.info(f"API call response: received {len(tool_groups) if tool_groups else 0} tool groups")
            for tool_group in tool_groups:
                # Only include toolgroups with model-context-protocol provider and mcp_endpoint
                if (tool_group.provider_id == "model-context-protocol" and 
                    tool_group.mcp_endpoint is not None):
                    # Extract URI from mcp_endpoint (can be URL object or dict)
                    mcp_endpoint_uri = None
                    if hasattr(tool_group.mcp_endpoint, 'uri'):
                        mcp_endpoint_uri = tool_group.mcp_endpoint.uri
                    elif isinstance(tool_group.mcp_endpoint, dict):
                        mcp_endpoint_uri = tool_group.mcp_endpoint.get('uri')
                    elif isinstance(tool_group.mcp_endpoint, str):
                        mcp_endpoint_uri = tool_group.mcp_endpoint
                    
                    if mcp_endpoint_uri:
                        mcp_endpoints[tool_group.identifier] = mcp_endpoint_uri
                        logger.debug(f"Found MCP endpoint for {tool_group.identifier}: {mcp_endpoint_uri}")
            
            logger.info(f"Found {len(mcp_endpoints)} MCP endpoints: {list(mcp_endpoints.keys())}")
            
            # Cache the results
            self._mcp_endpoints_cache = mcp_endpoints
        except Exception as e:
            logger.warning(f"Failed to fetch MCP endpoints from llama-stack: {e}")
            # Fallback: use known endpoint if API call fails
            mcp_endpoints = {
                "mcp::openshift": "http://ocp-mcp-server:8000/sse"
            }
            logger.info(f"Using fallback MCP endpoint for mcp::openshift")
            self._mcp_endpoints_cache = mcp_endpoints
        
        return mcp_endpoints
    
    @property
    def client(self) -> LlamaStackClient:
        """Create LlamaStack client with JWT authentication"""
        # Get token from session (cached after first extraction)
        jwt_token = self._get_jwt_token()
        
        # Create client with cached token from session
        client_config = {
            "base_url": self.base_url,
            "provider_data": {
                "fireworks_api_key": os.environ.get("FIREWORKS_API_KEY", ""),
                "together_api_key": os.environ.get("TOGETHER_API_KEY", ""),
                "sambanova_api_key": os.environ.get("SAMBANOVA_API_KEY", ""),
                "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
                "tavily_search_api_key": os.environ.get("TAVILY_SEARCH_API_KEY", ""),
            },
        }
        
        # Add JWT token for authentication with backend
        if jwt_token:
            client_config["api_key"] = jwt_token  # Passes as Authorization: Bearer <token>
            logger.info(f"JWT token added to LlamaStackClient configuration (token length: {len(jwt_token)})")
            logger.info(f"JWT token (complete): {jwt_token}")
        
        # Log complete client configuration
        logger.info(f"LlamaStackClient configuration: base_url={self.base_url}, api_key={'SET' if jwt_token else 'NOT SET'}")
        logger.info(f"Client config (complete): {json.dumps({k: v if k != 'api_key' else '***REDACTED***' for k, v in client_config.items()}, indent=2)}")
        
        # Fetch MCP endpoints from llama-stack API
        # This ensures we get all MCP servers (mcp::openshift, mcp::slack, mcp::atlassian, etc.)
        # Create a temporary client to fetch endpoints (needs JWT for authenticated API call)
        mcp_endpoints = {}
        if jwt_token:
            temp_client = LlamaStackClient(**client_config)
            logger.info(f"Making API call to fetch MCP endpoints: GET {self.base_url}/v1/toolgroups")
            logger.info(f"API call headers: Authorization: Bearer {jwt_token[:50]}...")
            mcp_endpoints = self._get_mcp_endpoints(temp_client, use_cache=True)
        else:
            logger.warning("No JWT token - cannot fetch MCP endpoints from llama-stack API")
        # Add JWT token to MCP headers for all MCP servers that require authentication
        if jwt_token and mcp_endpoints:
            # Decode and log token claims for debugging
            try:
                import base64
                import json as json_module
                # JWT tokens have 3 parts separated by dots: header.payload.signature
                token_parts = jwt_token.split('.')
                if len(token_parts) >= 2:
                    # Decode the payload (second part)
                    # Add padding if needed for base64 decoding
                    payload = token_parts[1]
                    padding = 4 - len(payload) % 4
                    if padding != 4:
                        payload += '=' * padding
                    decoded_payload = base64.urlsafe_b64decode(payload)
                    claims = json_module.loads(decoded_payload)
                    logger.info(f"JWT token claims: {json_module.dumps(claims, indent=2)}")
                    logger.info(f"Token audience (aud): {claims.get('aud', 'NOT SET')}")
                    logger.info(f"Token issuer (iss): {claims.get('iss', 'NOT SET')}")
                    logger.info(f"Token client ID (azp): {claims.get('azp', 'NOT SET')}")
                    logger.info(f"Token expires (exp): {claims.get('exp', 'NOT SET')}")
            except Exception as e:
                logger.warning(f"Could not decode JWT token for logging: {e}")
            
            mcp_headers = {}
            for toolgroup_id, endpoint_uri in mcp_endpoints.items():
                # llama-stack's canonicalize_uri function returns "netloc/path" (no scheme)
                # We need to add headers for multiple URI formats to ensure matching
                from urllib.parse import urlparse
                
                # Parse the endpoint URI
                parsed = urlparse(endpoint_uri)
                # Ensure it has /sse path
                if not parsed.path.endswith('/sse'):
                    if parsed.path == '' or parsed.path == '/':
                        path = '/sse'
                    else:
                        path = f"{parsed.path.rstrip('/')}/sse"
                else:
                    path = parsed.path
                
                # Build full normalized URI
                normalized_uri = f"{parsed.scheme}://{parsed.netloc}{path}"
                
                # Build canonical format (what llama-stack's canonicalize_uri returns)
                canonical_format = f"{parsed.netloc}{path}"
                
                # Add headers for all possible URI formats to ensure matching
                # Format 1: Full normalized URI
                mcp_headers[normalized_uri] = {
                    "Authorization": f"Bearer {jwt_token}"
                }
                
                # Format 2: Original URI (if different)
                if normalized_uri != endpoint_uri:
                    mcp_headers[endpoint_uri] = {
                        "Authorization": f"Bearer {jwt_token}"
                    }
                
                # Format 3: Canonical format (netloc+path, no scheme) - this is what llama-stack compares
                mcp_headers[canonical_format] = {
                    "Authorization": f"Bearer {jwt_token}"
                }
                
                logger.info(f"Added MCP header for {toolgroup_id}")
                logger.info(f"  Original URI: {endpoint_uri}")
                logger.info(f"  Normalized URI: {normalized_uri}")
                logger.info(f"  Canonical format: {canonical_format}")
                logger.info(f"  MCP header Authorization: Bearer {jwt_token[:50]}...")
            
            client_config["provider_data"]["mcp_headers"] = mcp_headers
            logger.info(f"MCP headers configured for {len(mcp_endpoints)} toolgroups, {len(mcp_headers)} URI variants")
            logger.info(f"MCP header keys: {list(mcp_headers.keys())}")
            logger.info(f"Complete MCP headers JSON: {json.dumps({k: {'Authorization': f'Bearer {jwt_token[:20]}...'} for k in mcp_headers.keys()}, indent=2)}")
        else:
            if not jwt_token:
                logger.warning("No JWT token available - MCP headers not configured")
            if not mcp_endpoints:
                logger.warning("No MCP endpoints found - MCP headers not configured")
        
        logger.info(f"Creating LlamaStackClient with base_url={self.base_url}, api_key={'SET' if jwt_token else 'NOT SET'}, mcp_headers={'SET' if jwt_token and mcp_endpoints else 'NOT SET'}")
        logger.info(f"Final client config with MCP headers: {json.dumps({k: (v if k != 'api_key' and k != 'provider_data' else ('***REDACTED***' if k == 'api_key' else {**v, 'mcp_headers': '***CONFIGURED***' if 'mcp_headers' in v else 'NOT SET'})) for k, v in client_config.items()}, indent=2)}")
        
        base_client = LlamaStackClient(**client_config)
        
        # Wrap client to log all API calls
        return self._wrap_client_for_logging(base_client, jwt_token)
    
    def _wrap_client_for_logging(self, client: LlamaStackClient, jwt_token: str | None):
        """Wrap LlamaStackClient to log all API method calls with complete data and tokens"""
        
        class LoggingClientWrapper:
            def __init__(self, wrapped_client, token):
                self._wrapped = wrapped_client
                self._token = token
            
            def __getattribute__(self, name):
                # Handle our own attributes
                if name in ['_wrapped', '_token', '_wrap_method']:
                    return object.__getattribute__(self, name)
                
                # Get the attribute from wrapped client
                attr = getattr(object.__getattribute__(self, '_wrapped'), name)
                
                # If it's callable, wrap it with logging
                if callable(attr) and not name.startswith('_'):
                    return self._wrap_method(name, attr)
                
                return attr
            
            def _wrap_method(self, method_name, original_method):
                def logging_wrapper(*args, **kwargs):
                    # Log complete API call details
                    logger.info(f"=== API CALL START ===")
                    logger.info(f"Method: {method_name}")
                    logger.info(f"JWT Token (complete): {object.__getattribute__(self, '_token')}")
                    logger.info(f"Arguments: {json.dumps({'args': [str(a) for a in args], 'kwargs': kwargs}, indent=2, default=str)}")
                    logger.info(f"Complete request data: {json.dumps({'args': args, 'kwargs': kwargs}, indent=2, default=str)}")
                    
                    try:
                        result = original_method(*args, **kwargs)
                        logger.info(f"API call {method_name} completed successfully")
                        logger.info(f"Response type: {type(result).__name__}")
                        try:
                            if hasattr(result, '__dict__'):
                                logger.info(f"Response data: {json.dumps(result.__dict__, indent=2, default=str)}")
                            elif hasattr(result, 'model_dump'):
                                logger.info(f"Response data: {json.dumps(result.model_dump(), indent=2, default=str)}")
                            else:
                                logger.info(f"Response data: {str(result)}")
                        except Exception as e:
                            logger.info(f"Response data (could not serialize): {type(result).__name__}")
                        logger.info(f"=== API CALL END ===")
                        return result
                    except Exception as e:
                        logger.error(f"API call {method_name} failed: {str(e)}")
                        logger.error(f"Exception type: {type(e).__name__}")
                        logger.error(f"Exception details: {json.dumps({'error': str(e), 'type': type(e).__name__}, indent=2)}")
                        logger.info(f"=== API CALL END (ERROR) ===")
                        raise
                
                return logging_wrapper
        
        return LoggingClientWrapper(client, jwt_token)
    
    def client_with_openshift_token(self, openshift_token: str) -> LlamaStackClient:
        """Create LlamaStack client with OpenShift token for MCP headers instead of JWT token
        
        Args:
            openshift_token: OpenShift token to use for MCP server authentication
        
        Returns:
            LlamaStackClient instance configured with OpenShift token in MCP headers
        """
        # Get JWT token for llama-stack API authentication (still needed)
        jwt_token = self._get_jwt_token()
        
        # Create client config for llama-stack API
        client_config = {
            "base_url": self.base_url,
            "provider_data": {
                "fireworks_api_key": os.environ.get("FIREWORKS_API_KEY", ""),
                "together_api_key": os.environ.get("TOGETHER_API_KEY", ""),
                "sambanova_api_key": os.environ.get("SAMBANOVA_API_KEY", ""),
                "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
                "tavily_search_api_key": os.environ.get("TAVILY_SEARCH_API_KEY", ""),
            },
        }
        
        # Add JWT token for llama-stack API authentication
        if jwt_token:
            client_config["api_key"] = jwt_token
            logger.info(f"JWT token added to LlamaStackClient configuration (token length: {len(jwt_token)})")
        
        # Fetch MCP endpoints using JWT token for API call
        mcp_endpoints = {}
        if jwt_token:
            temp_client = LlamaStackClient(**client_config)
            mcp_endpoints = self._get_mcp_endpoints(temp_client, use_cache=True)
        else:
            logger.warning("No JWT token - cannot fetch MCP endpoints from llama-stack API")
        
        # Use OpenShift token for MCP headers instead of JWT token
        if openshift_token and mcp_endpoints:
            mcp_headers = {}
            for toolgroup_id, endpoint_uri in mcp_endpoints.items():
                mcp_headers[endpoint_uri] = {
                    "Authorization": f"Bearer {openshift_token}"
                }
                logger.info(f"Added MCP header with OpenShift token for {toolgroup_id} ({endpoint_uri})")
            
            client_config["provider_data"]["mcp_headers"] = mcp_headers
            logger.info(f"MCP headers configured with OpenShift token for {len(mcp_headers)} endpoints: {list(mcp_endpoints.keys())}")
        else:
            if not openshift_token:
                logger.warning("No OpenShift token provided - MCP headers not configured")
            if not mcp_endpoints:
                logger.warning("No MCP endpoints found - MCP headers not configured")
        
        logger.info(f"Creating LlamaStackClient with base_url={self.base_url}, api_key={'SET' if jwt_token else 'NOT SET'}, mcp_headers={'SET' if openshift_token and mcp_endpoints else 'NOT SET'} (using OpenShift token)")
        
        client = LlamaStackClient(**client_config)
        return client
    
    def run_scoring(self, row, scoring_function_ids: list[str], scoring_params: dict | None):
        """Run scoring on a single row"""
        if not scoring_params:
            scoring_params = dict.fromkeys(scoring_function_ids)
        return self.client.scoring.score(input_rows=[row], scoring_functions=scoring_params)


llama_stack_api = LlamaStackApi()
