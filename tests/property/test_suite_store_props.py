"""Suite Store List-Then-Get Round-Trip."""
from __future__ import annotations

import boto3
import moto
from hypothesis import given, settings
from hypothesis import strategies as st

from rascal.models import TestCase, TestSuite
from rascal.suite_store import DynamoDBSuiteStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TABLE_NAME = "test-suites"
_REGION = "us-east-1"

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_text = st.text(min_size=1, max_size=50)
_metadata = st.fixed_dictionaries({}, optional={"a": _text, "b": _text})

st_test_case = st.builds(
    TestCase,
    input_text=_text,
    category=_text,
    metadata=_metadata,
)

st_test_suite = st.builds(
    TestSuite,
    suite_id=_text,
    name=_text,
    test_cases=st.lists(st_test_case, max_size=5),
    metadata=_metadata,
)

st_unique_suites = st.lists(
    st_test_suite,
    min_size=1,
    max_size=10,
    unique_by=lambda s: s.suite_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_table() -> None:
    """Create the DynamoDB table used by DynamoDBSuiteStore."""
    ddb = boto3.resource("dynamodb", region_name=_REGION)
    ddb.create_table(
        TableName=_TABLE_NAME,
        KeySchema=[{"AttributeName": "suiteId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "suiteId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _store_suite(table_name: str, suite: TestSuite) -> None:
    """Put a TestSuite into DynamoDB as the store would."""
    ddb = boto3.resource("dynamodb", region_name=_REGION)
    table = ddb.Table(table_name)
    table.put_item(Item={"suiteId": suite.suite_id, "data": suite.model_dump_json()})


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(suites=st_unique_suites)
@settings(max_examples=20, deadline=None)
def test_list_then_get_roundtrip(suites: list[TestSuite]) -> None:
    """Store suites, list IDs, then get each — returned suite equals stored suite.

    """
    with moto.mock_aws():
        _create_table()

        store = DynamoDBSuiteStore(table_name=_TABLE_NAME, region=_REGION)

        # Store all suites
        for suite in suites:
            _store_suite(_TABLE_NAME, suite)

        # list_suites() must contain every stored ID
        listed_ids = store.list_suites()
        for suite in suites:
            assert suite.suite_id in listed_ids, (
                f"suite_id {suite.suite_id!r} missing from list_suites()"
            )

        # get_suite() for each ID must return an equivalent TestSuite
        for suite in suites:
            retrieved = store.get_suite(suite.suite_id)
            assert retrieved == suite, (
                f"get_suite({suite.suite_id!r}) returned {retrieved!r}, "
                f"expected {suite!r}"
            )
