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

test('hours read as text, never as raw JSON with ids', async () => {
  const { text, serviceSummary } = await import('./lib.js');
  const odrs = { id: '0f9c', service_id: 'c557', notes: '24 hours, 7 days a week' };
  assert.equal(text([odrs]), '24 hours, 7 days a week');
  assert.equal(serviceSummary({ schedules: [odrs] }).Hours, '24 hours, 7 days a week');
  assert.equal(text({ freq: 'WEEKLY', byday: 'MO,TU', opens_at: '09:00', closes_at: '17:00' }), 'Mon, Tue 09:00–17:00');
  assert.equal(text({ byday: '2SU', opens_at: '10:00' }), '2Sun 10:00');
  assert.equal(text({ id: 'x', location_id: 'y', foo: 'bar' }), 'bar');
});
