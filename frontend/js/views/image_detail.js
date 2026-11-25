// 照片分析结果：原图/标注图、健康评分、生长状态、问题与建议、植被指数、拍摄信息。

import { api } from "../api.js";
import {
  ICONS, LEVEL_COLOR, confirmDialog, enums, fmtBytes, fmtCoord, fmtDateTime, fmtNum, fmtPct, fmtScore, h, icon, levelBadge,
  severityTag, statusBadge, toast,
} from "../utils.js";

function kv(pairs) {
  return h("dl", { class: "kv" }, pairs.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

function card(title, sub, ...body) {
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, title), sub ? h("span", { class: "sub" }, sub) : null), ...body);
}

function viewer(img) {
  const hasOverlay = Boolean(img.overlay_url);
  let mode = hasOverlay ? "overlay" : "original";
  const stage = h("div", {});
  const tabs = h("div", { class: "seg-tabs" });
  const legend = h("div", { class: "overlay-legend" },
    h("span", {}, h("i", { style: { background: "#00e5ff" } }), "识别的树冠轮廓"),
    h("span", {}, h("i", { style: { background: "#ffd600" } }), "黄化叶片"),
    h("span", {}, h("i", { style: { background: "#eb2d2d" } }), "褐变/坏死"),
    h("span", {}, h("i", { style: { background: "#be46ff" } }), "疑似白粉病斑"),
    h("span", {}, h("i", { style: { background: "#555" } }), "压暗区域：非树冠"));
  const draw = () => {
    if (mode === "compare") {
      stage.replaceChildren(h("div", { class: "compare" },
        h("img", { src: img.image_url, alt: "原图" }), h("img", { src: img.overlay_url, alt: "分析标注图" })));
    } else {
      stage.replaceChildren(h("div", { class: "stage" }, h("img", { src: mode === "overlay" ? img.overlay_url : img.image_url, alt: mode === "overlay" ? "分析标注图" : "原图" })));
    }
    legend.hidden = mode === "original";
    for (const b of tabs.children) b.classList.toggle("active", b.dataset.mode === mode);
  };
  const tab = (value, label) => h("button", { type: "button", dataset: { mode: value }, onclick: () => { mode = value; draw(); } }, label);
  if (hasOverlay) tabs.append(tab("overlay", "分析标注图"), tab("original", "原图"), tab("compare", "左右对比"));
  else tabs.append(tab("original", "原图"));
  draw();
  return h("div", { class: "card viewer" }, h("div", { class: "card-head" }, tabs,
    h("div", { class: "actions" }, h("a", { class: "btn small", href: img.image_url, download: img.original_filename }, icon(ICONS.download), "下载原图"))),
  stage, hasOverlay ? legend : null);
}

function healthCard(img, result, actions) {
  if (img.status !== "done" || !result) {
    const body = img.status === "failed"
      ? h("div", { class: "notice error" }, `分析失败：${img.error || "未知错误"}`)
      : h("div", { class: "notice" }, h("span", { class: "spinner" }), " 照片正在分析，结果出来后页面会自动刷新…");
    return card("健康状态", null, h("div", { class: "row", style: { marginBottom: "10px" } }, statusBadge(img.status)), body, actions);
  }
  const hs = result.health;
  const scoreBlock = hs.score === null
    ? h("div", { class: "big" }, "—")
    : h("div", { class: "big" }, fmtScore(hs.score), h("small", {}, " / 100"));
  const meter = hs.score === null ? null : h("div", { class: "meter", style: { marginTop: "12px" }, role: "img", "aria-label": `健康评分 ${fmtScore(hs.score)}` },
    h("span", { style: { width: `${hs.score}%`, background: LEVEL_COLOR[hs.level] } }));
  return card("健康状态", `可信度 ${fmtPct(hs.confidence, 0)}`,
    h("div", { class: "score-hero" }, scoreBlock, h("div", { class: "stack", style: { gap: "6px" } }, levelBadge(hs.level, hs.level_label))),
    meter,
    h("p", { style: { margin: "12px 0 0" } }, result.summary),
    actions);
}

