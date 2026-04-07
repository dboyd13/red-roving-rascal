"""Gateway configuration dataclasses and policy helpers."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IamGatewayConfig:
    """Configuration for the IAM (SigV4) AgentCore Gateway.

    Two independent knobs — compose them however you like:

    - ``resource_policy``: IAM resource policy controlling who can
      *reach* the gateway (the perimeter).
    - ``initial_cedar_policies``: Cedar policy texts seeded at deploy
      time controlling who gets *authorized* (the fine-grained gate).

    Use the standalone helper functions to build these:

    - :func:`resource_policy_for_accounts`
    - :func:`resource_policy_for_org`
    - :func:`cedar_permit_account`

    Examples::

        # Simple: resource policy only (small scale, static)
        IamGatewayConfig(
            resource_policy=resource_policy_for_accounts(["123456789012"]),
        )

        # Org perimeter + Cedar fine-grained
        IamGatewayConfig(
            resource_policy=resource_policy_for_org("o-abc123"),
            initial_cedar_policies=[
                cedar_permit_account("123456789012"),
                cedar_permit_account("987654321098"),
            ],
        )
    """

    resource_policy: dict | None = None
    initial_cedar_policies: list[str] | None = None


@dataclass
class CustomClaimRule:
    """A rule for validating a custom claim in the JWT token."""

    claim_name: str
    match_value: str
    value_type: str = "STRING"
    match_operator: str = "EQUALS"


@dataclass
class JwtGatewayConfig:
    """Configuration for the JWT (CUSTOM_JWT) AgentCore Gateway.

    Points at any OIDC-compliant identity provider.
    """

    discovery_url: str
    allowed_audiences: list[str] = field(default_factory=list)
    allowed_clients: list[str] | None = None
    allowed_scopes: list[str] | None = None
    required_claims: list[CustomClaimRule] | None = None
    initial_cedar_policies: list[str] | None = None


# --- Standalone helper functions ---


def resource_policy_for_accounts(account_ids: list[str]) -> dict:
    """Build a resource policy allowing specific AWS accounts.

    The ``GATEWAY_ARN`` placeholder is replaced at deploy time by the
    construct.
    """
    principals = [f"arn:aws:iam::{a}:root" for a in account_ids]
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": principals if len(principals) > 1 else principals[0]},
            "Action": "bedrock-agentcore:InvokeGateway",
            "Resource": "GATEWAY_ARN",
        }],
    }


def resource_policy_for_org(org_id: str) -> dict:
    """Build a resource policy allowing an entire AWS Organization."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": "*",
            "Action": "bedrock-agentcore:InvokeGateway",
            "Resource": "GATEWAY_ARN",
            "Condition": {"StringEquals": {"aws:PrincipalOrgID": org_id}},
        }],
    }


def resource_policy_allow_all() -> dict:
    """Build a wide-open resource policy (use with Cedar as the gate)."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": "*",
            "Action": "bedrock-agentcore:InvokeGateway",
            "Resource": "GATEWAY_ARN",
        }],
    }


def cedar_permit_account(account_id: str) -> str:
    """Generate a Cedar permit policy for a single AWS account.

    Uses ``AgentCore::IamEntity`` with ``principal.id like`` pattern
    matching on the account's IAM ARN prefix.
    """
    return (
        f'permit(\n'
        f'  principal is AgentCore::IamEntity,\n'
        f'  action,\n'
        f'  resource is AgentCore::Gateway\n'
        f')\n'
        f'when {{\n'
        f'  principal.id like "*:{account_id}:*"\n'
        f'}};'
    )
