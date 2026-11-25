// 轻量 SVG/HTML 图表：条形列表、折线趋势、位置分布图、分段条。
// 规范：细线条、发丝网格线；状态色始终配合图形与文字；悬停显示提示，数值同时可在文字/表格中读取。

import { h, s, LEVEL_COLOR, LEVEL_ORDER, levelIcon, levelLabel, levelShape, fmtScore } from "./utils.js";

const tooltipEl = () => document.getElementById("chart-tooltip");

export function showTooltip(event, title, rows) {
  const tip = tooltipEl();
  tip.replaceChildren(
    title ? h("div", { class: "tt-title" }, title) : null,
    ...rows.map(([label, value, key]) =>
      h("div", { class: "tt-row" }, key || null, h("strong", {}, value), h("span", { class: "muted" }, label)),
    ),
  );
  tip.hidden = false;
  const pad = 14;
  const { innerWidth: vw, innerHeight: vh } = window;
  const rect = tip.getBoundingClientRect();
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + rect.width > vw - 8) x = event.clientX - rect.width - pad;
  if (y + rect.height > vh - 8) y = event.clientY - rect.height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}

export function hideTooltip() {
  tooltipEl().hidden = true;
}

/**
 * 横向条形列表（单一序列）。
 * items: [{code, label, count}]；scale="total" 时条长表示占比（构成），"max" 时表示相对大小（排名）。
 */
export function barList(items, { colorFor, iconFor, scale = "max", unit = "张" } = {}) {
  if (!items.length) return h("div", { class: "empty" }, "暂无数据");
  const total = items.reduce((sum, it) => sum + it.count, 0);
  const denom = scale === "total" ? total : Math.max(...items.map((it) => it.count));
  return h(
    "div",
    { class: "bars" },
    items.map((it) => {
      const share = total ? it.count / total : 0;
      const fill = h("div", { class: "bar-fill", style: { width: `${denom ? (it.count / denom) * 100 : 0}%` } });
      if (colorFor) fill.style.background = colorFor(it.code);
      const row = h(
        "div",
        { class: "bar-row", tabindex: "0", "aria-label": `${it.label}：${it.count}${unit}，占 ${(share * 100).toFixed(1)}%` },
        h("div", { class: "name" }, iconFor ? iconFor(it.code) : null, it.label),
        h("div", { class: "bar-track" }, fill),
        h("div", { class: "val" }, String(it.count), h("small", {}, `${(share * 100).toFixed(0)}%`)),
      );
      const show = (e) => showTooltip(e, it.label, [["数量", `${it.count} ${unit}`], ["占比", `${(share * 100).toFixed(1)}%`]]);
      row.addEventListener("pointermove", show);
      row.addEventListener("pointerleave", hideTooltip);
      row.addEventListener("focus", () => {
        const r = row.getBoundingClientRect();
        show({ clientX: r.left + r.width / 2, clientY: r.top });
      });
      row.addEventListener("blur", hideTooltip);
      return row;
    }),
  );
}

/** 细分段条：一个整体按健康等级拆分（用于表格行内，旁边配有具体数字）。 */
export function levelSegBar(levelCounts) {
  const total = LEVEL_ORDER.reduce((sum, l) => sum + (levelCounts[l] || 0), 0);
  const label = LEVEL_ORDER.filter((l) => levelCounts[l]).map((l) => `${levelLabel(l)} ${levelCounts[l]}`).join("，");
  const bar = h("div", { class: "segbar", title: label || "暂无分析结果", role: "img", "aria-label": label || "暂无分析结果" });
  if (!total) return bar;
  for (const level of LEVEL_ORDER) {
    const n = levelCounts[level] || 0;
    if (!n) continue;
    bar.append(h("span", { style: { width: `${(n / total) * 100}%`, background: LEVEL_COLOR[level] } }));
  }
  return bar;
}

