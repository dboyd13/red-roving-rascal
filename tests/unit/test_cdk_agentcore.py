"""Unit tests for CDK construct — AgentCore Gateway configuration."""
from __future__ import annotations

import json

import pytest
from aws_cdk import App, Stack, assertions
from aws_cdk import aws_ecs as ecs

from rascal.cdk.construct import RascalBackendConstruct
from rascal.cdk.gateway_config import (
    IamGatewayConfig,
    JwtGatewayConfig,
    resource_policy_for_accounts,
    resource_policy_for_org,
    resource_policy_allow_all,
    cedar_permit_account,
)


@pytest.fixture()
def template() -> assertions.Template:
    """Bare IAM gateway — no resource policy, no Cedar."""
    app = App()
    stack = Stack(app, "TestStack")
    RascalBackendConstruct(
        stack, "Rascal",
        container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
    )
    return assertions.Template.from_stack(stack)


@pytest.fixture()
def rp_template() -> assertions.Template:
    """IAM gateway with resource policy only (no Cedar)."""
    app = App()
    stack = Stack(app, "TestStack")
    RascalBackendConstruct(
        stack, "Rascal",
        container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
        iam_gateway=IamGatewayConfig(
            resource_policy=resource_policy_for_accounts(["111122223333"]),
        ),
    )
    return assertions.Template.from_stack(stack)


@pytest.fixture()
def jwt_template() -> assertions.Template:
    """JWT gateway only."""
    app = App()
    stack = Stack(app, "TestStack")
    RascalBackendConstruct(
        stack, "Rascal",
        container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
        jwt_gateway=JwtGatewayConfig(
            discovery_url="https://example.auth0.com/",
            allowed_audiences=["my-api"],
        ),
    )
    return assertions.Template.from_stack(stack)


@pytest.fixture()
def cedar_template() -> assertions.Template:
    """IAM gateway with Cedar allowlisting + broad resource policy."""
    app = App()
    stack = Stack(app, "TestStack")
    RascalBackendConstruct(
        stack, "Rascal",
        container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
        iam_gateway=IamGatewayConfig(
            resource_policy=resource_policy_allow_all(),
            initial_cedar_policies=[
                cedar_permit_account("111122223333"),
                cedar_permit_account("444455556666"),
            ],
        ),
    )
    return assertions.Template.from_stack(stack)


class TestAgentCoreGateway:
    def test_gateway_created(self, template: assertions.Template) -> None:
        template.has_resource("AWS::BedrockAgentCore::Gateway", assertions.Match.any_value())

    def test_target_created(self, template: assertions.Template) -> None:
        template.has_resource("AWS::BedrockAgentCore::GatewayTarget", assertions.Match.any_value())

    def test_protocol_type_mcp(self, template: assertions.Template) -> None:
        template.has_resource_properties("AWS::BedrockAgentCore::Gateway", {"ProtocolType": "MCP"})


class TestOutputs:
    def test_gateway_id(self, template: assertions.Template) -> None:
        assert any("AgentCoreGatewayId" in k for k in template.find_outputs("*"))

    def test_endpoint(self, template: assertions.Template) -> None:
        assert any("AgentCoreEndpoint" in k for k in template.find_outputs("*"))


class TestApiGatewayLocked:
    def test_allows_only_agentcore(self, template: assertions.Template) -> None:
        template.has_resource_properties(
            "AWS::ApiGateway::RestApi",
            {"Policy": assertions.Match.object_like({
                "Statement": assertions.Match.array_with([
                    assertions.Match.object_like({
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                        "Action": "execute-api:Invoke",
                    }),
                ]),
            })},
        )


