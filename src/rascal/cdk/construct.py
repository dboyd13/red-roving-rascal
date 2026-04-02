"""Reusable CDK construct: AgentCore Gateway -> API Gateway (internal) ->
VPC Link -> NLB -> ECS Fargate -> DynamoDB.

AgentCore Gateway is the only external entry point. Supports dual gateways:
an IAM gateway for SigV4 consumers and/or a JWT gateway for OIDC
consumers (Auth0, Okta, Federate, etc.).

The API Gateway REST API is an internal implementation detail locked to the
AgentCore service principal. Uses only aws-cdk-lib + constructs.
"""
from __future__ import annotations

import json

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

from rascal.cdk.gateway_config import IamGatewayConfig, JwtGatewayConfig, CustomClaimRule


class RascalBackendConstruct(Construct):
    """Backend: AgentCore Gateway(s) -> API GW -> NLB -> ECS Fargate -> DynamoDB.

    Supports two gateway types (both optional, at least one required):

    - ``iam_gateway``: SigV4 auth for AWS consumers (pipelines, SDKs)
    - ``jwt_gateway``: JWT/OIDC auth for external consumers (MCP clients,
      Kiro, Claude Code) via any OIDC provider (Auth0, Okta, Federate)

    Backward compatibility: legacy ``jwt_issuer_url`` / ``jwt_audience`` params
    still work (single JWT gateway, no IAM gateway).
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
        # Gateway configs (new API)
        iam_gateway: IamGatewayConfig | None = None,
        jwt_gateway: JwtGatewayConfig | None = None,
        # Interceptors
        request_interceptor_arn: str | None = None,
        response_interceptor_arn: str | None = None,
        # Legacy params (backward compat — prefer iam_gateway / jwt_gateway)
        allowed_account_ids: list[str] | None = None,
        allowed_org_id: str | None = None,
        gateway_resource_policy: dict | None = None,
        jwt_issuer_url: str | None = None,
        jwt_audience: list[str] | None = None,
    ) -> None:
        super().__init__(scope, id)

        # --- Backward compat: convert legacy params to new config objects ---
        if jwt_issuer_url is not None and iam_gateway is None and jwt_gateway is None:
            # Legacy JWT mode: single CUSTOM_JWT gateway
            jwt_gateway = JwtGatewayConfig(
                discovery_url=jwt_issuer_url,
                allowed_audiences=jwt_audience or [],
            )
        elif iam_gateway is None and jwt_gateway is None:
            # Legacy IAM mode: single AWS_IAM gateway
            iam_gateway = IamGatewayConfig(
                allowed_account_ids=allowed_account_ids,
                allowed_org_id=allowed_org_id,
                resource_policy=gateway_resource_policy,
            )

        if iam_gateway is None and jwt_gateway is None:
            raise ValueError("At least one of iam_gateway or jwt_gateway must be provided")

        # --- Infrastructure (shared by all gateways) ---

        self._vpc = vpc or ec2.Vpc(
            self, "Vpc", max_azs=2, nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
            ],
        )

        self.evaluations_table = dynamodb.Table(
            self, "EvaluationsTable",
            partition_key=dynamodb.Attribute(name="evaluationId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal_policy, time_to_live_attribute="ttl",
        )
        self.suites_table = dynamodb.Table(
            self, "SuitesTable",
            partition_key=dynamodb.Attribute(name="suiteId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST, removal_policy=removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True),
        )

        cluster = ecs.Cluster(self, "Cluster", vpc=self._vpc)
        task_def = ecs.FargateTaskDefinition(self, "TaskDef", cpu=task_cpu, memory_limit_mib=task_memory_mib)
        self.task_role = task_def.task_role
        log_group = logs.LogGroup(self, "Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=removal_policy)

        task_def.add_container("App", image=container_image,
            port_mappings=[ecs.PortMapping(container_port=container_port)],
            environment={"EVALUATIONS_TABLE": self.evaluations_table.table_name,
                         "SUITES_TABLE": self.suites_table.table_name, "PORT": str(container_port)},
            logging=ecs.LogDrivers.aws_logs(stream_prefix="rascal", log_group=log_group))

        self.evaluations_table.grant_read_write_data(task_def.task_role)
        self.suites_table.grant_read_write_data(task_def.task_role)
        task_def.task_role.add_to_policy(iam.PolicyStatement(
            actions=["comprehend:DetectEntities", "comprehend:DetectPiiEntities"], resources=["*"]))

        sg = ec2.SecurityGroup(self, "Sg", vpc=self._vpc, description="Backend ECS service", allow_all_outbound=True)
        service = ecs.FargateService(self, "Service", cluster=cluster, task_definition=task_def,
            desired_count=desired_count, assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS), security_groups=[sg])

        nlb = elbv2.NetworkLoadBalancer(self, "Nlb", vpc=self._vpc, internet_facing=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS))
        listener = nlb.add_listener("Listener", port=80, protocol=elbv2.Protocol.TCP)
        listener.add_targets("Targets", port=container_port, targets=[service],
            health_check=elbv2.HealthCheck(protocol=elbv2.Protocol.TCP, interval=Duration.seconds(30)))
        sg.add_ingress_rule(ec2.Peer.ipv4(self._vpc.vpc_cidr_block), ec2.Port.tcp(container_port), "NLB traffic")

        # --- API Gateway (internal) ---
        vpc_link = apigw.VpcLink(self, "VpcLink", targets=[nlb])
        api = apigw.RestApi(self, "Api", rest_api_name="RascalApi",
            description="Internal API — access restricted to AgentCore Gateway",
            policy=iam.PolicyDocument(statements=[iam.PolicyStatement(
                effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
                actions=["execute-api:Invoke"], resources=["execute-api:/*"])]),
            deploy_options=apigw.StageOptions(stage_name="v1", metrics_enabled=True,
                throttling_rate_limit=100, throttling_burst_limit=50),
            default_method_options=apigw.MethodOptions(authorization_type=apigw.AuthorizationType.IAM))

        def nlb_int(method: str, path: str) -> apigw.Integration:
            return apigw.Integration(type=apigw.IntegrationType.HTTP_PROXY,
                integration_http_method=method, uri=f"http://{nlb.load_balancer_dns_name}{path}",
                options=apigw.IntegrationOptions(connection_type=apigw.ConnectionType.VPC_LINK,
                    vpc_link=vpc_link, passthrough_behavior=apigw.PassthroughBehavior.WHEN_NO_MATCH))

        mr = [apigw.MethodResponse(status_code="200")]
        eval_model = api.add_model("EvaluateRequestModel", content_type="application/json",
            model_name="EvaluateRequest", schema=apigw.JsonSchema(type=apigw.JsonSchemaType.OBJECT,
                properties={"pairs": apigw.JsonSchema(type=apigw.JsonSchemaType.ARRAY,
                    description="Input/output pairs to evaluate", items=apigw.JsonSchema(
                        type=apigw.JsonSchemaType.OBJECT, properties={
                            "input_text": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "output_text": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING)},
                        required=["input_text", "output_text"])),
                    "config": apigw.JsonSchema(type=apigw.JsonSchemaType.OBJECT, description="Scoring configuration")},
                required=["pairs", "config"]))

        api.root.add_resource("health").add_method("GET", nlb_int("GET", "/health"),
            authorization_type=apigw.AuthorizationType.NONE, method_responses=mr, operation_name="GetHealth")
        ev = api.root.add_resource("evaluate")
        ev.add_method("POST", nlb_int("POST", "/evaluate"), method_responses=mr,
            operation_name="Evaluate", request_models={"application/json": eval_model})
        ev.add_resource("{evaluation_id}").add_method("GET", apigw.Integration(
            type=apigw.IntegrationType.HTTP_PROXY, integration_http_method="GET",
            uri=f"http://{nlb.load_balancer_dns_name}/evaluate/{{evaluation_id}}",
            options=apigw.IntegrationOptions(connection_type=apigw.ConnectionType.VPC_LINK, vpc_link=vpc_link,
                request_parameters={"integration.request.path.evaluation_id": "method.request.path.evaluation_id"})),
            request_parameters={"method.request.path.evaluation_id": True}, method_responses=mr, operation_name="GetEvaluation")
        su = api.root.add_resource("suites")
        su.add_method("GET", nlb_int("GET", "/suites"), method_responses=mr, operation_name="ListSuites")
        su.add_resource("{suite_id}").add_method("GET", apigw.Integration(
            type=apigw.IntegrationType.HTTP_PROXY, integration_http_method="GET",
            uri=f"http://{nlb.load_balancer_dns_name}/suites/{{suite_id}}",
            options=apigw.IntegrationOptions(connection_type=apigw.ConnectionType.VPC_LINK, vpc_link=vpc_link,
                request_parameters={"integration.request.path.suite_id": "method.request.path.suite_id"})),
            request_parameters={"method.request.path.suite_id": True}, method_responses=mr, operation_name="GetSuite")

        self.api_endpoint = api.url
        cw.Alarm(self, "Api5xxAlarm", metric=api.metric_server_error(period=Duration.minutes(5)), threshold=5, evaluation_periods=2)
        cw.Alarm(self, "EcsCpuAlarm", metric=service.metric_cpu_utilization(period=Duration.minutes(5)), threshold=90, evaluation_periods=3)
        CfnOutput(self, "TaskRoleArn", value=task_def.task_role.role_arn)
        CfnOutput(self, "EvaluationsTableName", value=self.evaluations_table.table_name)
        CfnOutput(self, "SuitesTableName", value=self.suites_table.table_name)

        # --- Gateways ---
        # Lock API Gateway to AgentCore service principal (both gateways use this)
        api.node.default_child.add_property_override(  # type: ignore[union-attr]
            "Policy", {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "execute-api:Invoke", "Resource": ["execute-api:/*"]}]})

        interceptor_arns = [a for a in [request_interceptor_arn, response_interceptor_arn] if a]

        if iam_gateway is not None:
            self._create_gateway(api=api, prefix="", suffix="gw", authorizer_type="AWS_IAM",
                iam_config=iam_gateway, interceptor_arns=interceptor_arns,
                request_interceptor_arn=request_interceptor_arn, response_interceptor_arn=response_interceptor_arn)

        if jwt_gateway is not None:
            p = "OAuth" if iam_gateway is not None else ""
            s = "oauth" if iam_gateway is not None else "gw"
            self._create_gateway(api=api, prefix=p, suffix=s, authorizer_type="CUSTOM_JWT",
                jwt_config=jwt_gateway, interceptor_arns=interceptor_arns,
                request_interceptor_arn=request_interceptor_arn, response_interceptor_arn=response_interceptor_arn)

    def _create_gateway(
        self, *, api: apigw.RestApi, prefix: str, suffix: str,
        authorizer_type: str,
        iam_config: IamGatewayConfig | None = None,
        jwt_config: JwtGatewayConfig | None = None,
        interceptor_arns: list[str] | None = None,
        request_interceptor_arn: str | None = None,
        response_interceptor_arn: str | None = None,
    ) -> None:
        """Create a single AgentCore Gateway + Target."""
        stack = Stack.of(self)
        from aws_cdk import aws_lambda as lambda_, CustomResource
        p = prefix

        # IAM role for this gateway
        role = iam.Role(self, f"{p}GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description=f"Role for AgentCore {suffix.upper()} Gateway")
        role.add_to_policy(iam.PolicyStatement(actions=["execute-api:Invoke"],
            resources=[Fn.join("", ["arn:aws:execute-api:", stack.region, ":", stack.account, ":", api.rest_api_id, "/v1/*"])]))
        role.add_to_policy(iam.PolicyStatement(actions=["apigateway:GET"],
            resources=[Fn.join("", ["arn:aws:apigateway:", stack.region, "::/restapis/", api.rest_api_id, "/*"])]))
        if interceptor_arns:
            role.add_to_policy(iam.PolicyStatement(actions=["lambda:InvokeFunction"], resources=interceptor_arns))

        if not prefix:
            self.gateway_role = role

        # Authorizer config
        auth_cfg = None
        if authorizer_type == "CUSTOM_JWT" and jwt_config:
            jwt_props: dict = {
                "discovery_url": jwt_config.discovery_url,
                "allowed_audience": jwt_config.allowed_audiences,
            }
            if jwt_config.allowed_clients:
                jwt_props["allowed_clients"] = jwt_config.allowed_clients
            if jwt_config.allowed_scopes:
                jwt_props["allowed_scopes"] = jwt_config.allowed_scopes
            if jwt_config.required_claims:
                jwt_props["required_claims"] = [
                    agentcore.CfnGateway.CustomClaimValidationTypeProperty(
                        inbound_token_claim_name=c.claim_name,
                        inbound_token_claim_value_type=c.value_type,
                        authorizing_claim_match_value=agentcore.CfnGateway.AuthorizingClaimMatchValueProperty(
                            claim_match_value=c.match_value,
                            claim_match_operator=c.match_operator,
                        ),
                    ) for c in jwt_config.required_claims
                ]
            auth_cfg = agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(**jwt_props))

        # Interceptors
        ic: list = []
        if request_interceptor_arn:
            ic.append(agentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                interception_points=["REQUEST"], interceptor=agentcore.CfnGateway.InterceptorConfigurationProperty(
                    lambda_=agentcore.CfnGateway.LambdaInterceptorConfigurationProperty(arn=request_interceptor_arn)),
                input_configuration=agentcore.CfnGateway.InterceptorInputConfigurationProperty(pass_request_headers=True)))
        if response_interceptor_arn:
            ic.append(agentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                interception_points=["RESPONSE"], interceptor=agentcore.CfnGateway.InterceptorConfigurationProperty(
                    lambda_=agentcore.CfnGateway.LambdaInterceptorConfigurationProperty(arn=response_interceptor_arn))))

        gw = agentcore.CfnGateway(self, f"{p}AgentCoreGateway",
            name=f"{stack.stack_name[:34]}-rascal-{suffix}", authorizer_type=authorizer_type,
            protocol_type="MCP", role_arn=role.role_arn, authorizer_configuration=auth_cfg,
            description=f"Rascal MCP Gateway ({authorizer_type})", interceptor_configurations=ic or None)

        # Target
        tgt = agentcore.CfnGatewayTarget(self, f"{p}AgentCoreTarget", name="rascal-api",
            gateway_identifier=gw.attr_gateway_identifier,
            credential_provider_configurations=[agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                credential_provider_type="GATEWAY_IAM_ROLE")],
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    api_gateway=agentcore.CfnGatewayTarget.ApiGatewayTargetConfigurationProperty(
                        rest_api_id=api.rest_api_id, stage="v1",
                        api_gateway_tool_configuration=agentcore.CfnGatewayTarget.ApiGatewayToolConfigurationProperty(
                            tool_filters=[
                                agentcore.CfnGatewayTarget.ApiGatewayToolFilterProperty(filter_path="/evaluate", methods=["POST"]),
                                agentcore.CfnGatewayTarget.ApiGatewayToolFilterProperty(filter_path="/evaluate/*", methods=["GET"]),
                                agentcore.CfnGatewayTarget.ApiGatewayToolFilterProperty(filter_path="/suites", methods=["GET"]),
                                agentcore.CfnGatewayTarget.ApiGatewayToolFilterProperty(filter_path="/suites/*", methods=["GET"])])))))
        tgt.node.add_dependency(api.deployment_stage)

        # Store attributes
        lbl = "OAuth" if prefix else ""
        if prefix:
            self.jwt_gateway_id = gw.attr_gateway_identifier
            self.jwt_endpoint = gw.attr_gateway_url
            self.jwt_gateway_arn = gw.attr_gateway_arn
        else:
            self.agentcore_gateway_id = gw.attr_gateway_identifier
            self.agentcore_endpoint = gw.attr_gateway_url
            self.agentcore_gateway_arn = gw.attr_gateway_arn

        # Resource policy (IAM gateway only)
        if iam_config is not None:
            policy_doc = self._build_resource_policy(gateway_arn=gw.attr_gateway_arn,
                allowed_account_ids=iam_config.allowed_account_ids,
                allowed_org_id=iam_config.allowed_org_id,
                resource_policy=iam_config.resource_policy)
            if policy_doc is not None:
                self._apply_resource_policy(prefix=p, gateway=gw, policy_doc=policy_doc)

        CfnOutput(self, f"AgentCore{lbl}GatewayId", value=gw.attr_gateway_identifier)
        CfnOutput(self, f"AgentCore{lbl}Endpoint", value=gw.attr_gateway_url)
        CfnOutput(self, f"AgentCore{lbl}GatewayArn", value=gw.attr_gateway_arn)

    def _apply_resource_policy(self, *, prefix: str, gateway, policy_doc: dict) -> None:
        from aws_cdk import aws_lambda as lambda_, CustomResource
        p = prefix
        resource_policy_fn = lambda_.Function(self, f"{p}GatewayResourcePolicyFn",
            runtime=lambda_.Runtime.PYTHON_3_12, handler="index.handler", timeout=Duration.seconds(30),
            log_group=logs.LogGroup(self, f"{p}ResourcePolicyFnLogs", retention=logs.RetentionDays.ONE_WEEK),
            code=lambda_.Code.from_inline(
                'import json, os, urllib.request, urllib.parse\n'
                'import boto3\nfrom botocore.auth import SigV4Auth\nfrom botocore.awsrequest import AWSRequest\n'
                'def cfn_send(event, context, status, data={}):\n'
                '    body = json.dumps({"Status": status, "Reason": str(data.get("Error", "")),\n'
                '        "PhysicalResourceId": event.get("PhysicalResourceId", context.log_stream_name),\n'
                '        "StackId": event["StackId"], "RequestId": event["RequestId"],\n'
                '        "LogicalResourceId": event["LogicalResourceId"], "Data": data}).encode()\n'
                '    urllib.request.urlopen(urllib.request.Request(event["ResponseURL"], data=body,\n'
                '        headers={"Content-Type": ""}, method="PUT"))\n'
                'def _signed_request(method, url, body=None):\n'
                '    region = os.environ["AWS_REGION"]\n'
                '    req = AWSRequest(method=method, url=url, data=body, headers={"Content-Type": "application/json"})\n'
                '    creds = boto3.Session().get_credentials().get_frozen_credentials()\n'
                '    SigV4Auth(creds, "bedrock-agentcore", region).add_auth(req)\n'
                '    urllib.request.urlopen(urllib.request.Request(url, data=body, headers=dict(req.headers), method=method))\n'
                'def handler(event, context):\n'
                '    try:\n'
                '        region = os.environ["AWS_REGION"]\n'
                '        encoded = urllib.parse.quote(os.environ["GATEWAY_ARN"], safe="")\n'
                '        url = f"https://bedrock-agentcore-control.{region}.amazonaws.com/resourcepolicy/{encoded}"\n'
                '        if event["RequestType"] in ("Create", "Update"):\n'
                '            _signed_request("PUT", url, json.dumps({"policy": os.environ["POLICY"]}).encode())\n'
                '        elif event["RequestType"] == "Delete":\n'
                '            try: _signed_request("DELETE", url)\n'
                '            except: pass\n'
                '        cfn_send(event, context, "SUCCESS")\n'
                '    except Exception as e: cfn_send(event, context, "FAILED", {"Error": str(e)})\n'),
            environment={"GATEWAY_ARN": gateway.attr_gateway_arn, "POLICY": json.dumps(policy_doc)})
        resource_policy_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:PutResourcePolicy", "bedrock-agentcore:DeleteResourcePolicy"],
            resources=[gateway.attr_gateway_arn]))
        cr = CustomResource(self, f"{p}GatewayResourcePolicy",
            service_token=resource_policy_fn.function_arn, properties={"PolicyHash": json.dumps(policy_doc)})
        cr.node.add_dependency(gateway)

    @staticmethod
    def _build_resource_policy(*, gateway_arn: str, allowed_account_ids: list[str] | None = None,
                               allowed_org_id: str | None = None, resource_policy: dict | None = None) -> dict | None:
        if resource_policy is not None:
            policy = json.loads(json.dumps(resource_policy))
            for stmt in policy.get("Statement", []):
                if stmt.get("Resource") == "GATEWAY_ARN":
                    stmt["Resource"] = gateway_arn
            return policy
        if allowed_account_ids:
            principals = [f"arn:aws:iam::{a}:root" for a in allowed_account_ids]
            return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
                "Principal": {"AWS": principals if len(principals) > 1 else principals[0]},
                "Action": "bedrock-agentcore:InvokeGateway", "Resource": gateway_arn}]}
        if allowed_org_id:
            return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": "*",
                "Action": "bedrock-agentcore:InvokeGateway", "Resource": gateway_arn,
                "Condition": {"StringEquals": {"aws:PrincipalOrgID": allowed_org_id}}}]}
        return None
