#!/usr/bin/env python3
"""One-command CDK app for deploying rascal to any AWS account.

Usage:
    pip install -e ".[cdk]"
    npx cdk bootstrap   # first time only
    npx cdk deploy
"""
from __future__ import annotations

import os

import aws_cdk as cdk
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecr_assets as ecr_assets

from rascal.cdk.stack import RascalStack

app = cdk.App()

# Optional: set via env vars or cdk.json context
allowed = os.environ.get("RASCAL_ALLOWED_ACCOUNTS", "")
org_id = os.environ.get("RASCAL_ORG_ID") or None

RascalStack(
    app,
    "RascalStack",
    allowed_account_ids=[a.strip() for a in allowed.split(",") if a.strip()] or None,
    principal_org_id=org_id,
    container_image=ecs.ContainerImage.from_asset(
        ".",
        platform=ecr_assets.Platform.LINUX_AMD64,
    ),
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)

app.synth()
