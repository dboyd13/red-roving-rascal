"""Unit tests for CDK construct updates — suites table, new routes, env vars, outputs.

Validates: Requirements 8.7
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
        """Stack should have exactly three DynamoDB tables (data, jobs, suites)."""
        template.resource_count_is("AWS::DynamoDB::Table", 3)


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
