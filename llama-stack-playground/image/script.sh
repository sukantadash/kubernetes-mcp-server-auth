python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd src && python app.py


oc create secret docker-registry quay-io-push-secret \
  --docker-server=quay.io \
  --docker-username=sudash \
  --docker-password= \
  --docker-email=sudash@redhat.com \
  -n llama-stack

oc apply -f BuildConfig.yaml

oc start-build llama-stack-playground-build --from-dir=. --follow