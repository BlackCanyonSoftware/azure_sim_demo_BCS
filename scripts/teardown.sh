#!/bin/bash
# teardown.sh: stop the worker and delete everything the demo made in Azure (the whole resource group). Asks first.
# The code stays; scripts/deploy.sh all builds it again.
set -euo pipefail
AZ=${AZ:-$(command -v az || echo "$HOME/.local/bin/az")}
RG=${RG:-rg-simdemo}
echo "This deletes resource group $RG and everything in it:"
"$AZ" resource list -g "$RG" --query "[].{name:name, type:type}" -o table
read -r -p "Type the group name to go on: " answer
[ "$answer" = "$RG" ] || { echo "Left as it is."; exit 1; }
systemctl --user disable --now azure-sim-worker.service 2>/dev/null || true
rm -f "$HOME/.config/azure-sim-demo/worker.env"
"$AZ" group delete -n "$RG" --yes
echo "Deleted. The budget went with the group."
