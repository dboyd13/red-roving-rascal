"""Convenience stack wrapping RascalBackendConstruct."""
from __future__ import annotations

from constructs import Construct
from aws_cdk import Stack, RemovalPolicy
from aws_cdk import aws_ecs as ecs

from rascal.cdk.construct import RascalBackendConstruct
from rascal.cdk.gateway_config import IamGatewayConfig, JwtGatewayConfig


class RascalStack(Stack):
    """Standalone stack for the rascal backend."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        container_image: ecs.ContainerImage,
        iam_gateway: IamGatewayConfig | None = None,
        jwt_gateway: JwtGatewayConfig | None = None,
        request_interceptor_arn: str | None = None,
        response_interceptor_arn: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        self.backend = RascalBackendConstruct(
            self, "Backend",
            container_image=container_image,
            iam_gateway=iam_gateway,
            jwt_gateway=jwt_gateway,
            request_interceptor_arn=request_interceptor_arn,
            response_interceptor_arn=response_interceptor_arn,
            removal_policy=RemovalPolicy.DESTROY,
        )
