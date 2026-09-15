#!/usr/bin/env python3
"""Set up the Claims MicroHack with customer-owned Azure resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_FILE = Path(__file__).resolve().parent / "azuredeploy.json"
DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent / "customer-resources.json"
STATE_FILE = Path(__file__).resolve().parent / ".setup-state.json"
ENV_FILE = REPO_ROOT / ".env"
FOUNDRY_API_VERSION = "2025-04-01-preview"
MODEL_DEPLOYMENT_API_VERSION = "2024-10-01"
PROJECT_CONNECTION_API_VERSION = "2025-10-01-preview"
SEARCH_API_VERSION = "2023-11-01"
STORAGE_API_VERSION = "2023-05-01"
FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
COGNITIVE_SERVICES_USER_ROLE_ID = "a97b65f3-24c7-4388-baec-2e87135dc908"
SEARCH_SERVICE_CONTRIBUTOR_ROLE_ID = "7ca78c08-252a-4471-8644-bb5ff32d4ba0"
SEARCH_INDEX_DATA_READER_ROLE_ID = "1407120a-92aa-4202-b7e9-c0e197c71c8f"
STORAGE_BLOB_DATA_READER_ROLE_ID = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
STORAGE_BLOB_DATA_CONTRIBUTOR_ROLE_ID = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
AZURE_PROGRESS_INTERVAL_SECONDS = 30


class SetupError(RuntimeError):
    """A user-correctable setup failure."""


@dataclass(frozen=True)
class ResourceRef:
    resource_group: str
    name: str


def _run_process_with_progress(
    command: list[str],
    environment: dict[str, str] | None,
    progress_label: str,
) -> subprocess.CompletedProcess[str]:
    stop_reporting = threading.Event()

    def report_progress() -> None:
        elapsed = AZURE_PROGRESS_INTERVAL_SECONDS
        while not stop_reporting.wait(AZURE_PROGRESS_INTERVAL_SECONDS):
            print(f"  {progress_label} still running ({elapsed}s elapsed)...", flush=True)
            elapsed += AZURE_PROGRESS_INTERVAL_SECONDS

    reporter = threading.Thread(target=report_progress, daemon=True)
    reporter.start()
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
    finally:
        stop_reporting.set()
        reporter.join()


class AzureCli:
    def __init__(self, subscription_id: str, tenant_id: str = "") -> None:
        self.subscription_id = subscription_id
        self.tenant_id = tenant_id

    def run(
        self,
        *arguments: str,
        expect_json: bool = False,
        secret: bool = False,
        environment: dict[str, str] | None = None,
        allow_not_found: bool = False,
        progress_label: str = "",
    ) -> Any:
        command = ["az", *arguments, "--subscription", self.subscription_id]
        if expect_json:
            command.extend(["--output", "json"])
        try:
            process_environment = None
            if environment:
                process_environment = os.environ.copy()
                process_environment.update(environment)
            if progress_label:
                result = _run_process_with_progress(command, process_environment, progress_label)
            else:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    env=process_environment,
                )
        except FileNotFoundError as exc:
            raise SetupError("Azure CLI was not found. Install 'az' and run the command again.") from exc
        if result.returncode != 0:
            failure_text = f"{result.stderr}\n{result.stdout}".lower()
            if allow_not_found and any(
                marker in failure_text
                for marker in ("404", "notfound", "not found", "resourcenotfound")
            ):
                return None
            details = "Azure CLI returned an error while reading a secret." if secret else result.stderr.strip()
            if not secret and details.lower().startswith("error:"):
                details = details[6:].lstrip()
            raise SetupError(details or f"Azure CLI command failed: {' '.join(command[:3])}")
        output = result.stdout.strip()
        if expect_json:
            return json.loads(output) if output else None
        return output

    def ensure_session(self) -> None:
        account = self.run("account", "show", expect_json=True)
        if not account:
            raise SetupError("Azure CLI is not signed in. Run 'az login' first.")
        if account.get("id", "").lower() != self.subscription_id.lower():
            self.run("account", "set", "--subscription", self.subscription_id)
            account = self.run("account", "show", expect_json=True)
        if self.tenant_id and account.get("tenantId", "").lower() != self.tenant_id.lower():
            raise SetupError(
                "The Azure CLI session uses a different tenant. "
                f"Run 'az login --tenant {self.tenant_id}'."
            )


def management_url(resource_identifier: str, api_version: str) -> str:
    return f"https://management.azure.com{resource_identifier}?api-version={api_version}"


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SetupError(f"Configuration value '{path}' must be a non-empty string.")
    return value.strip()


def _azure_uuid(value: Any, path: str, required: bool = True) -> str:
    text = _required_string(value, path) if required else str(value or "").strip()
    if not text:
        return ""
    try:
        identifier = uuid.UUID(text)
    except ValueError as exc:
        raise SetupError(f"Configuration value '{path}' must be a valid UUID.") from exc
    if identifier.int == 0:
        raise SetupError(f"Configuration value '{path}' still contains the example placeholder UUID.")
    return text


def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    if not isinstance(value, dict):
        raise SetupError(f"Configuration section '{name}' must be an object.")
    return value


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SetupError(
            f"Configuration file not found: {path}. Copy customer-resources.example.json first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise SetupError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise SetupError("The customer resource configuration must be a JSON object.")
    validate_config(config)
    _apply_unique_resource_names(config)
    return config


def _apply_unique_resource_names(config: dict[str, Any]) -> None:
    if config["mode"] != "deploy":
        return
    deployment = config["deployment"]
    seed = "/".join(
        (
            config["subscriptionId"],
            deployment["resourceGroup"],
            config.get("participantObjectId", ""),
        )
    )
    suffix = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    resources = config["resources"]
    for resource_name, maximum_length, separator in (
        ("foundryAccount", 64, "-"),
        ("searchService", 60, "-"),
        ("storageAccount", 24, ""),
    ):
        resource = resources[resource_name]
        available_length = maximum_length - len(separator) - len(suffix)
        resource["name"] = f"{resource['name'][:available_length]}{separator}{suffix}"


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schemaVersion") != 1:
        raise SetupError("Only customer resource schemaVersion 1 is supported.")
    mode = _required_string(config.get("mode"), "mode")
    if mode not in {"deploy", "existing"}:
        raise SetupError("Configuration value 'mode' must be 'deploy' or 'existing'.")
    _azure_uuid(config.get("subscriptionId"), "subscriptionId")
    _azure_uuid(config.get("tenantId"), "tenantId", required=False)
    _azure_uuid(config.get("participantObjectId"), "participantObjectId", required=False)
    _required_string(config.get("location"), "location")

    deployment = _section(config, "deployment")
    _required_string(deployment.get("name"), "deployment.name")
    _required_string(deployment.get("resourceGroup"), "deployment.resourceGroup")
    for field_name in ("createResourceGroup", "deployModels", "deployRoleAssignments"):
        if not isinstance(deployment.get(field_name), bool):
            raise SetupError(f"Configuration value 'deployment.{field_name}' must be true or false.")

    resources = _section(config, "resources")
    for resource_name in ("foundryAccount", "searchService", "storageAccount"):
        resource = _section(resources, resource_name)
        _required_string(resource.get("resourceGroup"), f"resources.{resource_name}.resourceGroup")
        _required_string(resource.get("name"), f"resources.{resource_name}.name")
    foundry_project = _section(resources, "foundryProject")
    _required_string(foundry_project.get("name"), "resources.foundryProject.name")

    models = _section(config, "models")
    for model_name in ("primary", "documentAi"):
        model = _section(models, model_name)
        for field_name in ("deploymentName", "modelName", "format", "skuName"):
            _required_string(model.get(field_name), f"models.{model_name}.{field_name}")
        capacity = model.get("capacity")
        if not isinstance(capacity, int) or capacity < 1:
            raise SetupError(f"Configuration value 'models.{model_name}.capacity' must be positive.")
    _required_string(models["documentAi"].get("apiVersion"), "models.documentAi.apiVersion")

    for section_name, fields in {
        "search": (
            "indexName",
            "semanticConfigurationName",
            "knowledgeSourceName",
            "knowledgeBaseName",
            "apiVersion",
            "mcpApiVersion",
        ),
        "policies": ("containerName", "knowledgeSourceName", "knowledgeBaseName"),
        "claims": ("containerName",),
        "connections": ("searchName", "crashStatementsName", "policiesName"),
    }.items():
        section = _section(config, section_name)
        for field_name in fields:
            _required_string(section.get(field_name), f"{section_name}.{field_name}")

    resource_groups = {
        resources[name]["resourceGroup"]
        for name in ("foundryAccount", "searchService", "storageAccount")
    }
    if mode == "deploy" and resource_groups != {deployment["resourceGroup"]}:
        raise SetupError(
            "Deploy mode creates all resources in deployment.resourceGroup. "
            "Set each resourceGroup to that same value."
        )


def resource_ref(config: dict[str, Any], name: str) -> ResourceRef:
    resource = config["resources"][name]
    return ResourceRef(resource_group=resource["resourceGroup"], name=resource["name"])


def resource_id(config: dict[str, Any], name: str) -> str:
    subscription_id = config["subscriptionId"]
    ref = resource_ref(config, name)
    resource_type = {
        "foundryAccount": "Microsoft.CognitiveServices/accounts",
        "searchService": "Microsoft.Search/searchServices",
        "storageAccount": "Microsoft.Storage/storageAccounts",
    }[name]
    return f"/subscriptions/{subscription_id}/resourceGroups/{ref.resource_group}/providers/{resource_type}/{ref.name}"


def project_resource_id(config: dict[str, Any]) -> str:
    return (
        f"{resource_id(config, 'foundryAccount')}/projects/"
        f"{config['resources']['foundryProject']['name']}"
    )


def deployment_parameters(config: dict[str, Any]) -> dict[str, Any]:
    primary = config["models"]["primary"]
    document_ai = config["models"]["documentAi"]
    return {
        "location": {"value": config["location"]},
        "deployModelDeployments": {"value": bool(config["deployment"].get("deployModels", True))},
        "deployRoleAssignments": {
            "value": bool(config["deployment"].get("deployRoleAssignments", True))
        },
        "foundryAccountName": {"value": resource_ref(config, "foundryAccount").name},
        "foundryProjectName": {"value": config["resources"]["foundryProject"]["name"]},
        "searchServiceName": {"value": resource_ref(config, "searchService").name},
        "storageAccountName": {"value": resource_ref(config, "storageAccount").name},
        "blobContainerName": {"value": config["claims"]["containerName"]},
        "policiesContainerName": {"value": config["policies"]["containerName"]},
        "primaryModelDeploymentName": {"value": primary["deploymentName"]},
        "primaryModelName": {"value": primary["modelName"]},
        "primaryModelFormat": {"value": primary["format"]},
        "primaryModelSkuName": {"value": primary["skuName"]},
        "primaryModelCapacity": {"value": primary["capacity"]},
        "documentAiDeploymentName": {"value": document_ai["deploymentName"]},
        "documentAiModelName": {"value": document_ai["modelName"]},
        "documentAiModelVersion": {"value": document_ai.get("modelVersion", "1")},
        "documentAiModelFormat": {"value": document_ai["format"]},
        "documentAiModelPublisher": {"value": document_ai.get("publisher", "Mistral AI")},
        "documentAiSkuName": {"value": document_ai["skuName"]},
        "documentAiCapacity": {"value": document_ai["capacity"]},
        "searchConnectionName": {"value": config["connections"]["searchName"]},
        "crashStatementsConnectionName": {"value": config["connections"]["crashStatementsName"]},
        "policiesConnectionName": {"value": config["connections"]["policiesName"]},
        "crashStatementsKnowledgeBaseName": {"value": config["search"]["knowledgeBaseName"]},
        "policiesKnowledgeBaseName": {"value": config["policies"]["knowledgeBaseName"]},
        "mcpApiVersion": {"value": config["search"]["mcpApiVersion"]},
        "participantObjectId": {"value": config.get("participantObjectId", "")},
        "resourceTags": {"value": config.get("tags", {})},
    }


def _parameter_file(config: dict[str, Any]) -> Path:
    payload = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": deployment_parameters(config),
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".parameters.json", encoding="utf-8", delete=False
    ) as handle:
        json.dump(payload, handle, indent=2)
    return Path(handle.name)


def _state_matches(config: dict[str, Any]) -> bool:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return state == {
        "schemaVersion": 1,
        "subscriptionId": config["subscriptionId"],
        "resourceGroup": config["deployment"]["resourceGroup"],
        "deploymentName": config["deployment"]["name"],
        "createdResourceGroup": True,
    }


def _record_resource_group_ownership(config: dict[str, Any]) -> None:
    state = {
        "schemaVersion": 1,
        "subscriptionId": config["subscriptionId"],
        "resourceGroup": config["deployment"]["resourceGroup"],
        "deploymentName": config["deployment"]["name"],
        "createdResourceGroup": True,
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".setup-state.", dir=STATE_FILE.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary_file:
            json.dump(state, temporary_file, indent=2)
            temporary_file.write("\n")
        os.chmod(temporary_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary_name, STATE_FILE)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _unique_deployment_name(base_name: str) -> str:
    suffix = uuid.uuid4().hex
    return f"{base_name[:31]}-{suffix}"


def deploy(config: dict[str, Any], cli: AzureCli, what_if: bool) -> None:
    if config["mode"] != "deploy":
        raise SetupError("The deploy command requires configuration mode 'deploy'.")
    deployment = config["deployment"]
    resource_group = deployment["resourceGroup"]
    deployment_name = _unique_deployment_name(deployment["name"])
    group_exists = cli.run("group", "exists", "--name", resource_group).lower() == "true"
    if deployment.get("createResourceGroup", True):
        if group_exists and not _state_matches(config):
            raise SetupError(
                f"Resource group '{resource_group}' already exists and wasn't created by this setup. "
                "Choose a new group or set deployment.createResourceGroup to false."
            )
        if not group_exists:
            cli.run(
                "group",
                "create",
                "--name",
                resource_group,
                "--location",
                config["location"],
                "--tags",
                "workload=claims-microhack",
                "managed-by=setup_lab.py",
                f"deployment={deployment['name']}",
            )
            _record_resource_group_ownership(config)
    else:
        if not group_exists:
            raise SetupError(
                f"Resource group '{resource_group}' doesn't exist and createResourceGroup is false."
            )
        cli.run("group", "show", "--name", resource_group)

    parameter_file = _parameter_file(config)
    try:
        common = (
            "--name",
            deployment_name,
            "--resource-group",
            resource_group,
            "--template-file",
            str(TEMPLATE_FILE),
            "--parameters",
            f"@{parameter_file}",
        )
        print("Validating the Azure deployment...")
        cli.run(
            "deployment", "group", "validate", *common, progress_label="Deployment validation"
        )
        if what_if:
            print("Previewing Azure changes...")
            cli.run("deployment", "group", "what-if", *common, progress_label="What-if preview")
        print("Deploying Azure resources...")
        cli.run("deployment", "group", "create", *common, progress_label="Resource deployment")
    finally:
        parameter_file.unlink(missing_ok=True)


def _resource_api_version(identifier: str) -> str:
    normalized = identifier.lower()
    if "/microsoft.search/searchservices/" in normalized:
        return SEARCH_API_VERSION
    if "/microsoft.storage/storageaccounts/" in normalized:
        return STORAGE_API_VERSION
    return FOUNDRY_API_VERSION


def _resource_details(cli: AzureCli, identifier: str) -> dict[str, Any]:
    return cli.run(
        "rest",
        "--method",
        "get",
        "--url",
        management_url(identifier, _resource_api_version(identifier)),
        expect_json=True,
    )


def _principal_id(cli: AzureCli, identifier: str) -> str:
    principal_id = cli.run(
        "rest",
        "--method",
        "get",
        "--url",
        management_url(identifier, _resource_api_version(identifier)),
        "--query",
        "identity.principalId",
        "--output",
        "tsv",
    )
    if not principal_id:
        raise SetupError(f"Resource has no system-assigned managed identity: {identifier}")
    return principal_id


def _has_role_assignment(
    cli: AzureCli,
    principal_id: str,
    role_id: str,
    scope: str,
) -> bool:
    assignments = cli.run(
        "role",
        "assignment",
        "list",
        "--assignee-object-id",
        principal_id,
        "--scope",
        scope,
        expect_json=True,
    ) or []
    return any(
        item.get("roleDefinitionId", "").lower().endswith(role_id.lower())
        for item in assignments
    )


def _ensure_role_assignment(
    cli: AzureCli,
    principal_id: str,
    principal_type: str,
    role_id: str,
    scope: str,
    label: str,
) -> None:
    if not _has_role_assignment(cli, principal_id, role_id, scope):
        cli.run(
            "role",
            "assignment",
            "create",
            "--assignee-object-id",
            principal_id,
            "--assignee-principal-type",
            principal_type,
            "--role",
            role_id,
            "--scope",
            scope,
        )
    print(f"  [ok] {label}")


def ensure_access(config: dict[str, Any], cli: AzureCli) -> None:
    foundry_id = resource_id(config, "foundryAccount")
    search_id = resource_id(config, "searchService")
    storage_id = resource_id(config, "storageAccount")
    project_id = project_resource_id(config)
    project_principal_id = _principal_id(cli, project_id)
    foundry_principal_id = _principal_id(cli, foundry_id)
    search_principal_id = _principal_id(cli, search_id)

    _ensure_role_assignment(
        cli,
        project_principal_id,
        "ServicePrincipal",
        FOUNDRY_USER_ROLE_ID,
        foundry_id,
        "Project managed identity can use Foundry",
    )
    _ensure_role_assignment(
        cli,
        project_principal_id,
        "ServicePrincipal",
        SEARCH_SERVICE_CONTRIBUTOR_ROLE_ID,
        search_id,
        "Project managed identity can manage Search objects",
    )
    _ensure_role_assignment(
        cli,
        project_principal_id,
        "ServicePrincipal",
        SEARCH_INDEX_DATA_READER_ROLE_ID,
        search_id,
        "Project managed identity can retrieve Search data",
    )
    _ensure_role_assignment(
        cli,
        foundry_principal_id,
        "ServicePrincipal",
        SEARCH_SERVICE_CONTRIBUTOR_ROLE_ID,
        search_id,
        "Foundry managed identity can manage Search objects",
    )
    _ensure_role_assignment(
        cli,
        search_principal_id,
        "ServicePrincipal",
        STORAGE_BLOB_DATA_READER_ROLE_ID,
        storage_id,
        "Search managed identity can read Storage blobs",
    )

    participant_object_id = config.get("participantObjectId", "").strip()
    if participant_object_id:
        _ensure_role_assignment(
            cli,
            participant_object_id,
            "User",
            FOUNDRY_USER_ROLE_ID,
            foundry_id,
            "Participant can develop in Foundry",
        )
        _ensure_role_assignment(
            cli,
            participant_object_id,
            "User",
            COGNITIVE_SERVICES_USER_ROLE_ID,
            foundry_id,
            "Participant can call Foundry data-plane APIs",
        )
        _ensure_role_assignment(
            cli,
            participant_object_id,
            "User",
            STORAGE_BLOB_DATA_CONTRIBUTOR_ROLE_ID,
            storage_id,
            "Participant can manage Storage blobs",
        )


def _put_connection(
    cli: AzureCli,
    identifier: str,
    api_version: str,
    properties: dict[str, Any],
    contains_secret: bool = False,
) -> None:
    desired_category = properties.get("category", "")
    desired_target = properties.get("target", "").rstrip("/")
    existing = cli.run(
        "rest",
        "--method",
        "get",
        "--url",
        management_url(identifier, api_version),
        expect_json=True,
        secret=contains_secret,
        allow_not_found=True,
    )
    if existing:
        existing_properties = existing.get("properties", {})
        existing_category = existing_properties.get("category", "")
        existing_target = existing_properties.get("target", "").rstrip("/")
        if (existing_category, existing_target) != (desired_category, desired_target):
            raise SetupError(
                f"Connection name '{identifier.rsplit('/', 1)[-1]}' is already used for "
                f"category '{existing_category}' and target '{existing_target}'. "
                "Choose a dedicated connection name in customer-resources.json."
            )

    descriptor, body_file_name = tempfile.mkstemp(suffix=".json", text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as body_file:
            json.dump({"properties": properties}, body_file, separators=(",", ":"))
        os.chmod(body_file_name, stat.S_IRUSR | stat.S_IWUSR)
        cli.run(
            "rest",
            "--method",
            "put",
            "--url",
            management_url(identifier, api_version),
            "--body",
            f"@{body_file_name}",
            secret=contains_secret,
        )
    finally:
        Path(body_file_name).unlink(missing_ok=True)


def ensure_connections(config: dict[str, Any], cli: AzureCli) -> None:
    foundry_id = resource_id(config, "foundryAccount")
    project_id = project_resource_id(config)
    search_id = resource_id(config, "searchService")
    search = resource_ref(config, "searchService")
    search_endpoint = f"https://{search.name}.search.windows.net"
    location = config["location"]

    _put_connection(
        cli,
        f"{foundry_id}/connections/{config['connections']['searchName']}",
        FOUNDRY_API_VERSION,
        {
            "category": "CognitiveSearch",
            "target": search_endpoint,
            "authType": "ApiKey",
            "isSharedToAll": True,
            "credentials": {"key": _search_key(config, cli)},
            "metadata": {"ApiType": "Azure", "ResourceId": search_id, "location": location},
        },
        contains_secret=True,
    )
    print(f"  [ok] Foundry Search connection '{config['connections']['searchName']}'")

    remote_connections = (
        (
            config["connections"]["crashStatementsName"],
            config["search"]["knowledgeBaseName"],
        ),
        (
            config["connections"]["policiesName"],
            config["policies"]["knowledgeBaseName"],
        ),
    )
    for connection_name, knowledge_base_name in remote_connections:
        _put_connection(
            cli,
            f"{project_id}/connections/{connection_name}",
            PROJECT_CONNECTION_API_VERSION,
            {
                "category": "RemoteTool",
                "target": (
                    f"{search_endpoint}/knowledgebases/{knowledge_base_name}/mcp"
                    f"?api-version={config['search']['mcpApiVersion']}"
                ),
                "authType": "ProjectManagedIdentity",
                "audience": "https://search.azure.com/",
                "isSharedToAll": True,
                "metadata": {"ApiType": "Azure"},
            },
        )
        print(f"  [ok] Foundry RemoteTool connection '{connection_name}'")


def connect(config: dict[str, Any], cli: AzureCli) -> None:
    check_resources(config, cli)
    ensure_access(config, cli)
    ensure_connections(config, cli)


def check_resources(config: dict[str, Any], cli: AzureCli) -> None:
    checks = {
        "Foundry account": resource_id(config, "foundryAccount"),
        "Foundry project": project_resource_id(config),
        "Azure AI Search service": resource_id(config, "searchService"),
        "Storage account": resource_id(config, "storageAccount"),
    }
    resource_details: dict[str, dict[str, Any]] = {}
    for label, identifier in checks.items():
        resource_details[label] = _resource_details(cli, identifier)
        print(f"  [ok] {label}")

    if resource_details["Foundry account"].get("properties", {}).get("disableLocalAuth") is True:
        print("  [ok] Foundry account uses Microsoft Entra authentication")
    if resource_details["Azure AI Search service"].get("properties", {}).get("disableLocalAuth") is True:
        raise SetupError(
            "Azure AI Search service has local authentication disabled. This hackathon setup "
            "currently requires a Search admin key."
        )
    storage_properties = resource_details["Storage account"].get("properties", {})
    if storage_properties.get("allowSharedKeyAccess") is False:
        print("  [ok] Storage account uses Microsoft Entra authentication")

    for model in (config["models"]["primary"], config["models"]["documentAi"]):
        cli.run(
            "rest",
            "--method",
            "get",
            "--url",
            management_url(
                f"{resource_id(config, 'foundryAccount')}/deployments/{model['deploymentName']}",
                MODEL_DEPLOYMENT_API_VERSION,
            ),
        )
        print(f"  [ok] Model deployment '{model['deploymentName']}'")


def check_access(config: dict[str, Any], cli: AzureCli) -> None:
    foundry_id = resource_id(config, "foundryAccount")
    search_id = resource_id(config, "searchService")
    storage_id = resource_id(config, "storageAccount")
    project_principal_id = _principal_id(cli, project_resource_id(config))
    foundry_principal_id = _principal_id(cli, foundry_id)
    search_principal_id = _principal_id(cli, search_id)
    expected_assignments = [
        (project_principal_id, FOUNDRY_USER_ROLE_ID, foundry_id, "Project Foundry User"),
        (
            project_principal_id,
            SEARCH_SERVICE_CONTRIBUTOR_ROLE_ID,
            search_id,
            "Project Search Service Contributor",
        ),
        (
            project_principal_id,
            SEARCH_INDEX_DATA_READER_ROLE_ID,
            search_id,
            "Project Search Index Data Reader",
        ),
        (
            foundry_principal_id,
            SEARCH_SERVICE_CONTRIBUTOR_ROLE_ID,
            search_id,
            "Foundry Search Service Contributor",
        ),
        (
            search_principal_id,
            STORAGE_BLOB_DATA_READER_ROLE_ID,
            storage_id,
            "Search Storage Blob Data Reader",
        ),
    ]
    participant_object_id = config.get("participantObjectId", "").strip()
    if participant_object_id:
        expected_assignments.extend(
            (
                (
                    participant_object_id,
                    FOUNDRY_USER_ROLE_ID,
                    foundry_id,
                    "Participant Foundry User",
                ),
                (
                    participant_object_id,
                    COGNITIVE_SERVICES_USER_ROLE_ID,
                    foundry_id,
                    "Participant Cognitive Services User",
                ),
                (
                    participant_object_id,
                    STORAGE_BLOB_DATA_CONTRIBUTOR_ROLE_ID,
                    storage_id,
                    "Participant Storage Blob Data Contributor",
                ),
            )
        )
    for principal_id, role_id, scope, label in expected_assignments:
        if not _has_role_assignment(cli, principal_id, role_id, scope):
            raise SetupError(f"Missing role assignment: {label}. Run setup_lab.py connect.")
        print(f"  [ok] {label}")


def check_connections(config: dict[str, Any], cli: AzureCli) -> None:
    foundry_id = resource_id(config, "foundryAccount")
    project_id = project_resource_id(config)
    search = resource_ref(config, "searchService")
    search_endpoint = f"https://{search.name}.search.windows.net"
    expected_connections = (
        (
            f"{foundry_id}/connections/{config['connections']['searchName']}",
            FOUNDRY_API_VERSION,
            search_endpoint,
            config["connections"]["searchName"],
        ),
        (
            f"{project_id}/connections/{config['connections']['crashStatementsName']}",
            PROJECT_CONNECTION_API_VERSION,
            (
                f"{search_endpoint}/knowledgebases/{config['search']['knowledgeBaseName']}/mcp"
                f"?api-version={config['search']['mcpApiVersion']}"
            ),
            config["connections"]["crashStatementsName"],
        ),
        (
            f"{project_id}/connections/{config['connections']['policiesName']}",
            PROJECT_CONNECTION_API_VERSION,
            (
                f"{search_endpoint}/knowledgebases/{config['policies']['knowledgeBaseName']}/mcp"
                f"?api-version={config['search']['mcpApiVersion']}"
            ),
            config["connections"]["policiesName"],
        ),
    )
    for identifier, api_version, expected_target, label in expected_connections:
        connection = cli.run(
            "rest",
            "--method",
            "get",
            "--url",
            management_url(identifier, api_version),
            expect_json=True,
            secret=True,
        )
        actual_target = connection.get("properties", {}).get("target", "").rstrip("/")
        if actual_target != expected_target.rstrip("/"):
            raise SetupError(
                f"Foundry connection '{label}' targets '{actual_target}', not the configured resource."
            )
        print(f"  [ok] Foundry connection '{label}'")


def check_storage_data(config: dict[str, Any], cli: AzureCli) -> None:
    storage_arguments, storage_environment = _storage_data_access(config, cli)
    contains_secret = storage_environment is not None
    for container_name in (
        config["claims"]["containerName"],
        config["policies"]["containerName"],
    ):
        exists = cli.run(
            "storage",
            "container",
            "exists",
            "--name",
            container_name,
            "--query",
            "exists",
            "--output",
            "tsv",
            *storage_arguments,
            environment=storage_environment,
            secret=contains_secret,
        )
        if exists.lower() != "true":
            raise SetupError(f"Storage container '{container_name}' is missing. Run setup_lab.py configure.")
        print(f"  [ok] Storage container '{container_name}'")

    blob_names = set(
        cli.run(
            "storage",
            "blob",
            "list",
            "--container-name",
            config["policies"]["containerName"],
            "--query",
            "[].name",
            "--output",
            "tsv",
            *storage_arguments,
            environment=storage_environment,
            secret=contains_secret,
        ).splitlines()
    )
    expected_policy_names = {path.name for path in (REPO_ROOT / "data" / "policies").glob("*.md")}
    missing_policy_names = sorted(expected_policy_names - blob_names)
    if missing_policy_names:
        raise SetupError(
            "Policy container is missing sample files: " + ", ".join(missing_policy_names)
        )
    print(f"  [ok] {len(expected_policy_names)} policy documents")


def check_readiness(config: dict[str, Any], cli: AzureCli) -> None:
    foundry_key = _foundry_key(config, cli)
    if foundry_key:
        print("  [ok] Foundry key is accessible")
    else:
        print("  [ok] Foundry account uses Microsoft Entra authentication")
    storage_connection_string = _storage_connection_string(config, cli)
    if storage_connection_string:
        print("  [ok] Storage connection string is accessible")
    else:
        print("  [ok] Storage account uses Microsoft Entra authentication")
    for label, credential_reader in (("Search admin key", _search_key),):
        try:
            credential_reader(config, cli)
        except SetupError as exc:
            raise SetupError(f"Could not read the configured {label}.") from exc
        print(f"  [ok] {label} is accessible")
    check_access(config, cli)
    check_connections(config, cli)
    check_storage_data(config, cli)


def _storage_connection_string(config: dict[str, Any], cli: AzureCli) -> str:
    storage = resource_ref(config, "storageAccount")
    details = _resource_details(cli, resource_id(config, "storageAccount"))
    if details.get("properties", {}).get("allowSharedKeyAccess") is False:
        return ""
    return cli.run(
        "storage",
        "account",
        "show-connection-string",
        "--resource-group",
        storage.resource_group,
        "--name",
        storage.name,
        "--query",
        "connectionString",
        "--output",
        "tsv",
        secret=True,
    )


def _storage_data_access(
    config: dict[str, Any], cli: AzureCli
) -> tuple[tuple[str, ...], dict[str, str] | None]:
    connection_string = _storage_connection_string(config, cli)
    if connection_string:
        return (), {"AZURE_STORAGE_CONNECTION_STRING": connection_string}
    storage = resource_ref(config, "storageAccount")
    return ("--account-name", storage.name, "--auth-mode", "login"), None


def _storage_knowledge_source_connection(config: dict[str, Any], cli: AzureCli) -> str:
    connection_string = _storage_connection_string(config, cli)
    if connection_string:
        return connection_string
    return f"ResourceId={resource_id(config, 'storageAccount')}/;"


def _search_key(config: dict[str, Any], cli: AzureCli) -> str:
    search = resource_ref(config, "searchService")
    return cli.run(
        "search",
        "admin-key",
        "show",
        "--resource-group",
        search.resource_group,
        "--service-name",
        search.name,
        "--query",
        "primaryKey",
        "--output",
        "tsv",
        secret=True,
    )


def _foundry_key(config: dict[str, Any], cli: AzureCli) -> str:
    foundry = resource_ref(config, "foundryAccount")
    details = _resource_details(cli, resource_id(config, "foundryAccount"))
    if details.get("properties", {}).get("disableLocalAuth") is True:
        return ""
    return cli.run(
        "cognitiveservices",
        "account",
        "keys",
        "list",
        "--resource-group",
        foundry.resource_group,
        "--name",
        foundry.name,
        "--query",
        "key1",
        "--output",
        "tsv",
        secret=True,
    )


def ensure_containers_and_data(config: dict[str, Any], cli: AzureCli) -> None:
    storage_arguments, storage_environment = _storage_data_access(config, cli)
    contains_secret = storage_environment is not None
    for container_name in (
        config["claims"]["containerName"],
        config["policies"]["containerName"],
    ):
        cli.run(
            "storage",
            "container",
            "create",
            "--name",
            container_name,
            *storage_arguments,
            environment=storage_environment,
            secret=contains_secret,
        )

    policies_path = REPO_ROOT / "data" / "policies"
    cli.run(
        "storage",
        "blob",
        "upload-batch",
        "--destination",
        config["policies"]["containerName"],
        "--source",
        str(policies_path),
        "--pattern",
        "*.md",
        "--overwrite",
        "true",
        *storage_arguments,
        environment=storage_environment,
        secret=contains_secret,
    )
    print("  [ok] Storage containers and policy documents")


def env_values(config: dict[str, Any], cli: AzureCli) -> dict[str, str]:
    foundry = resource_ref(config, "foundryAccount")
    search = resource_ref(config, "searchService")
    project_name = config["resources"]["foundryProject"]["name"]
    project_endpoint = f"https://{foundry.name}.services.ai.azure.com/api/projects/{project_name}"
    foundry_endpoint = f"https://{foundry.name}.services.ai.azure.com"
    search_endpoint = f"https://{search.name}.search.windows.net"
    primary_model = config["models"]["primary"]["deploymentName"]
    document_ai = config["models"]["documentAi"]
    return {
        "AI_FOUNDRY_PROJECT_ENDPOINT": project_endpoint,
        "FOUNDRY_PROJECT_ENDPOINT": project_endpoint,
        "MODEL_DEPLOYMENT_NAME": primary_model,
        "FOUNDRY_MODEL": primary_model,
        "FOUNDRY_QUARANTINE_MODEL": primary_model,
        "MISTRAL_DOCUMENT_AI_ENDPOINT": foundry_endpoint,
        "MISTRAL_DOCUMENT_AI_KEY": _foundry_key(config, cli),
        "MISTRAL_DOCUMENT_AI_DEPLOYMENT_NAME": document_ai["deploymentName"],
        "MISTRAL_DOCUMENT_AI_API_VERSION": document_ai["apiVersion"],
        "FOUNDRY_IQ_SEARCH_ENDPOINT": search_endpoint,
        "FOUNDRY_IQ_SEARCH_KEY": _search_key(config, cli),
        "FOUNDRY_IQ_SEARCH_INDEX_NAME": config["search"]["indexName"],
        "FOUNDRY_IQ_SEARCH_API_VERSION": config["search"]["apiVersion"],
        "FOUNDRY_IQ_SEMANTIC_CONFIG_NAME": config["search"]["semanticConfigurationName"],
        "FOUNDRY_IQ_KNOWLEDGE_SOURCE_NAME": config["search"]["knowledgeSourceName"],
        "FOUNDRY_IQ_KNOWLEDGE_BASE_NAME": config["search"]["knowledgeBaseName"],
        "FOUNDRY_IQ_POLICIES_KNOWLEDGE_SOURCE_NAME": config["policies"]["knowledgeSourceName"],
        "FOUNDRY_IQ_POLICIES_KNOWLEDGE_BASE_NAME": config["policies"]["knowledgeBaseName"],
        "FOUNDRY_IQ_MCP_API_VERSION": config["search"]["mcpApiVersion"],
        "FOUNDRY_IQ_SEARCH_CONNECTION_NAME": config["connections"]["searchName"],
        "FOUNDRY_IQ_CRASH_STATEMENTS_CONNECTION_NAME": config["connections"]["crashStatementsName"],
        "FOUNDRY_IQ_POLICIES_CONNECTION_NAME": config["connections"]["policiesName"],
        "SEARCH_SERVICE_ENDPOINT": search_endpoint,
        "SEARCH_ADMIN_KEY": _search_key(config, cli),
        "AZURE_STORAGE_CONNECTION_STRING": _storage_knowledge_source_connection(config, cli),
        "AZURE_STORAGE_ACCOUNT_NAME": resource_ref(config, "storageAccount").name,
        "AZURE_STORAGE_RESOURCE_ID": resource_id(config, "storageAccount"),
        "AZURE_POLICIES_CONTAINER_NAME": config["policies"]["containerName"],
        "HUMAN_REVIEW_CONFIDENCE_THRESHOLD": "0.9",
    }


def write_env(values: dict[str, str]) -> None:
    groups = (
        (
            "Microsoft Foundry project and models",
            (
                "AI_FOUNDRY_PROJECT_ENDPOINT",
                "FOUNDRY_PROJECT_ENDPOINT",
                "MODEL_DEPLOYMENT_NAME",
                "FOUNDRY_MODEL",
                "FOUNDRY_QUARANTINE_MODEL",
            ),
        ),
        (
            "Mistral Document AI",
            (
                "MISTRAL_DOCUMENT_AI_ENDPOINT",
                "MISTRAL_DOCUMENT_AI_KEY",
                "MISTRAL_DOCUMENT_AI_DEPLOYMENT_NAME",
                "MISTRAL_DOCUMENT_AI_API_VERSION",
            ),
        ),
        (
            "Azure AI Search and Foundry IQ",
            tuple(key for key in values if key.startswith("FOUNDRY_IQ_"))
            + ("SEARCH_SERVICE_ENDPOINT", "SEARCH_ADMIN_KEY"),
        ),
        (
            "Azure Storage policy documents",
            (
                "AZURE_STORAGE_CONNECTION_STRING",
                "AZURE_STORAGE_ACCOUNT_NAME",
                "AZURE_STORAGE_RESOURCE_ID",
                "AZURE_POLICIES_CONTAINER_NAME",
            ),
        ),
        ("Sequential workflow", ("HUMAN_REVIEW_CONFIDENCE_THRESHOLD",)),
    )
    lines = ["# Generated by labautomation/setup_lab.py. Do not commit this file.", ""]
    for heading, keys in groups:
        lines.append(f"# {heading}")
        lines.extend(f"{key}={values[key]}" for key in keys)
        lines.append("")

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", dir=ENV_FILE.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary_file:
            temporary_file.write("\n".join(lines))
        os.chmod(temporary_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary_name, ENV_FILE)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    print(f"  [ok] Created {ENV_FILE} without displaying secret values")


def configure(config: dict[str, Any], cli: AzureCli) -> None:
    check_resources(config, cli)
    ensure_containers_and_data(config, cli)
    write_env(env_values(config, cli))


def cleanup(config: dict[str, Any], cli: AzureCli, confirmed: bool) -> None:
    if config["mode"] != "deploy":
        raise SetupError("Cleanup never deletes resources configured with mode 'existing'.")
    if not confirmed:
        raise SetupError("Cleanup requires --yes because it deletes the complete deployment resource group.")
    if not config["deployment"].get("createResourceGroup", True) or not _state_matches(config):
        raise SetupError(
            "Cleanup refused because this setup can't prove it created the resource group. "
            "Delete shared or externally created resources through your normal Azure process."
        )
    resource_group = config["deployment"]["resourceGroup"]
    tags = cli.run(
        "group", "show", "--name", resource_group, "--query", "tags", expect_json=True
    ) or {}
    expected_tags = {
        "workload": "claims-microhack",
        "managed-by": "setup_lab.py",
        "deployment": config["deployment"]["name"],
    }
    if any(tags.get(key) != value for key, value in expected_tags.items()):
        raise SetupError("Cleanup refused because the resource group ownership tags don't match.")
    cli.run("group", "delete", "--name", resource_group, "--yes", "--no-wait")
    STATE_FILE.unlink(missing_ok=True)
    print(f"Deletion started for resource group '{resource_group}'.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_FILE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Validate configuration and optionally Azure resources")
    check_parser.add_argument("--azure", action="store_true", help="Also verify the configured Azure resources")
    deploy_parser = subparsers.add_parser("deploy", help="Deploy a customer-owned lab stack")
    deploy_parser.add_argument("--what-if", action="store_true", help="Preview changes before deployment")
    subparsers.add_parser("connect", help="Configure RBAC and Foundry connections")
    subparsers.add_parser("configure", help="Validate resources, upload policies, and create .env")
    all_parser = subparsers.add_parser("all", help="Deploy when requested, then configure the lab")
    all_parser.add_argument("--what-if", action="store_true", help="Preview changes before deployment")
    cleanup_parser = subparsers.add_parser("cleanup", help="Delete a stack created in deploy mode")
    cleanup_parser.add_argument("--yes", action="store_true", help="Confirm resource group deletion")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config = load_config(args.config.resolve())
        print(f"Configuration is valid (mode: {config['mode']}).")
        if args.command == "check" and not args.azure:
            return 0
        if shutil.which("az") is None:
            raise SetupError("Azure CLI was not found. Install 'az' and run the command again.")
        cli = AzureCli(config["subscriptionId"], config.get("tenantId", ""))
        cli.ensure_session()
        if args.command == "check":
            check_resources(config, cli)
            check_readiness(config, cli)
        elif args.command == "deploy":
            deploy(config, cli, args.what_if)
        elif args.command == "connect":
            connect(config, cli)
        elif args.command == "configure":
            configure(config, cli)
        elif args.command == "all":
            if config["mode"] == "deploy":
                print("[1/3] Deploying the Azure lab stack")
                deploy(config, cli, args.what_if)
                print("[2/3] Configuring access and Foundry connections")
            else:
                print("[1/2] Configuring access and Foundry connections")
            connect(config, cli)
            final_phase = "[3/3]" if config["mode"] == "deploy" else "[2/2]"
            print(f"{final_phase} Uploading policies and writing the environment file")
            configure(config, cli)
            print("Setup complete.")
        elif args.command == "cleanup":
            cleanup(config, cli, args.yes)
        return 0
    except SetupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())