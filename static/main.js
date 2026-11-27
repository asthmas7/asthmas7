"use strict";

const main = document.querySelector("#main");
const detail = document.querySelector("#detail");
const colors = ["#3767ef", "#54a5e9", "#8466e8", "#27a18f", "#e99b58", "#cd75ac", "#a7b4cb"];
const views = {overview: "月度总览", trends: "趋势与对比", import: "导入与核对", review: "复盘与规则"};
const yuan = new Intl.NumberFormat("zh-CN", {style: "currency", currency: "CNY"});
const money = cents => yuan.format(cents / 100);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char =>
  ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const options = (values, selected) => values.map(value =>
  `<option ${value === selected ? "selected" : ""}>${esc(value)}</option>`).join("");
const net = row => row.kind === "消费" ? row.amount : row.kind === "退款" ? -row.amount : 0;
const sum = rows => rows.reduce((total, row) => total + net(row), 0);
const monthTitle = month => month ? `${month.slice(0, 4)} 年 ${Number(month.slice(5))} 月` : "—";
const shiftMonth = (month, delta) => {
  const [year, number] = month.split("-").map(Number);
  return new Date(Date.UTC(year, number - 1 + delta, 1)).toISOString().slice(0, 7);
};
const signed = amount => (amount > 0 ? "+" : "") + money(amount);
const isSpending = row => row.kind === "消费" || row.kind === "退款";
let store = {transactions: [], categories: [], batches: [], rules: []};
let month, comparison, toastTimer;
let card = "", category = "", pending = [], scope;
let revision = 0;
const markedForReview = row => ["可减少", "疑似浪费"].includes(row.tag);

async function request(url, init) {
  const response = await fetch(url, init);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : "操作失败，请核对输入后重试。");
  }
  return response.json();
}

async function refresh() {
  store = await request("/api/state");
  const current = new Date();
  const latestReal = store.transactions.find(row => !row.sample);
  scope ??= latestReal ? "real" : "all";
  month ??= (latestReal ?? store.transactions[0])?.day.slice(0, 7)
    ?? `${current.getFullYear()}-${String(current.getMonth()+1).padStart(2, "0")}`;
  comparison ??= shiftMonth(month, -1);
}

function notify(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4300);
}

function view() {
  const key = location.hash.slice(1);
  return Object.hasOwn(views, key) ? key : "overview";
}

function filtered() {
  return store.transactions.filter(row =>
    (!card || row.card === card) && (!category || row.category === category) &&
    (scope === "all" || (scope === "sample" ? row.sample : !row.sample)));
}

function heading(title, subtitle, controls = "") {
  return `<div class="heading"><div><h1>${title}</h1><p class="muted">${subtitle}</p></div>${controls}</div>`;
}

function section(title, subtitle, extra = "") {
  return `<div class="section-head"><div><h2>${title}</h2>${subtitle ? `<p class="muted">${subtitle}</p>` : ""}</div>${extra}</div>`;
}

function filters(trends = false) {
  const cards = [...new Set(store.transactions.map(row => row.card))].sort();
  return `<div class="filters">
    <input type="month" id="month" aria-label="${trends ? "对比月份 A" : "月份"}" value="${month}">
    ${trends ? `<span>对比</span><input type="month" id="comparison" aria-label="对比月份 B" value="${comparison}">` : ""}
    <select id="card" aria-label="卡片"><option value="">所有卡片</option>${options(cards, card)}</select>
    <select id="category" aria-label="类别"><option value="">所有类别</option>${options(store.categories, category)}</select>
    <select id="scope" aria-label="数据来源">
      <option value="all" ${scope === "all" ? "selected" : ""}>全部数据</option>
      <option value="real" ${scope === "real" ? "selected" : ""}>已导入账单</option>
      <option value="sample" ${scope === "sample" ? "selected" : ""}>演示数据</option>
    </select></div>`;
}

function kpi(title, icon, tone, value, caption) {
  return `<article class="kpi"><div class="kpi-title">${title}<span class="tile ${tone}">${icon}</span></div>
    <div class="number">${value}</div><small>${caption}</small></article>`;
}

