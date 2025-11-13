#keycloak Setup

#follw the steps in the redhat-bk/script.sh file to deploy the keycloak instance and realm configuration



#Kubernetes-mcp-server Setup
export CLUSTER_NAME=$(oc get infrastructure cluster -o jsonpath='{.status.apiServerURL}' 2>/dev/null | sed 's|https://api\.||' | sed 's|:6443||' || echo "")
export MCP_SERVER_SECRET #from the redhat-bk/script.sh file
export MCP_CLIENT_SECRET #from the redhat-bk/script.sh file


cd mcp-openshift/base

envsubst < configmap.yaml.template > configmap.yaml

oc project llama-stack

oc get configmap -n openshift-config-managed default-ingress-cert \
    -o jsonpath='{.data.ca-bundle\.crt}' > keycloak-ca.crt

oc create configmap keycloak-oidc-ca \
    --from-file=ca.crt=keycloak-ca.crt \
    -n llama-stack --dry-run=client -o yaml | oc apply -f -


oc apply -k mcp-openshift/base



#openshift-ai Setup
oc apply -k openshift-ai/operator/overlays/stable-2.25
oc apply -k openshift-ai/instance/overlays/stable-2.25

#llama-stack Setup

cp llama-stack/base/llama-stack-secret.yaml.template llama-stack/base/llama-stack-secret.yaml

cd llama-stack/base
envsubst < configmap.yaml.template > configmap.yaml
#update the llama-stack-secret.yaml with the correct values

#update the auth details in the configmap.yaml
    #   auth:
    #     provider_type: "oauth2_token"
    #     config:
    #       issuer: "https://keycloak-admin.apps.<YOUR_CLUSTER_NAME>/realms/openshift"
    #       audience: "openshift"
    #       verify_tls: true
    #       claims_mapping:
    #         sub: "roles"
    #         preferred_username: "roles"
    #         groups: "teams"
    #         email: "email"
    #       jwks:
    #         uri: "https://keycloak-admin.apps.<YOUR_CLUSTER_NAME>/realms/openshift/protocol/openid-connect/certs"
    #         key_recheck_period: 3600

oc apply -k llama-stack/overlay 



#llama-stack-playground Setup

cookieSecret=$(openssl rand -base64 24)  # 24 bytes for AES (32 chars base64)
sed -i '' 's|cookieSecret: "GENERATE_RANDOM_BASE64_STRING"|cookieSecret: "'${cookieSecret}'"|g' deployment/llama-stack-playground/chart/llama-stack-playground/values.yaml

cd llama-stack-playground/chart/llama-stack-playground
envsubst < values.yaml.template > values.yaml

kustomize build --enable-helm llama-stack-playground/overlay | oc apply -f-
