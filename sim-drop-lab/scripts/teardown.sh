#!/bin/bash
# teardown.sh: stop the worker and delete everything the demo made in Azure (the whole resource group). Asks first.
# The code stays; scripts/deploy.sh all builds it again.
set -euo pipefail
# The owner's Azure sign-in lives in ~/.azure-owner (~/.azure stays empty so Can-AI's terminal never has it).
export AZURE_CONFIG_DIR=${AZURE_CONFIG_DIR:-$HOME/.azure-owner}
AZ=${AZ:-$(command -v az || echo "$HOME/.local/bin/az")}
RG=${RG:-rg-simdemo}
echo "This deletes resource group $RG and everything in it:"
"$AZ" resource list -g "$RG" --query "[].{name:name, type:type}" -o table
app_id=$("$AZ" deployment group show -g "$RG" -n main --query properties.outputs.signInAppId.value -o tsv 2>/dev/null || true)
[ -n "$app_id" ] && echo "and the sign-in app registration $app_id in Entra ID"
read -r -p "Type the group name to go on: " answer
[ "$answer" = "$RG" ] || { echo "Left as it is."; exit 1; }
systemctl --user disable --now azure-sim-worker.service 2>/dev/null || true
rm -f "$HOME/.config/azure-sim-demo/worker.env"
"$AZ" group delete -n "$RG" --yes
[ -n "$app_id" ] && "$AZ" ad app delete --id "$app_id"
echo "Deleted. The budget went with the group. (A deleted app registration stays restorable in Entra ID for 30 days.)"