function chart(keys, groups, annual = false) {
  const values = keys.map(key => groups.has(key) ? sum(groups.get(key)) : null);
  const max = Math.max(1, ...values.map(value => Math.abs(value ?? 0)));
  const step = Math.max(100, Math.ceil(max / 40000) * 10000);
  const ceiling = step * 4;
  const labels = [4,3,2,1,0].map(i => `<span>${Math.round(i * step / 100).toLocaleString("zh-CN")}</span>`).join("");
  return `<div class="chart-scroll"><div class="chart" style="min-width:${Math.max(0, keys.length * 29 + 56)}px">
    <div class="axis" aria-hidden="true">${labels}</div><div class="plot">${keys.map((key, i) => {
      const value = values[i];
      const label = annual ? key : `${Number(key.slice(5))}月`;
      const description = `${annual ? key + " 年" : monthTitle(key)} · ${value === null ? "无账单" : money(value)}`;
      return `<button class="column ${key === month ? "selected" : ""}" data-${annual ? "year" : "month"}="${key}"
        title="${esc(description)}" aria-label="${esc(description)}"><span class="bar-area"><span
        class="bar ${value === null ? "unknown" : value < 0 ? "negative" : ""}"
        style="height:${value === 0 ? 2 : Math.abs(value ?? 0) / ceiling * 100}%"></span></span>
        <span class="month-label">${label}</span></button>`;
    }).join("")}</div></div></div>`;
}

function grouped(rows, length) {
  const result = new Map();
  for (const row of rows) {
    const key = row.day.slice(0, length);
    if (!result.has(key)) result.set(key, []);
    result.get(key).push(row);
  }
  return result;
}

function periodGroups(monthRows, coverage) {
  const result = grouped(monthRows, 7);
  for (const row of coverage) {
    const key = row.day.slice(0, 7);
    if (!result.has(key)) result.set(key, []);
  }
  return result;
}

function categoryBreakdown(rows) {
  const amounts = store.categories.map(name => sum(rows.filter(row => row.category === name)));
  const positive = amounts.reduce((total, amount) => total + Math.max(0, amount), 0);
  let position = 0;
  const slices = amounts.flatMap((amount, index) => {
    if (amount <= 0) return [];
    const start = position;
    position += amount / positive * 100;
    return [`${colors[index]} ${start}% ${position}%`];
  });
  return `<div class="breakdown"><div class="ring" style="background:${slices.length ? `conic-gradient(${slices.join(",")})` : "#edf1f7"}">
    <div><small>消费净额</small><strong>${rows.length ? money(sum(rows)) : "—"}</strong></div></div>
    <div class="legend">${store.categories.map((name, index) =>
      `<button data-category="${name}"><i class="dot" style="background:${colors[index]}"></i>
        <span>${name}</span><strong>${money(amounts[index])}</strong></button>`).join("")}</div></div>`;
}

