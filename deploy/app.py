#!/usr/bin/env python3
"""One-command CDK app for deploying rascal to any AWS account.

Usage:
    pip install -e ".[cdk]"
    npx cdk bootstrap
    RASCAL_ALLOWED_ACCOUNTS="123456789012" npx cdk deploy

Environment variables:
    RASCAL_ALLOWED_ACCOUNTS   Comma-separated account IDs for IAM gateway.
    RASCAL_ALLOWED_ORG        AWS Organization ID for org-wide access.
    RASCAL_CEDAR_ACCOUNTS     Comma-separated account IDs for Cedar allowlisting
                              (uses Cedar policies + broad resource policy).
    RASCAL_OAUTH_DISCOVERY    OIDC discovery URL — enables OAuth gateway.
    RASCAL_OAUTH_AUDIENCES    Comma-separated JWT audience values.
"""
from __future__ import annotations

import os

import aws_cdk as cdk
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecr_assets as ecr_assets

from rascal.cdk.stack import RascalStack
from rascal.cdk.gateway_config import (
    IamGatewayConfig,
    JwtGatewayConfig,
    resource_policy_for_accounts,
    resource_policy_for_org,
    resource_policy_allow_all,
    cedar_permit_account,
)

app = cdk.App()


def _parse_csv(env_var: str) -> list[str]:
    return [a.strip() for a in os.environ.get(env_var, "").split(",") if a.strip()]


# --- IAM gateway config ---
allowed_accounts = _parse_csv("RASCAL_ALLOWED_ACCOUNTS")
allowed_org = os.environ.get("RASCAL_ALLOWED_ORG", "").strip() or None
cedar_accounts = _parse_csv("RASCAL_CEDAR_ACCOUNTS")

iam_gw = None
if cedar_accounts:
    # Cedar allowlisting: Cedar is the gate, resource policy is the broad perimeter.
    # Use org-scoped perimeter if available, otherwise allow-all.
    perimeter = resource_policy_for_org(allowed_org) if allowed_org else resource_policy_allow_all()
    iam_gw = IamGatewayConfig(
        resource_policy=perimeter,
        initial_cedar_policies=[cedar_permit_account(a) for a in cedar_accounts],
    )
elif allowed_accounts:
    # Resource policy allowlisting (simple, static)
    iam_gw = IamGatewayConfig(resource_policy=resource_policy_for_accounts(allowed_accounts))
elif allowed_org:
    # Org-wide access via resource policy
    iam_gw = IamGatewayConfig(resource_policy=resource_policy_for_org(allowed_org))

# --- OAuth gateway config ---
discovery = os.environ.get("RASCAL_OAUTH_DISCOVERY", "").strip() or None
jwt_gw = None
if discovery:
    auds = _parse_csv("RASCAL_OAUTH_AUDIENCES")
    jwt_gw = JwtGatewayConfig(discovery_url=discovery, allowed_audiences=auds)

# Default: bare IAM gateway (same-account only)
if not iam_gw and not jwt_gw:
    iam_gw = IamGatewayConfig()

RascalStack(app, "RascalStack",
    container_image=ecs.ContainerImage.from_asset(".", platform=ecr_assets.Platform.LINUX_AMD64),
    iam_gateway=iam_gw, jwt_gateway=jwt_gw,
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1")))

app.synth()
