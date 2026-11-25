// 果树详情：基本信息、健康评分变化曲线、历次巡检记录。

import { api } from "../api.js";
import { lineChart } from "../charts.js";
import {
  ICONS, confirmDialog, emptyState, fmtCoord, fmtDateTime, fmtScore, h, icon, levelBadge, modal, toast,
} from "../utils.js";
import { treeForm } from "./trees.js";

export async function render(view, { ctx, params, isCurrent }) {
  const tree = await api.get(`/trees/${params[0]}`);
  if (!isCurrent()) return;
  ctx.setTitle(`果树 ${tree.code}`);
  const cleanups = [];

  const edit = async () => {
    const form = treeForm(tree);
    await modal("编辑果树", form.el, [
      { label: "取消", value: null },
      { label: "保存", primary: true, onClick: async () => {
        const body = form.values();
        if (!body.code) {
          toast("请填写果树编号", "error");
          return false;
        }
        try {
          await api.patch(`/trees/${tree.id}`, body);
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
    if (!(await confirmDialog("删除果树", `确定删除果树“${tree.code}”吗？已关联的照片会保留。`, "删除"))) return;
    try {
      await api.del(`/trees/${tree.id}`);
      toast("已删除");
      ctx.navigate("#/trees");
    } catch (err) {
      toast(err.message, "error");
    }
  };

  const info = h("div", { class: "card" },
    h("div", { class: "card-head" },
      h("a", { class: "btn small", href: "#/trees" }, icon(ICONS.back), "果树列表"),
      h("div", { class: "actions" }, h("button", { class: "btn small", onclick: edit }, "编辑"),
        h("button", { class: "btn small danger", onclick: remove }, icon(ICONS.trash), "删除"))),
    h("div", { class: "row", style: { gap: "6px 22px" } },
      [["编号", tree.code], ["品种", tree.variety], ["果园", tree.orchard], ["地块", tree.plot],
        ["坐标", tree.latitude !== null ? fmtCoord(tree.latitude, tree.longitude) : "未填写（无法自动关联照片）"],
        ["定植年份", tree.planted_year], ["巡检次数", `${tree.image_count} 次`]]
        .filter(([, v]) => v !== null && v !== undefined && v !== "")
        .map(([k, v]) => h("span", {}, h("span", { class: "muted" }, `${k}：`), v)),
      tree.last_health_level ? h("span", {}, h("span", { class: "muted" }, "最近状态："), levelBadge(tree.last_health_level, tree.last_health_level_label)) : null),
    tree.notes ? h("div", { class: "muted small", style: { marginTop: "6px" } }, tree.notes) : null);

  const history = tree.history;
  const chartBox = h("div", {});
  const chartCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "健康评分变化"), h("span", { class: "sub" }, "点击数据点查看当次照片")),
    history.length ? chartBox : emptyState("还没有关联到这棵树的已分析照片"));

  const table = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "巡检记录"), h("span", { class: "sub" }, `${history.length} 次`)),
    history.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "照片"), h("th", {}, "巡检时间"), h("th", { class: "num" }, "评分"), h("th", {}, "健康状态"),
        h("th", {}, "生长阶段"), h("th", {}, "长势"), h("th", {}, "主要问题"))),
      h("tbody", {}, [...history].reverse().map((p) => h("tr", {},
        h("td", {}, h("a", { href: `#/images/${p.image_id}` }, h("img", { class: "thumb", src: p.thumbnail_url, alt: "巡检照片", loading: "lazy" }))),
        h("td", { class: "nowrap" }, h("a", { href: `#/images/${p.image_id}` }, fmtDateTime(p.observed_at))),
        h("td", { class: "num" }, fmtScore(p.health_score)),
        h("td", {}, levelBadge(p.health_level, p.health_level_label)),
        h("td", {}, p.growth_stage_label || "—"),
        h("td", {}, p.vigor_label || "—"),
        h("td", {}, p.primary_issue || h("span", { class: "muted" }, "无"))))))) : null);

  view.replaceChildren(h("div", { class: "stack" }, info, chartCard, table));
  if (history.length) {
    cleanups.push(lineChart(chartBox, history.map((p) => ({
      x: p.observed_at.slice(0, 10),
      y: p.health_score,
      rows: [["健康状态", p.health_level_label || "—"], ["生长阶段", p.growth_stage_label || "—"], ["主要问题", p.primary_issue || "无"]],
      payload: p,
    })), {
      yMax: 100,
      height: 240,
      valueLabel: "健康评分",
      ariaLabel: `果树 ${tree.code} 健康评分变化`,
      onPointClick: (pt) => ctx.navigate(`#/images/${pt.payload.image_id}`),
    }));
  }
  return () => cleanups.forEach((fn) => fn());
}
