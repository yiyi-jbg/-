// 巡检任务详情：任务信息、统计、照片网格（按健康等级筛选）、拍摄位置分布。

import { api } from "../api.js";
import { levelLegend, scatterMap } from "../charts.js";
import {
  ICONS, LEVEL_ORDER, confirmDialog, emptyState, fmtDate, fmtDateTime, fmtNum, fmtScore, h, icon, levelBadge, levelIcon,
  levelLabel, modal, pager, statusBadge, toast,
} from "../utils.js";
import { taskForm } from "./tasks.js";

const PAGE_SIZE = 24;

function kpi(label, value, unit, foot) {
  return h("div", { class: "card kpi" }, h("div", { class: "label" }, label),
    h("div", { class: "value" }, value, unit ? h("small", {}, unit) : null), foot ? h("div", { class: "foot" }, foot) : null);
}

export function imageCard(img) {
  const done = img.status === "done";
  return h("a", { class: "img-card", href: `#/images/${img.id}` },
    h("div", { class: "ph" },
      h("img", { src: img.thumbnail_url, alt: img.original_filename, loading: "lazy" }),
      done && img.health_score !== null ? h("span", { class: "score" }, fmtScore(img.health_score)) : null),
    h("div", { class: "body" },
      h("div", { class: "row", style: { gap: "6px" } },
        done ? levelBadge(img.health_level, img.health_level_label) : statusBadge(img.status),
        img.growth_stage_label ? h("span", { class: "badge plain" }, img.growth_stage_label) : null),
      h("div", { class: "issue" }, done ? (img.primary_issue ? `主要问题：${img.primary_issue}` : "未发现明显异常") : img.status === "failed" ? (img.error || "分析失败") : "等待分析结果…"),
      h("div", { class: "fname", title: img.original_filename },
        img.tree_code ? `果树 ${img.tree_code} · ` : "", fmtDateTime(img.captured_at || img.uploaded_at))));
}

