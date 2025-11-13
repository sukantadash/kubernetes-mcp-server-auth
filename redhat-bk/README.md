# Keycloak External Authentication Setup for OpenShift

This guide explains how to deploy Red Hat Build of Keycloak and configure it as an External Authentication provider for OpenShift clusters. This setup enables OAuth/OIDC authentication for OpenShift console, CLI, and Kubernetes MCP Server.

## ⚠️ Important: Tech Preview Feature

**External Authentication is a Tech Preview feature** available in **OpenShift 4.19 and above**. This feature:

- Requires enabling Tech Preview features via FeatureGate
- Is not supported for production use
- May have limitations and breaking changes
- Requires cluster-admin privileges

## Prerequisites

- **OpenShift 4.19 or later** (required for External Authentication)
- Cluster-admin access to the OpenShift cluster
- `oc` CLI tool installed and configured
- `kustomize` installed (usually included with `oc`)
- `jq` installed (for testing)
- `envsubst` installed (usually included with `gettext` package)

## Overview

This setup deploys:

1. **Red Hat Build of Keycloak Operator** - Manages Keycloak lifecycle
2. **PostgreSQL Database** - Backend storage for Keycloak
3. **Keycloak Instance** - OIDC/OAuth2 identity provider
4. **OpenShift Realm** - Pre-configured realm with clients for:
   - OpenShift Console authentication
   - OpenShift CLI authentication (`oc login`)
   - MCP Client authentication
   - MCP Server token exchange
5. **External Authentication Configuration** - Integrates Keycloak with OpenShift authentication

## Architecture

```
┌─────────────────┐
│  OpenShift      │
│  Console/CLI    │
└────────┬────────┘
         │ OIDC/OAuth2
         ▼
┌─────────────────┐     ┌──────────────┐
│   Keycloak      │────▶│  PostgreSQL  │
│   (OIDC IdP)    │     │  Database    │
└────────┬────────┘     └──────────────┘
         │
         │ Token Exchange
         ▼
┌─────────────────┐
│  MCP Server     │
│  (User Context) │
└─────────────────┘
```

## Step-by-Step Deployment

### Step 1: Verify OpenShift Version

Ensure you're running OpenShift 4.19 or later:

```bash
oc version
oc get clusterversion version -o jsonpath='{.status.desired.version}'
```

**Minimum version**: 4.19.0

### Step 2: Enable Tech Preview Features

The script automatically enables Tech Preview features via FeatureGate. This is required for External Authentication.

**Warning**: Enabling Tech Preview features:
- Cannot be disabled once enabled
- May introduce breaking changes in future upgrades
- Is not recommended for production clusters

The FeatureGate configuration:
```yaml
apiVersion: config.openshift.io/v1
kind: FeatureGate
metadata:
  name: cluster
spec:
  featureSet: TechPreviewNoUpgrade
```

### Step 3: Run the Deployment Script

Navigate to the `redhat-bk` directory and run the script:

```bash
cd redhat-bk
./script.sh
```

The script will:

1. **Deploy Keycloak Operator**
   ```bash
   oc apply -k operator/overlays/stable/
   ```

2. **Generate Secrets**
   - `POSTGRES_PASSWORD`: Database password
   - `MCP_SERVER_SECRET`: Secret for MCP Server client
   - `OPENSHIFT_SECRET`: Secret for OpenShift API client
   - `OPENSHIFT_CONSOLE_SECRET`: Secret for OpenShift Console client
   - `CLUSTER_NAME`: Auto-detected from cluster
   - `RHBK_HOST`: Keycloak URL (auto-generated)

3. **Deploy Infrastructure**
   - PostgreSQL database
   - Keycloak instance
   - Keycloak realm with clients and scopes

4. **Configure External Authentication**
   - Extract OpenShift ingress CA certificate
   - Create ConfigMap for Keycloak OIDC CA
   - Configure OpenShift Authentication resource

5. **Display Generated Secrets**
   - **IMPORTANT**: Save these secrets securely!

### Step 4: Save Generated Secrets

The script will output secrets that you need to save:

```bash
RHBK_HOST: https://keycloak-admin.apps.<cluster-name>
CLUSTER_NAME: <cluster-name>
POSTGRES_PASSWORD: <generated-uuid>
MCP_SERVER_SECRET: <generated-uuid>
OPENSHIFT_SECRET: <generated-uuid>
OPENSHIFT_CONSOLE_SECRET: <generated-uuid>
```

