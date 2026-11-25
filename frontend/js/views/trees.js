// 果树档案：登记果树位置，巡检照片按 GPS 自动关联到最近的果树，形成单株健康档案。

import { api } from "../api.js";
import {
  ICONS, confirmDialog, debounce, emptyState, field, fmtCoord, fmtDateTime, fmtScore, h, icon, levelBadge, modal, pager, toast,
} from "../utils.js";

const PAGE_SIZE = 50;
let keepSearchFocus = false;

export function treeForm(tree = {}) {
  const inputs = {
    code: h("input", { type: "text", value: tree.code || "", maxlength: 60, placeholder: "如：A1-03-15" }),
    variety: h("input", { type: "text", value: tree.variety || "", maxlength: 120, placeholder: "如：红富士" }),
    orchard: h("input", { type: "text", value: tree.orchard || "", maxlength: 120 }),
    plot: h("input", { type: "text", value: tree.plot || "", maxlength: 120 }),
    latitude: h("input", { type: "number", step: "any", min: -90, max: 90, value: tree.latitude ?? "", placeholder: "如：34.302150" }),
    longitude: h("input", { type: "number", step: "any", min: -180, max: 180, value: tree.longitude ?? "", placeholder: "如：108.085710" }),
    planted_year: h("input", { type: "number", min: 1900, max: 2100, value: tree.planted_year ?? "" }),
    notes: h("textarea", { rows: 2, maxlength: 2000 }, tree.notes || ""),
  };
  const el = h("div", {},
    h("div", { class: "form-grid" },
      field("果树编号 *", inputs.code), field("品种", inputs.variety), field("果园", inputs.orchard), field("地块/片区", inputs.plot),
      field("纬度", inputs.latitude), field("经度", inputs.longitude), field("定植年份", inputs.planted_year), field("备注", inputs.notes, true)),
    h("div", { class: "muted small", style: { marginTop: "8px" } }, "坐标使用 WGS-84（与无人机照片一致）。填写坐标后，附近的巡检照片会自动关联到这棵树。"));
  const values = () => {
    const body = {};
    for (const [key, input] of Object.entries(inputs)) {
      const v = input.value.trim();
      if (["latitude", "longitude", "planted_year"].includes(key)) body[key] = v === "" ? null : Number(v);
      else body[key] = v || null;
    }
    return body;
  };
  return { el, values };
}

