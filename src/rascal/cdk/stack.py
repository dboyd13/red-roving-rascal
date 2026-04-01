"""Convenience stack wrapping RascalBackendConstruct."""
from __future__ import annotations

from constructs import Construct
from aws_cdk import Stack, RemovalPolicy
from aws_cdk import aws_ecs as ecs

from rascal.cdk.construct import RascalBackendConstruct


class RascalStack(Stack):
    """Standalone stack for the rascal backend."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        container_image: ecs.ContainerImage,
        allowed_account_ids: list[str] | None = None,
        allowed_org_id: str | None = None,
        gateway_resource_policy: dict | None = None,
        request_interceptor_arn: str | None = None,
        response_interceptor_arn: str | None = None,
        jwt_issuer_url: str | None = None,
        jwt_audience: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        self.backend = RascalBackendConstruct(
            self, "Backend",
            container_image=container_image,
            allowed_account_ids=allowed_account_ids,
            allowed_org_id=allowed_org_id,
            gateway_resource_policy=gateway_resource_policy,
            request_interceptor_arn=request_interceptor_arn,
            response_interceptor_arn=response_interceptor_arn,
            jwt_issuer_url=jwt_issuer_url,
            jwt_audience=jwt_audience,
            removal_policy=RemovalPolicy.DESTROY,
        )