function growthCard(result) {
  const g = result.growth;
  return card("生长状态", null, kv([
    ["生长阶段", h("span", {}, h("strong", {}, g.stage_label), h("div", { class: "muted small" }, g.stage_evidence))],
    ["长势", g.vigor_score !== null ? `${g.vigor_label}（${fmtScore(g.vigor_score)} 分）` : g.vigor_label],
    ["树冠覆盖度", `${fmtPct(g.canopy_coverage)}（画面中树冠所占比例）`],
    ["树冠叶片密度", g.foliage_density !== null ? `${fmtPct(g.foliage_density, 0)}（树冠轮廓内叶片覆盖比例）` : null],
    ["识别树冠数", g.crown_count ? `${g.crown_count} 个` : null],
    ["树冠投影面积", g.canopy_area_m2 !== null ? `${fmtNum(g.canopy_area_m2, 1)} 平方米` : null],
    ["平均冠幅", g.mean_crown_diameter_m !== null ? `${fmtNum(g.mean_crown_diameter_m, 2)} 米` : null],
    ["花朵占比", g.flower_ratio > 0.005 ? fmtPct(g.flower_ratio) : null],
    ["着色果实占比", g.fruit_ratio > 0.002 ? fmtPct(g.fruit_ratio) : null],
  ]), g.canopy_area_m2 === null && g.crown_count ? h("div", { class: "muted small", style: { marginTop: "8px" } }, "提示：照片为正射（云台 -90°）且带有飞行高度时，可估算树冠面积与冠幅。") : null);
}

function issuesCard(result) {
  const issues = result.health.issues;
  if (!issues.length) {
    return card("发现的问题", null, h("div", { class: "notice" }, "未发现明显异常。"));
  }
  return card("发现的问题", `${issues.length} 项`, issues.map((it) => h("div", { class: "issue-item" },
    h("div", { class: "head" }, h("strong", {}, it.name), severityTag(it.severity, it.severity_label), h("span", { class: "badge plain" }, it.category_label || it.category)),
    h("div", { class: "evidence" }, it.evidence),
    it.description ? h("div", { class: "small" }, it.description) : null,
    it.possible_causes.length ? [h("div", { class: "sub-title" }, "可能原因"), h("ul", {}, it.possible_causes.map((c) => h("li", {}, c)))] : null,
    it.suggestions.length ? [h("div", { class: "sub-title" }, "处理建议"), h("ul", {}, it.suggestions.map((c) => h("li", {}, c)))] : null)));
}

function adviceCard(result) {
  return card("管理建议", "综合以上结果", h("ol", { class: "suggest" }, result.health.suggestions.map((sg) => h("li", {}, sg))),
    h("div", { class: "muted small", style: { marginTop: "8px" } }, "说明：结果基于可见光图像的颜色与形态特征自动判断，用于辅助巡检，确诊病害请结合现场核查或取样检测。"));
}

function indicesCard(result) {
  const ix = result.indices;
  const q = result.quality;
  const rows = [
    ["黄化叶片占比", fmtPct(ix.chlorosis_ratio)], ["褐变/坏死占比", fmtPct(ix.necrosis_ratio)],
    ["零散病斑", `${ix.spot_count} 处（每万叶片像素 ${fmtNum(ix.spot_density, 1)} 处）`], ["白色斑块占比", fmtPct(ix.white_ratio)],
    ["ExG 超绿指数", ix.exg ?? "—"], ["GLI 绿叶指数", ix.gli ?? "—"], ["VARI 植被指数", ix.vari ?? "—"], ["NGRDI", ix.ngrdi ?? "—"],
    ["树冠绿度变异系数", ix.canopy_cv ?? "—"],
    ["图像亮度 / 对比度", `${fmtNum(q.brightness, 2)} / ${fmtNum(q.contrast, 2)}`], ["清晰度指标", fmtNum(q.sharpness, 0)],
  ];
  const penalties = Object.entries(result.health.penalties || {});
  return card("指标明细", result.analyzer,
    q.issues.length ? h("div", { class: "notice warn", style: { marginBottom: "10px" } }, "图像质量提示：", q.issues.map((i) => `${i.name}（${i.evidence}）`).join("；"), "。", q.issues[0].suggestions[0] || "") : null,
    kv(rows),
    penalties.length ? h("div", { class: "muted small", style: { marginTop: "8px" } }, "扣分明细：", penalties.map(([k, v]) => `${enums().issues[k] || k.replace(/^model:/, "模型识别 ")} -${v}`).join("，")) : null,
    result.model ? h("div", { class: "notice", style: { marginTop: "10px" } }, `AI 模型识别：${result.model.label}（置信度 ${fmtPct(result.model.confidence, 0)}）`) : null);
}

