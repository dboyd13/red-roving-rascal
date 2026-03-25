import { Construct } from 'constructs';
import {
  aws_ec2 as ec2,
  aws_ecs as ecs,
  aws_iam as iam,
  aws_dynamodb as dynamodb,
  aws_elasticloadbalancingv2 as elbv2,
  aws_apigateway as apigw,
  aws_logs as logs,
  aws_cloudwatch as cw,
  CfnOutput,
  Duration,
  RemovalPolicy,
} from 'aws-cdk-lib';

export interface RascalBackendProps {
  readonly vpc?: ec2.IVpc;

  /**
   * AWS account IDs allowed to call the API.
   * Deny-by-default: an empty list means no callers are permitted.
   */
  readonly allowedAccountIds?: string[];

  /** Optional org ID for org-level restriction. */
  readonly principalOrgId?: string;

  readonly containerImage?: ecs.ContainerImage;
  readonly taskCpu?: number;
  readonly taskMemoryMiB?: number;
  readonly desiredCount?: number;
  readonly containerPort?: number;
  readonly removalPolicy?: RemovalPolicy;
}

/**
 * Reusable CDK construct: API Gateway (IAM auth + deny-by-default) →
 * VPC Link → NLB → ECS Fargate → DynamoDB.
 *
 * Uses only aws-cdk-lib + constructs — no vendor-specific dependencies.
 */
export class RascalBackendConstruct extends Construct {
  public readonly apiEndpoint: string;
  public readonly taskRole: iam.IRole;
  public readonly dataTable: dynamodb.ITable;
  public readonly jobsTable: dynamodb.ITable;