**Save these values** - you'll need them for:
- MCP Server configuration
- Testing authentication
- Troubleshooting

### Step 5: Verify Deployment

Check that all components are running:

```bash
# Check FeatureGate
oc get featuregate cluster

# Check Keycloak Operator
oc get pods -n redhat-keycloak-operator

# Check Keycloak instance
oc get pods -n redhat-keycloak

# Check PostgreSQL
oc get pods -n redhat-keycloak

# Check Authentication configuration
oc get authentication cluster

# Check Authentication operator
oc get clusteroperator authentication
oc get pods -n openshift-authentication
```

### Step 6: Wait for Keycloak to be Ready

Wait for Keycloak to be fully deployed and ready:

```bash
# Watch Keycloak instance status
oc get keycloak -n redhat-keycloak -w

# Check Keycloak pod logs
oc logs -n redhat-keycloak -l app=keycloak --tail=50

# Verify Keycloak is accessible
export RHBK_HOST=$(oc get route -n redhat-keycloak keycloak -o jsonpath='{.spec.host}')
curl -k https://${RHBK_HOST}/realms/openshift/.well-known/openid-configuration
```

## Keycloak Realm Configuration

The script creates an `openshift` realm with the following configuration:

### Client Scopes

1. **groups**
   - Maps user groups to token claims
   - Default client scope
   - Includes group membership in tokens

2. **mcp-server**
   - Audience scope for MCP Server
   - Optional client scope
   - Used by MCP clients

3. **mcp:openshift**
   - Scope for OpenShift API access
   - Used during token exchange
   - Includes `openshift` audience in tokens

### Clients

1. **mcp-client** (Public Client)
   - Used by MCP clients to authenticate
   - Direct access grants enabled
   - Redirect URIs: `*`
   - Optional scope: `mcp-server`

2. **mcp-server** (Confidential Client)
   - Used by MCP Server for token exchange
   - Token exchange enabled (`standard.token.exchange.enabled: true`)
   - Default scope: `groups`
   - Optional scope: `mcp:openshift`
   - **Secret**: `MCP_SERVER_SECRET` (from script output)

3. **openshift** (Confidential Client)
   - Used for OpenShift API authentication
   - Protocol mappers for:
     - `preferred_username` (from username attribute)
     - `email` (from email attribute)
     - `groups` (from group membership)
   - Default scopes: `profile`, `email`, `groups`
   - **Secret**: `OPENSHIFT_SECRET` (from script output)

4. **openshift-console** (Confidential Client)
   - Used by OpenShift Console for authentication
   - **Secret**: `OPENSHIFT_CONSOLE_SECRET` (from script output)

5. **openshift-cli** (Public Client)
   - Used by `oc` CLI for authentication
   - Standard flow enabled
   - Used with `oc login --exec-plugin oc-oidc`

## External Authentication Configuration

The script configures OpenShift to use Keycloak as an external OIDC provider:

```yaml
apiVersion: config.openshift.io/v1
kind: Authentication
metadata:
  name: cluster
spec:
  oidcProviders:
    - name: 'rhbk-external-auth'
      issuer:
        issuerURL: https://keycloak-admin.apps.<cluster>/realms/openshift
        audiences: [openshift-console, openshift-cli]
        issuerCertificateAuthority:
          name: keycloak-oidc-ca
      claimMappings:
        username:
          claim: preferred_username
          prefixPolicy: NoPrefix
        groups:
          claim: groups
          prefix: ''
      oidcClients:
        - clientID: openshift-cli
          componentName: cli
          componentNamespace: openshift-console
        - clientID: openshift-console
          clientSecret:
            name: keycloak-client-openshift-console-secret
          componentName: console
          componentNamespace: openshift-console
  type: OIDC
```

This configuration:
- Enables Keycloak as an OIDC provider for OpenShift
- Maps Keycloak claims to OpenShift user/groups
- Configures console and CLI clients
- Uses the OpenShift ingress CA for certificate validation

## Testing the Setup

### 1. Test Keycloak Access

```bash
export RHBK_HOST="https://keycloak-admin.apps.<cluster-name>"
export RHBK_REALM="openshift"

# Get OpenID configuration
curl -k ${RHBK_HOST}/realms/${RHBK_REALM}/.well-known/openid-configuration | jq
```

