import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from labautomation import setup_lab

EXAMPLE_CONFIG = setup_lab.REPO_ROOT / "labautomation" / "customer-resources.example.json"


class SetupConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.config["subscriptionId"] = "11111111-1111-1111-1111-111111111111"
        self.config["participantObjectId"] = "22222222-2222-2222-2222-222222222222"

    def test_deploy_example_is_valid(self) -> None:
        setup_lab.validate_config(self.config)

    def test_placeholder_subscription_is_rejected(self) -> None:
        self.config["subscriptionId"] = "00000000-0000-0000-0000-000000000000"

        with self.assertRaisesRegex(setup_lab.SetupError, "placeholder UUID"):
            setup_lab.validate_config(self.config)

    def test_document_ai_api_version_is_required(self) -> None:
        del self.config["models"]["documentAi"]["apiVersion"]

        with self.assertRaisesRegex(setup_lab.SetupError, "models.documentAi.apiVersion"):
            setup_lab.validate_config(self.config)

    def test_existing_mode_allows_resources_in_different_groups(self) -> None:
        self.config["mode"] = "existing"
        self.config["resources"]["searchService"]["resourceGroup"] = "shared-search"
        self.config["resources"]["storageAccount"]["resourceGroup"] = "shared-storage"

        setup_lab.validate_config(self.config)

    def test_deploy_mode_requires_one_resource_group(self) -> None:
        self.config["resources"]["searchService"]["resourceGroup"] = "other-group"

        with self.assertRaisesRegex(setup_lab.SetupError, "Deploy mode creates all resources"):
            setup_lab.validate_config(self.config)

    def test_bootstrap_parameters_exist_in_arm_template(self) -> None:
        template = json.loads(setup_lab.TEMPLATE_FILE.read_text(encoding="utf-8"))
        emitted = set(setup_lab.deployment_parameters(self.config))

        self.assertEqual(set(), emitted - set(template["parameters"]))

    def test_role_assignment_setting_is_passed_to_arm(self) -> None:
        self.config["deployment"]["deployRoleAssignments"] = False

        parameters = setup_lab.deployment_parameters(self.config)

        self.assertEqual(False, parameters["deployRoleAssignments"]["value"])

    def test_resource_ids_use_explicit_resource_groups(self) -> None:
        self.config["mode"] = "existing"
        self.config["resources"]["searchService"]["resourceGroup"] = "shared-search"

        identifier = setup_lab.resource_id(self.config, "searchService")

        self.assertIn("/resourceGroups/shared-search/", identifier)
        self.assertTrue(identifier.endswith("/Microsoft.Search/searchServices/claims-search-unique"))


class AuthenticationModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.config["subscriptionId"] = "11111111-1111-1111-1111-111111111111"
        self.config["participantObjectId"] = "22222222-2222-2222-2222-222222222222"

    def test_foundry_key_is_empty_when_local_auth_is_disabled(self) -> None:
        class FakeCli:
            def run(self, *arguments: str, **_: object) -> object:
                self.last_arguments = arguments
                return {"properties": {"disableLocalAuth": True}}

        cli = FakeCli()

        key = setup_lab._foundry_key(self.config, cli)

        self.assertEqual("", key)
        self.assertEqual(("rest", "--method", "get"), cli.last_arguments[:3])

    def test_resource_check_accepts_entra_only_foundry(self) -> None:
        class FakeCli:
            def run(self, *arguments: str, **_: object) -> object:
                if arguments[:3] == ("rest", "--method", "get"):
                    identifier = arguments[4]
                    if "/deployments/" in identifier:
                        return {}
                    if "/Microsoft.CognitiveServices/accounts/" in identifier and "/projects/" not in identifier:
                        return {"properties": {"disableLocalAuth": True}}
                    if "/Microsoft.Search/searchServices/" in identifier:
                        return {"properties": {"disableLocalAuth": False}}
                    if "/Microsoft.Storage/storageAccounts/" in identifier:
                        return {"properties": {"allowSharedKeyAccess": True}}
                    return {"properties": {}}
                raise AssertionError(f"Unexpected Azure call: {arguments}")

        with patch("builtins.print"):
            setup_lab.check_resources(self.config, FakeCli())

    def test_env_uses_published_mistral_ai_endpoint(self) -> None:
        with (
            patch.object(setup_lab, "_foundry_key", return_value=""),
            patch.object(setup_lab, "_search_key", return_value="search-key"),
            patch.object(
                setup_lab,
                "_storage_knowledge_source_connection",
                return_value="ResourceId=/storage/;",
            ),
        ):
            values = setup_lab.env_values(self.config, object())

        self.assertEqual(
            "https://claims-foundry-unique.services.ai.azure.com",
            values["MISTRAL_DOCUMENT_AI_ENDPOINT"],
        )


