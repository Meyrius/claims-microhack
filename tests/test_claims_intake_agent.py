import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "docs" / "claims-intake-agent.py"
SPEC = importlib.util.spec_from_file_location("claims_intake_agent", MODULE_PATH)
assert SPEC and SPEC.loader
claims_intake_agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(claims_intake_agent)


class ClaimsIntakeAgentTests(unittest.TestCase):
    def test_repository_image_path_is_portable(self) -> None:
        image_path = (
            claims_intake_agent.REPO_ROOT
            / "data"
            / "claims"
            / "crash1"
            / "raw"
            / "statements"
            / "crash1_front.jpeg"
        )

        output_path = claims_intake_agent._output_image_path(image_path)

        self.assertEqual(
            "data/claims/crash1/raw/statements/crash1_front.jpeg",
            output_path,
        )


if __name__ == "__main__":
    unittest.main()