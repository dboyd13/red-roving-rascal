# red-roving-rascal

A pluggable analysis and scoring framework for evaluating input/output pairs against configurable quality criteria.

## Install (library only)

```bash
pip install red-roving-rascal
```

## Deploy to AWS

Deploys API Gateway → ECS Fargate → DynamoDB into your AWS account.

### Prerequisites

- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [Docker](https://docs.docker.com/get-docker/) installed and running
- [Node.js](https://nodejs.org/) ≥ 18 (for `npx cdk`)
- Python ≥ 3.10

### One-command deploy

```bash
git clone https://github.com/dboyd13/red-roving-rascal.git
cd red-roving-rascal
python3 -m venv .venv
.venv/bin/pip install -e ".[cdk]"
npx cdk@latest bootstrap   # first time per account/region
npx cdk@latest deploy
```

This builds the Docker image locally from the included `Dockerfile`, pushes it to a CDK-managed ECR repository, and deploys the full stack. Defaults to `us-east-1`; set `CDK_DEFAULT_REGION` to change:

```bash
CDK_DEFAULT_REGION=eu-west-1 npx cdk@latest deploy
```

### Access control (optional)

Restrict API access to specific AWS accounts:

```bash
RASCAL_ALLOWED_ACCOUNTS="111111111111,222222222222" npx cdk@latest deploy
```

Or to an AWS Organization:

```bash
RASCAL_ORG_ID="o-abc123" npx cdk@latest deploy
```

Without either, the API Gateway resource policy denies all requests by default.

### What gets deployed

- VPC with public/private subnets
- ECS Fargate service running the rascal backend
- Network Load Balancer (internal)
- API Gateway with IAM auth and VPC Link
- Three DynamoDB tables (data, jobs, suites)
- CloudWatch alarms (5xx errors, CPU utilization)

### Tear down

```bash
npx cdk@latest destroy
```

## Quick start (client SDK)

```python
from rascal.client import RascalClient

client = RascalClient(endpoint="https://your-api-endpoint.execute-api.us-east-1.amazonaws.com/v1")
```

## Extending with custom analyzers

rascal uses a plugin model. Write a wrapper that registers your components, then start the server:

```python
from rascal.registry import Registry
from rascal.server import run

# Register your custom analyzer + judge
Registry.register("analyzer.custom", MyAnalyzer())
Registry.register("judge.custom", MyJudge())

run()
```

See `src/rascal/registry.py` for the `Analyzer` and `Judge` protocol definitions.

## Development

```bash
pip install -e ".[cdk]"
pytest
```

## License

Apache 2.0