export async function render(view, { ctx, params, query, isCurrent }) {
  const taskId = params[0];
  const page = Number(query.get("page") || 1);
  const level = query.get("level") || "";
  const sort = query.get("sort") || "captured";
  const levelFilter = level === "failed" ? { status: "failed" } : { health_level: level };
  const [task, images, mapData] = await Promise.all([
    api.get(`/tasks/${taskId}`),
    api.get("/images", { task_id: taskId, page, page_size: PAGE_SIZE, sort, ...levelFilter }),
    api.get("/stats/map", { task_id: taskId }),
  ]);
  if (!isCurrent()) return;
  const cleanups = [];
  ctx.setTitle(task.name);

  const go = (changes) => {
    const qs = new URLSearchParams({ level, sort, page: String(page), ...changes });
    for (const [k, v] of [...qs]) if (!v || (k === "page" && v === "1") || (k === "sort" && v === "captured")) qs.delete(k);
    ctx.navigate(`#/tasks/${taskId}${qs.toString() ? `?${qs}` : ""}`);
  };

  const st = task.stats;
  const reanalyze = async () => {
    if (!(await confirmDialog("重新分析", `将使用当前分析算法重新分析本任务的 ${st.image_count} 张照片，确定继续吗？`, "重新分析"))) return;
    try {
      const res = await api.post(`/tasks/${taskId}/reanalyze`);
      toast(`已加入分析队列：${res.queued} 张`);
      ctx.refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  };
  const edit = async () => {
    const form = taskForm(task);
    await modal("编辑巡检任务", form.el, [
      { label: "取消", value: null },
      { label: "保存", primary: true, onClick: async () => {
        const body = form.values();
        if (!body.name) {
          toast("请填写任务名称", "error");
          return false;
        }
        try {
          await api.patch(`/tasks/${taskId}`, body);
          toast("已保存");
          ctx.refresh();
        } catch (err) {
          toast(err.message, "error");
          return false;
        }
      } },
    ]);
  };
  const remove = async () => {
    if (!(await confirmDialog("删除巡检任务", `确定删除“${task.name}”及其全部 ${st.image_count} 张照片吗？此操作不可恢复。`, "删除"))) return;
    try {
      await api.del(`/tasks/${taskId}`);
      toast("已删除");
      ctx.navigate("#/tasks");
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const meta = [
    ["果园", task.orchard], ["地块", task.plot], ["果树种类", task.fruit_type],
    ["飞行日期", task.flight_date ? fmtDate(task.flight_date) : null], ["飞行高度", task.flight_height_m ? `${task.flight_height_m} 米` : null],
    ["无人机", task.drone_model], ["飞手", task.operator], ["任务编号", task.mission_code],
    ["来源", task.source === "drone" ? "无人机推送" : "网页上传"], ["创建时间", fmtDateTime(task.created_at)],
  ].filter(([, v]) => v);

  const header = h("div", { class: "card" },
    h("div", { class: "card-head" },
      h("a", { href: "#/tasks", class: "btn small" }, icon(ICONS.back), "任务列表"),
      h("div", { class: "actions" },
        h("a", { class: "btn primary small", href: `#/upload?task=${taskId}` }, icon(ICONS.upload), "继续上传"),
        h("a", { class: "btn small", href: `/api/v1/tasks/${taskId}/export.csv`, download: "" }, icon(ICONS.download), "导出 CSV"),
        h("button", { class: "btn small", onclick: reanalyze }, icon(ICONS.refresh), "重新分析"),
        h("button", { class: "btn small", onclick: edit }, "编辑"),
        h("button", { class: "btn small danger", onclick: remove }, icon(ICONS.trash), "删除"))),
    h("div", { class: "row", style: { gap: "6px 22px" } }, meta.map(([k, v]) => h("span", {}, h("span", { class: "muted" }, `${k}：`), v))),
    task.notes ? h("div", { class: "muted small", style: { marginTop: "6px" } }, task.notes) : null);

  const kpis = h("div", { class: "kpis", style: { marginTop: "16px" } },
    kpi("照片总数", fmtNum(st.image_count), "张", st.pending_count ? `${st.pending_count} 张分析中` : `已全部分析${st.failed_count ? `，${st.failed_count} 张失败` : ""}`),
    kpi("平均健康评分", fmtScore(st.avg_health_score), "分"),
    kpi("健康照片", fmtNum(st.level_counts.healthy || 0), "张"),
    kpi("异常照片", fmtNum(st.abnormal_count), "张", LEVEL_ORDER.slice(1, 4).filter((l) => st.level_counts[l]).map((l) => `${levelLabel(l)} ${st.level_counts[l]}`).join("，") || "无"));

  const chip = (value, label, count, lv) => h("button", { class: `chip ${level === value ? "active" : ""}`, type: "button", onclick: () => go({ level: value, page: "1" }) },
    lv ? levelIcon(lv, 10) : null, label, count !== undefined ? h("span", { class: "muted" }, String(count)) : null);
  const filters = h("div", { class: "toolbar" },
    h("div", { class: "chips" },
      chip("", "全部", st.image_count),
      LEVEL_ORDER.map((l) => (st.level_counts[l] ? chip(l, levelLabel(l), st.level_counts[l], l) : null)),
      st.failed_count ? chip("failed", "分析失败", st.failed_count) : null),
    h("span", { class: "spacer" }),
    h("select", { "aria-label": "排序", onchange: (e) => go({ sort: e.target.value, page: "1" }) },
      [["captured", "按拍摄时间"], ["score_asc", "评分从低到高"], ["score_desc", "评分从高到低"], ["newest", "最近上传"]]
        .map(([v, t]) => h("option", { value: v, selected: v === sort }, t))));

  const gallery = h("div", { class: "card" },
    filters,
    images.items.length ? h("div", { class: "img-grid" }, images.items.map(imageCard)) : emptyState(st.image_count ? "没有符合筛选条件的照片" : "该任务还没有照片"),
    pager(page, PAGE_SIZE, images.total, (p) => go({ page: String(p) })));

  const mapBox = h("div", {});
  const mapCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "拍摄位置"), h("span", { class: "sub" }, `${mapData.images.length} 个带坐标的拍摄点`)),
    mapBox, h("div", { class: "chart-foot" }, levelLegend()));

  view.replaceChildren(header, kpis, h("div", { class: "grid split" }, gallery, mapCard));
  cleanups.push(scatterMap(mapBox, mapData, { height: 340, onSelect: (p) => ctx.navigate(`#/images/${p.image_id}`) }));

  if (st.pending_count) {
    const timer = setTimeout(() => isCurrent() && ctx.refresh(), 3000);
    cleanups.push(() => clearTimeout(timer));
  }
  return () => cleanups.forEach((fn) => fn());
}