### 2. Test User Authentication

Get an OAuth token using a test user:

```bash
export RHBK_HOST="https://keycloak-admin.apps.<cluster-name>"
export RHBK_REALM="openshift"
export RHBK_USERNAME="testdeveloper"  # Create this user in Keycloak
export RHBK_PASSWORD="<password>"
export MCP_CLIENT_ID="mcp-client"

RHBK_TOKEN=$(curl -s -X POST ${RHBK_HOST}/realms/${RHBK_REALM}/protocol/openid-connect/token \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -d scope=mcp-server \
    -d username=${RHBK_USERNAME} \
    -d password=${RHBK_PASSWORD} \
    -d grant_type=password \
    -d client_id=${MCP_CLIENT_ID} | jq -r '.access_token')

echo "Token: $RHBK_TOKEN"

# Decode token to verify claims
jq -R 'split(".") | .[1] | @base64d | fromjson' <<< "$RHBK_TOKEN"
```

### 3. Test Token Exchange

Exchange the OAuth token for an OpenShift token:

```bash
export MCP_SERVER_ID="mcp-server"
export MCP_SERVER_SECRET="<MCP_SERVER_SECRET>"  # From script output

K8S_TOKEN=$(curl -s ${RHBK_HOST}/realms/${RHBK_REALM}/protocol/openid-connect/token \
    -d grant_type=urn:ietf:params:oauth:grant-type:token-exchange \
    -d client_id=${MCP_SERVER_ID} \
    -d subject_token="${RHBK_TOKEN}" \
    -d subject_token_type=urn:ietf:params:oauth:token-type:access_token \
    -d audience=openshift \
    -d client_secret=${MCP_SERVER_SECRET} \
    -d requested_token_type=urn:ietf:params:oauth:token-type:access_token \
    -d scope=mcp:openshift | jq -r '.access_token')

echo "K8S Token: $K8S_TOKEN"

# Decode token to verify audience
jq -R 'split(".") | .[1] | @base64d | fromjson' <<< "$K8S_TOKEN"
```

### 4. Test OpenShift API Access

Use the exchanged token to access OpenShift API:

```bash
export OPENSHIFT_API_SERVER="https://api.<cluster-name>:6443"

# Get authenticated user info
curl -k ${OPENSHIFT_API_SERVER}/apis/authentication.k8s.io/v1/selfsubjectreviews \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${K8S_TOKEN}" \
    -X POST -d '{"kind":"SelfSubjectReview","apiVersion":"authentication.k8s.io/v1","metadata":{"creationTimestamp":null},"status":{"userInfo":{}}}' | jq

# List pods (if user has permissions)
curl -k -H "Authorization: Bearer ${K8S_TOKEN}" \
     "${OPENSHIFT_API_SERVER}/api/v1/namespaces/llama-stack/pods" | jq -r '.items[].metadata.name'
```

### 5. Test OpenShift CLI Login

Login to OpenShift using Keycloak:

```bash
export CLUSTER_NAME="<cluster-name>"
export KEYCLOAK_URL="https://keycloak-admin.apps.${CLUSTER_NAME}/realms/openshift"

oc login https://api.${CLUSTER_NAME}:6443 \
    --issuer-url ${KEYCLOAK_URL} \
    --exec-plugin oc-oidc \
    --client-id openshift-cli
```

This will open a browser for authentication.

## Creating Test Users

You need to create users in Keycloak for testing. Access the Keycloak admin console:

```bash
# Get Keycloak admin credentials
oc get secret -n redhat-keycloak keycloak-initial-admin -o jsonpath='{.data.password}' | base64 -d

# Get Keycloak admin URL
oc get route -n redhat-keycloak keycloak -o jsonpath='{.spec.host}'
```

Then:
1. Open `https://<keycloak-host>` in a browser
2. Login with admin credentials
3. Navigate to `openshift` realm
4. Go to Users → Add user
5. Set username, email, and password
6. Assign user to groups (e.g., `openshift_developers`)

## Troubleshooting

### FeatureGate Not Applied

```bash
# Check FeatureGate status
oc get featuregate cluster -o yaml

# Verify Tech Preview is enabled
oc get featuregate cluster -o jsonpath='{.spec.featureSet}'
# Should output: TechPreviewNoUpgrade
```

### Keycloak Not Starting