function niceTicks(max) {
  if (max <= 0) return [0, 1];
  const step = 10 ** Math.floor(Math.log10(max / 4));
  const nice = [1, 2, 5, 10].map((m) => m * step).find((st) => max / st <= 5) || step * 10;
  const ticks = [];
  for (let v = 0; v <= max + 1e-9; v += nice) ticks.push(Number(v.toFixed(6)));
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + nice);
  return ticks;
}

/**
 * 折线图（单一序列，类别型横轴，等间距）。悬停时竖向参考线吸附到最近的数据点。
 * points: [{x, y, rows: [[label, value]], payload}]
 * 返回清理函数。
 */
export function lineChart(container, points, opts = {}) {
  const {
    height = 220,
    yMin = 0,
    yMax: yMaxOpt,
    valueLabel = "数值",
    formatY = (v) => fmtScore(v),
    onPointClick,
    ariaLabel = valueLabel,
  } = opts;
  container.classList.add("chart");
  let active = -1;

  const draw = () => {
    const width = Math.max(280, container.clientWidth || 600);
    const values = points.map((p) => p.y).filter((v) => v !== null && v !== undefined);
    const ticks = yMaxOpt !== undefined ? niceTicksRange(yMin, yMaxOpt) : niceTicks(Math.max(1, ...values));
    const yMax = ticks[ticks.length - 1];
    const m = { top: 12, right: 40, bottom: 28, left: 36 };
    const iw = width - m.left - m.right;
    const ih = height - m.top - m.bottom;
    const n = points.length;
    const xAt = (i) => m.left + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
    const yAt = (v) => m.top + ih - ((v - yMin) / (yMax - yMin || 1)) * ih;

    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": ariaLabel });
    for (const t of ticks) {
      svg.append(s("line", { class: t === yMin ? "baseline" : "gridline", x1: m.left, x2: width - m.right, y1: yAt(t), y2: yAt(t) }));
      svg.append(s("text", { class: "tick", x: m.left - 8, y: yAt(t) + 4, "text-anchor": "end" }, formatY(t)));
    }
    // 横轴标签：根据宽度抽稀，避免重叠
    const maxLabels = Math.max(2, Math.floor(iw / 78));
    const every = Math.ceil(n / maxLabels);
    points.forEach((p, i) => {
      if (i % every !== 0 && i !== n - 1) return;
      if (i !== n - 1 && n - 1 - i < every && i !== 0) return;
      svg.append(s("text", { class: "tick", x: xAt(i), y: height - 8, "text-anchor": n === 1 ? "middle" : i === 0 ? "start" : i === n - 1 ? "end" : "middle" }, p.x));
    });

    // 折线与面积（遇到空值断开）
    const segments = [];
    let current = [];
    points.forEach((p, i) => {
      if (p.y === null || p.y === undefined) {
        if (current.length) segments.push(current);
        current = [];
      } else current.push([xAt(i), yAt(p.y)]);
    });
    if (current.length) segments.push(current);
    for (const seg of segments) {
      if (seg.length > 1) {
        const line = seg.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
        const area = `${line}L${seg[seg.length - 1][0].toFixed(1)} ${yAt(yMin)}L${seg[0][0].toFixed(1)} ${yAt(yMin)}Z`;
        svg.append(s("path", { class: "series-area", d: area }));
        svg.append(s("path", { class: "series-line", d: line }));
      }
    }
    const markers = points.map((p, i) =>
      p.y === null || p.y === undefined ? null : s("circle", { class: "marker", cx: xAt(i), cy: yAt(p.y), r: 4 }),
    );
    markers.forEach((mk) => mk && svg.append(mk));

    // 末端数值标签
    const lastIdx = points.map((p) => p.y).findLastIndex((v) => v !== null && v !== undefined);
    if (lastIdx >= 0) {
      svg.append(s("text", { class: "end-label", x: xAt(lastIdx) + 8, y: yAt(points[lastIdx].y) + 4 }, formatY(points[lastIdx].y)));
    }

    const cross = s("line", { class: "crosshair", y1: m.top, y2: m.top + ih, visibility: "hidden" });
    svg.append(cross);
    const hit = s("rect", {
      x: m.left - 12, y: 0, width: iw + 24, height, fill: "transparent", tabindex: "0",
      style: onPointClick ? "cursor:pointer" : "",
    });
    svg.append(hit);

    const nearest = (clientX) => {
      const r = svg.getBoundingClientRect();
      const x = ((clientX - r.left) / r.width) * width;
      let best = 0;
      let bestDist = Infinity;
      points.forEach((_, i) => {
        const d = Math.abs(xAt(i) - x);
        if (d < bestDist) [best, bestDist] = [i, d];
      });
      return best;
    };
    const focusPoint = (i, event) => {
      active = i;
      const p = points[i];
      cross.setAttribute("x1", xAt(i));
      cross.setAttribute("x2", xAt(i));
      cross.setAttribute("visibility", "visible");
      markers.forEach((mk, j) => mk && mk.setAttribute("r", j === i ? 5.5 : 4));
      const rows = [[valueLabel, p.y === null || p.y === undefined ? "—" : formatY(p.y), h("span", { class: "tt-key" })], ...(p.rows || [])];
      showTooltip(event, p.x, rows);
    };
    const clear = () => {
      cross.setAttribute("visibility", "hidden");
      markers.forEach((mk) => mk && mk.setAttribute("r", 4));
      hideTooltip();
    };
    hit.addEventListener("pointermove", (e) => focusPoint(nearest(e.clientX), e));
    hit.addEventListener("pointerleave", clear);
    hit.addEventListener("blur", clear);
    hit.addEventListener("click", (e) => onPointClick && onPointClick(points[nearest(e.clientX)]));
    hit.addEventListener("keydown", (e) => {
      if (!["ArrowLeft", "ArrowRight", "Enter"].includes(e.key)) return;
      e.preventDefault();
      if (e.key === "Enter" && active >= 0 && onPointClick) return onPointClick(points[active]);
      const next = Math.min(n - 1, Math.max(0, (active < 0 ? 0 : active) + (e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0)));
      const r = svg.getBoundingClientRect();
      focusPoint(next, { clientX: r.left + (xAt(next) / width) * r.width, clientY: r.top + 20 });
    });
    container.replaceChildren(svg);
  };

  draw();
  const ro = new ResizeObserver(() => draw());
  ro.observe(container);
  return () => ro.disconnect();
}

