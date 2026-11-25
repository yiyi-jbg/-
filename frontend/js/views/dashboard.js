// 总览：关键指标、健康等级/生长阶段/常见问题分布、健康趋势、位置分布、需关注照片。

import { api } from "../api.js";
import { barList, levelLegend, lineChart, scatterMap } from "../charts.js";
import {
  ICONS, LEVEL_COLOR, emptyState, fmtDateTime, fmtNum, fmtPct, fmtScore, h, icon, levelBadge, levelIcon, s,
} from "../utils.js";

export function treeLegendItem() {
  return h("span", {}, s("svg", { width: 12, height: 12, viewBox: "0 0 12 12", "aria-hidden": "true" },
    s("circle", { cx: 6, cy: 6, r: 4.5, fill: "none", stroke: "var(--axis)", "stroke-width": 1.5 })), "果树档案位置");
}

function kpi(label, value, foot, unit) {
  return h("div", { class: "card kpi" }, h("div", { class: "label" }, label),
    h("div", { class: "value" }, value, unit ? h("small", {}, unit) : null), foot ? h("div", { class: "foot" }, foot) : null);
}

/** 同一位置（约 1 米内）只保留最近一次巡检的照片 */
function latestPerLocation(points) {
  const seen = new Set();
  return points.filter((p) => {
    const key = `${p.latitude.toFixed(5)},${p.longitude.toFixed(5)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function trendTable(trend) {
  return h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, h("th", {}, "巡检日期"), h("th", { class: "num" }, "照片数"), h("th", { class: "num" }, "平均健康评分"), h("th", { class: "num" }, "异常照片"))),
    h("tbody", {}, trend.map((t) => h("tr", {},
      h("td", {}, t.date), h("td", { class: "num" }, t.image_count),
      h("td", { class: "num" }, t.avg_health_score ?? "—"), h("td", { class: "num" }, t.abnormal_count)))),
  ));
}

export async function render(view, { ctx, query, isCurrent }) {
  const taskId = query.get("task") || "";
  const [tasksPage, overview, mapData] = await Promise.all([
    api.get("/tasks", { page_size: 200 }),
    api.get("/stats/overview", { task_id: taskId, days: 30 }),
    api.get("/stats/map", { task_id: taskId }),
  ]);
  if (!isCurrent()) return;
  const cleanups = [];

  // —— 筛选行 ——
  const taskSelect = h("select", { "aria-label": "按巡检任务筛选", onchange: (e) => ctx.navigate(e.target.value ? `#/?task=${e.target.value}` : "#/") },
    h("option", { value: "" }, "全部巡检任务"),
    tasksPage.items.map((t) => h("option", { value: String(t.id), selected: String(t.id) === taskId }, t.name)));
  const filterRow = h("div", { class: "toolbar" }, taskSelect,
    overview.pending_count ? h("span", { class: "muted small" }, h("span", { class: "spinner" }), ` ${overview.pending_count} 张照片分析中，页面会自动刷新`) : null,
    h("span", { class: "spacer" }),
    h("a", { class: "btn primary", href: "#/upload" }, icon(ICONS.upload), "上传巡检照片"));

  if (!overview.image_count) {
    view.replaceChildren(filterRow, h("div", { class: "card" }, emptyState("还没有巡检照片。点击右上角“上传巡检照片”，或配置无人机自动推送。")));
    return;
  }

  // —— 关键指标 ——
  const abnormalShare = overview.analyzed_count ? overview.abnormal_count / overview.analyzed_count : 0;
  const kpis = h("div", { class: "kpis" },
    kpi("巡检任务", fmtNum(overview.task_count), `登记果树 ${fmtNum(overview.tree_count)} 棵`),
    kpi("巡检照片", fmtNum(overview.image_count), `已分析 ${fmtNum(overview.analyzed_count)}，待分析 ${fmtNum(overview.pending_count)}${overview.failed_count ? `，失败 ${overview.failed_count}` : ""}`),
    kpi("平均健康评分", fmtScore(overview.avg_health_score), "满分 100，85 分及以上为健康", "分"),
    kpi("异常照片", fmtNum(overview.abnormal_count), `占已分析照片 ${fmtPct(abnormalShare)}`, "张"),
  );

  // —— 分布 ——
  const levelCard = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "健康等级分布"), h("span", { class: "sub" }, "按照片数")),
    barList(overview.health_levels, { scale: "total", colorFor: (c) => LEVEL_COLOR[c], iconFor: (c) => levelIcon(c, 11) }));
  const stageCard = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "生长阶段分布"), h("span", { class: "sub" }, "按物候期")),
    barList(overview.growth_stages, { scale: "total" }));
  const issueCard = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "常见问题"), h("span", { class: "sub" }, "出现该问题的照片数")),
    overview.top_issues.length ? barList(overview.top_issues, { scale: "max" }) : h("div", { class: "empty" }, "未发现异常"));

  // —— 健康趋势 ——
  const trendBox = h("div", {});
  let showTable = false;
  const toggle = h("button", { class: "linkish", type: "button" }, "查看数据表");
  const trendCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "平均健康评分趋势"), h("span", { class: "sub" }, "按巡检日期，最近 30 个巡检日"), h("div", { class: "actions" }, toggle)),
    trendBox);
  let disposeTrend = null;
  const drawTrend = () => {
    if (disposeTrend) disposeTrend();
    disposeTrend = null;
    if (!overview.trend.length) {
      trendBox.replaceChildren(h("div", { class: "empty" }, "暂无已完成分析的照片"));
    } else if (showTable) {
      trendBox.replaceChildren(trendTable(overview.trend));
    } else {
      disposeTrend = lineChart(trendBox, overview.trend.map((t) => ({
        x: t.date.slice(5),
        y: t.avg_health_score,
        rows: [["照片数", `${t.image_count} 张`], ["异常照片", `${t.abnormal_count} 张`]],
      })), { yMax: 100, valueLabel: "平均健康评分", ariaLabel: "平均健康评分趋势折线图" });
    }
    toggle.textContent = showTable ? "查看图表" : "查看数据表";
  };
  toggle.addEventListener("click", () => {
    showTable = !showTable;
    drawTrend();
  });
  cleanups.push(() => disposeTrend && disposeTrend());

  // —— 位置分布 ——
  const points = taskId ? mapData.images : latestPerLocation(mapData.images);
  const mapBox = h("div", {});
  const mapCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "巡检位置分布"), h("span", { class: "sub" }, taskId ? "本次任务的拍摄点" : "各位置最近一次巡检结果")),
    mapBox,
    h("div", { class: "chart-foot" }, levelLegend(undefined, mapData.trees.length ? [treeLegendItem()] : [])));

  // —— 需关注 ——
  const alerts = overview.recent_alerts;
  const alertCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "需关注的照片"), h("span", { class: "sub" }, "中度及严重异常，按评分从低到高")),
    alerts.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "照片"), h("th", {}, "巡检任务"), h("th", {}, "果树"), h("th", {}, "拍摄时间"), h("th", { class: "num" }, "评分"), h("th", {}, "健康等级"), h("th", {}, "主要问题"))),
      h("tbody", {}, alerts.map((a) => h("tr", {},
        h("td", {}, h("a", { href: `#/images/${a.id}` }, h("img", { class: "thumb", src: a.thumbnail_url, alt: a.original_filename, loading: "lazy" }))),
        h("td", {}, h("a", { href: `#/tasks/${a.task_id}` }, a.task_name || `任务 ${a.task_id}`)),
        h("td", {}, a.tree_id ? h("a", { href: `#/trees/${a.tree_id}` }, a.tree_code) : "—"),
        h("td", { class: "nowrap" }, fmtDateTime(a.captured_at || a.uploaded_at)),
        h("td", { class: "num" }, fmtScore(a.health_score)),
        h("td", {}, levelBadge(a.health_level, a.health_level_label)),
        h("td", {}, h("a", { href: `#/images/${a.id}` }, a.primary_issue || "查看详情"))))))) : h("div", { class: "empty" }, "暂无中度及以上异常，果园整体状况良好"));

  view.replaceChildren(
    filterRow,
    kpis,
    h("div", { class: "grid cols-3", style: { marginBottom: "16px" } }, levelCard, stageCard, issueCard),
    h("div", { class: "grid split", style: { marginBottom: "16px" } }, trendCard, mapCard),
    alertCard,
  );
  drawTrend();
  cleanups.push(scatterMap(mapBox, { images: points, trees: mapData.trees }, { height: 280, onSelect: (p) => ctx.navigate(`#/images/${p.image_id}`) }));

  if (overview.pending_count) {
    const timer = setTimeout(() => isCurrent() && ctx.refresh(), 5000);
    cleanups.push(() => clearTimeout(timer));
  }
  return () => cleanups.forEach((fn) => fn());
}
