#!/bin/bash
# deploy.sh: build or update the whole demo.
#
#   scripts/deploy.sh preview   what Azure would create or change (az deployment group what-if); makes only the empty resource group
#   scripts/deploy.sh infra     create/update the Azure resources from infra/main.bicep
#   scripts/deploy.sh worker    write the worker's settings (listen-only key, write-only link) and start it on this PC
#   scripts/deploy.sh web       publish the page and its API to the Static Web App
#   scripts/deploy.sh invite    invite the signed-in account as "operator" (prints a link to open once)
#   scripts/deploy.sh all       infra, worker, web, invite
#
# Settings (environment): RG (default rg-simdemo), LOCATION (default eastus2), PREFIX (default simdemo), BUDGET_USD (default 5).
set -euo pipefail
cd "$(dirname "$0")/.."
DEMO=$PWD
AZ=${AZ:-$(command -v az || echo "$HOME/.local/bin/az")}
RG=${RG:-rg-simdemo}
LOCATION=${LOCATION:-eastus2}
PREFIX=${PREFIX:-simdemo}
BUDGET_USD=${BUDGET_USD:-5}
CONF=$HOME/.config/azure-sim-demo
VENV=$HOME/.local/share/azure-sim-demo/venv

say() { printf '\n== %s\n' "$*"; }
out() { "$AZ" deployment group show -g "$RG" -n main --query "properties.outputs.$1.value" -o tsv; }
params() {
  printf '%s\n' "prefix=$PREFIX" "budgetUsd=$BUDGET_USD" "budgetEmail=$("$AZ" account show --query user.name -o tsv)" "budgetStart=$(date -u +%Y-%m-01)"
}

preview() {
  say "resource group $RG in $LOCATION"
  "$AZ" group show -n "$RG" -o none 2>/dev/null || echo "(it does not exist yet: 'infra' creates it)"
  "$AZ" group create -n "$RG" -l "$LOCATION" --tags project=azure-sim-demo -o none
  "$AZ" deployment group what-if -g "$RG" -n main -f infra/main.bicep --parameters $(params)
}

infra() {
  say "resource providers"
  for p in Microsoft.Web Microsoft.ServiceBus Microsoft.Storage Microsoft.Insights Microsoft.OperationalInsights Microsoft.Consumption; do
    "$AZ" provider register -n "$p" --wait -o none
  done
  say "resource group $RG in $LOCATION"
  "$AZ" group create -n "$RG" -l "$LOCATION" --tags project=azure-sim-demo -o none
  say "deploying infra/main.bicep (a few minutes)"
  "$AZ" deployment group create -g "$RG" -n main -f infra/main.bicep --parameters $(params) \
    --query "properties.outputs.{web:webUrl.value, storage:storageName.value, bus:busName.value}" -o table
}

worker() {
  local storage bus key policy_end sas listen
  storage=$(out storageName); bus=$(out busName)
  say "worker access: write-only link from a stored access policy, listen-only queue key"
  key=$("$AZ" storage account keys list -g "$RG" -n "$storage" --query "[0].value" -o tsv)
  policy_end=$(date -u -d "+180 days" +%Y-%m-%dT%H:%MZ)
  if "$AZ" storage container policy show -c results -n worker --account-name "$storage" --account-key "$key" -o none 2>/dev/null; then
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
  local name token
  name=$(out webName)
  token=$("$AZ" staticwebapp secrets list -n "$name" -g "$RG" --query properties.apiKey -o tsv)
  say "publishing web/ and api/ to $name (the Static Web Apps CLI, in a throwaway Node container)"
  docker run --rm -e SWA_CLI_DEPLOYMENT_TOKEN="$token" -v "$DEMO:/src" -w /src node:22-bookworm \
    npx -y @azure/static-web-apps-cli@2.0.2 deploy ./web --api-location ./api --api-language python --api-version 3.11 --env production
  echo "page: $(out webUrl)"
}

invite() {
  local name host who
  name=$(out webName); host=$(out webUrl); host=${host#https://}
  who=$("$AZ" account show --query user.name -o tsv)
  say "inviting $who as operator (the link works once, for 24 hours)"
  "$AZ" staticwebapp users invite -n "$name" -g "$RG" --authentication-provider aad --user-details "$who" --role operator \
    --domain "$host" --invitation-expiration-in-hours 24 --query invitationUrl -o tsv
}

case "${1:-}" in
  preview) preview ;;
  infra) infra ;;
  worker) worker ;;
  web) web ;;
  invite) invite ;;
  all) infra; worker; web; invite ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
