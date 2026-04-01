"""Reusable CDK construct: AgentCore Gateway -> API Gateway (internal) ->
VPC Link -> NLB -> ECS Fargate -> DynamoDB.

AgentCore Gateway is the only external entry point. It provides managed
authn (SigV4 via AWS_IAM, JWT/OAuth2 via CUSTOM_JWT) and MCP protocol
support. The API Gateway REST API is an internal implementation detail
locked to the AgentCore service principal.

Uses only aws-cdk-lib + constructs. No vendor-specific dependencies.
"""
from __future__ import annotations

import json
import urllib.parse

from constructs import Construct
from aws_cdk import (
    Duration,
    Fn,
    RemovalPolicy,
    CfnOutput,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    aws_elasticloadbalancingv2 as elbv2,
    aws_apigateway as apigw,
    aws_logs as logs,
    aws_cloudwatch as cw,
    aws_bedrockagentcore as agentcore,
)


class RascalBackendConstruct(Construct):
    """Backend infrastructure: AgentCore Gateway -> API GW -> NLB -> ECS Fargate -> DynamoDB.

    AgentCore Gateway is always provisioned as the only external entry point.

    Allowlisting modes:
        - ``allowed_account_ids``: explicit IAM principal allowlisting via gateway resource policy
        - ``allowed_org_id``: org-wide access via ``aws:PrincipalOrgID`` condition
        - ``gateway_resource_policy``: escape hatch — caller provides the full policy dict

    Interceptor support:
        - ``request_interceptor_arn``: Lambda ARN for a REQUEST interceptor
        - ``response_interceptor_arn``: Lambda ARN for a RESPONSE interceptor
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        vpc: ec2.IVpc | None = None,
        container_image: ecs.ContainerImage,
        task_cpu: int = 1024,
        task_memory_mib: int = 2048,
        desired_count: int = 1,
        container_port: int = 8080,
        removal_policy: RemovalPolicy = RemovalPolicy.DESTROY,
        # Allowlisting (pick one)
        allowed_account_ids: list[str] | None = None,
        allowed_org_id: str | None = None,
        gateway_resource_policy: dict | None = None,
        # Interceptors (optional — caller creates the Lambdas)
        request_interceptor_arn: str | None = None,
        response_interceptor_arn: str | None = None,
        # JWT auth
        jwt_issuer_url: str | None = None,
        jwt_audience: list[str] | None = None,
    ) -> None:
        super().__init__(scope, id)

        # VPC
        self._vpc = vpc or ec2.Vpc(
            self, "Vpc", max_azs=2, nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
            ],
        )

        # DynamoDB
        self.evaluations_table = dynamodb.Table(
            self, "EvaluationsTable",
            partition_key=dynamodb.Attribute(name="evaluationId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal_policy,
            time_to_live_attribute="ttl",
        )
        self.suites_table = dynamodb.Table(
            self, "SuitesTable",
            partition_key=dynamodb.Attribute(name="suiteId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
        )

        # ECS
        cluster = ecs.Cluster(self, "Cluster", vpc=self._vpc)
        task_def = ecs.FargateTaskDefinition(self, "TaskDef", cpu=task_cpu, memory_limit_mib=task_memory_mib)
        self.task_role = task_def.task_role

        log_group = logs.LogGroup(self, "Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=removal_policy)

        task_def.add_container(
            "App",
            image=container_image,
            port_mappings=[ecs.PortMapping(container_port=container_port)],
            environment={
                "EVALUATIONS_TABLE": self.evaluations_table.table_name,
                "SUITES_TABLE": self.suites_table.table_name,
                "PORT": str(container_port),
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix="rascal", log_group=log_group),
        )

        self.evaluations_table.grant_read_write_data(task_def.task_role)
        self.suites_table.grant_read_write_data(task_def.task_role)

        # Comprehend access for the default analyzer
        task_def.task_role.add_to_policy(iam.PolicyStatement(
            actions=["comprehend:DetectEntities", "comprehend:DetectPiiEntities"],
            resources=["*"],
        ))

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

        # VPC Link + API Gateway (internal — locked to AgentCore service principal)
        vpc_link = apigw.VpcLink(self, "VpcLink", targets=[nlb])

        api_policy = iam.PolicyDocument(
            statements=[iam.PolicyStatement(
                effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
                actions=["execute-api:Invoke"], resources=["execute-api:/*"],
            )],
        )

        api = apigw.RestApi(
            self, "Api", rest_api_name="RascalApi",
            description="Internal API — access restricted to AgentCore Gateway",
            policy=api_policy,
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
                options=apigw.IntegrationOptions(
                    connection_type=apigw.ConnectionType.VPC_LINK,
                    vpc_link=vpc_link,
                    passthrough_behavior=apigw.PassthroughBehavior.WHEN_NO_MATCH,
                ),
            )

        default_responses = [apigw.MethodResponse(status_code="200")]

        evaluate_request_model = api.add_model(
            "EvaluateRequestModel",
            content_type="application/json",
            model_name="EvaluateRequest",
            schema=apigw.JsonSchema(
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "pairs": apigw.JsonSchema(
                        type=apigw.JsonSchemaType.ARRAY,
                        description="Input/output pairs to evaluate",
                        items=apigw.JsonSchema(
                            type=apigw.JsonSchemaType.OBJECT,
                            properties={
                                "input_text": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                                "output_text": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            },
                            required=["input_text", "output_text"],
                        ),
                    ),
                    "config": apigw.JsonSchema(type=apigw.JsonSchemaType.OBJECT, description="Scoring configuration"),
                },
                required=["pairs", "config"],
            ),
        )

        # Routes
        api.root.add_resource("health").add_method(
            "GET", nlb_integration("GET", "/health"),
            authorization_type=apigw.AuthorizationType.NONE,
            method_responses=default_responses, operation_name="GetHealth",
        )

        evaluate_resource = api.root.add_resource("evaluate")
        evaluate_resource.add_method(
            "POST", nlb_integration("POST", "/evaluate"),
            method_responses=default_responses, operation_name="Evaluate",
            request_models={"application/json": evaluate_request_model},
        )
        evaluate_resource.add_resource("{evaluation_id}").add_method(
            "GET",
            apigw.Integration(
                type=apigw.IntegrationType.HTTP_PROXY, integration_http_method="GET",
                uri=f"http://{nlb.load_balancer_dns_name}/evaluate/{{evaluation_id}}",
                options=apigw.IntegrationOptions(
                    connection_type=apigw.ConnectionType.VPC_LINK, vpc_link=vpc_link,
                    request_parameters={"integration.request.path.evaluation_id": "method.request.path.evaluation_id"},
                ),
            ),
            request_parameters={"method.request.path.evaluation_id": True},
            method_responses=default_responses, operation_name="GetEvaluation",
        )

        suites_resource = api.root.add_resource("suites")
        suites_resource.add_method(
            "GET", nlb_integration("GET", "/suites"),
            method_responses=default_responses, operation_name="ListSuites",
        )
        suites_resource.add_resource("{suite_id}").add_method(
            "GET",
            apigw.Integration(
                type=apigw.IntegrationType.HTTP_PROXY, integration_http_method="GET",
                uri=f"http://{nlb.load_balancer_dns_name}/suites/{{suite_id}}",
                options=apigw.IntegrationOptions(
                    connection_type=apigw.ConnectionType.VPC_LINK, vpc_link=vpc_link,
                    request_parameters={"integration.request.path.suite_id": "method.request.path.suite_id"},
                ),
            ),
            request_parameters={"method.request.path.suite_id": True},
            method_responses=default_responses, operation_name="GetSuite",
        )

        self.api_endpoint = api.url

        # Alarms
        cw.Alarm(self, "Api5xxAlarm", metric=api.metric_server_error(period=Duration.minutes(5)), threshold=5, evaluation_periods=2)
        cw.Alarm(self, "EcsCpuAlarm", metric=service.metric_cpu_utilization(period=Duration.minutes(5)), threshold=90, evaluation_periods=3)

        CfnOutput(self, "TaskRoleArn", value=task_def.task_role.role_arn)
        CfnOutput(self, "EvaluationsTableName", value=self.evaluations_table.table_name)
        CfnOutput(self, "SuitesTableName", value=self.suites_table.table_name)

        # --- AgentCore Gateway ---
        self._provision_agentcore(
            api=api,
            jwt_issuer_url=jwt_issuer_url,
            jwt_audience=jwt_audience,
            allowed_account_ids=allowed_account_ids,
            allowed_org_id=allowed_org_id,
            gateway_resource_policy=gateway_resource_policy,
            request_interceptor_arn=request_interceptor_arn,
            response_interceptor_arn=response_interceptor_arn,
        )

    def _provision_agentcore(
        self,
        *,
        api: apigw.RestApi,
        jwt_issuer_url: str | None,
        jwt_audience: list[str] | None,
        allowed_account_ids: list[str] | None,
        allowed_org_id: str | None,
        gateway_resource_policy: dict | None,
        request_interceptor_arn: str | None,
        response_interceptor_arn: str | None,
    ) -> None:
        """Provision AgentCore Gateway with pluggable auth configuration."""
        stack = Stack.of(self)
        from aws_cdk import aws_lambda as lambda_, CustomResource

        # --- Gateway IAM role ---
        self.gateway_role = iam.Role(
            self, "GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Role for AgentCore Gateway",
        )
        self.gateway_role.add_to_policy(iam.PolicyStatement(
            actions=["execute-api:Invoke"],
            resources=[Fn.join("", [
                "arn:aws:execute-api:", stack.region, ":", stack.account, ":",
                api.rest_api_id, "/v1/*",
            ])],
        ))
        self.gateway_role.add_to_policy(iam.PolicyStatement(
            actions=["apigateway:GET"],
            resources=[Fn.join("", [
                "arn:aws:apigateway:", stack.region, "::/restapis/",
                api.rest_api_id, "/*",
            ])],
        ))

        # Grant invoke on interceptor Lambdas
        interceptor_arns = [a for a in [request_interceptor_arn, response_interceptor_arn] if a]
        if interceptor_arns:
            self.gateway_role.add_to_policy(iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=interceptor_arns,
            ))

        # --- Authorizer config ---
        authorizer_type = "AWS_IAM"
        authorizer_config = None
        if jwt_issuer_url is not None:
            authorizer_type = "CUSTOM_JWT"
            authorizer_config = agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=jwt_issuer_url,
                    allowed_audience=jwt_audience or [],
                ),
            )

        # --- Interceptor configurations ---
        interceptor_configs: list = []
        if request_interceptor_arn:
            interceptor_configs.append(
                agentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                    interception_points=["REQUEST"],
                    interceptor=agentcore.CfnGateway.InterceptorConfigurationProperty(
                        lambda_=agentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                            arn=request_interceptor_arn,
                        ),
                    ),
                    input_configuration=agentcore.CfnGateway.InterceptorInputConfigurationProperty(
                        pass_request_headers=True,
                    ),
                ),
            )
        if response_interceptor_arn:
            interceptor_configs.append(
                agentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                    interception_points=["RESPONSE"],
                    interceptor=agentcore.CfnGateway.InterceptorConfigurationProperty(
                        lambda_=agentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                            arn=response_interceptor_arn,
                        ),
                    ),
                ),
            )

        # --- AgentCore Gateway ---
        gateway = agentcore.CfnGateway(
            self, "AgentCoreGateway",
            name=f"{stack.stack_name}-rascal-gw",
            authorizer_type=authorizer_type,
            protocol_type="MCP",
            role_arn=self.gateway_role.role_arn,
            authorizer_configuration=authorizer_config,
            description="Rascal MCP Gateway",
            interceptor_configurations=interceptor_configs or None,
        )

        self.agentcore_gateway_id = gateway.attr_gateway_identifier
        self.agentcore_endpoint = gateway.attr_gateway_url
        self.agentcore_gateway_arn = gateway.attr_gateway_arn

        # --- AgentCore Target ---
        target = agentcore.CfnGatewayTarget(
            self, "AgentCoreTarget",
            name="rascal-api",
            gateway_identifier=gateway.attr_gateway_identifier,
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                ),
            ],
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    api_gateway=agentcore.CfnGatewayTarget.ApiGatewayTargetConfigurationProperty(
                        rest_api_id=api.rest_api_id,
                        stage="v1",
                        api_gateway_tool_configuration=agentcore.CfnGatewayTarget.ApiGatewayToolConfigurationProperty(
                            tool_filters=[
                                agentcore.CfnGatewayTarget.ApiGatewayToolFilterProperty(
                                    filter_path="/evaluate", methods=["POST"],
                                ),
                                agentcore.CfnGatewayTarget.ApiGatewayToolFilterProperty(
                                    filter_path="/evaluate/*", methods=["GET"],
                                ),
                                agentcore.CfnGatewayTarget.ApiGatewayToolFilterProperty(
                                    filter_path="/suites", methods=["GET"],
                                ),
                                agentcore.CfnGatewayTarget.ApiGatewayToolFilterProperty(
                                    filter_path="/suites/*", methods=["GET"],
                                ),
                            ],
                        ),
                    ),
                ),
            ),
        )
        target.node.add_dependency(api.deployment_stage)

        # Lock API Gateway to AgentCore service principal
        api.node.default_child.add_property_override(  # type: ignore[union-attr]
            "Policy",
            {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "execute-api:Invoke",
                    "Resource": ["execute-api:/*"],
                    "Condition": {"ArnEquals": {"aws:SourceArn": self.gateway_role.role_arn}},
                }],
            },
        )

        # --- Gateway resource policy ---
        # Determines who can call bedrock-agentcore:InvokeGateway on this gateway.
        # Built from convenience props or the escape-hatch dict.
        policy_doc = self._build_resource_policy(
            gateway_arn=gateway.attr_gateway_arn,
            allowed_account_ids=allowed_account_ids,
            allowed_org_id=allowed_org_id,
            gateway_resource_policy=gateway_resource_policy,
        )

        if policy_doc is not None:
            resource_policy_fn = lambda_.Function(
                self, "GatewayResourcePolicyFn",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="index.handler",
                timeout=Duration.seconds(30),
                log_group=logs.LogGroup(
                    self, "ResourcePolicyFnLogs",
                    retention=logs.RetentionDays.ONE_WEEK,
                ),
                code=lambda_.Code.from_inline(
                    'import json, os, urllib.request, urllib.parse\n'
                    'import boto3\n'
                    'from botocore.auth import SigV4Auth\n'
                    'from botocore.awsrequest import AWSRequest\n'
                    'def cfn_send(event, context, status, data={}):\n'
                    '    body = json.dumps({"Status": status, "Reason": str(data.get("Error", "")),\n'
                    '        "PhysicalResourceId": event.get("PhysicalResourceId", context.log_stream_name),\n'
                    '        "StackId": event["StackId"], "RequestId": event["RequestId"],\n'
                    '        "LogicalResourceId": event["LogicalResourceId"], "Data": data}).encode()\n'
                    '    urllib.request.urlopen(urllib.request.Request(event["ResponseURL"], data=body,\n'
                    '        headers={"Content-Type": ""}, method="PUT"))\n'
                    'def _signed_request(method, url, body=None):\n'
                    '    region = os.environ["AWS_REGION"]\n'
                    '    headers = {"Content-Type": "application/json"}\n'
                    '    req = AWSRequest(method=method, url=url, data=body, headers=headers)\n'
                    '    creds = boto3.Session().get_credentials().get_frozen_credentials()\n'
                    '    SigV4Auth(creds, "bedrock-agentcore", region).add_auth(req)\n'
                    '    r = urllib.request.urlopen(urllib.request.Request(\n'
                    '        url, data=body, headers=dict(req.headers), method=method))\n'
                    '    return r.status\n'
                    'def handler(event, context):\n'
                    '    try:\n'
                    '        region = os.environ["AWS_REGION"]\n'
                    '        gw_arn = os.environ["GATEWAY_ARN"]\n'
                    '        encoded_arn = urllib.parse.quote(gw_arn, safe="")\n'
                    '        base = f"https://bedrock-agentcore-control.{region}.amazonaws.com"\n'
                    '        url = f"{base}/resourcepolicy/{encoded_arn}"\n'
                    '        if event["RequestType"] in ("Create", "Update"):\n'
                    '            _signed_request("PUT", url,\n'
                    '                json.dumps({"policy": os.environ["POLICY"]}).encode())\n'
                    '        elif event["RequestType"] == "Delete":\n'
                    '            try:\n'
                    '                _signed_request("DELETE", url)\n'
                    '            except Exception:\n'
                    '                pass\n'
                    '        cfn_send(event, context, "SUCCESS")\n'
                    '    except Exception as e:\n'
                    '        cfn_send(event, context, "FAILED", {"Error": str(e)})\n'
                ),
                environment={
                    "GATEWAY_ARN": gateway.attr_gateway_arn,
                    "POLICY": json.dumps(policy_doc),
                },
            )
            resource_policy_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock-agentcore:PutResourcePolicy", "bedrock-agentcore:DeleteResourcePolicy"],
                resources=[gateway.attr_gateway_arn],
            ))

            gw_resource_policy = CustomResource(
                self, "GatewayResourcePolicy",
                service_token=resource_policy_fn.function_arn,
                properties={
                    "PolicyHash": json.dumps(policy_doc),
                },
            )
            # Resource policy applied LAST — after gateway + interceptors are ready
            gw_resource_policy.node.add_dependency(gateway)

        # Outputs
        CfnOutput(self, "AgentCoreGatewayId", value=gateway.attr_gateway_identifier)
        CfnOutput(self, "AgentCoreEndpoint", value=gateway.attr_gateway_url)
        CfnOutput(self, "AgentCoreGatewayArn", value=gateway.attr_gateway_arn)

    @staticmethod
    def _build_resource_policy(
        *,
        gateway_arn: str,
        allowed_account_ids: list[str] | None,
        allowed_org_id: str | None,
        gateway_resource_policy: dict | None,
    ) -> dict | None:
        """Build the gateway resource policy from convenience props or escape hatch.

        Returns None if no allowlisting is configured (same-account only).
        """
        if gateway_resource_policy is not None:
            # Escape hatch — caller owns the policy. Fill in Resource placeholder.
            policy = json.loads(json.dumps(gateway_resource_policy))
            for stmt in policy.get("Statement", []):
                if stmt.get("Resource") == "GATEWAY_ARN":
                    stmt["Resource"] = gateway_arn
            return policy

        if allowed_account_ids:
            principals = [f"arn:aws:iam::{acct}:root" for acct in allowed_account_ids]
            return {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"AWS": principals if len(principals) > 1 else principals[0]},
                    "Action": "bedrock-agentcore:InvokeGateway",
                    "Resource": gateway_arn,
                }],
            }

        if allowed_org_id:
            return {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "bedrock-agentcore:InvokeGateway",
                    "Resource": gateway_arn,
                    "Condition": {"StringEquals": {"aws:PrincipalOrgID": allowed_org_id}},
                }],
            }

        return None
