import os
import unittest
from unittest.mock import Mock, patch

from docs import index_crash_statements


class IndexCrashStatementsTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "FOUNDRY_IQ_SEARCH_ENDPOINT": "https://claims-search.search.windows.net",
            "FOUNDRY_IQ_SEARCH_KEY": "test-key",
            "FOUNDRY_IQ_SEARCH_INDEX_NAME": "crash-statements",
        },
    )
    def test_upload_makes_statement_reference_searchable(self) -> None:
        client = Mock()

        index_crash_statements.upload_document(
            client,
            "crash3_front",
            "crash3_front.jpeg",
            {
                "text": "## Accident Statement\nClaim details",
                "annotation": {
                    "claimant_name": "Michael Rodriguez",
                    "policy_number": "LIAB-AUTO-001",
                },
            },
        )

        document = client.post.call_args.kwargs["json"]["value"][0]
        self.assertEqual(
            "Statement ID: crash3_front\n"
            "Source File: crash3_front.jpeg\n\n"
            "## Accident Statement\nClaim details",
            document["content"],
        )
        client.post.return_value.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()