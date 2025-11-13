#Deploy the Redhat build of Keycloak operator
oc apply -k operator/overlays/stable/


#Deploy the Keycloak instance and realm configuration
export POSTGRES_PASSWORD=$(uuidgen)
export MCP_SERVER_SECRET=$(uuidgen)
export MCP_CLIENT_SECRET=$(uuidgen)
export OPENSHIFT_SECRET=$(uuidgen)
export OPENSHIFT_CONSOLE_SECRET=$(uuidgen)
export CLUSTER_NAME=$(oc get infrastructure cluster -o jsonpath='{.status.apiServerURL}' 2>/dev/null | sed 's|https://api\.||' | sed 's|:6443||' || echo "")
export RHBK_HOST=https://keycloak-admin.apps.${CLUSTER_NAME}



echo "RHBK_HOST: $RHBK_HOST"
echo "CLUSTER_NAME: $CLUSTER_NAME"
echo "POSTGRES_PASSWORD: $POSTGRES_PASSWORD"
echo "MCP_SERVER_SECRET: $MCP_SERVER_SECRET"
echo "MCP_CLIENT_SECRET: $MCP_CLIENT_SECRET"
echo "OPENSHIFT_SECRET: $OPENSHIFT_SECRET"
echo "OPENSHIFT_CONSOLE_SECRET: $OPENSHIFT_CONSOLE_SECRET"


cd cluster
envsubst < 01_postgresql.yaml  | oc apply -f -
envsubst < 02_keycloak.yaml  | oc apply -f -
envsubst < 04_realm.yaml  | oc apply -f -
oc apply -f 00_featuregate.yaml
oc apply -f clusterrolebinding.yaml

oc get configmap -n openshift-config-managed default-ingress-cert \
    -o jsonpath='{.data.ca-bundle\.crt}' > keycloak-ca.crt

oc create configmap keycloak-oidc-ca \
    --from-file=ca.crt=keycloak-ca.crt \
    -n llama-stack --dry-run=client -o yaml | oc apply -f -


envsubst < authentication-config.yaml  | oc apply -f -


oc get authentication cluster


oc get clusteroperator authentication

oc get pods -n openshift-authentication




#Test the setup
#Testing Token Exchange
#First, set some basic information including the RHBK host, username and password


# Test configuration - replace with your actual values
export RHBK_REALM=openshift
export RHBK_USERNAME=testdeveloper
export RHBK_PASSWORD=dummy
export MCP_CLIENT_ID=mcp-client
export MCP_SERVER_ID=mcp-server
export MCP_CLIENT_SECRET=${MCP_CLIENT_SECRET}
export MCP_SERVER_SECRET=${MCP_SERVER_SECRET}  # From script output above
export OPENSHIFT_API_SERVER=https://api.${CLUSTER_NAME}:6443
export RHBK_HOST=https://keycloak-admin.apps.${CLUSTER_NAME}
echo "MCP_SERVER_ID: $MCP_SERVER_ID"
echo "MCP_SERVER_SECRET: $MCP_SERVER_SECRET"
echo "OPENSHIFT_API_SERVER: $OPENSHIFT_API_SERVER"
echo "RHBK_USERNAME: $RHBK_USERNAME"
echo "RHBK_PASSWORD: $RHBK_PASSWORD"
echo "RHBK_REALM: $RHBK_REALM"
echo "RHBK_HOST: $RHBK_HOST"
echo "MCP_CLIENT_ID: $MCP_CLIENT_ID"

RHBK_TOKEN=$(curl -s -X POST $RHBK_HOST/realms/$RHBK_REALM/protocol/openid-connect/token \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -d scope=mcp-server \
    -d username=$RHBK_USERNAME \
    -d password=$RHBK_PASSWORD \
    -d grant_type=password \
    -d client_secret=$MCP_CLIENT_SECRET \
    -d client_id=$MCP_CLIENT_ID | jq -r '.access_token')

echo "RHBK_TOKEN: $RHBK_TOKEN"
# Decode the returned token to verify claims
jq -R 'split(".") | .[1] | @base64d | fromjson' <<< "$RHBK_TOKEN"

#Taking the role of the MCP Server, set several variables related to the mcp-server Client that is used to authenticate against RHBK


#Perform the token exchange by authenticating using the mcp-server Client, while requesting the openshift audience and the mcp:openshift scope using the RHBK_TOKEN retrieved previously:


K8S_TOKEN=$(curl -s $RHBK_HOST/realms/$RHBK_REALM/protocol/openid-connect/token \
    -d grant_type=urn:ietf:params:oauth:grant-type:token-exchange \
    -d client_id=$MCP_SERVER_ID \
    -d subject_token="$RHBK_TOKEN" \
    -d subject_token_type=urn:ietf:params:oauth:token-type:access_token \
    -d audience=openshift \
    -d client_secret=$MCP_SERVER_SECRET \
    -d requested_token_type=urn:ietf:params:oauth:token-type:access_token \
    -d scope=mcp:openshift | jq -r '.access_token')
echo "K8S_TOKEN: $K8S_TOKEN"
#Now, decode the returned JWT:

jq -R 'split(".") | .[1] | @base64d | fromjson' <<< "$K8S_TOKEN"

#Notice how it has the openshift audience which is needed to make requests to OpenShift

#Finally invoke OpenShift by first setting details related to the openshift cluster



#Retrieve information about the authenticated user

curl -k $OPENSHIFT_API_SERVER/apis/authentication.k8s.io/v1/selfsubjectreviews \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $K8S_TOKEN" \
    -X POST -d '{"kind":"SelfSubjectReview","apiVersion":"authentication.k8s.io/v1","metadata":{"creationTimestamp":null},"status":{"userInfo":{}}}'




curl -k -H "Authorization: Bearer ${K8S_TOKEN}" \
     "${OPENSHIFT_API_SERVER}/api/v1/namespaces/openshift-console/pods" | jq -r '.items[].metadata.name'

curl -k -H "Authorization: Bearer ${K8S_TOKEN}" \
     "${OPENSHIFT_API_SERVER}/api/v1/namespaces/llama-stack/pods" | jq -r '.items[].metadata.name'

# Example oc login command (replace with your cluster details):
# oc login https://api.<YOUR_CLUSTER_NAME>:6443 --issuer-url https://keycloak-admin.apps.<YOUR_CLUSTER_NAME>/realms/openshift --exec-plugin oc-oidc --client-id openshift-cli



------------------------------------------------
make build

./kubernetes-mcp-server --port 8080 --require-oauth

npx @modelcontextprotocol/inspector@latest