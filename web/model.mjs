// 环境组在收集阶段由测量口径、输入及运行环境共同计算。
export function groups(records) {
  const result = new Map();
  for (const record of records) {
    if (!result.has(record.group)) result.set(record.group, []);
    result.get(record.group).push(record);
  }
  for (const rows of result.values()) rows.sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id, undefined, { numeric: true }));
  return [...result.entries()].sort((a, b) => b[1].at(-1).date.localeCompare(a[1].at(-1).date));
}

export function series(records, operation, metric) {
  const samples = [...new Set(records.flatMap(r => r.summary.filter(s => s.operation === operation).map(s => s.sample)))];
  return samples.map(sample => ({ sample, points: records.map((record, index) => {
    const row = record.summary.find(s => s.sample === sample && s.operation === operation);
    return { record, index, value: row?.[metric] ?? null };
  }) }));
}

export function change(current, previous) {
  if (!Number.isFinite(previous) || !Number.isFinite(current) || previous <= 0) return null;
  return (current / previous - 1) * 100;
}