class TestEcsTaskRole:
    def test_no_agentcore_permissions(self, template: assertions.Template) -> None:
        template_json = template.to_json()
        task_role_ref = None
        for lid, resource in template_json.get("Resources", {}).items():
            if resource.get("Type") == "AWS::ECS::TaskDefinition":
                task_role_ref = (
                    resource.get("Properties", {})
                    .get("TaskRoleArn", {})
                    .get("Fn::GetAtt", [None])[0]
                )
                break
        for lid, resource in template_json.get("Resources", {}).items():
            if resource.get("Type") != "AWS::IAM::Policy":
                continue
            roles = resource.get("Properties", {}).get("Roles", [])
            if not any(
                (isinstance(r, dict) and r.get("Ref") == task_role_ref) or r == task_role_ref
                for r in roles
            ):
                continue
            for stmt in resource.get("Properties", {}).get("PolicyDocument", {}).get("Statement", []):
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                for action in actions:
                    assert "bedrock-agentcore" not in action.lower()


class TestAuthorizerConfig:
    def test_aws_iam_by_default(self, template: assertions.Template) -> None:
        template.has_resource_properties("AWS::BedrockAgentCore::Gateway", {"AuthorizerType": "AWS_IAM"})

    def test_jwt_when_provided(self, jwt_template: assertions.Template) -> None:
        jwt_template.has_resource_properties("AWS::BedrockAgentCore::Gateway", {"AuthorizerType": "CUSTOM_JWT"})

    def test_jwt_config(self, jwt_template: assertions.Template) -> None:
        jwt_template.has_resource_properties(
            "AWS::BedrockAgentCore::Gateway",
            {"AuthorizerConfiguration": assertions.Match.object_like({
                "CustomJWTAuthorizer": assertions.Match.object_like({
                    "DiscoveryUrl": "https://example.auth0.com/",
                }),
            })},
        )


class TestPolicyEngine:
    def test_always_provisioned_iam(self, template: assertions.Template) -> None:
        """Bare IAM gateway without Cedar does NOT get a policy engine."""
        template_json = template.to_json()
        for _lid, resource in template_json.get("Resources", {}).items():
            assert resource.get("Type") != "AWS::BedrockAgentCore::PolicyEngine", \
                "IAM gateway without Cedar policies should not have a PolicyEngine"

    def test_not_provisioned_jwt_without_cedar(self, jwt_template: assertions.Template) -> None:
        """JWT gateways without Cedar policies rely on JWT config alone."""
        template_json = jwt_template.to_json()
        for _lid, resource in template_json.get("Resources", {}).items():
            assert resource.get("Type") != "AWS::BedrockAgentCore::PolicyEngine", \
                "JWT gateway without Cedar policies should not have a PolicyEngine"

    def test_provisioned_jwt_with_cedar(self) -> None:
        """JWT gateways WITH Cedar policies get a policy engine."""
        app = App()
        stack = Stack(app, "TestStack")
        RascalBackendConstruct(
            stack, "Rascal",
            container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
            jwt_gateway=JwtGatewayConfig(
                discovery_url="https://example.auth0.com/",
                allowed_audiences=["my-api"],
                initial_cedar_policies=[
                    'permit(principal is AgentCore::OAuthUser, action, resource);',
                ],
            ),
        )
        tmpl = assertions.Template.from_stack(stack)
        tmpl.has_resource("AWS::BedrockAgentCore::PolicyEngine", assertions.Match.any_value())
        tmpl.has_resource("AWS::BedrockAgentCore::Policy", assertions.Match.any_value())

    def test_gateway_links_to_engine(self, cedar_template) -> None:
        """Gateway with Cedar references the policy engine."""
        cedar_template.has_resource_properties(
            "AWS::BedrockAgentCore::Gateway",
            {"PolicyEngineConfiguration": assertions.Match.object_like({
                "Mode": "ENFORCE",
            })},
        )

    def test_gateway_role_has_policy_permissions(self, template: assertions.Template) -> None:
        template_json = template.to_json()
        found = False
        for _lid, resource in template_json.get("Resources", {}).items():
            if resource.get("Type") != "AWS::IAM::Policy":
                continue
            for stmt in resource.get("Properties", {}).get("PolicyDocument", {}).get("Statement", []):
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                if "bedrock-agentcore:AuthorizeAction" in actions:
                    found = True
                    break
        assert found


