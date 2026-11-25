// 通用工具：安全的 DOM 构建、格式化、状态徽标、弹窗与提示。

const SVG_NS = "http://www.w3.org/2000/svg";

/** 创建元素。字符串子节点一律作为文本插入（不解析 HTML），防止注入。 */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  applyAttrs(el, attrs);
  appendChildren(el, children);
  return el;
}

export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  appendChildren(el, children);
  return el;
}

function applyAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
}

function appendChildren(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** 仅用于本文件内的静态图标路径（不含任何外部数据）。 */
export function icon(path, size = 16) {
  return s("svg", { viewBox: "0 0 24 24", width: size, height: size, "aria-hidden": "true" }, s("path", { d: path }));
}

export const ICONS = {
  upload: "M12 3 6.5 8.5l1.4 1.4L11 6.8V16h2V6.8l3.1 3.1 1.4-1.4zM5 18v2h14v-2z",
  plus: "M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z",
  refresh: "M17.6 6.4A8 8 0 1 0 20 12h-2a6 6 0 1 1-1.8-4.2L13 11h7V4z",
  trash: "M9 3h6l1 2h4v2H4V5h4zm-3 6h12l-1 12H7z",
  download: "M11 4h2v8.2l3.1-3.1 1.4 1.4L12 16l-5.5-5.5 1.4-1.4 3.1 3.1zM5 18h14v2H5z",
  back: "M15.4 6.6 14 5.2 7.2 12l6.8 6.8 1.4-1.4L10 12z",
  next: "M8.6 17.4 10 18.8 16.8 12 10 5.2 8.6 6.6 14 12z",
  photo: "M4 5h16v14H4zm2 2v8l3.5-4 2.5 3 3.5-4.5L18 13V7z",
  tree: "M12 2a6 6 0 0 0-3.9 10.6A5 5 0 0 0 11 21.9V22h2v-.1a5 5 0 0 0 2.9-9.3A6 6 0 0 0 12 2z",
  csv: "M6 2h9l5 5v15H6zm8 1.5V8h4.5zM8 12v2h8v-2zm0 4v2h8v-2z",
};

// ---------------------------------------------------------------------------
// 健康等级：颜色 + 图形 + 文字，三者同时出现，色觉障碍用户也能区分
// ---------------------------------------------------------------------------

export const LEVEL_ORDER = ["healthy", "mild", "moderate", "severe", "unknown"];
export const LEVEL_COLOR = {
  healthy: "var(--good)",
  mild: "var(--warning)",
  moderate: "var(--serious)",
  severe: "var(--critical)",
  unknown: "var(--unknown)",
};
const DEFAULT_LABELS = {
  health_levels: { healthy: "健康", mild: "轻度异常", moderate: "中度异常", severe: "严重异常", unknown: "无法评估" },
  growth_stages: {},
  vigor_levels: {},
  severities: { low: "轻微", medium: "中等", high: "严重" },
  issues: {},
};
let enumLabels = DEFAULT_LABELS;

export function setEnums(enums) {
  enumLabels = { ...DEFAULT_LABELS, ...(enums || {}) };
}

export function enums() {
  return enumLabels;
}

export function levelLabel(level) {
  return enumLabels.health_levels[level] || "待分析";
}

/** 每个等级对应一个形状：健康●、轻度◆、中度▲、严重■、无法评估○ */
export function levelShape(level, cx, cy, r, attrs = {}) {
  const fill = LEVEL_COLOR[level] || LEVEL_COLOR.unknown;
  const base = { fill, ...attrs };
  switch (level) {
    case "mild":
      return s("path", { ...base, d: `M${cx} ${cy - r * 1.25}L${cx + r * 1.25} ${cy}L${cx} ${cy + r * 1.25}L${cx - r * 1.25} ${cy}Z` });
    case "moderate":
      return s("path", { ...base, d: `M${cx} ${cy - r * 1.25}L${cx + r * 1.2} ${cy + r * 0.95}L${cx - r * 1.2} ${cy + r * 0.95}Z` });
    case "severe":
      return s("rect", { ...base, x: cx - r * 0.95, y: cy - r * 0.95, width: r * 1.9, height: r * 1.9, rx: r * 0.25 });
    case "healthy":
      return s("circle", { ...base, cx, cy, r });
    default:
      return s("circle", { ...base, cx, cy, r: r * 0.8, fill: "none", stroke: fill, "stroke-width": r * 0.45 });
  }
}

export function levelIcon(level, size = 12) {
  return s("svg", { viewBox: "0 0 12 12", width: size, height: size, "aria-hidden": "true" }, levelShape(level, 6, 6, 4.2));
}

export function levelBadge(level, label) {
  if (!level) return h("span", { class: "badge plain" }, "待分析");
  const badge = h("span", { class: "badge" }, levelIcon(level, 11), label || levelLabel(level));
  badge.style.setProperty("--c", LEVEL_COLOR[level] || LEVEL_COLOR.unknown);
  return badge;
}

const STATUS = {
  pending: ["等待分析", "var(--unknown)"],
  processing: ["分析中", "var(--series-1)"],
  done: ["已完成", "var(--good)"],
  failed: ["分析失败", "var(--critical)"],
};

export function statusBadge(status) {
  const [label, color] = STATUS[status] || [status, "var(--unknown)"];
  const content = [];
  if (status === "processing" || status === "pending") content.push(h("span", { class: "spinner", style: { width: "10px", height: "10px", borderWidth: "2px" } }));
  else content.push(h("span", { class: "status-dot", style: { background: color, marginRight: "0" } }));
  return h("span", { class: "badge plain" }, ...content, label);
}

export function severityTag(severity, label) {
  return h("span", { class: `sev sev-${severity}` }, label || enumLabels.severities[severity] || severity);
}

// ---------------------------------------------------------------------------
// 格式化
// ---------------------------------------------------------------------------

export function fmtDateTime(value) {
  if (!value) return "—";
  return String(value).replace("T", " ").slice(0, 16);
}

export function fmtDate(value) {
  if (!value) return "—";
  return String(value).slice(0, 10);
}

export function fmtNum(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPct(ratio, digits = 1) {
  if (ratio === null || ratio === undefined) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function fmtScore(score) {
  return score === null || score === undefined ? "—" : Number(score).toFixed(0);
}

export function fmtBytes(bytes) {
  if (!bytes && bytes !== 0) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtCoord(lat, lon) {
  if (lat === null || lat === undefined || lon === null || lon === undefined) return "—";
  return `${Number(lat).toFixed(6)}, ${Number(lon).toFixed(6)}`;
}

export function debounce(fn, ms = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// ---------------------------------------------------------------------------
// 提示、弹窗、空状态
// ---------------------------------------------------------------------------

export function toast(message, type = "info") {
  const el = h("div", { class: `toast ${type === "error" ? "error" : ""}` }, message);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), type === "error" ? 6000 : 3000);
}

export function modal(title, body, actions = []) {
  return new Promise((resolve) => {
    const close = (value) => {
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = (e) => {
      if (e.key === "Escape") close(null);
    };
    const buttons = actions.map((a) =>
      h("button", { class: `btn ${a.primary ? "primary" : ""} ${a.danger ? "danger" : ""}`, type: "button", onclick: async () => {
        if (a.onClick) {
          const result = await a.onClick();
          if (result === false) return;
          close(result === undefined ? a.value : result);
        } else close(a.value);
      } }, a.label),
    );
    const dialog = h("div", { class: "modal", role: "dialog", "aria-modal": "true" }, h("h2", {}, title), body, h("div", { class: "foot" }, buttons));
    const backdrop = h("div", { class: "modal-backdrop", onclick: (e) => e.target === backdrop && close(null) }, dialog);
    document.addEventListener("keydown", onKey);
    document.body.append(backdrop);
    const first = dialog.querySelector("input, select, textarea, button.primary");
    if (first) first.focus();
  });
}

export function confirmDialog(title, message, confirmLabel = "确定") {
  return modal(title, h("p", { class: "muted", style: { margin: 0 } }, message), [
    { label: "取消", value: false },
    { label: confirmLabel, value: true, primary: true },
  ]);
}

export function emptyState(text, iconPath = ICONS.photo) {
  return h("div", { class: "empty" }, icon(iconPath, 42), text);
}

export function pager(page, pageSize, total, onChange) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return h("div", { class: "pager" }, `共 ${total} 条`);
  return h(
    "div",
    { class: "pager" },
    `共 ${total} 条，第 ${page}/${pages} 页`,
    h("button", { class: "btn small", disabled: page <= 1, onclick: () => onChange(page - 1) }, "上一页"),
    h("button", { class: "btn small", disabled: page >= pages, onclick: () => onChange(page + 1) }, "下一页"),
  );
}

/** 表单字段 */
export function field(label, input, wide = false) {
  return h("label", { class: `field ${wide ? "wide" : ""}` }, label, input);
}