  constructor(scope: Construct, id: string, props: RascalBackendProps = {}) {
    super(scope, id);

    const containerPort = props.containerPort ?? 8080;
    const removalPolicy = props.removalPolicy ?? RemovalPolicy.DESTROY;

    // ── VPC ──────────────────────────────────────────────────────────
    const vpc = props.vpc ?? new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        { name: 'Public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'Private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 },
      ],
    });

    // ── DynamoDB ─────────────────────────────────────────────────────
    this.dataTable = new dynamodb.Table(this, 'DataTable', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
      pointInTimeRecovery: true,
    });

    this.jobsTable = new dynamodb.Table(this, 'JobsTable', {
      partitionKey: { name: 'jobId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
      timeToLiveAttribute: 'ttl',
    });

    // ── ECS ──────────────────────────────────────────────────────────
    const cluster = new ecs.Cluster(this, 'Cluster', { vpc });

    const taskDef = new ecs.FargateTaskDefinition(this, 'TaskDef', {
      cpu: props.taskCpu ?? 1024,
      memoryLimitMiB: props.taskMemoryMiB ?? 2048,
    });
    this.taskRole = taskDef.taskRole;

    taskDef.executionRole?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
    );

    const logGroup = new logs.LogGroup(this, 'Logs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy,
    });

    taskDef.addContainer('App', {
      image: props.containerImage ?? ecs.ContainerImage.fromRegistry('amazon/amazon-ecs-sample'),
      portMappings: [{ containerPort }],
      environment: {
        DATA_TABLE: this.dataTable.tableName,
        JOBS_TABLE: this.jobsTable.tableName,
        PORT: containerPort.toString(),
      },
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'rascal', logGroup }),
    });

    this.dataTable.grantReadWriteData(taskDef.taskRole);
    this.jobsTable.grantReadWriteData(taskDef.taskRole);

    const sg = new ec2.SecurityGroup(this, 'Sg', {
      vpc,
      description: 'Backend ECS service',
      allowAllOutbound: true,
    });

    const service = new ecs.FargateService(this, 'Service', {
      cluster,
      taskDefinition: taskDef,
      desiredCount: props.desiredCount ?? 1,
      assignPublicIp: false,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroups: [sg],
    });

    // ── NLB ──────────────────────────────────────────────────────────
    const nlb = new elbv2.NetworkLoadBalancer(this, 'Nlb', {
      vpc,
      internetFacing: false,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    nlb.addListener('Listener', { port: 80, protocol: elbv2.Protocol.TCP })
      .addTargets('Targets', {
        port: containerPort,
        targets: [service],
        healthCheck: { protocol: elbv2.Protocol.TCP, interval: Duration.seconds(30) },
      });

    sg.addIngressRule(
      ec2.Peer.ipv4(vpc.vpcCidrBlock),
      ec2.Port.tcp(containerPort),
      'NLB health checks and traffic',
    );

    // ── VPC Link ────────────────────────────────────────────────────
    const vpcLink = new apigw.VpcLink(this, 'VpcLink', { targets: [nlb] });

    // ── API Gateway (IAM auth + deny-by-default resource policy) ────
    const api = new apigw.RestApi(this, 'Api', {
      restApiName: 'RascalApi',
      description: 'Backend API with IAM auth and deny-by-default resource policy',
      policy: this.buildResourcePolicy(props),
      deployOptions: {
        stageName: 'v1',
        metricsEnabled: true,
        throttlingRateLimit: 100,
        throttlingBurstLimit: 50,
      },
      defaultMethodOptions: {
        authorizationType: apigw.AuthorizationType.IAM,
      },
    });

    const nlbIntegration = (method: string, path: string) => new apigw.Integration({
      type: apigw.IntegrationType.HTTP_PROXY,
      integrationHttpMethod: method,
      uri: `http://${nlb.loadBalancerDnsName}${path}`,
      options: { connectionType: apigw.ConnectionType.VPC_LINK, vpcLink },
    });

    // POST /jobs
    api.root.addResource('jobs').addMethod('POST', nlbIntegration('POST', '/jobs'));

    // GET /jobs/{job_id}
    const jobId = api.root.getResource('jobs')!.addResource('{job_id}');
    jobId.addMethod('GET', new apigw.Integration({
      type: apigw.IntegrationType.HTTP_PROXY,
      integrationHttpMethod: 'GET',
      uri: `http://${nlb.loadBalancerDnsName}/jobs/{job_id}`,
      options: {
        connectionType: apigw.ConnectionType.VPC_LINK,
        vpcLink,
        requestParameters: { 'integration.request.path.job_id': 'method.request.path.job_id' },
      },
    }), { requestParameters: { 'method.request.path.job_id': true } });

    // GET /health (no auth — for NLB probes)
    api.root.addResource('health').addMethod('GET', nlbIntegration('GET', '/health'), {
      authorizationType: apigw.AuthorizationType.NONE,
    });

    this.apiEndpoint = api.url;

    // ── CloudWatch Alarms ───────────────────────────────────────────
    new cw.Alarm(this, 'Api5xxAlarm', {
      metric: api.metricServerError({ period: Duration.minutes(5) }),
      threshold: 5,
      evaluationPeriods: 2,
    });

    new cw.Alarm(this, 'EcsCpuAlarm', {
      metric: service.metricCpuUtilization({ period: Duration.minutes(5) }),
      threshold: 90,
      evaluationPeriods: 3,
    });

    // ── Outputs ─────────────────────────────────────────────────────
    new CfnOutput(this, 'ApiEndpoint', { value: api.url });
    new CfnOutput(this, 'TaskRoleArn', { value: taskDef.taskRole.roleArn });
    new CfnOutput(this, 'DataTableName', { value: this.dataTable.tableName });
    new CfnOutput(this, 'JobsTableName', { value: this.jobsTable.tableName });
  }

  /**
   * Deny-by-default API Gateway resource policy.
   * If no account IDs are provided, the policy denies all principals.
   */
  private buildResourcePolicy(props: RascalBackendProps): iam.PolicyDocument {
    const statements: iam.PolicyStatement[] = [];
    const allowedAccounts = props.allowedAccountIds ?? [];
    const hasAccounts = allowedAccounts.length > 0;
    const hasOrgId = !!props.principalOrgId;

    if (hasAccounts || hasOrgId) {
      const allow = new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        principals: [new iam.AnyPrincipal()],
        actions: ['execute-api:Invoke'],
        resources: ['execute-api:/*'],
      });

      const conditions: Record<string, string | string[]> = {};
      if (hasAccounts) conditions['aws:PrincipalAccount'] = allowedAccounts;
      if (hasOrgId) conditions['aws:PrincipalOrgID'] = props.principalOrgId!;
      allow.addCondition('StringEquals', conditions);
      statements.push(allow);

      const deny = new iam.PolicyStatement({
        effect: iam.Effect.DENY,
        principals: [new iam.AnyPrincipal()],
        actions: ['execute-api:Invoke'],
        resources: ['execute-api:/*'],
      });
      const denyConditions: Record<string, string | string[]> = {};
      if (hasAccounts) denyConditions['aws:PrincipalAccount'] = allowedAccounts;
      if (hasOrgId) denyConditions['aws:PrincipalOrgID'] = props.principalOrgId!;
      deny.addCondition('StringNotEquals', denyConditions);
      statements.push(deny);
    } else {
      // No accounts configured — deny everyone
      statements.push(new iam.PolicyStatement({
        effect: iam.Effect.DENY,
        principals: [new iam.AnyPrincipal()],
        actions: ['execute-api:Invoke'],
        resources: ['execute-api:/*'],
      }));
    }

    return new iam.PolicyDocument({ statements });
  }
}
