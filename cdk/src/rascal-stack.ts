import { Construct } from 'constructs';
import { Stack, StackProps, RemovalPolicy } from 'aws-cdk-lib';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import { RascalBackendConstruct } from './rascal-construct';

export interface RascalStackProps extends StackProps {
  /** Account IDs allowed to call the API. Empty = deny all. */
  readonly allowedAccountIds?: string[];
  readonly principalOrgId?: string;
  readonly containerImage?: ecs.ContainerImage;
}

export class RascalStack extends Stack {
  public readonly backend: RascalBackendConstruct;

  constructor(scope: Construct, id: string, props: RascalStackProps = {}) {
    super(scope, id, props);

    this.backend = new RascalBackendConstruct(this, 'Backend', {
      allowedAccountIds: props.allowedAccountIds,
      principalOrgId: props.principalOrgId,
      containerImage: props.containerImage,
      removalPolicy: RemovalPolicy.DESTROY,
    });
  }
}