function metaCard(img) {
  return card("拍摄信息", null, kv([
    ["所属任务", h("a", { href: `#/tasks/${img.task_id}` }, img.task_name || `任务 ${img.task_id}`)],
    ["关联果树", img.tree_id ? h("span", {}, h("a", { href: `#/trees/${img.tree_id}` }, img.tree_code), img.tree_distance_m !== null ? ` （距离 ${fmtNum(img.tree_distance_m, 1)} 米）` : "") : "未关联（无坐标或附近没有登记的果树）"],
    ["拍摄时间", fmtDateTime(img.captured_at)],
    ["上传时间", fmtDateTime(img.uploaded_at)],
    ["拍摄位置", fmtCoord(img.latitude, img.longitude)],
    ["画面中心地面点", img.ground_latitude !== null && (img.ground_latitude !== img.latitude || img.ground_longitude !== img.longitude) ? fmtCoord(img.ground_latitude, img.ground_longitude) : null],
    ["相对高度", img.altitude_rel !== null ? `${fmtNum(img.altitude_rel, 1)} 米` : null],
    ["海拔高度", img.altitude_abs !== null ? `${fmtNum(img.altitude_abs, 1)} 米` : null],
    ["云台俯仰 / 偏航", img.gimbal_pitch !== null ? `${fmtNum(img.gimbal_pitch, 1)}° / ${fmtNum(img.gimbal_yaw, 1)}°` : null],
    ["相机", [img.camera_make, img.camera_model].filter(Boolean).join(" ") || null],
    ["焦距", img.focal_length_mm ? `${fmtNum(img.focal_length_mm, 1)} mm${img.focal_length_35mm ? `（等效 ${fmtNum(img.focal_length_35mm)} mm）` : ""}` : null],
    ["地面分辨率", img.gsd_cm ? `${fmtNum(img.gsd_cm, 2)} 厘米/像素` : null],
    ["尺寸 / 大小", `${img.width}×${img.height} · ${fmtBytes(img.file_size)}`],
    ["文件名", img.original_filename],
  ]));
}

export async function render(view, { ctx, params, isCurrent }) {
  const img = await api.get(`/images/${params[0]}`);
  if (!isCurrent()) return;
  ctx.setTitle(img.original_filename);
  const result = img.result;
  const timers = [];

  const reanalyze = async () => {
    try {
      await api.post(`/images/${img.id}/reanalyze`);
      toast("已重新加入分析队列");
      ctx.refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  };
  const remove = async () => {
    if (!(await confirmDialog("删除照片", `确定删除照片“${img.original_filename}”及其分析结果吗？`, "删除"))) return;
    try {
      await api.del(`/images/${img.id}`);
      toast("已删除");
      ctx.navigate(`#/tasks/${img.task_id}`);
    } catch (err) {
      toast(err.message, "error");
    }
  };
  const actions = h("div", { class: "row", style: { marginTop: "14px" } },
    h("button", { class: "btn small", onclick: reanalyze }, icon(ICONS.refresh), "重新分析"),
    h("button", { class: "btn small danger", onclick: remove }, icon(ICONS.trash), "删除照片"));

  const prev = h("a", { class: "btn small", hidden: true }, icon(ICONS.back), "上一张");
  const next = h("a", { class: "btn small", hidden: true }, "下一张", icon(ICONS.next));
  const position = h("span", { class: "muted small" });
  const nav = h("div", { class: "toolbar" },
    h("a", { class: "btn small", href: `#/tasks/${img.task_id}` }, icon(ICONS.back), img.task_name || "返回任务"),
    h("span", { class: "spacer" }), position, prev, next);

  const right = h("div", { class: "stack" }, healthCard(img, result, actions));
  if (img.status === "done" && result) right.append(growthCard(result), issuesCard(result), adviceCard(result), indicesCard(result));
  right.append(metaCard(img));
  view.replaceChildren(nav, h("div", { class: "detail" }, viewer(img), right));

  // 同一任务内的上一张/下一张（按拍摄时间）
  api.get("/images", { task_id: img.task_id, sort: "captured", page_size: 500 }).then((list) => {
    if (!isCurrent()) return;
    const ids = list.items.map((i) => i.id);
    const idx = ids.indexOf(img.id);
    if (idx < 0) return;
    position.textContent = `${idx + 1} / ${list.total}`;
    if (idx > 0) Object.assign(prev, { href: `#/images/${ids[idx - 1]}`, hidden: false });
    if (idx < ids.length - 1) Object.assign(next, { href: `#/images/${ids[idx + 1]}`, hidden: false });
  }).catch(() => {});

  if (img.status === "pending" || img.status === "processing") {
    timers.push(setTimeout(() => isCurrent() && ctx.refresh(), 2500));
  }
  return () => timers.forEach(clearTimeout);
}
