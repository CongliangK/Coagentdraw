'use strict';
// No project-specific records in this file. The dataset is read on every reload.
const $ = id => document.getElementById(id);
let catalog = null;
let catalogHash = '';
let tag = '';
let chosen = {};
let storageKey = '';

function node(tagName, className, content) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = content;
  return element;
}
function ratioLabel(image) {
  let a = image.width, b = image.height;
  while (b) [a, b] = [b, a % b];
  return `${image.width / a}:${image.height / a} · ${image.aspect_ratio.toFixed(3)}`;
}
function media(record, thumb = false) {
  return `/${thumb ? 'thumbnail' : 'media'}/${encodeURIComponent(record.id)}`;
}
function isChosen(record) { return chosen[record.id] === record.image.sha256; }
function remember() {
  try { localStorage.setItem(storageKey, JSON.stringify(chosen)); } catch (_) { /* In-memory selection still works. */ }
}
function exportLabel() {
  const size = catalog.records.filter(isChosen).length;
  $('export').textContent = `导出试拼清单（${size}）`;
  $('export').disabled = size === 0;
}
function addSourceLink(parent, source) {
  if (source.url === 'unknown') return;
  const link = node('a', '', '来源 ↗');
  link.href = source.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  parent.append(link);
}
function card(record) {
  const article = node('article', 'card');
  const picture = node('button', 'picture');
  picture.setAttribute('aria-label', `放大 ${record.title}`);
  const image = node('img');
  image.src = media(record, true);
  image.alt = record.title;
  image.loading = 'lazy';
  image.addEventListener('error', () => {
    image.alt = '原图缺失或已经改变，请重新读取并检查数据集';
  });
  picture.append(image);
  picture.addEventListener('click', () => show(record));
  const info = node('div', 'info');
  info.append(node('span', 'badge', record.material));
  if (record.priority === 'recommended') info.append(node('span', 'recommended', '优先看'));
  info.append(node('h3', '', `${record.id} · ${record.title}`));
  info.append(node('p', 'meaning', record.meaning));
  info.append(node('p', 'dimensions', `${record.image.width}×${record.image.height} · ${ratioLabel(record.image)}${record.image.has_alpha ? ' · 含透明像素' : ''}`));
  const links = node('div', 'links');
  const original = node('a', '', '原图');
  original.href = media(record);
  original.target = '_blank';
  original.rel = 'noopener';
  links.append(original);
  addSourceLink(links, record.source);
  info.append(links);
  const label = node('label', 'choose');
  const checkbox = node('input');
  checkbox.type = 'checkbox';
  checkbox.checked = isChosen(record);
  checkbox.addEventListener('change', () => {
    if (checkbox.checked) chosen[record.id] = record.image.sha256;
    else delete chosen[record.id];
    remember();
    exportLabel();
    if ($('scope').value === 'chosen') render();
  });
  label.append(checkbox, document.createTextNode('加入试拼清单'));
  info.append(label);
  article.append(picture, info);
  return article;
}
function render() {
  if (!catalog) return;
  const query = $('query').value.trim().toLocaleLowerCase();
  const scope = $('scope').value;
  const group = $('group').value;
  const material = $('material').value;
  const ratio = $('ratio').value;
  const found = catalog.records.filter(record => {
    if (scope === 'main' && record.priority === 'parked') return false;
    if (scope === 'recommended' && record.priority !== 'recommended') return false;
    if (scope === 'chosen' && !isChosen(record)) return false;
    if (scope === 'parked' && record.priority !== 'parked') return false;
    if (group && record.group !== group || material && record.material !== material) return false;
    if (tag && !record.tags.includes(tag)) return false;
    const r = record.image.aspect_ratio;
    if (ratio === 'portrait' && r >= .9 || ratio === 'landscape' && r <= 1.1 || ratio === 'square' && (r < .9 || r > 1.1)) return false;
    return !query || [record.id, record.title, record.meaning, record.source.creator, record.source.url, record.source.rights, ...record.tags].join(' ').toLocaleLowerCase().includes(query);
  });
  found.sort((a, b) => Number(b.priority === 'recommended') - Number(a.priority === 'recommended'));
  $('count').textContent = `显示 ${found.length} / ${catalog.records.length} 张`;
  $('grid').replaceChildren(...found.map(card));
  if (!found.length) $('grid').append(node('p', 'empty', '当前条件下没有图片，试试其他筛选。'));
  exportLabel();
}
function show(record) {
  $('vtitle').textContent = `${record.id} · ${record.title}`;
  $('vimg').src = record.image.mime === 'image/tiff' ? `/preview/${encodeURIComponent(record.id)}` : media(record);
  $('vimg').alt = record.title;
  $('details').replaceChildren();
  const items = {
    '设计意义': record.meaning,
    '尺寸／比例': `${record.image.width} × ${record.image.height} / ${ratioLabel(record.image)}`,
    '原图路径': record.file,
    '来源作者': record.source.creator,
    '使用限制': record.source.rights,
    '获取日期': record.source.retrieved_at,
    '图片摘要': record.image.sha256,
    ...(record.image.mime === 'image/tiff' ? {'预览说明': 'TIFF使用最长边2048像素的PNG观察副本，原图保持不变。'} : {}),
  };
  for (const [name, value] of Object.entries(items)) $('details').append(node('dt', '', name), node('dd', '', value));
  $('original').href = media(record);
  $('source').hidden = record.source.url === 'unknown';
  $('source').removeAttribute('href');
  if (record.source.url !== 'unknown') $('source').href = record.source.url;
  $('viewer').showModal();
}
function options(id, values, first) {
  const previous = $(id).value;
  $(id).replaceChildren(new Option(first, ''), ...values.map(value => new Option(value, value)));
  if (values.includes(previous)) $(id).value = previous;
}
function tags() {
  const all = [...new Set(catalog.records.flatMap(record => record.tags))];
  if (!all.includes(tag)) tag = '';
  $('tags').replaceChildren();
  for (const value of ['', ...all]) {
    const button = node('button', value === tag ? 'active' : '', value || '全部标签');
    button.setAttribute('aria-pressed', String(value === tag));
    button.addEventListener('click', () => { tag = value; tags(); render(); });
    $('tags').append(button);
  }
}
async function reload() {
  $('error').hidden = true;
  $('reload').disabled = true;
  try {
    const response = await fetch('/api/catalog', {cache: 'no-store'});
    if (!response.ok) throw new Error(await response.text());
    const body = await response.json();
    const next = body.catalog;
    const nextKey = `cwb-reference-selection:${next.id}`;
    if (nextKey !== storageKey) {
      chosen = {};
      try {
        const stored = JSON.parse(localStorage.getItem(nextKey) || '{}');
        if (stored && typeof stored === 'object' && !Array.isArray(stored)) chosen = stored;
      } catch (_) { /* Ignore unavailable or malformed browser storage. */ }
    }
    catalog = next;
    catalogHash = body.sha256;
    storageKey = nextKey;
    // Changed/deleted assets never silently retain their old selection.
    chosen = Object.fromEntries(catalog.records.filter(isChosen).map(record => [record.id, record.image.sha256]));
    remember();
    $('title').textContent = catalog.title;
    document.title = catalog.title + ' · Coagentdraw';
    $('description').textContent = catalog.description;
    const stats = [['张资料', catalog.records.length], ['张含透明像素', catalog.records.filter(x => x.image.has_alpha).length], ['张优先推荐', catalog.records.filter(x => x.priority === 'recommended').length]];
    $('stats').replaceChildren();
    for (const [label, count] of stats) {
      const item = node('span');
      item.append(node('b', '', String(count)), document.createTextNode(label));
      $('stats').append(item);
    }
    options('group', [...new Set(catalog.records.map(x => x.group))], '全部分组');
    options('material', [...new Set(catalog.records.map(x => x.material))], '全部素材类型');
    tags();
    if ($('viewer').open) $('viewer').close();
    render();
  } catch (error) {
    // Never leave stale images or a stale export button presented as current data.
    catalog = null;
    $('grid').replaceChildren();
    $('stats').replaceChildren();
    $('count').textContent = '资料集未加载';
    $('export').disabled = true;
    if ($('viewer').open) $('viewer').close();
    $('error').textContent = `无法读取资料集：${error.message}`;
    $('error').hidden = false;
  } finally { $('reload').disabled = false; }
}
function exportChosen() {
  if (!catalog) return;
  const result = {schema: 'cwb-reference-shortlist-v1', catalog_id: catalog.id, catalog_sha256: catalogHash,
    notice: '本地试拼清单；不代表已导入Krita、作者已采纳或艺术批准。', records: catalog.records.filter(isChosen)};
  const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2) + '\n'], {type: 'application/json'}));
  const link = node('a');
  link.href = url;
  link.download = 'reference-shortlist.json';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('close').addEventListener('click', () => $('viewer').close());
$('viewer').addEventListener('click', event => { if (event.target === $('viewer')) $('viewer').close(); });
for (const id of ['scope', 'group', 'material', 'ratio']) $(id).addEventListener('change', render);
$('query').addEventListener('input', render);
$('reload').addEventListener('click', reload);
$('export').addEventListener('click', exportChosen);
reload();