class TestResourcePolicyOnly:
    def test_resource_policy_applied(self, rp_template: assertions.Template) -> None:
        """Resource policy Lambda is created when resource_policy is provided."""
        rp_template.has_resource_properties(
            "AWS::Lambda::Function",
            {"Environment": assertions.Match.object_like({
                "Variables": assertions.Match.object_like({
                    "POLICY": assertions.Match.any_value(),
                }),
            })},
        )

    def test_no_cedar_seed(self, rp_template: assertions.Template) -> None:
        """No Cedar seed Lambda when no initial_cedar_policies."""
        template_json = rp_template.to_json()
        for _lid, resource in template_json.get("Resources", {}).items():
            if resource.get("Type") != "AWS::Lambda::Function":
                continue
            env_vars = resource.get("Properties", {}).get("Environment", {}).get("Variables", {})
            assert "CEDAR_POLICIES" not in env_vars


class TestCedarPolicySeeding:
    def test_cedar_policies_created(self, cedar_template: assertions.Template) -> None:
        """Cedar policies are created as native CFN resources."""
        cedar_template.resource_count_is("AWS::BedrockAgentCore::Policy", 2)

    def test_policy_engine_created(self, cedar_template: assertions.Template) -> None:
        cedar_template.has_resource("AWS::BedrockAgentCore::PolicyEngine", assertions.Match.any_value())

    def test_no_policies_without_cedar(self, template: assertions.Template) -> None:
        """IAM gateway without Cedar policies still gets engine but no Policy resources."""
        template_json = template.to_json()
        for _lid, resource in template_json.get("Resources", {}).items():
            assert resource.get("Type") != "AWS::BedrockAgentCore::Policy", \
                "Should not have Policy resources without initial_cedar_policies"

    def test_jwt_gateway_cedar_seeding(self) -> None:
        """JWT gateway also supports Cedar policy seeding."""
        app = App()
        stack = Stack(app, "TestStack")
        RascalBackendConstruct(
            stack, "Rascal",
            container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
            jwt_gateway=JwtGatewayConfig(
                discovery_url="https://example.auth0.com/",
                allowed_audiences=["my-api"],
                initial_cedar_policies=[
                    'permit(principal is AgentCore::OAuthUser, action, resource);',
                ],
            ),
        )
        tmpl = assertions.Template.from_stack(stack)
        tmpl.resource_count_is("AWS::BedrockAgentCore::Policy", 1)
        tmpl.has_resource("AWS::BedrockAgentCore::PolicyEngine", assertions.Match.any_value())


class TestHelperFunctions:
    def test_resource_policy_for_accounts(self) -> None:
        policy = resource_policy_for_accounts(["111122223333"])
        stmt = policy["Statement"][0]
        assert stmt["Principal"]["AWS"] == "arn:aws:iam::111122223333:root"
        assert stmt["Resource"] == "GATEWAY_ARN"

    def test_resource_policy_for_accounts_multiple(self) -> None:
        policy = resource_policy_for_accounts(["111122223333", "444455556666"])
        assert len(policy["Statement"][0]["Principal"]["AWS"]) == 2

    def test_resource_policy_for_org(self) -> None:
        policy = resource_policy_for_org("o-abc123")
        assert policy["Statement"][0]["Condition"]["StringEquals"]["aws:PrincipalOrgID"] == "o-abc123"

    def test_resource_policy_allow_all(self) -> None:
        assert resource_policy_allow_all()["Statement"][0]["Principal"] == "*"

    def test_cedar_permit_account(self) -> None:
        cedar = cedar_permit_account("111122223333")
        assert "AgentCore::IamEntity" in cedar
        assert "111122223333" in cedar
        assert "permit(" in cedar
        assert "resource is AgentCore::Gateway" in cedar
        assert 'principal.id like "*:111122223333:*"' in cedar


class TestZeroAuthInBackend:
    def test_backend_no_auth_imports(self) -> None:
        import importlib, inspect, rascal.app
        importlib.reload(rascal.app)
        source = inspect.getsource(rascal.app)
        assert "verifiedpermissions" not in source
        assert "bedrock-agentcore-control" not in source