function table(rows, limit = rows.length) {
  if (!rows.length) return '<p class="empty">没有符合筛选条件的交易。</p>';
  return `<div class="table-wrap"><table><thead><tr><th>交易日</th><th>商户 / 摘要</th><th>类别</th><th>卡片</th>
    <th>类型</th><th>金额</th><th>复盘</th><th>来源</th></tr></thead><tbody>${rows.slice(0, limit).map(row => `<tr>
    <td>${row.day}</td><td><button class="link" data-detail="${row.id}">${esc(row.merchant)}</button></td>
    <td>${esc(row.category)}</td><td>${esc(row.card)}</td><td>${esc(row.kind)}</td>
    <td>${money(row.kind === "退款" ? -row.amount : row.amount)}</td><td>${esc(row.tag || "—")}</td>
    <td>${row.batch ? `<a href="/api/batches/${row.batch}/pdf#page=${row.page}" target="_blank" rel="noopener">PDF · 第 ${row.page} 页</a>` : "演示"}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function sampleBanner() {
  return store.transactions.some(row => row.sample) ? `<div class="sample"><span>◉ 演示数据已加载。导入真实账单后，可切换数据来源。</span>
    <button class="button small" data-clear>移除演示交易</button></div>` : "";
}

const analysisStates = new Map();
const analysisKey = (period, baseline, sample) => [period, baseline, sample].join("/");
const sampleAnalysis = () => scope === "sample" ||
  (scope === "all" && !store.transactions.some(row => !row.sample));

function selectedAnalysis() {
  return [month, view() === "trends" ? comparison : shiftMonth(month, -1)];
}

function transactionReview(candidates, result, loaded) {
  if (!loaded) return "";
  if (!candidates.length) return '<p class="muted">本月暂无有足够依据的待复盘交易。</p>';
  const suggestions = new Map((result?.transaction_review ?? []).map(item => [item.transaction_id, item]));
  const transactions = new Map(store.transactions.map(row => [row.id, row]));
  return `<div class="stack"><h3>优先核对的交易</h3>${candidates.map(candidate => {
    const row = transactions.get(candidate["交易编号"]);
    if (!row) return "";
    const suggestion = suggestions.get(row.id);
    const evidence = candidate["依据"].map(reason => ({
      "疑似重复": `同日同卡同商户同金额共 ${reason["同日同卡同商户同额笔数"]} 笔，可核对订单记录。`,
      "历史反馈": `近六个月这家商户有 ${reason["标记可减少或疑似浪费的笔数"]} 笔被你标记为可减少或疑似浪费，
        ${reason["合理支出笔数"]} 笔标为合理。`,
      "疑似周期扣费": `过去六个月有 ${reason["过去六个月相同金额出现月数"]} 个月在该商户出现同额扣费，可检查是否仍使用。`,
      "金额明显高于以往": `该商户历史单笔中位数为 ${money(reason["历史同商户单笔中位数_分"] ?? 0)}，可检查是否为一次性支出。`,
    })[reason["类型"]]).filter(Boolean);
    return `<article class="hint-row">
      <div class="between"><div><strong>${esc(row.merchant)} · ${money(row.amount)}</strong>
      <small> · ${esc(row.day)} · ${esc(row.category)}</small></div>
      <button class="button small" data-detail="${row.id}">核对并反馈</button></div>
      ${evidence.map(line => `<p class="muted">${esc(line)}</p>`).join("")}
      ${suggestion ? `<p>AI 建议：${esc(suggestion.reason)} ${esc(suggestion.action)}</p>` : ""}</article>`;
  }).join("")}</div>`;
}

function analysisPanel(period, baseline) {
  const sample = sampleAnalysis();
  const exists = store.transactions.some(row => Boolean(row.sample) === sample && row.day.startsWith(period));
  if (!exists) return "";
  const status = analysisStates.get(analysisKey(period, baseline, sample));
  const result = status?.analysis;
  const title = section("支出复盘",
    `${monthTitle(period)} · ${sample ? "演示数据" : `对比 ${monthTitle(baseline)} · 全部已入账卡片`}`,
    sample || !store.ai_enabled || result ? "" :
      `<button class="button small" data-analyze="" ${status?.busy ? "disabled" : ""}>${status?.busy ? "分析中…" : "生成分析"}</button>`);
  const sections = result ? [
    ["消费分类", result.categories], ["月度趋势与年度汇总", result.trends],
    ["异常与疑似重复", result.anomalies], ["复盘建议", result.review],
  ] : [];
  return `<section class="panel" id="analysis-panel">${title}
    ${transactionReview(status?.candidates ?? [], result, Boolean(status?.candidates))}
    ${result ? `<div class="stack"><p>${esc(result.summary)}</p>${sections.filter(([, values]) => values.length)
      .map(([name, values]) => `<div><h3>${name}</h3><ul>${values.map(value => `<li>${esc(value)}</li>`).join("")}</ul></div>`)
      .join("")}<small>AI 提示供核对，金额以账单和本地统计为准。</small></div>`
    : sample && !status?.error ? "" : `<p class="muted" role="status">${esc(status?.error || (status?.busy ? "正在生成账单复盘…" :
      store.ai_enabled ? "分析会发送匿名汇总给 OpenAI。" : "配置 OPENAI_API_KEY 后可生成分析。"))}</p>`}</section>`;
}

async function fetchAnalysis(period, baseline, generate = false) {
  const sample = sampleAnalysis();
  const key = analysisKey(period, baseline, sample);
  if (!generate && analysisStates.has(key)) return;
  if (generate && analysisStates.get(key)?.generating) return;
  const status = {busy: generate, generating: generate};
  analysisStates.set(key, status);
  updateAnalysisPanel();
  try {
    const query = {month:period, comparison:baseline};
    const response = generate ? await request("/api/analysis", {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(query),
    }) : await request("/api/analysis?" + new URLSearchParams({...query, sample:String(sample)}));
    status.analysis = response.analysis;
    status.candidates = response.candidates;
  } catch (error) {
    status.error = error.message;
  } finally {
    status.busy = false;
    status.generating = false;
    if (analysisStates.get(key) === status) updateAnalysisPanel();
  }
}

async function waitForAnalysis(period, baseline) {
  const key = analysisKey(period, baseline, false);
  const status = {busy:true, generating:true};
  analysisStates.set(key, status);
  try {
    for (let attempt = 0; attempt < 30 && analysisStates.get(key) === status; attempt++) {
      const query = new URLSearchParams({month:period, comparison:baseline});
      const response = await request("/api/analysis?" + query);
      status.analysis = response.analysis;
      status.candidates = response.candidates;
      if (analysisStates.get(key) === status) updateAnalysisPanel();
      if (status.analysis) return;
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    status.error = "分析尚未返回。可稍后重试。";
  } catch (error) {
    status.error = error.message;
  } finally {
    status.busy = false;
    status.generating = false;
    if (analysisStates.get(key) === status) updateAnalysisPanel();
  }
}

function updateAnalysisPanel() {
  const panel = document.querySelector("#analysis-panel");
  if (panel) panel.outerHTML = analysisPanel(...selectedAnalysis());
}

function overview() {
  const rows = filtered();
  const coverage = store.transactions.filter(row => (!card || row.card === card) &&
    (scope === "all" || (scope === "sample" ? row.sample : !row.sample)));
  const months = periodGroups(rows, coverage);
  const current = months.get(month);
  const previous = months.get(shiftMonth(month, -1));
  const list = current ?? [];
  const spending = list.filter(isSpending);
  const expense = sum(list);
  const delta = expense - sum(previous ?? []);
  const review = list.filter(markedForReview);
  const count = new Set(spending.map(row => row.category)).size;
  const active = category ? `<button class="button small" data-category="">清除类别筛选</button>` : "";
  return heading("月度总览", "查看支出去向，复盘每一笔花销。", filters()) + sampleBanner() +
    `<div class="kpis">${kpi("消费净额", "¥", "", current ? money(expense) : "—",
       current && previous ? `<span class="${delta > 0 ? "up" : "down"}">较上月 ${signed(delta)}</span>` : "无可比的上月账单")}
      ${kpi("消费交易", "≡", "green", String(list.filter(row => row.kind === "消费").length), "仅计消费笔数")}
      ${kpi("支出类别", "◈", "purple", String(count), "当月有消费或退款的类别")}
      ${kpi("标记待复盘", "⚑", "orange", String(review.length), review.length ? `标记交易净额 ${money(sum(review))}` : "暂无标记交易")}</div>
    ${analysisPanel(month, shiftMonth(month, -1))}
    <div class="grid">
      <section class="panel">${section("消费趋势", "最近 6 个月 · 人民币", '<a class="button small" href="#trends">查看对比 ↗</a>')}
      ${chart(Array.from({length:6}, (_, index) => shiftMonth(month, index - 5)), months)}</section>
      <section class="panel">${section("支出分类", "点击类别筛选本月交易", active)}${categoryBreakdown(spending)}</section>
    </div>
    <section class="panel">${section("交易明细", `${monthTitle(month)} · ${list.length} 笔`, '<span class="pill">人民币 CNY</span>')}${table(list)}</section>
    <section class="panel">${section("主要商户", "按消费净额排序")}${topMerchants(spending)}</section>`;
}

function topMerchants(rows) {
  const totals = new Map();
  for (const row of rows) totals.set(row.merchant, (totals.get(row.merchant) ?? 0) + net(row));
  const entries = [...totals].sort((a,b) => b[1] - a[1]).slice(0, 8);
  return entries.length ? entries.map(([merchant, total]) =>
    `<div class="between hint-row"><span>${esc(merchant)}</span><strong>${money(total)}</strong></div>`).join("")
    : '<p class="empty">暂无消费交易。</p>';
}

function trends() {
  const rows = filtered();
  const coverage = store.transactions.filter(row => (!card || row.card === card) &&
    (scope === "all" || (scope === "sample" ? row.sample : !row.sample)));
  const months = periodGroups(rows, coverage);
  const keys = [...months.keys(), month, comparison].sort();
  const first = keys[0], last = keys.at(-1);
  const range = [];
  for (let key = first; key <= last && range.length < 240; key = shiftMonth(key, 1)) range.push(key);
  const years = new Map();
  for (const [key, bucket] of months) {
    const year = key.slice(0, 4);
    years.set(year, [...(years.get(year) ?? []), ...bucket]);
  }
  const a = months.get(month), b = months.get(comparison);
  function compare(label, rowsA, rowsB) {
    const amountA = sum(rowsA ?? []), amountB = sum(rowsB ?? []);
    const delta = amountA - amountB;
    const valid = rowsA && rowsB;
    const pct = valid && amountB > 0 ? (delta / amountB * 100).toFixed(1) + "%" : "—";
    return `<tr><td>${label}</td><td>${rowsA ? money(amountA) : "无账单"}</td><td>${rowsB ? money(amountB) : "无账单"}</td>
      <td>${valid ? signed(delta) : "—"}</td><td>${pct}</td></tr>`;
  }
  return heading("趋势与对比", "按月查看长期走势，比较任意两个月。", filters(true)) +
    `<section class="panel">${section("月度趋势", "每根柱表示一个月。橙色表示退款超过消费，空白表示无账单。")}
      ${chart(range, months)}</section>
    <section class="panel">${section("两月对比", "差额按 A 月减 B 月计算。")}
      <div class="table-wrap"><table><thead><tr><th>类别</th><th>A · ${monthTitle(month)}</th>
        <th>B · ${monthTitle(comparison)}</th><th>差额</th><th>变化率</th></tr></thead><tbody>
      ${compare("合计", a, b)}${store.categories.filter(name => !category || name === category).map(name =>
        compare(name, a?.filter(row => row.category === name), b?.filter(row => row.category === name))).join("")}
      </tbody></table></div></section>
    <section class="panel">${section("年度汇总", "仅汇总已有账单月份。")}
      ${years.size ? chart([...years.keys()].sort(), years, true) : '<p class="empty">暂无年度数据。</p>'}
    </section>
    ${analysisPanel(month, comparison)}
    <section class="panel">${section("所选月份交易", monthTitle(month))}${table(a ?? [])}</section>`;
}

function importPage() {
  return heading("导入与核对", "上传 PDF，确认交易后入账。") +
    `<section class="panel">${section("选择账单", "一次最多 10 份，每份不超过 10 MB。")}
      <div class="fields"><label class="field">PDF 文件<input id="pdfs" type="file" accept=".pdf,application/pdf" multiple></label>
      <a class="button" href="/api/example.pdf">信用卡样例</a>
      <a class="button" href="/api/example.pdf?card_type=借记卡">借记卡样例</a></div>
      <p class="muted">支持中国银行人民币信用卡账单，以及「日期　商户　金额　交易类型」样例格式。</p>
      ${store.ai_enabled ? '<p class="callout">本地未识别到交易时，原始 PDF 会发送给 OpenAI 提取交易。确认入账后，系统会发送匿名汇总生成分析。</p>' : ""}
      ${pending.map(importItem).join("")}</section>
    <section class="panel">${section("导入记录", "打开原始 PDF 核对。")}
    ${store.batches.length ? store.batches.map(batch => `<div class="between hint-row">
      <div><a href="/api/batches/${batch.id}/pdf" target="_blank" rel="noopener">${esc(batch.name)}</a>
      <small> · ${esc(batch.card)} · ${esc(batch.card_type)}</small></div>
      <small>${esc(batch.created)} UTC</small></div>`).join("") : '<p class="empty">暂无已导入账单。</p>'}</section>`;
}

function importItem(item, i) {
  if (item.done) return `<div class="import-item"><strong>${esc(item.file.name)}</strong> · 已入账 ${item.done} 笔</div>`;
  return `<div class="import-item" data-import-item="${i}">
    <h3>${esc(item.file.name)}</h3>
    <form data-preview="${i}" class="fields">
      <label class="field">卡片别名<input name="card" maxlength="80" required placeholder="例如：工资卡" value="${esc(item.card)}"></label>
      <label class="field">卡类型<select name="cardType">${options(["借记卡", "信用卡"], item.cardType)}</select></label>
      <button class="button" ${item.busy ? "disabled" : ""}>${item.busy ? "处理中…" : "解析预览"}</button>
    </form>
    ${item.error ? `<p class="callout">${esc(item.error)}</p>` : ""}
    ${item.preview ? `<div class="actions"><p class="muted">卡片：${esc(item.card)} · ${esc(item.cardType)}。修改卡片后请重新解析。</p></div>
      ${item.preview.warnings.length ? `<details class="callout" open><summary>${item.preview.warnings.length} 项解析提示</summary>
        <ul>${item.preview.warnings.map(warning => `<li>${esc(warning)}</li>`).join("")}</ul></details>` : ""}
      ${previewTable(item, i)}` : ""}</div>`;
}

function previewTable(item, i) {
  return `<form data-confirm="${i}">
    <p class="callout" data-reconcile="">正在核对…</p>
    <div class="table-wrap"><table><thead><tr><th>入账</th><th>日期</th><th>商户 / 摘要</th><th>金额（元）</th>
      <th>类型</th><th>类别</th><th>来源</th></tr></thead><tbody>
    ${item.preview.rows.map((row, index) => `<tr data-index="${index}">
      <td><input name="include" type="checkbox" aria-label="保留第 ${index+1} 笔" ${(row.selected ?? !row.duplicate) ? "checked" : ""}></td>
      <td><input name="day" type="date" aria-label="交易日期" value="${row.day}"></td>
      <td><input name="merchant" type="text" aria-label="商户" maxlength="200" value="${esc(row.merchant)}"></td>
      <td><input name="amount" type="number" aria-label="金额" min="0" max="1000000000" step="0.01" value="${row.amount === null ? "" : row.amount / 100}"></td>
      <td><select name="kind" aria-label="类型">${options([...(row.kind === "待核对" ? ["待核对"] : []), "消费", "退款", "转账", "还款"], row.kind)}</select></td>
      <td><select name="category" aria-label="类别">${options(store.categories, row.category)}</select></td>
      <td title="${esc(row.source)}">${row.manual
        ? `<input name="page" type="number" min="1" max="${item.preview.page_count}" value="${row.page ?? ""}" aria-label="原始页码"> 页 · 人工补录`
        : `第 ${row.page} 页 ${row.duplicate ? '<span class="warn">· 疑似重复</span>' : ""}`}</td></tr>`).join("")}
    </tbody></table></div>
    <div class="actions"><button class="button" type="button" data-add-row="${i}" ${item.busy ? "disabled" : ""}>补录交易</button>
      <button class="button primary" ${item.busy ? "disabled" : ""}>确认入账</button></div></form>`;
}

function readPreviewRows(form, selectedOnly = true) {
  return [...form.querySelectorAll("tr[data-index]")].flatMap(row => {
    const value = key => row.querySelector(`[name=${key}]`);
    const include = value("include").checked;
    if (selectedOnly && !include) return [];
    return [{
      index: value("page") ? null : Number(row.dataset.index),
      page: value("page") ? Number(value("page").value) : undefined,
      day: value("day").value, merchant: value("merchant").value.trim(),
      amount: value("amount").value === "" ? null : Math.round(Number(value("amount").value) * 100),
      kind: value("kind").value, category: value("category").value, selected: include,
    }];
  });
}

function rememberPreview(form, item) {
  item.preview.rows = readPreviewRows(form, false).map((row, index) => ({...item.preview.rows[index], ...row}));
}

function reconcile(form) {
  const item = pending[Number(form.dataset.confirm)];
  const rows = readPreviewRows(form);
  const actual = sum(rows);
  const expected = item.preview.expected;
  form.querySelector("[data-reconcile]").textContent = `已选 ${rows.length} 笔 · 消费净额 ${money(actual)} · ` +
    (expected === null ? "PDF 未提供可对账的消费净额" :
      `PDF 净额 ${money(expected)} · ${actual === expected ? "已对平" : "差额 " + signed(actual - expected)}`);
}

function reviewPage() {
  const rows = filtered().filter(row => row.day.startsWith(month));
  const marked = rows.filter(markedForReview);
  const counts = new Map();
  const duplicates = [];
  for (const row of rows.filter(row => row.kind === "消费")) {
    const key = JSON.stringify([row.day, row.card, row.merchant.trim().toLowerCase(), row.amount]);
    if (counts.has(key)) duplicates.push(row);
    else counts.set(key, row.id);
  }
  const previous = filtered().filter(row => row.day.startsWith(shiftMonth(month, -1)));
  const increases = store.categories.flatMap(name => {
    const before = sum(previous.filter(row => row.category === name));
    const after = sum(rows.filter(row => row.category === name));
    return before > 0 && after - before >= 10000 && after >= before * 1.3
      ? [`<div class="between hint-row"><span>${name}较上月增加 ${signed(after - before)}</span><button class="link" data-review-category="${name}">查看交易</button></div>`]
      : [];
  });
  return heading("复盘与规则", "回看已标记的开销，管理系统从反馈中学到的分类。", filters()) +
    `<section class="panel">${section("待复盘交易", monthTitle(month))}${table(marked)}</section>
      <section class="panel">${section("值得核对", "同卡同日同商户同金额，或类别较上月增加至少 30% 且增加至少 ¥100。")}
      ${duplicates.map(row => `<div class="between hint-row"><span>疑似重复扣费 · ${esc(row.merchant)} · ${money(row.amount)}</span>
        <button class="link" data-detail="${row.id}">查看交易</button></div>`).join("")}
      ${increases.join("") || (!duplicates.length ? '<p class="empty">本月暂无提示。</p>' : "")}</section>
      <section class="panel">${section("已学习的商户规则", "应用于以后导入的同名商户。")}
      ${store.rules.length ? store.rules.map(rule => `<div class="between hint-row"><span>${esc(rule.merchant)} → ${esc(rule.category)}</span>
        <button class="button small" data-remove-rule="${esc(rule.merchant)}">撤销规则</button></div>`).join("") : '<p class="empty">保存交易时勾选学习，即可添加规则。</p>'}</section>`;
}

function render() {
  const current = view();
  for (const link of document.querySelectorAll("nav a")) {
    if (link.hash === "#" + current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.querySelector("#breadcrumb").textContent = "账单分析 / " + views[current];
  main.innerHTML = {overview, trends, import: importPage, review: reviewPage}[current]();
  main.querySelectorAll("[data-confirm]").forEach(reconcile);
  main.setAttribute("aria-busy", "false");
  if (main.querySelector("#analysis-panel")) void fetchAnalysis(...selectedAnalysis());
}

function showDetail(id) {
  const row = store.transactions.find(item => item.id === id);
  if (!row) return;
  const original = store.transactions.find(item => item.id === row.original_id);
  detail.innerHTML = `<form data-feedback="${row.id}">
    <div class="between"><h2>${esc(row.merchant)}</h2><button class="button small" type="button" data-close>关闭</button></div>
    <p class="muted">${row.day} · ${esc(row.card)} · ${esc(row.kind)} · ${money(row.kind === "退款" ? -row.amount : row.amount)}</p>
    ${row.batch ? `<p><a href="/api/batches/${row.batch}/pdf#page=${row.page}" target="_blank" rel="noopener">原始 PDF · 第 ${row.page} 页</a>
      <br><small>${esc(row.source)}</small></p>` : ""}
    ${original ? `<button type="button" class="link" data-original="${original.id}">查看关联原消费 · ${original.day}</button>` : ""}
    <label class="field">类别<select name="category">${options(store.categories, row.category)}</select></label>
    <label class="field">复盘标记<select name="tag"><option value="">无标记</option>${options(["可减少", "疑似浪费", "合理支出"], row.tag)}</select></label>
    <label class="field">备注<textarea name="note" maxlength="2000" rows="3">${esc(row.note)}</textarea></label>
    <label><input name="learn" type="checkbox"> 以后同名商户使用这个分类</label>
    <button class="button primary">保存反馈</button></form>`;
  detail.showModal();
}

function handleFilter(event) {
  const input = event.target;
  if (input.id === "month" && !input.value) { input.value = month; return; }
  if (input.id === "comparison" && !input.value) { input.value = comparison; return; }
  if (input.id === "month") month = input.value;
  else if (input.id === "comparison") comparison = input.value;
  else if (input.id === "category") category = input.value;
  else if (input.id === "card") card = input.value;
  else if (input.id === "scope") { scope = input.value; card = ""; }
  else if (input.id === "pdfs") {
    if (pending.some(item => item.busy)) { notify("请等待当前账单处理完成。"); return; }
    if (input.files.length > 10) { input.value = ""; notify("一次最多选择 10 份 PDF。"); return; }
    pending = [...input.files].map(file => ({file, card: "", cardType: "信用卡"}));
    revision++;
  } else {
    const item = input.closest("[data-import-item]");
    if (item) {
      const model = pending[Number(item.dataset.importItem)];
      const confirm = input.closest("[data-confirm]");
      if (confirm) { rememberPreview(confirm, model); reconcile(confirm); }
      if (input.name === "card" || input.name === "cardType") {
        const form = input.form;
        model.card = form.elements.card.value;
        model.cardType = form.elements.cardType.value;
        if (model.preview) {
          model.preview = null;
          item.querySelector("[data-confirm]")?.remove();
          item.querySelector("details")?.remove();
        }
      }
    }
    return;
  }
  render();
}

main.addEventListener("change", handleFilter);
main.addEventListener("input", event => {
  const form = event.target.closest("[data-confirm]");
  if (form) {
    rememberPreview(form, pending[Number(form.dataset.confirm)]);
    reconcile(form);
  } else if (event.target.name === "card") {
    handleFilter(event);
  }
});

main.addEventListener("click", async event => {
  const button = event.target.closest("button");
  if (!button) return;
  const data = button.dataset;
  try {
    if (data.detail) { showDetail(Number(data.detail)); return; }
    if (Object.hasOwn(data, "analyze")) { void fetchAnalysis(...selectedAnalysis(), true); return; }
    if (Object.hasOwn(data, "addRow")) {
      const item = pending[Number(data.addRow)];
      item.preview.rows.push({manual:true, page:1, day:"", merchant:"", amount:null, kind:"消费", category:"其他"});
      render();
      const form = main.querySelector(`[data-confirm="${data.addRow}"]`);
      form.querySelector("tbody tr:last-child [name=day]").focus();
      return;
    }
    if (data.month) month = data.month;
    else if (data.year) {
      const row = filtered().find(item => item.day.startsWith(data.year));
      month = row?.day.slice(0, 7) ?? data.year + "-12";
      location.hash = "overview";
    } else if (Object.hasOwn(data, "category")) {
      category = category === data.category ? "" : data.category;
      render();
      document.querySelector(".grid + .panel")?.scrollIntoView({block:"nearest"});
      return;
    } else if (data.reviewCategory) {
      category = data.reviewCategory; location.hash = "overview";
    } else if (data.removeRule) {
      button.disabled = true;
      await request("/api/rules?merchant=" + encodeURIComponent(data.removeRule), {method:"DELETE"});
      await refresh(); notify("规则已撤销。");
    } else if (Object.hasOwn(data, "clear")) {
      if (!window.confirm("移除演示交易及这些交易上的备注？")) return;
      button.disabled = true;
      await request("/api/sample", {method:"DELETE"});
      await refresh();
      scope = "real";
      card = "";
      notify("演示交易已移除。");
    } else return;
    render();
  } catch (error) {
    button.disabled = false;
    notify(error.message);
  }
});

main.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const index = Number(form.dataset.preview ?? form.dataset.confirm);
  const item = pending[index];
  if (!item || item.busy) return;
  const atStart = revision;
  const body = new FormData();
  body.append("file", item.file);
  const confirming = Object.hasOwn(form.dataset, "confirm");
  if (confirming) {
    const rows = readPreviewRows(form);
    if (!rows.length || rows.some(row => !row.day || !row.merchant || row.amount === null || row.amount < 0)) {
      notify("请选择至少一笔交易，并填写日期、商户和有效金额。");
      return;
    }
    if (rows.some(row => row.kind === "待核对")) {
      notify("请为选中的待核对交易选择收支类型。");
      return;
    }
    const expected = item.preview.expected;
    if ((item.preview.warnings.length || (expected !== null && sum(rows) !== expected)) &&
        !window.confirm("存在解析提示或对账差额。确认按当前选中结果入账？")) return;
    body.append("payload", JSON.stringify({card:item.card, card_type:item.cardType, rows,
      preview_token:item.preview.preview_token}));
  } else {
    item.card = form.elements.card.value.trim();
    item.cardType = form.elements.cardType.value;
    if (!item.card) { notify("请输入卡片别名。"); return; }
    body.append("card", item.card);
  }
  item.error = "";
  item.busy = true;
  render();
  try {
    if (confirming) {
      const result = await request("/api/import", {method:"POST", body});
      item.done = result.count;
      analysisStates.clear();
      await refresh();
      if (store.ai_enabled) {
        for (const period of result.months) void waitForAnalysis(period, shiftMonth(period, -1));
      }
      const latest = store.transactions.find(row => !row.sample);
      if (latest) month = latest.day.slice(0, 7);
      scope = "real";
      card = "";
      category = "";
      notify(`已入账 ${result.count} 笔。`);
    } else {
      item.preview = await request("/api/preview", {method:"POST", body});
    }
  } catch (error) {
    item.error = error.message;
  } finally {
    item.busy = false;
    if (atStart === revision) render();
  }
});

detail.addEventListener("click", event => {
  if (event.target.closest("[data-close]")) detail.close();
  const original = event.target.closest("[data-original]");
  if (original) {
    detail.close();
    showDetail(Number(original.dataset.original));
  }
});
detail.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const {category, tag, note, learn} = form.elements;
  const submit = form.querySelector('[class="button primary"]');
  submit.disabled = true;
  try {
    await request("/api/transactions/" + form.dataset.feedback, {
      method:"PATCH", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({category:category.value, tag:tag.value, note:note.value, learn:learn.checked}),
    });
    analysisStates.clear();
    await refresh();
    detail.close();
    render();
    notify(learn.checked ? "反馈已保存，分类规则已更新。" : "反馈已保存。");
  } catch (error) {
    notify(error.message);
  } finally {
    submit.disabled = false;
  }
});
window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });
refresh().then(render).catch(error => {
  main.textContent = "加载失败：" + error.message;
  main.setAttribute("aria-busy", "false");
});
