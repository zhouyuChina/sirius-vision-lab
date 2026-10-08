'use strict';
const $ = (id) => document.getElementById(id);
// Relative paths preserve a reverse-proxy prefix such as /vision/.
const adminBase = new URL(location.pathname.endsWith('/admin') ? `${location.pathname}/` : './', location.href);
let csrf = '', cursor = null, selected = null, loading = false;
const labels = {avatar_tag: '头像打标', garment_attr: '商品属性', success: '成功', parse_error: '解析失败', provider_error: '服务失败', correct: '对', wrong: '错', uncertain: '存疑'};
function message(text) { $('message').textContent = text; }
function showLogin() {
  csrf = ''; $('detail').close(); $('workspace').hidden = true;
  $('logout').hidden = true; $('login').hidden = false;
}
async function api(path, options = {}) {
  const response = await fetch(new URL(path, adminBase), {
    ...options, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...options.headers}
  });
  if (response.redirected || response.status === 401) {
    showLogin(); throw new Error('请重新登录');
  }
  if (!response.ok) throw new Error(response.status === 403 ? '操作未获授权，请重新登录后重试' : '请求失败，请稍后重试');
  return response.json();
}
function cardStat(label, value) {
  const card = document.createElement('div'); card.className = 'stat';
  const title = document.createElement('span'); title.textContent = label;
  const number = document.createElement('strong'); number.textContent = value;
  card.append(title, number); return card;
}
async function stats() {
  const data = await api('stats');
  $('stats').replaceChildren(cardStat('调用量', data.calls), cardStat('P50 / P95 延迟', `${data.latency_p50_ms} / ${data.latency_p95_ms} ms`), cardStat('Tokens', data.tokens), cardStat('复核进度', `${data.reviewed} / ${data.calls}`));
}
async function openDetail(id) {
  try {
    message('正在加载详情…');
    const record = await api(`records/${id}`); selected = record.id;
    $('detail-image').src = new URL(`records/${id}/image`, adminBase);
    $('input').textContent = JSON.stringify({task: record.task, template_version: record.template_version, options: record.input_options}, null, 2);
    $('fields').textContent = JSON.stringify(record.parsed_fields, null, 2);
    $('raw').textContent = record.raw_output || '无输出';
    $('note').value = record.review_note || '';
    $('review-state').textContent = `复核：${labels[record.review] || '未复核'}`;
    $('detail-message').textContent = ''; $('detail').showModal(); message('');
  } catch (error) { message(error.message); }
}
async function records(append = false) {
  if (loading) return;
  loading = true; $('more').disabled = true; message('正在加载记录…');
  try {
    const query = new URLSearchParams({limit: '30'});
    for (const name of ['task', 'status']) if ($(name).value) query.set(name, $(name).value);
    if ($('date-from').value) query.set('date_from', new Date(`${$('date-from').value}T00:00:00`).toISOString());
    if ($('date-to').value) query.set('date_to', new Date(`${$('date-to').value}T23:59:59.999`).toISOString());
    if (append && cursor) query.set('cursor', cursor);
    const data = await api(`records?${query}`);
    if (!append) $('records').replaceChildren();
    for (const record of data.items) {
      const card = document.createElement('button'); card.className = 'card';
      const image = document.createElement('img'); image.src = new URL(`records/${record.id}/image`, adminBase); image.alt = `${labels[record.task]}输入图片`; image.loading = 'lazy';
      const caption = document.createElement('span'); caption.className = 'card-info';
      caption.textContent = `${labels[record.task]} · ${labels[record.status]}\n${new Date(record.created_at).toLocaleString()} · ${labels[record.review] || '未复核'}`;
      card.append(image, caption); card.onclick = () => openDetail(record.id); $('records').append(card);
    }
    if (!$('records').children.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = '暂无符合条件的识别记录'; $('records').append(empty); }
    cursor = data.next_cursor; $('more').hidden = !cursor; message('');
  } catch (error) { message(error.message); }
  finally { loading = false; $('more').disabled = false; }
}
async function start() {
  $('login').hidden = true; $('workspace').hidden = false; $('logout').hidden = false;
  await Promise.all([stats(), records()]);
}
$('login-form').onsubmit = async (event) => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; message('正在登录…');
  try { const data = await api('login', {method: 'POST', body: JSON.stringify({password: $('password').value})}); csrf = data.csrf_token; $('password').value = ''; await start(); }
  catch (error) { message(error.message); }
  finally { button.disabled = false; }
};
$('logout').onclick = async () => { try { await api('logout', {method: 'POST'}); showLogin(); message('已退出'); } catch (error) { message(error.message); } };
$('filters').onsubmit = (event) => { event.preventDefault(); records(); };
$('refresh').onclick = () => Promise.all([records(), stats()]).catch(error => message(error.message));
$('more').onclick = () => records(true);
$('close').onclick = () => $('detail').close();
for (const button of document.querySelectorAll('[data-review]')) button.onclick = async () => {
  const buttons = [...document.querySelectorAll('[data-review]')]; buttons.forEach(item => item.disabled = true);
  try {
    await api(`records/${selected}/review`, {method: 'PATCH', body: JSON.stringify({review: button.dataset.review, review_note: $('note').value})});
    $('review-state').textContent = `复核：${labels[button.dataset.review]}`; $('detail-message').textContent = '复核已保存';
    await Promise.all([stats(), records()]);
  } catch (error) { $('detail-message').textContent = error.message; }
  finally { buttons.forEach(item => item.disabled = false); }
};
api('session').then(data => { csrf = data.csrf_token; return start(); }).catch(() => { showLogin(); message('请输入管理口令'); });
