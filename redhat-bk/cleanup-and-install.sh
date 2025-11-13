#!/bin/bash
set -e

# Configuration
# Auto-detect cluster name from infrastructure if not provided
if [ -z "$CLUSTER_NAME" ]; then
    CLUSTER_NAME=$(oc get infrastructure cluster -o jsonpath='{.status.apiServerURL}' 2>/dev/null | sed 's|https://api\.||' | sed 's|:6443||' || echo "")
    if [ -z "$CLUSTER_NAME" ]; then
        # Fallback: try to get from ingress domain
        CLUSTER_NAME=$(oc get ingresscontroller default -n openshift-ingress-operator -o jsonpath='{.status.domain}' 2>/dev/null || echo "")
    fi
    if [ -z "$CLUSTER_NAME" ]; then
        echo "❌ Error: Could not auto-detect cluster name. Please set CLUSTER_NAME environment variable."
        exit 1
    fi
fi
NAMESPACE="redhat-keycloak"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "Keycloak Cleanup and Install Script"
echo "=========================================="
echo ""
echo "📋 Cluster Name: ${CLUSTER_NAME}"
echo ""

# Function to cleanup resources
cleanup() {
    echo "🧹 Cleaning up existing resources..."
    
    # Delete realm imports
    echo "  - Deleting KeycloakRealmImport resources..."
    oc delete keycloakrealmimport --all -n ${NAMESPACE} 2>/dev/null || true
    
    # Delete Keycloak instance
    echo "  - Deleting Keycloak instance..."
    oc delete keycloak --all -n ${NAMESPACE} 2>/dev/null || true
    
    # Delete PostgreSQL
    echo "  - Deleting PostgreSQL..."
    oc delete postgresql --all -n ${NAMESPACE} 2>/dev/null || true
    oc delete secret postgresql-credentials -n ${NAMESPACE} 2>/dev/null || true
    
    # Remove Keycloak from Authentication config (new format)
    echo "  - Removing Keycloak identity provider from Authentication configuration..."
    if oc get authentication cluster &>/dev/null; then
        # Get current Authentication config and remove keycloak provider
        oc get authentication cluster -o json 2>/dev/null | \
            jq 'del(.spec.oidcProviders[]? | select(.name == "keycloak-oidc-server"))' | \
            oc apply -f - 2>/dev/null || true
    fi
    
    # Also remove from OAuth config (legacy, if exists)
    echo "  - Removing Keycloak identity provider from OAuth configuration (legacy)..."
    oc get oauth cluster -o json 2>/dev/null | \
        jq 'del(.spec.identityProviders[]? | select(.name == "keycloak"))' | \
        oc apply -f - 2>/dev/null || true
    
    # Delete secrets
    echo "  - Deleting secrets..."
    oc delete secret keycloak-client-openshift-secret -n openshift-config 2>/dev/null || true
    oc delete secret keycloak-client-oc-cli-secret -n openshift-config 2>/dev/null || true
    oc delete secret keycloak-client-console-secret -n openshift-config 2>/dev/null || true
    
    # Note: We keep the CA bundle configmap as it may be needed for other purposes
    # and can be reused if it exists
    
    # Uninstall operator (do this last so operator can clean up finalizers)
    echo "  - Uninstalling Keycloak operator..."
    # Delete subscription first
    oc delete subscription rhbk-operator -n ${NAMESPACE} 2>/dev/null || true
    # Wait a bit for operator to process deletion
    sleep 5
    # Delete CSV (ClusterServiceVersion) if it exists
    CSV_NAME=$(oc get csv -n ${NAMESPACE} -o jsonpath='{.items[?(@.spec.displayName=="Red Hat Build of Keycloak")].metadata.name}' 2>/dev/null || echo "")
    if [ -n "$CSV_NAME" ]; then
        echo "  - Deleting ClusterServiceVersion: ${CSV_NAME}"
        oc delete csv ${CSV_NAME} -n ${NAMESPACE} 2>/dev/null || true
    fi
    # Delete operatorgroup
    oc delete operatorgroup keycloak-operator-group -n ${NAMESPACE} 2>/dev/null || true
    # Wait for operator to be removed
    echo "  - Waiting for operator to be removed..."
    sleep 10
    
    # Wait for resources to be deleted
    echo "  - Waiting for resources to be deleted..."
    sleep 5
    
    # Delete namespace/project (this will delete all remaining resources)
    echo "  - Deleting namespace/project: ${NAMESPACE}..."
    oc delete project ${NAMESPACE} 2>/dev/null || true
    # Wait for namespace to be deleted
    echo "  - Waiting for namespace to be deleted..."
    MAX_WAIT=60
    ELAPSED=0
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        if ! oc get project ${NAMESPACE} &>/dev/null; then
            echo "  - Namespace deleted"
            break
        fi
        echo "  - Waiting for namespace deletion... (${ELAPSED}s/${MAX_WAIT}s)"
        sleep 5
        ELAPSED=$((ELAPSED + 5))
    done
    
    echo "✅ Cleanup completed"
    echo ""
}

