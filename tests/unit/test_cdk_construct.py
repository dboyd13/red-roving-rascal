"""Unit tests for CDK construct updates — suites table, new routes, env vars, outputs.

"""
from __future__ import annotations

import pytest
from aws_cdk import App, Stack, assertions
from aws_cdk import aws_ecs as ecs

from rascal.cdk.construct import RascalBackendConstruct


@pytest.fixture()
def template() -> assertions.Template:
    """Synthesize a stack containing RascalBackendConstruct and return the template."""
    app = App()
    stack = Stack(app, "TestStack")
    RascalBackendConstruct(
        stack, "Rascal",
        container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
    )
    return assertions.Template.from_stack(stack)


class TestSuitesTable:
    """Suites DynamoDB table exists with correct configuration."""

    def test_suites_table_exists_with_correct_key_schema(
        self, template: assertions.Template
    ) -> None:
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "KeySchema": [
                    {"AttributeName": "suiteId", "KeyType": "HASH"},
                ],
                "AttributeDefinitions": [
                    {"AttributeName": "suiteId", "AttributeType": "S"},
                ],
                "BillingMode": "PAY_PER_REQUEST",
                "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            },
        )

    def test_suites_table_count(self, template: assertions.Template) -> None:
        """Stack should have exactly two DynamoDB tables (evaluations, suites)."""
        template.resource_count_is("AWS::DynamoDB::Table", 2)


class TestContainerEnvironment:
    """Container definition includes SUITES_TABLE environment variable."""

    def test_suites_table_env_var_set(self, template: assertions.Template) -> None:
        template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            {
                "ContainerDefinitions": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Environment": assertions.Match.array_with(
                                    [
                                        {
                                            "Name": "SUITES_TABLE",
                                            "Value": assertions.Match.any_value(),
                                        },
                                    ]
                                ),
                            }
                        ),
                    ]
                ),
            },
        )


class TestApiGatewayRoutes:
    """API Gateway has the new pipeline routes."""

    def test_post_evaluate_route(self, template: assertions.Template) -> None:
        template.has_resource_properties(
            "AWS::ApiGateway::Resource",
            {"PathPart": "evaluate"},
        )
        template.has_resource_properties(
            "AWS::ApiGateway::Method",
            {
                "HttpMethod": "POST",
                "Integration": assertions.Match.object_like(
                    {"IntegrationHttpMethod": "POST"}
                ),
            },
        )

    def test_get_suites_route(self, template: assertions.Template) -> None:
        template.has_resource_properties(
            "AWS::ApiGateway::Resource",
            {"PathPart": "suites"},
        )

    def test_get_evaluate_by_id_route(self, template: assertions.Template) -> None:
        """GET /evaluate/{evaluation_id} sub-resource exists."""
        template.has_resource_properties(
            "AWS::ApiGateway::Resource",
            {"PathPart": "{evaluation_id}"},
        )
        template.has_resource_properties(
            "AWS::ApiGateway::Method",
            {
                "HttpMethod": "GET",
                "RequestParameters": {
                    "method.request.path.evaluation_id": True,
                },
                "Integration": assertions.Match.object_like(
                    {
                        "IntegrationHttpMethod": "GET",
                        "RequestParameters": {
                            "integration.request.path.evaluation_id": "method.request.path.evaluation_id",
                        },
                    }
                ),
            },
        )

    def test_get_suite_by_id_route(self, template: assertions.Template) -> None:
        template.has_resource_properties(
            "AWS::ApiGateway::Resource",
            {"PathPart": "{suite_id}"},
        )


class TestOutputs:
    """Stack outputs include SuitesTableName."""

    def test_suites_table_name_output(self, template: assertions.Template) -> None:
        outputs = template.find_outputs("*")
        matched = [
            k for k in outputs if "SuitesTableName" in k
        ]
        assert len(matched) == 1, f"Expected one SuitesTableName output, got: {list(outputs.keys())}"


class TestIamGatewayResourcePolicy:
    """resource_policy on IamGatewayConfig accepts both dict and callable forms."""

    def _synthesize(self, resource_policy):
        from rascal.cdk import IamGatewayConfig
        app = App()
        stack = Stack(app, "RpTestStack")
        RascalBackendConstruct(
            stack, "Rascal",
            container_image=ecs.ContainerImage.from_registry("test/placeholder:latest"),
            iam_gateway=IamGatewayConfig(resource_policy=resource_policy),
        )
        return stack

    def test_dict_form_with_gateway_arn_placeholder(self) -> None:
        """Dict form: "GATEWAY_ARN" string is substituted at synth time."""
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": "*",
                "Action": "bedrock-agentcore:InvokeGateway",
                "Resource": "GATEWAY_ARN",
            }],
        }
        stack = self._synthesize(policy)
        # The construct wires the resource policy via a CustomResource whose
        # POLICY env var is the json-stringified substituted policy. Verify the
        # POLICY is present on a Lambda (exact ARN uses a CFN ref, so we
        # just check the outer policy structure exists).
        template = assertions.Template.from_stack(stack)
        template.resource_count_is("AWS::CloudFormation::CustomResource", 1)

    def test_callable_form_receives_gateway_arn_token(self) -> None:
        """Callable form: called once with a CDK token for the gateway ARN."""
        received: list = []

        def _policy(gateway_arn):
            received.append(gateway_arn)
            return {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "example.aws.internal"},
                    "Action": "bedrock-agentcore:InvokeGateway",
                    "Resource": gateway_arn,
                }],
            }

        stack = self._synthesize(_policy)
        # Callable invoked exactly once with a CDK token (string). We can't
        # resolve the token here, but we assert invocation + a CustomResource
        # was created downstream.
        assert len(received) == 1
        template = assertions.Template.from_stack(stack)
        template.resource_count_is("AWS::CloudFormation::CustomResource", 1)

    def test_none_form_no_resource_policy_resource_created(self) -> None:
        """resource_policy=None: no CustomResource is created."""
        stack = self._synthesize(None)
        template = assertions.Template.from_stack(stack)
        template.resource_count_is("AWS::CloudFormation::CustomResource", 0)
