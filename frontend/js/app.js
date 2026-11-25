// 前端入口：基于 hash 的路由、页面框架与全局状态。

import { api, getApiKey } from "./api.js";
import { hideTooltip } from "./charts.js";
import { h, setEnums, toast } from "./utils.js";
import * as dashboard from "./views/dashboard.js";
import * as imageDetail from "./views/image_detail.js";
import * as settings from "./views/settings.js";
import * as taskDetail from "./views/task_detail.js";
import * as tasks from "./views/tasks.js";
import * as treeDetail from "./views/tree_detail.js";
import * as trees from "./views/trees.js";
import * as upload from "./views/upload.js";

const ROUTES = [
  { pattern: /^\/?$/, view: dashboard, nav: "dashboard", title: "总览" },
  { pattern: /^\/upload$/, view: upload, nav: "upload", title: "上传分析" },
  { pattern: /^\/tasks$/, view: tasks, nav: "tasks", title: "巡检任务" },
  { pattern: /^\/tasks\/(\d+)$/, view: taskDetail, nav: "tasks", title: "任务详情" },
  { pattern: /^\/images\/(\d+)$/, view: imageDetail, nav: "tasks", title: "照片分析结果" },
  { pattern: /^\/trees$/, view: trees, nav: "trees", title: "果树档案" },
  { pattern: /^\/trees\/(\d+)$/, view: treeDetail, nav: "trees", title: "果树详情" },
  { pattern: /^\/settings$/, view: settings, nav: "settings", title: "系统设置" },
];

const viewEl = document.getElementById("view");
const titleEl = document.getElementById("page-title");
const actionsEl = document.getElementById("topbar-right");
let cleanup = null;
let renderToken = 0;
let lastPath = null;

export const ctx = {
  info: null,
  navigate(hash) {
    location.hash = hash;
  },
  setTitle(text) {
    titleEl.textContent = text;
    document.title = `${text} - 智慧果园巡检分析系统`;
  },
  setActions(...nodes) {
    actionsEl.replaceChildren(...nodes.filter(Boolean));
  },
  /** 不清空页面直接重新渲染（用于轮询刷新，避免闪烁） */
  refresh() {
    route(true);
  },
};

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = raw.split("?");
  return { path: path || "/", query: new URLSearchParams(qs || "") };
}

async function route(soft = false) {
  const { path, query } = parseHash();
  let match = null;
  let def = null;
  for (const r of ROUTES) {
    match = path.match(r.pattern);
    if (match) {
      def = r;
      break;
    }
  }
  if (!def) {
    location.hash = "#/";
    return;
  }
  if (cleanup) {
    try {
      cleanup();
    } catch {
      /* ignore */
    }
    cleanup = null;
  }
  hideTooltip();
  document.getElementById("sidebar").classList.remove("open");
  for (const a of document.querySelectorAll(".nav a")) a.classList.toggle("active", a.dataset.nav === def.nav);

  const token = ++renderToken;
  // 同一页面仅查询参数变化（筛选、翻页、搜索）时不清空页面，避免闪烁
  if (path === lastPath) soft = true;
  lastPath = path;
  if (!soft) {
    ctx.setTitle(def.title);
    ctx.setActions();
    viewEl.replaceChildren(h("div", { class: "empty" }, h("span", { class: "spinner" }), " 加载中…"));
  }
  try {
    const result = await def.view.render(viewEl, {
      params: match.slice(1),
      query,
      ctx,
      isCurrent: () => token === renderToken,
    });
    if (token !== renderToken) {
      if (typeof result === "function") result();
      return;
    }
    cleanup = typeof result === "function" ? result : null;
  } catch (err) {
    if (token !== renderToken) return;
    console.error(err);
    viewEl.replaceChildren(
      h("div", { class: "card" }, h("div", { class: "notice error" }, `页面加载失败：${err.message || err}`)),
    );
  }
}

function renderSidebarFoot() {
  const info = ctx.info;
  const foot = document.getElementById("sidebar-foot");
  if (!info) return;
  const keyText = info.api_key_required ? (getApiKey() ? "已配置接口密钥" : "未填写接口密钥") : "内网模式（无需密钥）";
  foot.replaceChildren(h("div", {}, `版本 ${info.version}`), h("div", {}, keyText));
}

async function init() {
  document.getElementById("menu-btn").addEventListener("click", () => {
    document.getElementById("sidebar").classList.toggle("open");
  });
  window.addEventListener("api-unauthorized", () => {
    toast("该操作需要接口密钥，请在“系统设置”中填写", "error");
  });
  window.addEventListener("api-key-changed", renderSidebarFoot);
  try {
    ctx.info = await api.get("/system/info");
    setEnums(ctx.info.enums);
    renderSidebarFoot();
  } catch (err) {
    toast(`无法获取系统信息：${err.message}`, "error");
  }
  window.addEventListener("hashchange", () => route());
  route();
}

init();