```bash
# Check Keycloak operator logs
oc logs -n redhat-keycloak-operator -l name=keycloak-operator --tail=100

# Check Keycloak instance status
oc get keycloak -n redhat-keycloak -o yaml

# Check Keycloak pod logs
oc logs -n redhat-keycloak -l app=keycloak --tail=100

# Check PostgreSQL connection
oc get pods -n redhat-keycloak -l app=postgresql
oc logs -n redhat-keycloak -l app=postgresql --tail=50
```

### Authentication Not Working

```bash
# Check Authentication resource
oc get authentication cluster -o yaml

# Check Authentication operator
oc get clusteroperator authentication
oc get pods -n openshift-authentication

# Check authentication operator logs
oc logs -n openshift-authentication -l app=oauth-openshift --tail=100
```

### Token Exchange Failing

1. Verify `mcp-server` client has token exchange enabled:
   ```bash
   # Check Keycloak admin console
   # Clients → mcp-server → Advanced → Token Exchange Enabled: ON
   ```

2. Verify client secret is correct:
   ```bash
   # Use the MCP_SERVER_SECRET from script output
   ```

3. Check Keycloak realm logs for errors

### CA Certificate Issues

```bash
# Verify CA certificate ConfigMap exists
oc get configmap keycloak-oidc-ca -n llama-stack

# Check certificate content
oc get configmap keycloak-oidc-ca -n llama-stack -o jsonpath='{.data.ca\.crt}' | openssl x509 -text -noout

# Verify certificate is mounted in authentication pods
oc get pod -n openshift-authentication -o yaml | grep -A 5 keycloak-oidc-ca
```

## Cleanup

To remove the Keycloak setup:

```bash
# Remove External Authentication
oc delete authentication cluster

# Remove Keycloak realm
oc delete keycloakrealmimport -n redhat-keycloak openshift-realm-import

# Remove Keycloak instance
oc delete keycloak -n redhat-keycloak keycloak

# Remove PostgreSQL
oc delete postgresql -n redhat-keycloak postgresql

# Remove Keycloak operator (optional)
oc delete subscription -n redhat-keycloak-operator keycloak-operator
oc delete operatorgroup -n redhat-keycloak-operator keycloak-operator

# Remove FeatureGate (cannot be disabled, but can be removed)
# Note: This may cause issues if other Tech Preview features are in use
```

**Note**: The FeatureGate cannot be disabled once enabled. Removing it may cause issues if other components depend on Tech Preview features.

## Security Considerations

1. **Secrets Management**: Store generated secrets securely (use Sealed Secrets, External Secrets, Vault, etc.)

2. **Keycloak Admin Access**: Protect Keycloak admin console access

3. **User Management**: Implement proper user provisioning and deprovisioning

4. **Token Expiration**: Configure appropriate token lifetimes in Keycloak realm settings

5. **TLS**: Always use HTTPS for Keycloak endpoints

6. **Network Policies**: Implement network policies to restrict access to Keycloak

7. **Audit Logging**: Enable audit logging in Keycloak

## Limitations

- **Tech Preview**: Not recommended for production
- **FeatureGate**: Cannot be disabled once enabled
- **Upgrade Risks**: May have breaking changes in future versions
- **Performance**: May have performance implications for large clusters
- **Support**: Limited support compared to stable features

## Next Steps

After deploying Keycloak:

1. Configure Kubernetes MCP Server to use OAuth (see main [README.md](../README.md))
2. Create users and groups in Keycloak
3. Configure RBAC in OpenShift for Keycloak users
4. Test authentication flows
5. Document your specific configuration

## Additional Resources

- [OpenShift External Authentication Documentation](https://docs.openshift.com/container-platform/4.19/authentication/configuring-oauth-clients.html)
- [Red Hat Build of Keycloak Documentation](https://www.keycloak.org/docs/latest/)
- [Keycloak Token Exchange](https://www.keycloak.org/docs/latest/securing_apps/#_token-exchange)
- [OpenShift Feature Gates](https://docs.openshift.com/container-platform/4.19/post_installation_configuration/cluster-tasks.html#cluster-feature-gates_post-install-cluster-tasks)

## Support

For issues:
1. Check component logs (Keycloak, Authentication operator, PostgreSQL)
2. Verify FeatureGate is enabled
3. Check OpenShift version (must be 4.19+)
4. Review Keycloak realm configuration in admin console
5. Test token exchange manually using curl commands

