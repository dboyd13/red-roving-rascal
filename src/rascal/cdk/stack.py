"""Convenience stack wrapping RascalBackendConstruct."""
from __future__ import annotations

from constructs import Construct
from aws_cdk import Stack, StackProps, RemovalPolicy
from aws_cdk import aws_ecs as ecs

from rascal.cdk.construct import RascalBackendConstruct


class RascalStack(Stack):
    """Standalone stack for the rascal backend."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        allowed_account_ids: list[str] | None = None,
        principal_org_id: str | None = None,
        container_image: ecs.ContainerImage | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        self.backend = RascalBackendConstruct(
            self, "Backend",
            allowed_account_ids=allowed_account_ids,
            principal_org_id=principal_org_id,
            container_image=container_image,
            removal_policy=RemovalPolicy.DESTROY,
        )
