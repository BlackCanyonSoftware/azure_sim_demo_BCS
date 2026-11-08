# Sim Drop Lab: a hybrid Azure demo

A web page on Azure queues physics jobs; a home PC with a GPU runs them and sends the results back. The Azure side costs close to nothing
because the expensive part, the GPU, stays at home. Nothing at home is open to the internet: the worker only makes outgoing connections.

```
 browser ──sign-in (Entra ID)──► App Service web app (app/): page + Python API, Free F1, operators only
                                        │  managed identity, role "Service Bus Data Sender" (no keys)
                                        ▼
                               Azure Service Bus queue "sim-jobs" ◄─── listen-only key ─── home worker (worker/)
                                                                                              │ MuJoCo on the GPU,
                                                                                              │ via Can-AI's sim tool
                                        ┌─────────────────────────────── write-only link ─────┘
                                        ▼
                               Azure Blob Storage "results" ──30-minute read links (user delegation SAS)──► browser
   Application Insights + Log Analytics: logs and traces from the web app and the worker.   Budget: emails at 50/80/100 %.
```

## What each piece teaches

| Piece | Azure service | Ideas it covers | Exam areas |
|---|---|---|---|
| `infra/main.bicep` | Azure Resource Manager, Bicep (+ its Microsoft Graph extension) | infrastructure as code, resource groups, `what-if`, outputs, creating an Entra app registration from the same file | AZ-104, AZ-204 |
| The page and API | App Service (Linux, Free F1), Python/Flask | App Service plans, app settings, build on deploy (Oryx), startup command, deploying with an Entra sign-in instead of publishing passwords | AZ-204, AZ-104 |
| Sign-in | Microsoft Entra ID, App Service authentication ("Easy Auth") | app registrations vs service principals, single-tenant apps, ID tokens without a client secret, the `X-MS-CLIENT-PRINCIPAL-*` headers, authorisation in the app (operator list) | AZ-900, AZ-500, AZ-204 |
| No keys in the web app | Managed identity, Azure RBAC | system-assigned identity, role assignments at the smallest scope (one container, one queue), data-plane vs control-plane roles | AZ-500, AZ-104 |
| The queue | Service Bus (Basic) | decoupling, at-least-once delivery, message locks and renewal, dead-letter queue, time-to-live | AZ-204 |
| Worker keys | Shared access policies | least privilege for a machine outside Azure: a listen-only queue key, a write-only container link | AZ-500 |
| Results | Blob Storage | private containers, user delegation SAS (signed with a key lent to an identity), stored access policies (revocable), lifecycle rules | AZ-104, AZ-204 |
| Monitoring | Application Insights, Log Analytics | OpenTelemetry, distributed traces, daily cap on log volume | AZ-204, AZ-104 |
| Cost | Cost Management budget | budgets and alerts | AZ-900 |
| Governance | Azure Policy (the subscription's own) | Azure for Students has an "allowed regions" policy; deployments outside it are refused | AZ-104 |
| Hybrid | (the worker) | pull-based workers, outbound-only networking, heartbeats | design |

## Running it

Needs the Azure CLI signed in as you, kept in `~/.azure-owner` (`az-owner login`, or `AZURE_CONFIG_DIR=~/.azure-owner az login`) and, on the worker PC, Can-AI's sim tool (`~/ai-suite/scripts/canai-sim-setup`).

```bash
scripts/deploy.sh preview   # what would be created; changes nothing except registering providers and an empty resource group
scripts/deploy.sh all       # infra, worker, web
```

The signed-in account is the only operator. Open the page `deploy.sh web` printed and sign in with the same Microsoft account; the
first time, Entra ID asks you to allow the app to read your basic profile.

To let someone else from the same directory in, add their Entra object id to the web app's `OPERATORS` setting (comma-separated).

The worker runs as a user service: `systemctl --user status azure-sim-worker`, logs with `journalctl --user -u azure-sim-worker -f`.

To remove everything: `scripts/teardown.sh` (asks for the group name first; it also deletes the sign-in app registration).

## Regions

Azure for Students only allows deployments in `canadacentral`, `francecentral`, `westus`, `mexicocentral` and `northcentralus` (check
yours with `az policy assignment list`). The demo uses `westus`; set `LOCATION` to pick another. Azure Static Web Apps, the first
design, runs in none of these regions, which is why the page is an App Service web app.

## Cost

| Resource | Tier | Expected |
|---|---|---|
| App Service plan | Free F1 (Linux) | $0 (60 CPU minutes a day; the app sleeps when idle) |
| Service Bus | Basic | about $0.05 per million operations; the worker's polling is a few cents a month |
| Storage | Standard LRS, Hot | cents (jobs are deleted after 14 days) |
| Log Analytics | Pay as you go, 1 GB/day cap | first 5 GB a month free |

## Security choices

- Sign-in is required for every page and file (App Service authentication), only accounts in the directory can sign in, and only
  listed operators can use the API.
- The web app holds no keys or passwords: it uses its managed identity, with roles on just the results container and the job queue.
  There is no client secret for sign-in either, and password-based publishing (FTP and Kudu basic auth) is switched off.
- No public blob access; every read is a link for one file that expires in 30 minutes.
- The worker's upload link comes from a stored access policy: deleting the `worker` policy on the container cancels it at once.
- The worker checks every job again (`app/jobs.py` rules) and runs only drop tests with SimReady asset names; the queue is not trusted.
- The page has a strict Content-Security-Policy.
- The worker's keys live in `~/.config/azure-sim-demo/worker.env` (mode 600), never in this repo.

## Next steps to learn more

1. **Azure Arc** for the home PC: it becomes an Azure resource with its own managed identity, so the worker needs no keys either.
2. **Key Vault**: move the worker's keys there and have the worker fetch them with its Arc identity.
3. **App roles**: give the app registration an "operator" role and assign people in Entra ID, instead of the `OPERATORS` list.
4. **Cloud GPU burst** (needs a pay-as-you-go subscription; Azure for Students has no GPU quota): a second worker on a spot NC-series VM
   or Azure Container Apps serverless GPUs, started when the queue is long. The queue design already allows several workers.

Contains NVIDIA SimReady assets (CC-BY-4.0).
