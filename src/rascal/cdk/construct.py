"""Reusable CDK construct: API Gateway (IAM auth + deny-by-default) ->
VPC Link -> NLB -> ECS Fargate -> DynamoDB.

Uses only aws-cdk-lib + constructs. No vendor-specific dependencies.
"""
from __future__ import annotations

from constructs import Construct
from aws_cdk import (
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    aws_elasticloadbalancingv2 as elbv2,
    aws_apigateway as apigw,
    aws_logs as logs,
    aws_cloudwatch as cw,
)


class RascalBackendConstruct(Construct):
    """Backend infrastructure: API GW -> NLB -> ECS Fargate -> DynamoDB."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        vpc: ec2.IVpc | None = None,
        allowed_account_ids: list[str] | None = None,
        principal_org_id: str | None = None,
        container_image: ecs.ContainerImage | None = None,
        task_cpu: int = 1024,
        task_memory_mib: int = 2048,
        desired_count: int = 1,
        container_port: int = 8080,
        removal_policy: RemovalPolicy = RemovalPolicy.DESTROY,
    ) -> None:
        super().__init__(scope, id)

        allowed_account_ids = allowed_account_ids or []

        # VPC
        self._vpc = vpc or ec2.Vpc(
            self, "Vpc", max_azs=2, nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
            ],
        )

        # DynamoDB
        self.data_table = dynamodb.Table(
            self, "DataTable",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal_policy,
            point_in_time_recovery=True,
        )
        self.jobs_table = dynamodb.Table(
            self, "JobsTable",
            partition_key=dynamodb.Attribute(name="jobId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal_policy,
            time_to_live_attribute="ttl",
        )

        # ECS
        cluster = ecs.Cluster(self, "Cluster", vpc=self._vpc)
        task_def = ecs.FargateTaskDefinition(self, "TaskDef", cpu=task_cpu, memory_limit_mib=task_memory_mib)
        self.task_role = task_def.task_role

        log_group = logs.LogGroup(self, "Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=removal_policy)

        task_def.add_container(
            "App",
            image=container_image or ecs.ContainerImage.from_registry("amazon/amazon-ecs-sample"),
            port_mappings=[ecs.PortMapping(container_port=container_port)],
            environment={
                "DATA_TABLE": self.data_table.table_name,
                "JOBS_TABLE": self.jobs_table.table_name,
                "PORT": str(container_port),
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix="rascal", log_group=log_group),
        )

        self.data_table.grant_read_write_data(task_def.task_role)
        self.jobs_table.grant_read_write_data(task_def.task_role)

        sg = ec2.SecurityGroup(self, "Sg", vpc=self._vpc, description="Backend ECS service", allow_all_outbound=True)

        service = ecs.FargateService(
            self, "Service", cluster=cluster, task_definition=task_def,
            desired_count=desired_count, assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[sg],
        )

        # NLB
        nlb = elbv2.NetworkLoadBalancer(
            self, "Nlb", vpc=self._vpc, internet_facing=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )
        listener = nlb.add_listener("Listener", port=80, protocol=elbv2.Protocol.TCP)
        listener.add_targets(
            "Targets", port=container_port, targets=[service],
            health_check=elbv2.HealthCheck(protocol=elbv2.Protocol.TCP, interval=Duration.seconds(30)),
        )
        sg.add_ingress_rule(ec2.Peer.ipv4(self._vpc.vpc_cidr_block), ec2.Port.tcp(container_port), "NLB traffic")

        # VPC Link + API Gateway
        vpc_link = apigw.VpcLink(self, "VpcLink", targets=[nlb])

        api = apigw.RestApi(
            self, "Api", rest_api_name="RascalApi",
            description="Backend API with IAM auth and deny-by-default resource policy",
            policy=self._build_resource_policy(allowed_account_ids, principal_org_id),
            deploy_options=apigw.StageOptions(
                stage_name="v1", metrics_enabled=True,
                throttling_rate_limit=100, throttling_burst_limit=50,
            ),
            default_method_options=apigw.MethodOptions(authorization_type=apigw.AuthorizationType.IAM),
        )

        def nlb_integration(method: str, path: str) -> apigw.Integration:
            return apigw.Integration(
                type=apigw.IntegrationType.HTTP_PROXY,
                integration_http_method=method,
                uri=f"http://{nlb.load_balancer_dns_name}{path}",
                options=apigw.IntegrationOptions(connection_type=apigw.ConnectionType.VPC_LINK, vpc_link=vpc_link),
            )

        # Routes
        api.root.add_resource("jobs").add_method("POST", nlb_integration("POST", "/jobs"))

        job_id = api.root.get_resource("jobs").add_resource("{job_id}")
        job_id.add_method(
            "GET",
            apigw.Integration(
                type=apigw.IntegrationType.HTTP_PROXY, integration_http_method="GET",
                uri=f"http://{nlb.load_balancer_dns_name}/jobs/{{job_id}}",
                options=apigw.IntegrationOptions(
                    connection_type=apigw.ConnectionType.VPC_LINK, vpc_link=vpc_link,
                    request_parameters={"integration.request.path.job_id": "method.request.path.job_id"},
                ),
            ),
            request_parameters={"method.request.path.job_id": True},
        )

        api.root.add_resource("health").add_method(
            "GET", nlb_integration("GET", "/health"),
            method_options=apigw.MethodOptions(authorization_type=apigw.AuthorizationType.NONE),
        )

        self.api_endpoint = api.url

        # Alarms
        cw.Alarm(self, "Api5xxAlarm", metric=api.metric_server_error(period=Duration.minutes(5)), threshold=5, evaluation_periods=2)
        cw.Alarm(self, "EcsCpuAlarm", metric=service.metric_cpu_utilization(period=Duration.minutes(5)), threshold=90, evaluation_periods=3)

        # Outputs
        CfnOutput(self, "ApiEndpoint", value=api.url)
        CfnOutput(self, "TaskRoleArn", value=task_def.task_role.role_arn)
        CfnOutput(self, "DataTableName", value=self.data_table.table_name)
        CfnOutput(self, "JobsTableName", value=self.jobs_table.table_name)

    @staticmethod
    def _build_resource_policy(
        allowed_account_ids: list[str], principal_org_id: str | None,
    ) -> iam.PolicyDocument:
        statements: list[iam.PolicyStatement] = []
        has_accounts = len(allowed_account_ids) > 0
        has_org = principal_org_id is not None

        if has_accounts or has_org:
            conditions: dict[str, object] = {}
            if has_accounts:
                conditions["aws:PrincipalAccount"] = allowed_account_ids
            if has_org:
                conditions["aws:PrincipalOrgID"] = principal_org_id

            statements.append(iam.PolicyStatement(
                effect=iam.Effect.ALLOW, principals=[iam.AnyPrincipal()],
                actions=["execute-api:Invoke"], resources=["execute-api:/*"],
                conditions={"StringEquals": conditions},
            ))
            statements.append(iam.PolicyStatement(
                effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
                actions=["execute-api:Invoke"], resources=["execute-api:/*"],
                conditions={"StringNotEquals": conditions},
            ))
        else:
            statements.append(iam.PolicyStatement(
                effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
                actions=["execute-api:Invoke"], resources=["execute-api:/*"],
            ))

        return iam.PolicyDocument(statements=statements)
