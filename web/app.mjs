import { groups, series, change } from './model.mjs';

const $ = id => document.getElementById(id);
const colors = ['#3b82f6', '#e67e22', '#20a88a', '#b27af5'];
const format = (n, digits = 2) => n.toLocaleString('zh-CN', { maximumFractionDigits: digits });
const basename = path => path.split('/').at(-1);
const unit = metric => ({ median_ms: 'ms', max_rss_mib: 'MiB', output_bytes: '字节' })[metric];
let environments = [];

function link(text, href) {
  const a = document.createElement(href ? 'a' : 'span');
  a.textContent = text;
  if (href) a.href = href;
  return a;
}

function commitURL(record) {
  if (record.demo) return `history/${record.id}.json`;
  return `https://github.com/${record.repository}/commit/${record.commit}`;
}

function svg(tag, attributes = {}, text) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

function drawChart(records, operation, metric) {
  const lines = series(records, operation, metric);
  const values = lines.flatMap(s => s.points.map(p => p.value)).filter(Number.isFinite);
  const max = Math.max(1, ...values) * 1.12;
  const x = index => 80 + (records.length === 1 ? 420 : index * 840 / (records.length - 1));
  const y = value => 320 - value * 270 / max;
  const chart = svg('svg', { viewBox: '0 0 960 380', role: 'img', 'aria-label': `${$('operation').selectedOptions[0].textContent}：${$('metric').selectedOptions[0].textContent}趋势` });
  chart.append(svg('text', { x: 80, y: 22 }, unit(metric)));
  for (let i = 0; i <= 5; i++) {
    const value = max * i / 5;
    chart.append(svg('line', { x1: 80, x2: 920, y1: y(value), y2: y(value), class: 'grid' }));
    chart.append(svg('text', { x: 70, y: y(value) + 4, 'text-anchor': 'end' }, format(value, metric === 'output_bytes' ? 0 : 1)));
  }
  const tickStep = Math.max(1, Math.ceil(records.length / 7));
  records.forEach((record, i) => {
    const last = records.length - 1;
    if (i !== last && (i % tickStep || last - i < tickStep)) return;
    chart.append(svg('text', { x: x(i), y: 345, 'text-anchor': 'middle' }, record.commit.slice(0, 7)));
    chart.append(svg('text', { x: x(i), y: 365, 'text-anchor': 'middle' }, new Date(record.date).toLocaleDateString('zh-CN')));
  });
  $('legend').replaceChildren();
  lines.forEach((line, i) => {
    const color = colors[i % colors.length];
    let path = '';
    let connected = false;
    for (const point of line.points) {
      if (!Number.isFinite(point.value)) { connected = false; continue; }
      path += `${connected ? 'L' : 'M'}${x(point.index)},${y(point.value)} `;
      connected = true;
    }
    chart.append(svg('path', { d: path, fill: 'none', stroke: color, 'stroke-width': 2 }));
    for (const point of line.points.filter(p => Number.isFinite(p.value))) {
      const title = `${basename(line.sample)} · ${point.record.commit.slice(0, 12)} · ${format(point.value)} ${unit(metric)} · ${point.record.date}`;
      const a = svg('a', { href: commitURL(point.record), 'aria-label': title });
      const circle = svg('circle', { cx: x(point.index), cy: y(point.value), r: 4, fill: color });
      circle.append(svg('title', {}, title));
      a.append(circle);
      chart.append(a);
    }
    const label = document.createElement('span');
    label.textContent = basename(line.sample);
    label.style.setProperty('--series', color);
    $('legend').append(label);
  });
  $('chart').replaceChildren(chart);
}

function render() {
  const records = environments.find(([id]) => id === $('group').value)[1];
  const latest = records.at(-1);
  const previous = records.at(-2);
  const operation = $('operation').value;
  const metric = $('metric').value;
  $('environment').textContent = `${latest.cpu} · ${latest.go} · GOMAXPROCS=1 · ${records.length} 次测量 · 环境 ${latest.group.slice(0, 12)}`;
  $('latest').replaceChildren(new Date(latest.date).toLocaleString('zh-CN') + ` · ${latest.ofd_ref} → `, link(latest.commit.slice(0, 12), commitURL(latest)), ' · ', link('原始 JSON', `history/${latest.id}.json`));
  drawChart(records, operation, metric);
  $('results').replaceChildren();
  for (const row of latest.summary.filter(s => s.operation === operation)) {
    const before = previous?.summary.find(s => s.operation === operation && s.sample === row.sample);
    const delta = change(row[metric], before?.[metric]);
    const tr = document.createElement('tr');
    for (const text of [basename(row.sample), `${format(row.median_ms)} ms`, `${format(row.min_ms)}–${format(row.max_ms)} ms`, `${format(row.max_rss_mib)} MiB`, format(row.output_bytes, 0), delta === null ? '—' : `${delta > 0 ? '+' : ''}${format(delta)}%`]) {
      const td = document.createElement('td');
      td.textContent = text;
      tr.append(td);
    }
    $('results').append(tr);
  }
  $('history').replaceChildren();
  for (const record of [...records].reverse()) {
    const tr = document.createElement('tr');
    for (const content of [new Date(record.date).toLocaleString('zh-CN'), record.ofd_ref, link(record.commit.slice(0, 12), commitURL(record)), link(record.benchmark_commit.slice(0, 12), record.demo ? null : `https://github.com/${record.workflow_repository}/commit/${record.benchmark_commit}`), link(record.demo ? '演示运行' : '查看运行', record.demo ? null : record.run_url), link('JSON', `history/${record.id}.json`)]) {
      const td = document.createElement('td');
      td.append(content);
      tr.append(td);
    }
    $('history').append(tr);
  }
}

try {
  const response = await fetch('data.json', { cache: 'no-cache' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  if (data.schema_version !== 1 || !Array.isArray(data.records)) throw new Error('不支持的历史数据格式');
  $('demo-banner').hidden = !data.demo;
  environments = groups(data.records);
  if (!environments.length) {
    $('status').textContent = '尚无 CI 性能记录。首次 Performance 工作流成功后，数据会自动出现在这里。';
  } else {
    for (const [id, records] of environments) {
      const last = records.at(-1);
      $('group').add(new Option(`${last.cpu} · ${last.go} · ${id.slice(0, 8)} · ${records.length} 次`, id));
    }
    for (const id of ['group', 'operation', 'metric']) $(id).addEventListener('change', render);
    $('status').hidden = true;
    $('dashboard').hidden = false;
    render();
  }
} catch (error) {
  $('status').hidden = false;
  $('status').textContent = `加载失败：${error.message}。请刷新页面或从上方运行记录查看原始产物。`;
}
