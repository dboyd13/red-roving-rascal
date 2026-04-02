"""Gateway configuration dataclasses for the rascal CDK construct."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IamGatewayConfig:
    """Configuration for the IAM (SigV4) AgentCore Gateway.

    Controls who can call ``bedrock-agentcore:InvokeGateway`` via
    resource policy. Pick one allowlisting mode or provide a full
    escape-hatch policy.

    Examples::

        # Allow specific accounts
        IamGatewayConfig(allowed_account_ids=["123456789012"])

        # Allow an entire AWS Organization
        IamGatewayConfig(allowed_org_id="o-abc123")
    """

    allowed_account_ids: list[str] | None = None
    allowed_org_id: str | None = None
    resource_policy: dict | None = None  # escape hatch


@dataclass
class CustomClaimRule:
    """A rule for validating a custom claim in the JWT token.

    Examples::

        # Require group == "SecurityTeam"
        CustomClaimRule(claim_name="group", match_value="SecurityTeam")

        # Require roles array contains "admin"
        CustomClaimRule(
            claim_name="roles",
            value_type="STRING_ARRAY",
            match_value="admin",
            match_operator="CONTAINS",
        )
    """

    claim_name: str
    match_value: str
    value_type: str = "STRING"  # STRING or STRING_ARRAY
    match_operator: str = "EQUALS"  # EQUALS, CONTAINS, CONTAINS_ANY


@dataclass
class JwtGatewayConfig:
    """Configuration for the JWT (CUSTOM_JWT) AgentCore Gateway.

    Points at any OIDC-compliant identity provider. The construct does
    not create IdP resources — the caller sets up the IdP externally
    and provides the discovery URL.

    At least one of ``allowed_audiences``, ``allowed_clients``,
    ``allowed_scopes``, or ``required_claims`` must be provided.

    Examples::

        # Auth0 (supports DCR — works with MCP clients like Kiro)
        JwtGatewayConfig(
            discovery_url="https://tenant.us.auth0.com/.well-known/openid-configuration",
            allowed_audiences=["https://my-gateway.../mcp"],
        )

        # Restrict to specific clients and scopes
        JwtGatewayConfig(
            discovery_url="https://idp.example.com/.well-known/openid-configuration",
            allowed_audiences=["my-api"],
            allowed_clients=["client-id-1", "client-id-2"],
            allowed_scopes=["rascal/evaluate", "rascal/read"],
        )

        # Require custom claims (e.g. team membership)
        JwtGatewayConfig(
            discovery_url="https://idp.example.com/.well-known/openid-configuration",
            allowed_audiences=["my-api"],
            required_claims=[
                CustomClaimRule(claim_name="team", match_value="security"),
            ],
        )
    """

    discovery_url: str
    allowed_audiences: list[str] = field(default_factory=list)
    allowed_clients: list[str] | None = None
    allowed_scopes: list[str] | None = None
    required_claims: list[CustomClaimRule] | None = None
