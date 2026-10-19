const $ = (sel) => document.querySelector(sel);

const state = { today: null, day: null, items: [], topic: "전체", text: "" };

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => (toast.hidden = true), 3500);
}

// "2026-10-19T21:04+09:00" → 같은 날이면 "21:04", 아니면 "10/18 21:04" (모두 한국 시간)
function formatTime(iso) {
  if (!iso) return "";
  const time = iso.slice(11, 16);
  if (iso.slice(0, 10) === state.day) return time;
  return `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))} ${time}`;
}

function safeUrl(url) {
  return /^https?:\/\//i.test(url) ? url : "#";
}

async function loadStatus() {
  const status = await api("/api/status");
  state.today = status.today;

  const chip = $("#kipris-chip");
  chip.textContent = status.kipris_connected ? "KIPRIS 연결됨" : "KIPRIS 인증키 없음";
  chip.classList.toggle("ok", status.kipris_connected);

  const days = status.news_days.includes(status.today) ? status.news_days : [status.today, ...status.news_days];
  const select = $("#day-select");
  select.replaceChildren(...days.map((d) => el("option", { value: d, textContent: d === status.today ? `${d} (오늘)` : d })));
  if (!state.day || !days.includes(state.day)) state.day = days[0];
  select.value = state.day;
}

async function loadNews() {
  const data = await api(`/api/news?day=${encodeURIComponent(state.day)}`);
  state.items = data.items;
  renderTopicFilters();
  renderNews();
}

function renderTopicFilters() {
  const topics = ["전체", ...new Set(state.items.map((a) => a.topic))];
  if (!topics.includes(state.topic)) state.topic = "전체";
  $("#topic-filters").replaceChildren(
    ...topics.map((t) => {
      const button = el("button", { type: "button", textContent: t });
      button.setAttribute("aria-pressed", String(t === state.topic));
      button.addEventListener("click", () => {
        state.topic = t;
        renderTopicFilters();
        renderNews();
      });
      return button;
    }),
  );
}

function renderNews() {
  const text = state.text.trim().toLowerCase();
  const items = state.items.filter(
    (a) => (state.topic === "전체" || a.topic === state.topic) && (!text || a.title.toLowerCase().includes(text)),
  );

  const groups = new Map();
  for (const a of items) {
    if (!groups.has(a.topic)) groups.set(a.topic, []);
    groups.get(a.topic).push(a);
  }

  const label = state.day === state.today ? "오늘" : state.day;
  $("#news-summary").textContent = state.items.length
    ? `${label} 새로 들어온 기사 ${state.items.length}건${items.length !== state.items.length ? ` · 지금 보이는 기사 ${items.length}건` : ""}`
    : "";

  const container = $("#news-groups");
  if (!state.items.length) {
    container.replaceChildren(
      el("div", { className: "empty", textContent: `${label} 수집한 뉴스가 아직 없어요. '지금 새로 가져오기'를 눌러 보세요.` }),
    );
    return;
  }
  if (!items.length) {
    container.replaceChildren(el("div", { className: "empty", textContent: "조건에 맞는 기사가 없어요." }));
    return;
  }

  container.replaceChildren(
    ...[...groups].map(([topic, articles]) => {
      const section = el(
        "section",
        { className: "group" },
        el("h2", {}, topic, el("span", { className: "count", textContent: `${articles.length}건` })),
        el("ul", { className: "articles" }, ...articles.map(renderArticle)),
      );
      section.dataset.topic = topic;
      return section;
    }),
  );
}

function renderArticle(a) {
  const meta = el("div", { className: "meta" });
  if (a.source) meta.append(el("span", { textContent: a.source }));
  if (a.published_at) meta.append(el("span", { textContent: formatTime(a.published_at) }));
  meta.append(el("span", { className: "tag", textContent: a.query }));
  return el(
    "li",
    {},
    el("a", { href: safeUrl(a.url), target: "_blank", rel: "noopener noreferrer", textContent: a.title }),
    meta,
  );
}

async function refresh() {
  const button = $("#refresh-btn");
  button.disabled = true;
  button.textContent = "가져오는 중…";
  try {
    const result = await api("/api/news/refresh", { method: "POST" });
    const failed = result.results.filter((r) => r.error).length;
    showToast(`새 기사 ${result.added}건을 가져왔어요${failed ? ` (검색어 ${failed}개 실패)` : ""}.`);
    state.day = null;
    await loadStatus();
    await loadNews();
  } catch (err) {
    showToast(`가져오지 못했어요: ${err.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "지금 새로 가져오기";
  }
}

function setupTabs() {
  const buttons = document.querySelectorAll(".tabs button");
  for (const button of buttons) {
    button.addEventListener("click", () => {
      for (const b of buttons) {
        const selected = b === button;
        b.setAttribute("aria-selected", String(selected));
        $(`#tab-${b.dataset.tab}`).hidden = !selected;
      }
    });
  }
}

async function init() {
  setupTabs();
  $("#day-select").addEventListener("change", (e) => {
    state.day = e.target.value;
    loadNews();
  });
  $("#text-filter").addEventListener("input", (e) => {
    state.text = e.target.value;
    renderNews();
  });
  $("#refresh-btn").addEventListener("click", refresh);

  try {
    await loadStatus();
    await loadNews();
  } catch (err) {
    $("#news-groups").replaceChildren(el("div", { className: "empty", textContent: `불러오지 못했어요: ${err.message}` }));
  }
}

init();
