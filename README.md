# Sim Drop Lab: a hybrid Azure demo

A web page on Azure queues physics jobs; a home PC with a GPU runs them and sends the results back. The Azure side costs close to nothing
because the expensive part, the GPU, stays at home. Nothing at home is open to the internet: the worker only makes outgoing connections.

```
 browser ──sign-in (Entra ID)──► Azure Static Web App ─── page (web/) + Python API (api/), "operator" role only
                                        │  send-only key
                                        ▼
                               Azure Service Bus queue "sim-jobs" ◄─── listen-only key ─── home worker (worker/)
                                                                                              │ MuJoCo on the GPU,
                                                                                              │ via Can-AI's sim tool
                                        ┌─────────────────────────────── write-only link ─────┘
                                        ▼
                               Azure Blob Storage "results" ──30-minute read links──► browser
   Application Insights + Log Analytics: logs and traces from the API and the worker.   Budget: emails at 50/80/100 %.
```

## What each piece teaches

| Piece | Azure service | Ideas it covers | Exam areas |
|---|---|---|---|
| `infra/main.bicep` | Azure Resource Manager, Bicep | infrastructure as code, resource groups, `what-if`, outputs, `listKeys()` | AZ-104, AZ-204 |
| The page and API | Static Web Apps (Free), managed Functions (Python) | serverless HTTP functions, app settings, built-in auth, route rules, custom roles by invitation | AZ-204 |
| Sign-in | Microsoft Entra ID | identity provider, roles vs authentication, the `x-ms-client-principal` header | AZ-900, AZ-500 |
| The queue | Service Bus (Basic) | decoupling, at-least-once delivery, message locks and renewal, dead-letter queue, time-to-live | AZ-204 |
| Keys | Shared access policies | least privilege: send-only for the web, listen-only for the worker | AZ-500 |
| Results | Blob Storage | private containers, SAS links, stored access policies (revocable), lifecycle rules | AZ-104, AZ-204 |
| Monitoring | Application Insights, Log Analytics | OpenTelemetry, distributed traces, daily cap on log volume | AZ-204, AZ-104 |
| Cost | Cost Management budget | budgets and alerts | AZ-900 |
| Hybrid | (the worker) | pull-based workers, outbound-only networking, heartbeats | design |

## Running it

Needs the Azure CLI signed in (`az login`), Docker (for publishing the page), and on the worker PC Can-AI's sim tool
(`~/ai-suite/scripts/canai-sim-setup`).

```bash
scripts/deploy.sh preview   # what would be created, changes nothing in Azure except an empty resource group
scripts/deploy.sh all       # infra, worker, web, invite
```

`invite` prints a one-time link: open it, sign in with the same Microsoft account, and you have the operator role. Then open the page
`deploy.sh web` printed.

The worker runs as a user service: `systemctl --user status azure-sim-worker`, logs with `journalctl --user -u azure-sim-worker -f`.

To remove everything from Azure: `scripts/teardown.sh` (asks for the group name first).

## Cost

| Resource | Tier | Expected |
|---|---|---|
| Static Web App | Free | $0 |
| Service Bus | Basic | about $0.05 per million operations; the worker's polling is a few cents a month |
| Storage | Standard LRS, Hot | cents (jobs are deleted after 14 days) |
| Log Analytics | Pay as you go, 1 GB/day cap | first 5 GB a month free |

## Security choices

- No public blob access; every read is a SAS link for one file that expires in 30 minutes.
- The worker's upload link comes from a stored access policy: deleting the `worker` policy on the container cancels it at once.
- The worker checks every job again (`api/jobs.py` rules) and runs only drop tests with SimReady asset names; the queue is not trusted.
- The page has a strict Content-Security-Policy, and GitHub and X sign-in are switched off: only Entra ID.
- Secrets live in the Static Web App's settings and in `~/.config/azure-sim-demo/worker.env` (mode 600), never in this repo.

## Next steps to learn more

1. **Azure Arc** for the home PC: it becomes an Azure resource with its own managed identity, so the worker needs no keys at all.
2. **Managed identity + RBAC** instead of keys: move the API to a Function App on the Flex Consumption plan.
3. **Key Vault** for the API's storage key, referenced from app settings.
4. **Cloud GPU burst** (needs a pay-as-you-go subscription; Azure for Students has no GPU quota): a second worker on a spot NC-series VM
   or Azure Container Apps serverless GPUs, started when the queue is long. The queue design already allows several workers.

Contains NVIDIA SimReady assets (CC-BY-4.0).
