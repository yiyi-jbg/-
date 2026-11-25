// 上传分析：选择/新建巡检任务 → 拖拽上传照片（显示进度）→ 实时查看分析结果。

import { api, uploadWithProgress } from "../api.js";
import { ICONS, field, fmtScore, h, icon, levelBadge, statusBadge, toast } from "../utils.js";

const ACCEPT = "image/jpeg,image/png,image/tiff,.jpg,.jpeg,.png,.tif,.tiff";
const CONCURRENCY = 3;

export async function render(view, { ctx, query, isCurrent }) {
  const tasks = (await api.get("/tasks", { page_size: 200 })).items;
  if (!isCurrent()) return;
  const timers = [];
  const rows = [];
  let uploading = 0;
  const warnUnload = (e) => {
    e.preventDefault();
    e.returnValue = "";
  };
  let mode = tasks.length ? "existing" : "new";
  const preselect = query.get("task");

  // —— 第一步：巡检任务 ——
  const taskSelect = h("select", {}, tasks.map((t) =>
    h("option", { value: String(t.id), selected: String(t.id) === preselect }, `${t.name}（${t.stats.image_count} 张）`)));
  const today = new Date().toISOString().slice(0, 10);
  const inputs = {
    name: h("input", { type: "text", value: `${today} 果园巡检`, maxlength: 200 }),
    orchard: h("input", { type: "text", placeholder: "如：示范果园", maxlength: 120 }),
    plot: h("input", { type: "text", placeholder: "如：A1 区", maxlength: 120 }),
    fruit_type: h("input", { type: "text", placeholder: "如：苹果、柑橘", maxlength: 60 }),
    flight_date: h("input", { type: "date", value: today }),
    flight_height_m: h("input", { type: "number", min: 1, max: 1000, step: "0.1", placeholder: "照片缺少高度信息时使用" }),
    drone_model: h("input", { type: "text", placeholder: "如：DJI Mavic 3E", maxlength: 120 }),
    operator: h("input", { type: "text", maxlength: 60 }),
  };
  const existingBox = h("div", { class: "form-grid" }, field("巡检任务", taskSelect, true));
  const newBox = h("div", { class: "form-grid" },
    field("任务名称 *", inputs.name), field("果园", inputs.orchard), field("地块/片区", inputs.plot),
    field("果树种类", inputs.fruit_type), field("飞行日期", inputs.flight_date), field("飞行高度（米）", inputs.flight_height_m),
    field("无人机型号", inputs.drone_model), field("飞手", inputs.operator));
  const tabExisting = h("button", { type: "button", disabled: !tasks.length }, "选择已有任务");
  const tabNew = h("button", { type: "button" }, "新建任务");
  const syncMode = () => {
    tabExisting.classList.toggle("active", mode === "existing");
    tabNew.classList.toggle("active", mode === "new");
    existingBox.hidden = mode !== "existing";
    newBox.hidden = mode !== "new";
  };
  tabExisting.addEventListener("click", () => { mode = "existing"; syncMode(); });
  tabNew.addEventListener("click", () => { mode = "new"; syncMode(); });
  syncMode();

  async function ensureTask() {
    if (mode === "existing") return Number(taskSelect.value);
    const name = inputs.name.value.trim();
    if (!name) throw new Error("请填写任务名称");
    const body = { name };
    for (const key of ["orchard", "plot", "fruit_type", "drone_model", "operator", "flight_date"]) {
      const v = inputs[key].value.trim();
      if (v) body[key] = v;
    }
    if (inputs.flight_height_m.value) body.flight_height_m = Number(inputs.flight_height_m.value);
    const task = await api.post("/tasks", body);
    taskSelect.prepend(h("option", { value: String(task.id) }, `${task.name}（0 张）`));
    taskSelect.value = String(task.id);
    tabExisting.disabled = false;
    mode = "existing";
    syncMode();
    toast(`已创建巡检任务“${task.name}”`);
    return task.id;
  }

  // —— 第二步：选择照片 ——
  const fileInput = h("input", { type: "file", multiple: true, accept: ACCEPT, hidden: true });
  const dropzone = h("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": "选择要上传的照片" },
    icon(ICONS.upload, 40),
    h("div", { class: "big" }, "拖拽无人机照片到这里，或点击选择文件"),
    h("div", { class: "muted small" }, "支持 JPG / PNG / TIFF，可一次选择整个航线的照片；GPS、飞行高度、云台角度等信息会自动读取"));
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
    handleFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener("change", () => {
    handleFiles(fileInput.files);
    fileInput.value = "";
  });

  // —— 第三步：上传与结果 ——
  const list = h("div", { class: "upload-list" });
  const summary = h("div", { class: "muted small" });
  const resultLink = h("a", { class: "btn", hidden: true }, "查看任务分析结果");
  const resultCard = h("div", { class: "card", hidden: true },
    h("div", { class: "card-head" }, h("h2", {}, "上传与分析进度"), summary, h("div", { class: "actions" }, resultLink)), list);

  function updateSummary() {
    const total = rows.length;
    const uploaded = rows.filter((r) => r.imageId).length;
    const analyzed = rows.filter((r) => r.analyzed).length;
    const rejected = rows.filter((r) => r.rejected).length;
    summary.textContent = `共 ${total} 张：已上传 ${uploaded}，分析完成 ${analyzed}${rejected ? `，未接收 ${rejected}` : ""}`;
  }

  function showResult(row, img) {
    if (img.status === "done") {
      row.analyzed = true;
      row.state.replaceChildren(h("a", { href: `#/images/${img.id}`, class: "row", style: { gap: "6px" } },
        levelBadge(img.health_level, img.health_level_label),
        img.health_score !== null ? h("strong", {}, `${fmtScore(img.health_score)} 分`) : null));
    } else if (img.status === "failed") {
      row.analyzed = true;
      row.state.replaceChildren(h("a", { href: `#/images/${img.id}`, title: img.error || "" }, statusBadge("failed")));
    } else {
      row.state.replaceChildren(statusBadge(img.status));
    }
  }

  async function uploadOne(taskId, row) {
    row.state.replaceChildren("上传中…");
    try {
      const res = await uploadWithProgress(`/tasks/${taskId}/images`, "files", row.file, (p) => {
        row.bar.style.width = `${Math.round(p * 100)}%`;
      });
      row.bar.style.width = "100%";
      const item = res.items[0];
      if (item.status === "rejected") {
        row.rejected = true;
        row.state.replaceChildren(h("span", { class: "badge plain", title: item.error }, `未接收：${item.error}`));
      } else {
        row.imageId = item.image_id;
        row.state.replaceChildren(item.status === "duplicate" ? h("span", { class: "badge plain" }, "已上传过") : statusBadge("pending"));
      }
    } catch (err) {
      row.rejected = true;
      row.state.replaceChildren(h("span", { class: "badge plain", title: err.message }, `失败：${err.message}`));
    }
    updateSummary();
  }

  async function poll(taskId) {
    if (!isCurrent()) return;
    const waiting = rows.filter((r) => r.taskId === taskId && r.imageId && !r.analyzed);
    if (!waiting.length) return;
    try {
      const page = await api.get("/images", { task_id: taskId, page_size: 500 });
      const byId = new Map(page.items.map((img) => [img.id, img]));
      for (const row of waiting) {
        const img = byId.get(row.imageId);
        if (img) showResult(row, img);
      }
    } catch {
      /* 网络波动时下一轮重试 */
    }
    updateSummary();
    if (rows.some((r) => r.taskId === taskId && r.imageId && !r.analyzed)) timers.push(setTimeout(() => poll(taskId), 2000));
  }

  async function handleFiles(fileList) {
    const files = [...fileList].filter((f) => /\.(jpe?g|png|tiff?)$/i.test(f.name) || f.type.startsWith("image/"));
    if (!files.length) {
      toast("请选择 JPG、PNG 或 TIFF 格式的照片", "error");
      return;
    }
    let taskId;
    try {
      taskId = await ensureTask();
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    resultCard.hidden = false;
    resultLink.href = `#/tasks/${taskId}`;
    resultLink.hidden = false;
    const batch = files.map((file) => {
      const bar = h("span");
      const state = h("div", { class: "state" }, "等待上传");
      list.append(h("div", { class: "upload-item" }, h("div", { class: "fn", title: file.name }, file.name), h("div", { class: "progress" }, bar), state));
      const row = { file, bar, state, taskId, imageId: null, analyzed: false, rejected: false };
      rows.push(row);
      return row;
    });
    updateSummary();
    // 上传过程中在站内切换页面不会中断上传；关闭浏览器标签页前给出提醒
    uploading += 1;
    window.addEventListener("beforeunload", warnUnload);
    let next = 0;
    const runner = async () => {
      while (next < batch.length) await uploadOne(taskId, batch[next++]);
    };
    try {
      await Promise.all(Array.from({ length: Math.min(CONCURRENCY, batch.length) }, runner));
    } finally {
      uploading -= 1;
      if (!uploading) window.removeEventListener("beforeunload", warnUnload);
    }
    poll(taskId);
  }

  view.replaceChildren(
    h("div", { class: "stack" },
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "1. 选择巡检任务"), h("span", { class: "sub" }, "一次飞行/一条航线对应一个任务"),
          h("div", { class: "actions" }, h("div", { class: "seg-tabs" }, tabExisting, tabNew))),
        existingBox, newBox),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "2. 上传照片"), h("span", { class: "sub" }, "照片上传完成后在后台自动分析，期间可以浏览其他页面")),
        dropzone, fileInput),
      resultCard,
      h("div", { class: "notice" }, "提示：需要无人机机场或地面站自动推送照片时，请参考“系统设置 → 无人机对接”。"),
    ),
  );
  return () => timers.forEach(clearTimeout);
}
