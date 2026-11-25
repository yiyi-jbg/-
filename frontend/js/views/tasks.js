// 巡检任务列表：搜索、新建、删除。

import { api } from "../api.js";
import { levelSegBar } from "../charts.js";
import {
  ICONS, confirmDialog, debounce, emptyState, field, fmtDate, fmtScore, h, icon, modal, pager, toast,
} from "../utils.js";

const PAGE_SIZE = 20;
let keepSearchFocus = false;

export function taskForm(task = {}) {
  const inputs = {
    name: h("input", { type: "text", value: task.name || "", maxlength: 200 }),
    mission_code: h("input", { type: "text", value: task.mission_code || "", maxlength: 120, placeholder: "可选，无人机/机场侧任务编号" }),
    orchard: h("input", { type: "text", value: task.orchard || "", maxlength: 120 }),
    plot: h("input", { type: "text", value: task.plot || "", maxlength: 120 }),
    fruit_type: h("input", { type: "text", value: task.fruit_type || "", maxlength: 60, placeholder: "如：苹果" }),
    flight_date: h("input", { type: "date", value: task.flight_date || "" }),
    flight_height_m: h("input", { type: "number", min: 1, max: 1000, step: "0.1", value: task.flight_height_m ?? "" }),
    drone_model: h("input", { type: "text", value: task.drone_model || "", maxlength: 120 }),
    operator: h("input", { type: "text", value: task.operator || "", maxlength: 60 }),
    notes: h("textarea", { rows: 2, maxlength: 2000 }, task.notes || ""),
  };
  const el = h("div", { class: "form-grid" },
    field("任务名称 *", inputs.name), field("任务编号", inputs.mission_code), field("果园", inputs.orchard),
    field("地块/片区", inputs.plot), field("果树种类", inputs.fruit_type), field("飞行日期", inputs.flight_date),
    field("飞行高度（米）", inputs.flight_height_m), field("无人机型号", inputs.drone_model), field("飞手", inputs.operator),
    field("备注", inputs.notes, true));
  const values = () => {
    const body = {};
    for (const [key, input] of Object.entries(inputs)) {
      const v = input.value.trim();
      if (key === "flight_height_m") body[key] = v ? Number(v) : null;
      else body[key] = v || null;
    }
    return body;
  };
  return { el, values, inputs };
}

export async function render(view, { ctx, query, isCurrent }) {
  const page = Number(query.get("page") || 1);
  const q = query.get("q") || "";
  const data = await api.get("/tasks", { page, page_size: PAGE_SIZE, q });
  if (!isCurrent()) return;

  const go = (params) => {
    const qs = new URLSearchParams({ ...(q ? { q } : {}), ...params });
    for (const [k, v] of [...qs]) if (!v || (k === "page" && v === "1")) qs.delete(k);
    ctx.navigate(`#/tasks${qs.toString() ? `?${qs}` : ""}`);
  };

  const search = h("input", { type: "search", placeholder: "搜索任务名称、果园、地块、编号", value: q, "aria-label": "搜索巡检任务" });
  search.addEventListener("input", debounce(() => {
    keepSearchFocus = true;
    go({ q: search.value.trim(), page: "1" });
  }, 400));

  const createTask = async () => {
    const form = taskForm();
    await modal("新建巡检任务", form.el, [
      { label: "取消", value: null },
      { label: "创建", primary: true, onClick: async () => {
        const body = form.values();
        if (!body.name) {
          toast("请填写任务名称", "error");
          return false;
        }
        try {
          const task = await api.post("/tasks", body);
          toast("任务已创建");
          ctx.navigate(`#/tasks/${task.id}`);
        } catch (err) {
          toast(err.message, "error");
          return false;
        }
      } },
    ]);
  };

  const remove = async (task) => {
    const ok = await confirmDialog("删除巡检任务", `确定删除“${task.name}”及其全部 ${task.stats.image_count} 张照片和分析结果吗？此操作不可恢复。`, "删除");
    if (!ok) return;
    try {
      await api.del(`/tasks/${task.id}`);
      toast("已删除");
      ctx.refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const toolbar = h("div", { class: "toolbar" }, search, h("span", { class: "spacer" }),
    h("button", { class: "btn", onclick: createTask }, icon(ICONS.plus), "新建任务"),
    h("a", { class: "btn primary", href: "#/upload" }, icon(ICONS.upload), "上传照片"));

  const table = data.items.length
    ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {},
        h("th", {}, "任务名称"), h("th", {}, "果园 / 地块"), h("th", {}, "飞行日期"), h("th", {}, "来源"),
        h("th", { class: "num" }, "照片"), h("th", { class: "num" }, "平均评分"), h("th", { class: "num" }, "异常"),
        h("th", {}, "健康构成"), h("th", {}, ""))),
      h("tbody", {}, data.items.map((t) => {
        const st = t.stats;
        return h("tr", {},
          h("td", {}, h("a", { href: `#/tasks/${t.id}` }, t.name), t.mission_code ? h("div", { class: "muted small" }, t.mission_code) : null),
          h("td", {}, [t.orchard, t.plot].filter(Boolean).join(" / ") || "—"),
          h("td", { class: "nowrap" }, fmtDate(t.flight_date || t.created_at)),
          h("td", {}, h("span", { class: "badge plain" }, t.source === "drone" ? "无人机推送" : "网页上传")),
          h("td", { class: "num" }, `${st.analyzed_count}/${st.image_count}`, st.pending_count ? h("div", { class: "muted small" }, `${st.pending_count} 张分析中`) : null),
          h("td", { class: "num" }, fmtScore(st.avg_health_score)),
          h("td", { class: "num" }, st.abnormal_count || "0"),
          h("td", {}, levelSegBar(st.level_counts)),
          h("td", { class: "nowrap" },
            h("a", { class: "btn small", href: `#/tasks/${t.id}` }, "查看"), " ",
            h("button", { class: "btn small danger", onclick: () => remove(t) }, "删除")));
      }))))
    : emptyState(q ? "没有符合条件的巡检任务" : "还没有巡检任务，上传照片时可直接新建任务");

  view.replaceChildren(toolbar, h("div", { class: "card" }, table, pager(page, PAGE_SIZE, data.total, (p) => go({ page: String(p) }))));
  if (keepSearchFocus) {
    keepSearchFocus = false;
    search.focus();
    search.setSelectionRange(search.value.length, search.value.length);
  }
}
