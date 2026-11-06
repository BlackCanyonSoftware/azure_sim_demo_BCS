#!/bin/bash
# deploy.sh: build or update the whole demo.
#
#   scripts/deploy.sh preview   what Azure would create or change (az deployment group what-if); makes only the empty resource group
#   scripts/deploy.sh infra     create/update the Azure resources from infra/main.bicep
#   scripts/deploy.sh worker    write the worker's settings (listen-only key, write-only link) and start it on this PC
#   scripts/deploy.sh web       upload app/ (the page and its API) to the App Service web app
#   scripts/deploy.sh all       infra, worker, web
#
# The signed-in account becomes the only operator (its Entra object id goes into the web app's OPERATORS setting).
# Settings (environment): RG (default rg-simdemo), LOCATION (default westus: Azure for Students allows canadacentral,
# francecentral, westus, mexicocentral and northcentralus), PREFIX (default simdemo), BUDGET_USD (default 5).
set -euo pipefail
cd "$(dirname "$0")/.."
DEMO=$PWD
AZ=${AZ:-$(command -v az || echo "$HOME/.local/bin/az")}
RG=${RG:-rg-simdemo}
LOCATION=${LOCATION:-westus}
PREFIX=${PREFIX:-simdemo}
BUDGET_USD=${BUDGET_USD:-5}
CONF=$HOME/.config/azure-sim-demo
VENV=$HOME/.local/share/azure-sim-demo/venv

say() { printf '\n== %s\n' "$*"; }
out() { "$AZ" deployment group show -g "$RG" -n main --query "properties.outputs.$1.value" -o tsv; }
params() {
  printf '%s\n' "prefix=$PREFIX" "location=$LOCATION" "operators=$("$AZ" ad signed-in-user show --query id -o tsv)" "budgetUsd=$BUDGET_USD" "budgetEmail=$("$AZ" account show --query user.name -o tsv)" "budgetStart=$(date -u +%Y-%m-01)"
}

providers() {
  say "resource providers (a one-time switch per subscription; costs nothing)"
  for p in Microsoft.Web Microsoft.ServiceBus Microsoft.Storage Microsoft.Insights Microsoft.OperationalInsights Microsoft.Consumption; do
    "$AZ" provider register -n "$p" --wait -o none
  done
}

preview() {
  providers
  say "resource group $RG in $LOCATION"
  "$AZ" group show -n "$RG" -o none 2>/dev/null || echo "(it does not exist yet: 'infra' creates it)"
  "$AZ" group create -n "$RG" -l "$LOCATION" --tags project=azure-sim-demo -o none
  "$AZ" deployment group what-if -g "$RG" -n main -f infra/main.bicep --parameters $(params)
}

infra() {
  providers
  say "resource group $RG in $LOCATION"
  "$AZ" group create -n "$RG" -l "$LOCATION" --tags project=azure-sim-demo -o none
  say "deploying infra/main.bicep (a few minutes)"
  "$AZ" deployment group create -g "$RG" -n main -f infra/main.bicep --parameters $(params) \
    --query "properties.outputs.{web:webUrl.value, signIn:signInAppId.value, storage:storageName.value, bus:busName.value}" -o table
}

worker() {
  local storage bus key policy_end sas listen
  storage=$(out storageName); bus=$(out busName)
  say "worker access: write-only link from a stored access policy, listen-only queue key"
  key=$("$AZ" storage account keys list -g "$RG" -n "$storage" --query "[0].value" -o tsv)
  policy_end=$(date -u -d "+180 days" +%Y-%m-%dT%H:%MZ)
  # ('policy show' succeeds even when the policy is missing, so look in the list instead)
  if "$AZ" storage container policy list -c results --account-name "$storage" --account-key "$key" --query "keys(@)" -o tsv | grep -qx worker; then
    "$AZ" storage container policy update -c results -n worker --permissions acw --expiry "$policy_end" --account-name "$storage" --account-key "$key" -o none
  else
    "$AZ" storage container policy create -c results -n worker --permissions acw --expiry "$policy_end" --account-name "$storage" --account-key "$key" -o none
  fi
  sas=$("$AZ" storage container generate-sas -n results --policy-name worker --account-name "$storage" --account-key "$key" -o tsv)
  listen=$("$AZ" servicebus queue authorization-rule keys list -g "$RG" --namespace-name "$bus" --queue-name sim-jobs -n worker-listen \
           --query primaryConnectionString -o tsv)
  mkdir -p "$CONF"
  ( umask 077
    printf '%s\n' "JOBS_LISTEN=$listen" "JOBS_QUEUE=sim-jobs" "RESULTS_URL=https://$storage.blob.core.windows.net/results?$sas" \
      "APPLICATIONINSIGHTS_CONNECTION_STRING=$(out insightsConnection)" > "$CONF/worker.env" )
  say "worker environment"
  UV=$(command -v uv || echo "$HOME/.local/bin/uv")
  [ -x "$VENV/bin/python" ] || "$UV" venv -q --python 3.12 "$VENV"
  "$UV" pip install -q --python "$VENV/bin/python" -r worker/requirements.txt
  mkdir -p "$HOME/.config/systemd/user"
  ln -sf "$DEMO/worker/azure-sim-worker.service" "$HOME/.config/systemd/user/azure-sim-worker.service"
  systemctl --user daemon-reload
  systemctl --user enable --now azure-sim-worker.service
  systemctl --user restart azure-sim-worker.service
  sleep 5
  systemctl --user --no-pager status azure-sim-worker.service | head -5
}

web() {
  local name zip
  name=$(out webName)
  zip=$(mktemp --suffix=.zip)
  say "uploading app/ to $name (App Service installs requirements.txt itself; the first time takes a few minutes)"
  ( cd app && python3 -I -c 'import pathlib, sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as z:
    [z.write(p) for p in sorted(pathlib.Path(".").rglob("*")) if p.is_file() and "__pycache__" not in p.parts]' "$zip" )
  "$AZ" webapp deploy -g "$RG" -n "$name" --src-path "$zip" --type zip --track-status true -o none
  rm -f "$zip"
  echo "page: $(out webUrl)   (sign in with your Microsoft account; the first visit wakes the free app up, give it a minute)"
}

case "${1:-}" in
  preview) preview ;;
  infra) infra ;;
  worker) worker ;;
  web) web ;;
  all) infra; worker; web ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