# Function to install resources
install() {
    echo "🚀 Installing Keycloak..."
    echo ""
    
    # Create namespace if it doesn't exist
    echo "📦 Creating namespace if needed..."
    oc apply -f ${SCRIPT_DIR}/operator/base/namespace.yaml
    echo "  - Namespace ${NAMESPACE} ready"
    echo ""
    
    # Generate secrets
    echo "📝 Generating secrets..."
    postgres_password=$(uuidgen)
    mcp_server_secret=$(uuidgen)
    openshift_secret=$(uuidgen)
    oc_cli_secret=$(uuidgen)
    console_secret=$(uuidgen)
    echo "  - PostgreSQL Password: ${postgres_password}"
    echo "  - MCP Server Secret: ${mcp_server_secret}"
    echo "  - OpenShift Client Secret: ${openshift_secret}"
    echo "  - OC CLI Secret: ${oc_cli_secret}"
    echo "  - Console Secret: ${console_secret}"
    echo ""
    
    # Deploy operator
    echo "📦 Deploying Keycloak operator..."
    oc apply -k ${SCRIPT_DIR}/operator/overlays/stable/
    echo "  - Waiting for operator to be ready..."
    MAX_WAIT=300
    ELAPSED=0
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        if oc get deployment rhbk-operator -n ${NAMESPACE} &>/dev/null; then
            if oc wait --for=condition=available --timeout=10s deployment/rhbk-operator -n ${NAMESPACE} &>/dev/null; then
                echo "  - Operator is ready"
                break
            fi
        fi
        echo "  - Waiting for operator... (${ELAPSED}s/${MAX_WAIT}s)"
        sleep 5
        ELAPSED=$((ELAPSED + 5))
    done
    echo ""
    
    # Create OpenShift client secrets
    echo "🔐 Creating OpenShift client secrets..."
    oc create secret generic keycloak-client-openshift-secret \
        --from-literal=clientSecret="${openshift_secret}" \
        -n openshift-config --dry-run=client -o yaml | oc apply -f -
    
    oc create secret generic keycloak-client-oc-cli-secret \
        --from-literal=clientSecret="${oc_cli_secret}" \
        -n openshift-config --dry-run=client -o yaml | oc apply -f - 2>/dev/null || true
    
    oc create secret generic keycloak-client-console-secret \
        --from-literal=clientSecret="${console_secret}" \
        -n openshift-config --dry-run=client -o yaml | oc apply -f - 2>/dev/null || true
    echo ""
    
    # Create Keycloak OIDC CA bundle configmap if it doesn't exist
    echo "🔐 Creating Keycloak OIDC CA bundle configmap..."
    if ! oc get configmap keycloak-oidc-ca -n openshift-config &>/dev/null; then
        oc get configmap -n openshift-config-managed default-ingress-cert \
            -o jsonpath='{.data.ca-bundle\.crt}' > /tmp/keycloak-oidc-ca.crt 2>/dev/null || true
        if [ -s /tmp/keycloak-oidc-ca.crt ]; then
            oc create configmap keycloak-oidc-ca \
                --from-file=ca.crt=/tmp/keycloak-oidc-ca.crt \
                -n openshift-config --dry-run=client -o yaml | oc apply -f -
            rm -f /tmp/keycloak-oidc-ca.crt
            echo "  - OIDC CA bundle created"
        else
            echo "  - ⚠️  Warning: Could not create OIDC CA bundle. You may need to create it manually."
        fi
    else
        echo "  - OIDC CA bundle already exists"
    fi
    echo ""
    
    # Update YAML files with secrets and cluster name
    echo "📝 Updating configuration files..."
    cd ${SCRIPT_DIR}/cluster
    
    # Backup original files
    cp 01_postgresql.yaml 01_postgresql.yaml.bak 2>/dev/null || true
    cp 02_keycloak.yaml 02_keycloak.yaml.bak 2>/dev/null || true
    cp 04_realm.yaml 04_realm.yaml.bak 2>/dev/null || true
    cp authentication-provider.yaml authentication-provider.yaml.bak 2>/dev/null || true
    cp authentication-clients.yaml authentication-clients.yaml.bak 2>/dev/null || true
    
    # Replace PostgreSQL password
    sed -i '' "s|CHANGE_ME_IN_PRODUCTION|${postgres_password}|g" 01_postgresql.yaml
    
    # Replace secrets in realm
    sed -i '' "s|secret: \"YOUR_MCP_SERVER_SECRET_HERE\"|secret: \"${mcp_server_secret}\"|g" 04_realm.yaml
    sed -i '' "s|secret: \"YOUR_OPENSHIFT_CLIENT_SECRET_HERE\"|secret: \"${openshift_secret}\"|g" 04_realm.yaml
    sed -i '' "s|secret: \"YOUR_OC_CLI_SECRET_HERE\"|secret: \"${oc_cli_secret}\"|g" 04_realm.yaml
    sed -i '' "s|secret: \"YOUR_CONSOLE_SECRET_HERE\"|secret: \"${console_secret}\"|g" 04_realm.yaml
    
    # Replace cluster name
    sed -i '' "s|YOUR_CLUSTER_NAME|${CLUSTER_NAME}|g" 02_keycloak.yaml
    sed -i '' "s|YOUR_CLUSTER_NAME|${CLUSTER_NAME}|g" 04_realm.yaml
    sed -i '' "s|YOUR_CLUSTER_NAME|${CLUSTER_NAME}|g" authentication-provider.yaml
    
    echo "  - Updated 01_postgresql.yaml"
    echo "  - Updated 02_keycloak.yaml"
    echo "  - Updated 04_realm.yaml"
    echo "  - Updated authentication-provider.yaml"
    
    # Update test script with secrets and cluster name
    cd ${SCRIPT_DIR}
    if [ -f test-script.sh ]; then
        cp test-script.sh test-script.sh.bak 2>/dev/null || true
        sed -i '' "s|YOUR_MCP_SERVER_SECRET_HERE|${mcp_server_secret}|g" test-script.sh
        sed -i '' "s|YOUR_CLUSTER_NAME|${CLUSTER_NAME}|g" test-script.sh
        echo "  - Updated test-script.sh"
    fi
    cd ${SCRIPT_DIR}/cluster
    echo ""
 
    # Deploy Keycloak and realm using kustomize
    echo "🏗️  Deploying Keycloak instance and realm..."
    oc apply -k ${SCRIPT_DIR}/cluster
    
    # Apply FeatureGate (if not already enabled)
    echo "🔐 Checking FeatureGate configuration..."
    if ! oc get featuregate cluster &>/dev/null; then
        echo "  - FeatureGate not found, creating..."
        oc apply -f ${SCRIPT_DIR}/cluster/00_featuregate.yaml
        echo "  - ⚠️  Warning: TechPreviewNoUpgrade feature gate enabled. This cannot be reverted!"
        echo "  - Waiting for cluster to stabilize..."
        sleep 30
    else
        CURRENT_FG=$(oc get featuregate cluster -o jsonpath='{.spec.featureSet}' 2>/dev/null || echo "")
        if [ "$CURRENT_FG" != "TechPreviewNoUpgrade" ]; then
            echo "  - ⚠️  Warning: FeatureGate exists but is not set to TechPreviewNoUpgrade"
            echo "  - Current featureSet: ${CURRENT_FG}"
            echo "  - You may need to manually update the FeatureGate"
        else
            echo "  - FeatureGate already configured with TechPreviewNoUpgrade"
        fi
    fi
    echo ""
    
    # Wait for Keycloak to be ready
    echo "⏳ Waiting for Keycloak to be ready..."
    MAX_WAIT=300
    ELAPSED=0
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        if oc get keycloak -n ${NAMESPACE} -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q "True"; then
            echo "✅ Keycloak is ready"
            break
        fi
        echo "  - Waiting... (${ELAPSED}s/${MAX_WAIT}s)"
        sleep 10
        ELAPSED=$((ELAPSED + 10))
    done
    
    # Wait for realm import
    echo "⏳ Waiting for realm import to complete..."
    sleep 10
    MAX_WAIT=180
    ELAPSED=0
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        STATUS=$(oc get keycloakrealmimport -n ${NAMESPACE} -o jsonpath='{.items[0].status.conditions[?(@.type=="Done")].status}' 2>/dev/null || echo "False")
        if [ "$STATUS" = "True" ]; then
            echo "✅ Realm import completed"
            break
        fi
        HAS_ERRORS=$(oc get keycloakrealmimport -n ${NAMESPACE} -o jsonpath='{.items[0].status.conditions[?(@.type=="HasErrors")].status}' 2>/dev/null || echo "False")
        if [ "$HAS_ERRORS" = "True" ]; then
            echo "❌ Realm import has errors. Check logs:"
            oc get keycloakrealmimport -n ${NAMESPACE} -o yaml | grep -A 5 "message:"
            exit 1
        fi
        echo "  - Waiting... (${ELAPSED}s/${MAX_WAIT}s)"
        sleep 10
        ELAPSED=$((ELAPSED + 10))
    done
    
    echo ""
    echo "✅ Installation completed"
    echo ""
   

    
    # Display Keycloak URL
    KEYCLOAK_URL=$(oc get keycloak -n ${NAMESPACE} -o jsonpath='{.items[0].spec.instances[0].hostname}' 2>/dev/null || echo "")
    if [ -z "$KEYCLOAK_URL" ]; then
        KEYCLOAK_URL=$(oc get route -n ${NAMESPACE} -l app=keycloak -o jsonpath='{.items[0].spec.host}' 2>/dev/null || echo "")
    fi
    if [ -z "$KEYCLOAK_URL" ]; then
        KEYCLOAK_URL="https://keycloak-admin.apps.${CLUSTER_NAME}"
    else
        KEYCLOAK_URL="https://${KEYCLOAK_URL}"
    fi
    echo "📋 Keycloak URL: ${KEYCLOAK_URL}"
    echo "📋 Realm: openshift"
    echo "📋 Test User: testdeveloper / dummy"
    echo ""
    echo "ℹ️  Note: It may take a few minutes for OAuth pods to restart and authentication to be fully available."
    echo ""
}

# Main execution
main() {
    if [ "$1" = "cleanup-only" ]; then
        cleanup
    elif [ "$1" = "install-only" ]; then
        install
    else
        cleanup
        install
    fi
}

# Run main function
main "$@"

