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

Set `subscriptionId`, `location`, globally unique resource names, and your Microsoft
Entra user object ID. Keep `participantObjectId` empty if an administrator manages
participant access separately.

Set `deployment.deployRoleAssignments` to `false` when the deployment identity can
create resources but a separate administrator will run `setup_lab.py connect`.

Before choosing a location, confirm that both configured model deployments and enough
quota are available there. Availability is subscription- and model-specific; ARM
validation reports an unavailable model, SKU, or capacity before setup writes `.env`.

The deploy template keeps Storage shared-key authentication disabled, uses Microsoft
Entra ID for Blob operations, and enables the public Blob endpoint required by a local
Dev Container. Customer Azure Policies can deny the deployment or modify these
settings. Review effective policy assignments with the customer's Azure administrator
and use an approved exception or private-connectivity design when required.

### Use existing resources

Change `mode` to `existing`, set every resource name and its actual resource group,
and set `deployment.deployModels` to `false`. The Foundry project must belong to the
configured Foundry account, and both configured model deployment names must already
exist in that account.

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