class BootstrapSecurityTests(unittest.TestCase):
    def test_long_azure_operation_reports_periodic_progress(self) -> None:
        completed = subprocess.CompletedProcess(args=["az"], returncode=0, stdout="", stderr="")

        def delayed_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
            time.sleep(0.03)
            return completed

        with (
            patch.object(setup_lab, "AZURE_PROGRESS_INTERVAL_SECONDS", 0.01),
            patch("subprocess.run", side_effect=delayed_run),
            patch("builtins.print") as output,
        ):
            setup_lab.AzureCli("subscription").run(
                "deployment", "group", "what-if", progress_label="What-if preview"
            )

        printed = " ".join(str(call) for call in output.call_args_list)
        self.assertIn("What-if preview still running", printed)

    def test_secret_cli_failure_redacts_stderr(self) -> None:
        cli = setup_lab.AzureCli("00000000-0000-0000-0000-000000000000")
        failed = subprocess.CompletedProcess(
            args=["az"],
            returncode=1,
            stdout="",
            stderr="response contained TOP-SECRET",
        )

        with (
            patch("subprocess.run", return_value=failed),
            self.assertRaises(setup_lab.SetupError) as context,
        ):
            cli.run("storage", "account", "show-connection-string", secret=True)

        self.assertNotIn("TOP-SECRET", str(context.exception))
        self.assertIn("reading a secret", str(context.exception))

    def test_non_secret_cli_failure_preserves_actionable_stderr(self) -> None:
        cli = setup_lab.AzureCli("00000000-0000-0000-0000-000000000000")
        failed = subprocess.CompletedProcess(
            args=["az"],
            returncode=1,
            stdout="",
            stderr="ERROR: The request may be blocked by network rules of storage account.",
        )

        with (
            patch("subprocess.run", return_value=failed),
            self.assertRaises(setup_lab.SetupError) as context,
        ):
            cli.run("storage", "container", "exists", secret=False)

        self.assertEqual(
            "The request may be blocked by network rules of storage account.",
            str(context.exception),
        )

    def test_entra_storage_calls_are_not_marked_secret(self) -> None:
        config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        config["subscriptionId"] = "11111111-1111-1111-1111-111111111111"

        class FakeCli:
            def run(self, *arguments: str, **kwargs: object) -> object:
                if arguments[:3] == ("rest", "--method", "get"):
                    return {"properties": {"allowSharedKeyAccess": False}}
                self.secret = kwargs.get("secret")
                raise setup_lab.SetupError("blocked by network rules")

        cli = FakeCli()
        with self.assertRaisesRegex(setup_lab.SetupError, "blocked by network rules"):
            setup_lab.ensure_containers_and_data(config, cli)

        self.assertIs(False, cli.secret)

    def test_write_env_contains_contract_without_printing_secrets(self) -> None:
        values = {
            "AI_FOUNDRY_PROJECT_ENDPOINT": "https://foundry/project",
            "FOUNDRY_PROJECT_ENDPOINT": "https://foundry/project",
            "MODEL_DEPLOYMENT_NAME": "model",
            "FOUNDRY_MODEL": "model",
            "FOUNDRY_QUARANTINE_MODEL": "model",
            "MISTRAL_DOCUMENT_AI_ENDPOINT": "https://foundry",
            "MISTRAL_DOCUMENT_AI_KEY": "secret-foundry-key",
            "MISTRAL_DOCUMENT_AI_DEPLOYMENT_NAME": "document-model",
            "MISTRAL_DOCUMENT_AI_API_VERSION": "version",
            "FOUNDRY_IQ_SEARCH_ENDPOINT": "https://search",
            "FOUNDRY_IQ_SEARCH_KEY": "secret-search-key",
            "FOUNDRY_IQ_SEARCH_INDEX_NAME": "index",
            "FOUNDRY_IQ_SEARCH_API_VERSION": "search-version",
            "FOUNDRY_IQ_SEMANTIC_CONFIG_NAME": "semantic",
            "FOUNDRY_IQ_KNOWLEDGE_SOURCE_NAME": "source",
            "FOUNDRY_IQ_KNOWLEDGE_BASE_NAME": "base",
            "FOUNDRY_IQ_POLICIES_KNOWLEDGE_SOURCE_NAME": "policy-source",
            "FOUNDRY_IQ_POLICIES_KNOWLEDGE_BASE_NAME": "policy-base",
            "FOUNDRY_IQ_MCP_API_VERSION": "mcp-version",
            "FOUNDRY_IQ_SEARCH_CONNECTION_NAME": "search-connection",
            "FOUNDRY_IQ_CRASH_STATEMENTS_CONNECTION_NAME": "crash-connection",
            "FOUNDRY_IQ_POLICIES_CONNECTION_NAME": "policy-connection",
            "SEARCH_SERVICE_ENDPOINT": "https://search",
            "SEARCH_ADMIN_KEY": "secret-search-key",
            "AZURE_STORAGE_CONNECTION_STRING": "secret-storage-connection",
            "AZURE_STORAGE_ACCOUNT_NAME": "storage-account",
            "AZURE_STORAGE_RESOURCE_ID": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/storage-account",
            "AZURE_POLICIES_CONTAINER_NAME": "policies",
            "HUMAN_REVIEW_CONFIDENCE_THRESHOLD": "0.9",
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            with patch.object(setup_lab, "ENV_FILE", env_file), patch("builtins.print") as output:
                setup_lab.write_env(values)

            contents = env_file.read_text(encoding="utf-8")
            printed = " ".join(str(call) for call in output.call_args_list)

        self.assertIn("FOUNDRY_IQ_SEARCH_CONNECTION_NAME=search-connection", contents)
        self.assertIn("AZURE_STORAGE_CONNECTION_STRING=secret-storage-connection", contents)
        self.assertIn("AZURE_STORAGE_ACCOUNT_NAME=storage-account", contents)
        self.assertNotIn("secret-search-key", printed)
        self.assertNotIn("secret-storage-connection", printed)

    def test_storage_data_access_uses_entra_when_shared_keys_are_disabled(self) -> None:
        config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        config["subscriptionId"] = "11111111-1111-1111-1111-111111111111"

        class FakeCli:
            def run(self, *arguments: str, **_: object) -> object:
                self.last_arguments = arguments
                return {"properties": {"allowSharedKeyAccess": False}}

        cli = FakeCli()

        arguments, environment = setup_lab._storage_data_access(config, cli)

        self.assertEqual(
            ("--account-name", "claimsstorageunique", "--auth-mode", "login"),
            arguments,
        )
        self.assertIsNone(environment)

    def test_storage_knowledge_source_uses_resource_id_without_shared_keys(self) -> None:
        config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        config["subscriptionId"] = "11111111-1111-1111-1111-111111111111"

        with patch.object(setup_lab, "_storage_connection_string", return_value=""):
            connection_string = setup_lab._storage_knowledge_source_connection(config, object())

        self.assertEqual(
            "ResourceId=/subscriptions/11111111-1111-1111-1111-111111111111/"
            "resourceGroups/rg-claims-microhack/providers/Microsoft.Storage/"
            "storageAccounts/claimsstorageunique/;",
            connection_string,
        )

    def test_connection_secret_is_written_to_file_not_process_arguments(self) -> None:
        calls: list[tuple[str, ...]] = []

        class FakeCli:
            def run(self, *arguments: str, **kwargs: object) -> object:
                calls.append(arguments)
                if kwargs.get("allow_not_found"):
                    return None
                return ""

        setup_lab._put_connection(
            FakeCli(),
            "/resource/connections/name",
            "api-version",
            {
                "category": "CognitiveSearch",
                "target": "https://search.example",
                "credentials": {"key": "TOP-SECRET"},
            },
            contains_secret=True,
        )

        self.assertNotIn("TOP-SECRET", repr(calls))
        self.assertTrue(any(argument.startswith("@") for argument in calls[1]))


class OwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.config["subscriptionId"] = "11111111-1111-1111-1111-111111111111"
        self.config["participantObjectId"] = "22222222-2222-2222-2222-222222222222"

    def test_deploy_uses_one_unique_name_for_all_deployment_steps(self) -> None:
        class FakeCli:
            def __init__(self) -> None:
                self.calls: list[tuple[str, ...]] = []

            def run(self, *arguments: str, **_: object) -> str:
                self.calls.append(arguments)
                if arguments[:2] == ("group", "exists"):
                    return "true"
                return ""

        self.config["deployment"]["createResourceGroup"] = False
        cli = FakeCli()

        with patch.object(setup_lab.uuid, "uuid4", return_value=setup_lab.uuid.UUID(int=1)):
            setup_lab.deploy(self.config, cli, what_if=True)

        deployment_calls = [call for call in cli.calls if call[:2] == ("deployment", "group")]
        deployment_names = [call[call.index("--name") + 1] for call in deployment_calls]
        expected_name = f"{self.config['deployment']['name']}-{'0' * 31}1"
        self.assertEqual([expected_name] * 3, deployment_names)
        self.assertLessEqual(len(expected_name), 64)

    def test_deploy_refuses_existing_unowned_resource_group(self) -> None:
        class FakeCli:
            def run(self, *arguments: str, **_: object) -> str:
                if arguments[:2] == ("group", "exists"):
                    return "true"
                raise AssertionError(f"Unexpected Azure call: {arguments}")

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / ".setup-state.json"
            with (
                patch.object(setup_lab, "STATE_FILE", state_file),
                self.assertRaisesRegex(setup_lab.SetupError, "wasn't created by this setup"),
            ):
                setup_lab.deploy(self.config, FakeCli(), what_if=False)

    def test_cleanup_refuses_without_ownership_record(self) -> None:
        class FakeCli:
            def run(self, *arguments: str, **_: object) -> object:
                raise AssertionError(f"Cleanup must not call Azure: {arguments}")

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / ".setup-state.json"
            with (
                patch.object(setup_lab, "STATE_FILE", state_file),
                self.assertRaisesRegex(setup_lab.SetupError, "can't prove"),
            ):
                setup_lab.cleanup(self.config, FakeCli(), confirmed=True)

    def test_cleanup_deletes_group_with_matching_state_and_tags(self) -> None:
        class FakeCli:
            def __init__(self) -> None:
                self.calls: list[tuple[str, ...]] = []

            def run(self, *arguments: str, **_: object) -> object:
                self.calls.append(arguments)
                if arguments[:2] == ("group", "show"):
                    return {
                        "workload": "claims-microhack",
                        "managed-by": "setup_lab.py",
                        "deployment": self_config["deployment"]["name"],
                    }
                return None

        self_config = self.config
        state = {
            "schemaVersion": 1,
            "subscriptionId": self.config["subscriptionId"],
            "resourceGroup": self.config["deployment"]["resourceGroup"],
            "deploymentName": self.config["deployment"]["name"],
            "createdResourceGroup": True,
        }
        cli = FakeCli()
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / ".setup-state.json"
            state_file.write_text(json.dumps(state), encoding="utf-8")
            with patch.object(setup_lab, "STATE_FILE", state_file), patch("builtins.print"):
                setup_lab.cleanup(self.config, cli, confirmed=True)

            self.assertFalse(state_file.exists())

        self.assertEqual(("group", "show"), cli.calls[0][:2])
        self.assertEqual(("group", "delete"), cli.calls[1][:2])

    def test_connection_name_collision_is_rejected(self) -> None:
        class FakeCli:
            def run(self, *_: str, **kwargs: object) -> object:
                if kwargs.get("allow_not_found"):
                    return {
                        "properties": {
                            "category": "CognitiveSearch",
                            "target": "https://other-search.example",
                        }
                    }
                raise AssertionError("A conflicting connection must not be updated")

        with self.assertRaisesRegex(setup_lab.SetupError, "already used"):
            setup_lab._put_connection(
                FakeCli(),
                "/resource/connections/name",
                "api-version",
                {"category": "CognitiveSearch", "target": "https://search.example"},
            )


class CommandDispatchTests(unittest.TestCase):
    def test_check_azure_runs_resource_and_readiness_checks(self) -> None:
        config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        config["subscriptionId"] = "11111111-1111-1111-1111-111111111111"
        config["participantObjectId"] = "22222222-2222-2222-2222-222222222222"

        class FakeCli:
            def ensure_session(self) -> None:
                return None

        cli = FakeCli()
        with (
            patch("sys.argv", ["setup_lab.py", "check", "--azure"]),
            patch.object(setup_lab, "load_config", return_value=config),
            patch.object(setup_lab.shutil, "which", return_value="az"),
            patch.object(setup_lab, "AzureCli", return_value=cli),
            patch.object(setup_lab, "check_resources") as check_resources,
            patch.object(setup_lab, "check_readiness") as check_readiness,
            patch("builtins.print"),
        ):
            result = setup_lab.main()

        self.assertEqual(0, result)
        check_resources.assert_called_once_with(config, cli)
        check_readiness.assert_called_once_with(config, cli)


class RoleAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.config["subscriptionId"] = "11111111-1111-1111-1111-111111111111"
        self.config["participantObjectId"] = "22222222-2222-2222-2222-222222222222"

    def test_existing_role_assignment_is_not_created_again(self) -> None:
        role_id = setup_lab.FOUNDRY_USER_ROLE_ID

        class FakeCli:
            def __init__(self) -> None:
                self.calls: list[tuple[str, ...]] = []

            def run(self, *arguments: str, **_: object) -> object:
                self.calls.append(arguments)
                return [{"roleDefinitionId": f"/providers/Microsoft.Authorization/roleDefinitions/{role_id}"}]

        cli = FakeCli()
        with patch("builtins.print"):
            setup_lab._ensure_role_assignment(
                cli,
                "principal",
                "User",
                role_id,
                "/scope",
                "label",
            )

        self.assertEqual(1, len(cli.calls))
        self.assertEqual(("role", "assignment", "list"), cli.calls[0][:3])


if __name__ == "__main__":
    unittest.main()