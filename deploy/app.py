#!/usr/bin/env python3
"""One-command CDK app for deploying rascal to any AWS account.

Usage:
    pip install -e ".[cdk]"
    npx cdk bootstrap   # first time only
    RASCAL_ALLOWED_ACCOUNTS="123456789012" npx cdk deploy

Environment variables:
    RASCAL_ALLOWED_ACCOUNTS  Comma-separated 12-digit account IDs to permit
                             via gateway resource policy at deploy time.
    RASCAL_JWT_ISSUER_URL    JWT issuer URL (enables CUSTOM_JWT authorizer).
    RASCAL_JWT_AUDIENCE      Comma-separated JWT audience values.
"""
from __future__ import annotations

import os

import aws_cdk as cdk
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecr_assets as ecr_assets

from rascal.cdk.stack import RascalStack

app = cdk.App()

allowed = os.environ.get("RASCAL_ALLOWED_ACCOUNTS", "")

RascalStack(
    app,
    "RascalStack",
    container_image=ecs.ContainerImage.from_asset(
        ".",
        platform=ecr_assets.Platform.LINUX_AMD64,
    ),
    allowed_account_ids=[a.strip() for a in allowed.split(",") if a.strip()] or None,
    jwt_issuer_url=os.environ.get("RASCAL_JWT_ISSUER_URL") or None,
    jwt_audience=[a.strip() for a in os.environ.get("RASCAL_JWT_AUDIENCE", "").split(",") if a.strip()] or None,
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)

app.synth()
