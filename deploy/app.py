#!/usr/bin/env python3
"""One-command CDK app for deploying rascal to any AWS account.

Usage:
    pip install -e ".[cdk]"
    npx cdk bootstrap
    RASCAL_ALLOWED_ACCOUNTS="123456789012" npx cdk deploy

Environment variables:
    RASCAL_ALLOWED_ACCOUNTS   Comma-separated account IDs for IAM gateway.
    RASCAL_OAUTH_DISCOVERY    OIDC discovery URL — enables OAuth gateway.
    RASCAL_OAUTH_AUDIENCES    Comma-separated JWT audience values.
    RASCAL_JWT_ISSUER_URL     Legacy: JWT issuer (single JWT gateway).
    RASCAL_JWT_AUDIENCE       Legacy: comma-separated JWT audiences.
"""
from __future__ import annotations

import os

import aws_cdk as cdk
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecr_assets as ecr_assets

from rascal.cdk.stack import RascalStack
from rascal.cdk.gateway_config import IamGatewayConfig, JwtGatewayConfig

app = cdk.App()

# IAM gateway
allowed = os.environ.get("RASCAL_ALLOWED_ACCOUNTS", "")
accts = [a.strip() for a in allowed.split(",") if a.strip()]
iam_gw = IamGatewayConfig(allowed_account_ids=accts) if accts else None

# OAuth gateway
discovery = os.environ.get("RASCAL_OAUTH_DISCOVERY")
jwt_gw = None
if discovery:
    auds = [a.strip() for a in os.environ.get("RASCAL_OAUTH_AUDIENCES", "").split(",") if a.strip()]
    jwt_gw = JwtGatewayConfig(discovery_url=discovery, allowed_audiences=auds)

# Legacy JWT (only when new API not used)
jwt_url = None
jwt_aud = None
if not iam_gw and not jwt_gw:
    jwt_url = os.environ.get("RASCAL_JWT_ISSUER_URL") or None
    jwt_aud = [a.strip() for a in os.environ.get("RASCAL_JWT_AUDIENCE", "").split(",") if a.strip()] or None

RascalStack(app, "RascalStack",
    container_image=ecs.ContainerImage.from_asset(".", platform=ecr_assets.Platform.LINUX_AMD64),
    iam_gateway=iam_gw, jwt_gateway=jwt_gw,
    jwt_issuer_url=jwt_url, jwt_audience=jwt_aud,
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1")))

app.synth()
