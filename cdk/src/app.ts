#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { RascalStack } from './rascal-stack';

const app = new App();

const allowedAccounts = (app.node.tryGetContext('allowedAccounts') ?? '')
  .split(',')
  .filter((s: string) => s.length > 0);

new RascalStack(app, 'RascalStack', {
  allowedAccountIds: allowedAccounts,
  principalOrgId: app.node.tryGetContext('principalOrgId'),
});
