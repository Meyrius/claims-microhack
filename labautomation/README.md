---
title: Customer-owned Azure setup
description: Deploy or connect the Azure resources used by the Claims MicroHack
---

# Customer-owned Azure setup

The setup supports two modes:

* `deploy` creates an isolated Foundry, Azure AI Search, and Storage stack in a
	resource group in your subscription.
* `existing` connects explicitly named resources that already exist in your
	subscription. The resources can be in different resource groups.

Both modes use the same runtime configuration and challenge instructions. They support
GitHub Codespaces and local terminals on Windows, macOS, and Linux.

## Files

* [`customer-resources.example.json`](./customer-resources.example.json) is the
	secret-free configuration template.
* [`setup_lab.py`](./setup_lab.py) validates resources, deploys infrastructure,
	configures access and connections, uploads policy documents, and creates `.env`.
* [`azuredeploy.json`](./azuredeploy.json) defines the resources created in `deploy`
	mode.

## Permissions

The person running setup must be able to:

* read the configured subscription and resources;
* create the configured resources in `deploy` mode;
* create role assignments on the Foundry, Search, and Storage resources;
* list the Search key and, when local authentication is enabled, the Foundry and
	Storage credentials; and
* create Foundry connections and upload blobs.

Azure Contributor can create resources but can't create role assignments. If your
organization separates those responsibilities, have an Owner, User Access
Administrator, or Role Based Access Control Administrator run the `connect` command or
preassign the roles listed below. See [Foundry RBAC](https://learn.microsoft.com/azure/ai-foundry/concepts/rbac-azure-ai-foundry)
and [Azure AI Search RBAC](https://learn.microsoft.com/azure/search/search-security-rbac).

The setup assigns:

* Foundry User to the project's managed identity and the optional participant;
* Cognitive Services User to the optional participant for keyless model calls;
* Search Service Contributor and Search Index Data Reader to the project identity; and
* Search Service Contributor to the Foundry account identity;
* Storage Blob Data Reader to the Search managed identity; and
* Storage Blob Data Contributor to the optional participant.

## Configure the resource contract

Copy the example without committing the resulting file:

```bash
cp labautomation/customer-resources.example.json labautomation/customer-resources.json
```

On Windows PowerShell, use:

```powershell
Copy-Item labautomation/customer-resources.example.json labautomation/customer-resources.json
```

Set `subscriptionId`, `location`, resource name prefixes, and your Microsoft Entra user
object ID. In deploy mode, setup appends a stable participant-specific suffix to the
Foundry, Search, and Storage names. Keep `participantObjectId` empty if an administrator
manages participant access separately.

Set `deployment.deployRoleAssignments` to `false` when the deployment identity can
create resources but a separate administrator will run `setup_lab.py connect`.

Before choosing a location, confirm that the configured services, SKUs, model deployments,
and model quota are available there. Availability is subscription- and model-specific;
ARM validation reports an unavailable resource, model, SKU, or capacity before setup
writes `.env`. Run the subscription-aware preflight to check the configured region and
receive up to three alternatives that support the complete lab stack:

```bash
python labautomation/setup_lab.py --config labautomation/customer-resources.json check-region
```

The `deploy` and `all` commands run this preflight automatically before creating resources.
The preflight checks provider registration and regional support for Storage, Azure AI
Search, and Microsoft Foundry; StorageV2 `Standard_LRS` subscription availability;
the Search `Basic` offering; and both model SKUs and their remaining quota. Child
resources inherit their parent scope.
The subsequent ARM validation remains authoritative for Azure Policy, permissions, name
availability, and service capacity changes after the check.

### Use an organizer-hosted Document AI deployment

Subscriptions that cannot purchase the Mistral partner offer can use a deployment hosted
by the hackathon organizer. Set `deployment.deployDocumentAi` to `false`; the setup then
deploys and validates the primary model but skips the Mistral deployment and its quota
check. After setup writes `.env`, replace these values with the endpoint and deployment
details supplied by the organizer:

```dotenv
MISTRAL_DOCUMENT_AI_ENDPOINT=https://<organizer-account>.services.ai.azure.com
MISTRAL_DOCUMENT_AI_KEY=<shared-through-a-secret-channel>
MISTRAL_DOCUMENT_AI_DEPLOYMENT_NAME=mistral-document-ai-2512
MISTRAL_DOCUMENT_AI_API_VERSION=2024-05-01-preview
```

Do not store the key in `customer-resources.json` or commit `.env`. The organizer should
use a dedicated Foundry account for the event, monitor its quota and cost, and rotate the
key after the event. Running `setup_lab.py all` again rewrites `.env`, so reapply the
organizer values afterward.

The deploy template keeps Storage shared-key authentication disabled, uses Microsoft
Entra ID for Blob operations, and enables the public Blob endpoint required by a local
Dev Container. Customer Azure Policies can deny the deployment or modify these
settings. Review effective policy assignments with the customer's Azure administrator
and use an approved exception or private-connectivity design when required.

### Use existing resources

Change `mode` to `existing`, set every resource name and its actual resource group,
and set `deployment.deployModels` to `false`. The Foundry project must belong to the
configured Foundry account. The primary deployment and, unless
`deployment.deployDocumentAi` is `false`, the Document AI deployment must already exist
in that account.

The initial implementation supports resources in one tenant and subscription. It does
not connect resources across tenants or subscriptions.

## Run setup

Sign in and select the subscription:

```bash
az login
az account set --subscription "<subscription-id>"
```

In a browser-based Codespace, `az login --use-device-code` is also available.

Validate the local contract without changing Azure:

```bash
python labautomation/setup_lab.py --config labautomation/customer-resources.json check
```

Run the complete setup. In `deploy` mode, `--what-if` previews the ARM changes before
the same command deploys them:

```bash
python labautomation/setup_lab.py --config labautomation/customer-resources.json all --what-if
```

In `existing` mode, `all` skips deployment and only validates, connects, uploads, and
configures the named resources.

You can also run the phases independently:

```bash
python labautomation/setup_lab.py --config labautomation/customer-resources.json check --azure
python labautomation/setup_lab.py --config labautomation/customer-resources.json deploy --what-if
python labautomation/setup_lab.py --config labautomation/customer-resources.json connect
python labautomation/setup_lab.py --config labautomation/customer-resources.json configure
```

`configure` creates the two Blob containers, uploads the five policy Markdown files,
and writes the repository-root `.env`. It doesn't print credential values and restricts
the file to the current user where supported by the operating system. Foundry model and
Storage operations use Microsoft Entra ID when local authentication is disabled. Search
continues to use an admin key for the challenge scripts and Foundry Search connection.
For a production workload, replace that remaining durable key with Microsoft Entra ID
and use approved private connectivity.

## What remains participant work

Infrastructure setup does not create or populate the `crash-statements` index and does
not create the knowledge sources, knowledge bases, or agents. Those are learning tasks
in Challenges 2 and 3:

1. `docs/index_crash_statements.py` creates and populates the Search index.
2. `docs/create_knowledge_base.py` creates the statement knowledge source and base.
3. `docs/create_knowledge_base.py --policies` creates the policy knowledge source and
	 base from the uploaded policy files.
4. The agent scripts register the two Foundry agents.

The RemoteTool connections can exist before their knowledge bases. They become usable
after the matching knowledge bases are created.

## Cleanup

Cleanup is available only when `deploy` mode created a new resource group and the
local ownership record plus Azure resource-group tags still match. It refuses to
delete a pre-existing or externally managed group:

```bash
python labautomation/setup_lab.py --config labautomation/customer-resources.json cleanup --yes
```

The command refuses to delete resources in `existing` mode.