export async function render(view, { ctx, query, isCurrent }) {
  const page = Number(query.get("page") || 1);
  const q = query.get("q") || "";
  const data = await api.get("/trees", { page, page_size: PAGE_SIZE, q });
  if (!isCurrent()) return;
  const radius = ctx.info ? ctx.info.tree_match_radius_m : 3;

  const go = (params) => {
    const qs = new URLSearchParams({ ...(q ? { q } : {}), ...params });
    for (const [k, v] of [...qs]) if (!v || (k === "page" && v === "1")) qs.delete(k);
    ctx.navigate(`#/trees${qs.toString() ? `?${qs}` : ""}`);
  };
  const search = h("input", { type: "search", placeholder: "搜索编号、品种、果园、地块", value: q, "aria-label": "搜索果树" });
  search.addEventListener("input", debounce(() => {
    keepSearchFocus = true;
    go({ q: search.value.trim(), page: "1" });
  }, 400));

  const addTree = async () => {
    const form = treeForm();
    await modal("登记果树", form.el, [
      { label: "取消", value: null },
      { label: "保存", primary: true, onClick: async () => {
        const body = form.values();
        if (!body.code) {
          toast("请填写果树编号", "error");
          return false;
        }
        try {
          await api.post("/trees", body);
          toast("已登记");
          ctx.refresh();
        } catch (err) {
          toast(err.message, "error");
          return false;
        }
      } },
    ]);
  };

  const csvInput = h("input", { type: "file", accept: ".csv,text/csv", hidden: true });
  csvInput.addEventListener("change", async () => {
    const file = csvInput.files[0];
    csvInput.value = "";
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await api.postForm("/trees/import", form);
      const msg = `导入完成：新增 ${res.created} 棵，更新 ${res.updated} 棵，关联照片 ${res.matched_images} 张`;
      if (res.errors.length) {
        modal("导入结果", h("div", {}, h("p", {}, msg), h("div", { class: "notice warn" }, h("ul", { style: { margin: 0, paddingLeft: "18px" } }, res.errors.map((e) => h("li", {}, e))))), [{ label: "知道了", value: true, primary: true }]);
      } else toast(msg);
      ctx.refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  const showCsvHelp = () => modal("CSV 导入格式", h("div", {},
    h("p", { class: "muted", style: { marginTop: 0 } }, "第一行为表头，支持中文或英文列名；编号已存在时会更新该果树。Excel 另存为 CSV（UTF-8 或 GBK 均可）。"),
    h("pre", { class: "code" }, "编号,纬度,经度,品种,果园,地块,定植年份,备注\nA1-01-01,34.30215,108.08571,红富士,示范果园,A1区,2016,\nA1-01-02,34.30215,108.08578,红富士,示范果园,A1区,2016,")),
  [{ label: "取消", value: false }, { label: "选择 CSV 文件", primary: true, onClick: () => csvInput.click() }]);

  const rematch = async () => {
    try {
      const res = await api.post("/trees/rematch");
      toast(`重新匹配完成：${res.matched_images} 张照片已关联果树`);
      ctx.refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const remove = async (tree) => {
    if (!(await confirmDialog("删除果树", `确定删除果树“${tree.code}”吗？已关联的照片会保留。`, "删除"))) return;
    try {
      await api.del(`/trees/${tree.id}`);
      toast("已删除");
      ctx.refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const toolbar = h("div", { class: "toolbar" }, search, h("span", { class: "spacer" }),
    h("button", { class: "btn", onclick: rematch, title: `按 ${radius} 米范围重新关联照片与果树` }, icon(ICONS.refresh), "重新匹配照片"),
    h("button", { class: "btn", onclick: showCsvHelp }, icon(ICONS.csv), "导入 CSV"), csvInput,
    h("button", { class: "btn primary", onclick: addTree }, icon(ICONS.plus), "登记果树"));

  const table = data.items.length
    ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "编号"), h("th", {}, "品种"), h("th", {}, "果园 / 地块"), h("th", {}, "坐标"),
        h("th", { class: "num" }, "定植年份"), h("th", { class: "num" }, "巡检次数"), h("th", {}, "最近巡检"), h("th", { class: "num" }, "评分"),
        h("th", {}, "健康状态"), h("th", {}, "生长阶段"), h("th", {}, ""))),
      h("tbody", {}, data.items.map((t) => h("tr", {},
        h("td", {}, h("a", { href: `#/trees/${t.id}` }, t.code)),
        h("td", {}, t.variety || "—"),
        h("td", {}, [t.orchard, t.plot].filter(Boolean).join(" / ") || "—"),
        h("td", { class: "num nowrap" }, fmtCoord(t.latitude, t.longitude)),
        h("td", { class: "num" }, t.planted_year ?? "—"),
        h("td", { class: "num" }, t.image_count),
        h("td", { class: "nowrap" }, fmtDateTime(t.last_inspected_at)),
        h("td", { class: "num" }, fmtScore(t.last_health_score)),
        h("td", {}, t.last_health_level ? levelBadge(t.last_health_level, t.last_health_level_label) : h("span", { class: "muted" }, "暂无")),
        h("td", {}, t.last_growth_stage_label || "—"),
        h("td", { class: "nowrap" }, h("a", { class: "btn small", href: `#/trees/${t.id}` }, "档案"), " ",
          h("button", { class: "btn small danger", onclick: () => remove(t) }, "删除")))))))
    : emptyState(q ? "没有符合条件的果树" : "还没有登记果树。可逐棵登记，或用 CSV 批量导入（含编号和经纬度）。", ICONS.tree);

  view.replaceChildren(toolbar,
    h("div", { class: "notice", style: { marginBottom: "16px" } }, `带 GPS 的巡检照片会自动关联到 ${radius} 米范围内最近的果树（倾斜拍摄时按画面中心的地面位置计算），从而形成每棵树的健康档案。`),
    h("div", { class: "card" }, table, pager(page, PAGE_SIZE, data.total, (p) => go({ page: String(p) }))));
  if (keepSearchFocus) {
    keepSearchFocus = false;
    search.focus();
    search.setSelectionRange(search.value.length, search.value.length);
  }
}