function niceTicksRange(min, max) {
  const step = (max - min) / 4;
  return [0, 1, 2, 3, 4].map((i) => min + step * i);
}

// ---------------------------------------------------------------------------
// 位置分布图：按经纬度绘制照片拍摄位置（形状+颜色表示健康等级），灰色圆圈为果树档案位置
// ---------------------------------------------------------------------------

const M_PER_DEG_LAT = 110_540;
const M_PER_DEG_LON = 111_320;

export function scatterMap(container, { images = [], trees = [] }, { height = 300, onSelect } = {}) {
  container.classList.add("map");
  const all = [...images, ...trees];
  if (!all.length) {
    container.replaceChildren(h("div", { class: "empty" }, "暂无带 GPS 坐标的照片"));
    return () => {};
  }
  const lat0 = all.reduce((sum, p) => sum + p.latitude, 0) / all.length;
  const cosLat = Math.cos((lat0 * Math.PI) / 180);
  const project = (p) => [(p.longitude - all[0].longitude) * M_PER_DEG_LON * cosLat, (p.latitude - all[0].latitude) * M_PER_DEG_LAT];
  const xy = all.map(project);
  let [minX, maxX, minY, maxY] = [Infinity, -Infinity, Infinity, -Infinity];
  for (const [x, y] of xy) {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  const spanX = Math.max(maxX - minX, 10);
  const spanY = Math.max(maxY - minY, 10);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;

  let hovered = null;
  const draw = () => {
    const width = Math.max(260, container.clientWidth || 500);
    const pad = 26;
    const scale = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY); // 像素/米，x、y 等比例
    const toPx = ([x, y]) => [width / 2 + (x - cx) * scale, height / 2 - (y - cy) * scale];

    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": `照片位置分布，共 ${images.length} 个点` });
    svg.append(s("rect", { class: "frame", x: 0.5, y: 0.5, width: width - 1, height: height - 1, rx: 8 }));

    // 比例尺
    const targetM = (width * 0.22) / scale;
    const niceM = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000].find((v) => v >= targetM) || targetM;
    const barPx = niceM * scale;
    const sx = 14;
    const sy = height - 14;
    svg.append(s("path", { class: "scale", fill: "none", d: `M${sx} ${sy - 5}V${sy}H${sx + barPx}V${sy - 5}` }));
    svg.append(s("text", { class: "scale-text", x: sx + barPx + 6, y: sy + 1 }, niceM >= 1000 ? `${niceM / 1000} km` : `${niceM} m`));
    svg.append(s("text", { class: "north", x: width - 18, y: 22, "text-anchor": "middle" }, "N"));
    svg.append(s("path", { fill: "var(--text-2)", d: `M${width - 18} 26l-4 9h8z` }));

    trees.forEach((t) => {
      const [x, y] = toPx(project(t));
      svg.append(s("circle", { class: "tree-pt", cx: x, cy: y, r: 7 }));
    });
    const pts = images.map((p) => {
      const [x, y] = toPx(project(p));
      const level = p.status === "done" ? p.health_level || "unknown" : null;
      const shape = levelShape(level || "unknown", x, y, 5, { class: "pt" });
      if (!level) shape.setAttribute("opacity", "0.6");
      svg.append(shape);
      return { p, x, y, shape };
    });

    const hit = s("rect", { x: 0, y: 0, width, height, fill: "transparent", style: onSelect ? "cursor:pointer" : "" });
    svg.append(hit);
    const nearest = (e) => {
      const r = svg.getBoundingClientRect();
      const mx = ((e.clientX - r.left) / r.width) * width;
      const my = ((e.clientY - r.top) / r.height) * height;
      let best = null;
      let bestD = 24; // 命中半径 24px
      for (const pt of pts) {
        const d = Math.hypot(pt.x - mx, pt.y - my);
        if (d < bestD) [best, bestD] = [pt, d];
      }
      return best;
    };
    hit.addEventListener("pointermove", (e) => {
      const pt = nearest(e);
      if (hovered && hovered !== pt) hovered.shape.classList.remove("hover");
      hovered = pt;
      if (!pt) return hideTooltip();
      pt.shape.classList.add("hover");
      const p = pt.p;
      const level = p.status === "done" ? p.health_level || "unknown" : null;
      showTooltip(e, p.original_filename, [
        ["健康评分", fmtScore(p.health_score), level ? levelIcon(level, 10) : null],
        ["状态", level ? p.health_level_label || levelLabel(level) : "待分析"],
      ]);
    });
    hit.addEventListener("pointerleave", () => {
      if (hovered) hovered.shape.classList.remove("hover");
      hovered = null;
      hideTooltip();
    });
    hit.addEventListener("click", (e) => {
      const pt = nearest(e);
      if (pt && onSelect) {
        hideTooltip();
        onSelect(pt.p);
      }
    });
    container.replaceChildren(svg);
  };
  draw();
  const ro = new ResizeObserver(() => draw());
  ro.observe(container);
  return () => ro.disconnect();
}

/** 健康等级图例：图形 + 文字 */
export function levelLegend(levels = LEVEL_ORDER, extra = []) {
  return h(
    "div",
    { class: "legend" },
    levels.map((l) => h("span", {}, levelIcon(l, 12), levelLabel(l))),
    extra,
  );
}
