"""Unit tests for CDK construct — AgentCore Gateway configuration."""
from __future__ import annotations

import pytest
from aws_cdk import App, Stack, assertions
from aws_cdk import aws_ecs as ecs

from rascal.cdk.construct import RascalBackendConstruct


@pytest.fixture()
def template() -> assertions.Template:
    app = App()
    stack = Stack(app, "TestStack")
    RascalBackendConstruct(
        stack, "Rascal",
        container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
    )
    return assertions.Template.from_stack(stack)


@pytest.fixture()
def jwt_template() -> assertions.Template:
    app = App()
    stack = Stack(app, "TestStack")
    RascalBackendConstruct(
        stack, "Rascal",
        container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
        jwt_issuer_url="https://example.auth0.com/",
        jwt_audience=["my-api"],
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


class TestZeroAuthInBackend:
    def test_backend_no_auth_imports(self) -> None:
        import importlib, inspect, rascal.app
        importlib.reload(rascal.app)
        source = inspect.getsource(rascal.app)
        assert "verifiedpermissions" not in source
        assert "bedrock-agentcore-control" not in source
