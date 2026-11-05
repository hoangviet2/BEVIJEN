import assert from 'node:assert/strict';
import { test } from 'node:test';
import { hasRole, sessionUser, toValue, zipLabel } from './lib.js';

const jwt = (p) => 'h.' + Buffer.from(JSON.stringify(p)).toString('base64url') + '.s';

test('roles are hierarchical', () => {
  assert.ok(hasRole(['admin'], 'lead'));
  assert.ok(hasRole(['lead'], 'specialist'));
  assert.ok(!hasRole(['specialist'], 'lead'));
  assert.ok(!hasRole([], 'specialist'));
});

test('sessionUser reads Cognito tokens and drops expired ones', () => {
  const u = sessionUser({ token: jwt({ email: 'ana@x.org', 'cognito:groups': ['lead'], exp: 2000 }) }, 1000);
  assert.deepEqual([u.email, u.groups], ['ana@x.org', ['lead']]);
  assert.equal(sessionUser({ token: jwt({ email: 'a', exp: 900 }) }, 1000), null);
  assert.equal(sessionUser({ token: 'garbage' }), null);
  assert.equal(sessionUser({ devUser: 'd@x', devGroups: ['admin'] }).email, 'd@x');
});

test('zip buckets display nicely', () => {
  assert.equal(zipLabel('ZIP#94044'), '94044');
  assert.equal(zipLabel('COUNTY#Solano'), 'Solano County');
  assert.equal(zipLabel('VIRTUAL'), 'Online / phone only');
});

test('list fields split on commas', () => {
  assert.deepEqual(toValue('languages', 'English, Spanish', null), ['English', 'Spanish']);
  assert.equal(toValue('eligibility_description', 'Open, to all', 'x'), 'Open, to all');
});
