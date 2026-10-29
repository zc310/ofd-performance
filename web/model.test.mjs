import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groups, series, change } from './model.mjs';

test('环境变化后分组；相同环境按时间排序并保留同一提交的重跑', () => {
  const records = [
    { id: '12', group: 'a', date: '2026-10-29T12:00:00Z' },
    { id: '10', group: 'b', date: '2026-10-29T13:00:00Z' },
    { id: '11', group: 'a', date: '2026-10-29T11:00:00Z' },
  ];
  assert.deepEqual(groups(records).map(([id, rows]) => [id, rows.map(r => r.id)]), [['b', ['10']], ['a', ['11', '12']]]);
});

test('缺失项目保留空点，不把缺失记录误当作零值', () => {
  const records = [
    { summary: [{ sample: 'one', operation: 'pdf', median_ms: 12 }] },
    { summary: [{ sample: 'one', operation: 'png', median_ms: 2 }] },
    { summary: [{ sample: 'one', operation: 'pdf', median_ms: 0 }] },
  ];
  assert.deepEqual(series(records, 'pdf', 'median_ms')[0].points.map(p => p.value), [12, null, 0]);
});

test('百分比变化正确处理零基数和无上一条数据的情况', () => {
  assert.equal(change(150, 100), 50);
  assert.equal(change(0, 100), -100);
  assert.equal(change(100, 0), null);
  assert.equal(change(100, undefined), null);
});
