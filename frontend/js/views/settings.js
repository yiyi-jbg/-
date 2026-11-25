// 系统设置：接口密钥、系统信息、无人机对接说明。

import { api, getApiKey, setApiKey } from "../api.js";
import { field, h, toast } from "../utils.js";

export async function render(view, { ctx }) {
  const info = ctx.info || (await api.get("/system/info"));
  const origin = window.location.origin;

  const keyInput = h("input", { type: "password", value: getApiKey(), placeholder: "与服务端 ORCHARD_API_KEY 一致", autocomplete: "off" });
  const save = () => {
    setApiKey(keyInput.value.trim());
    window.dispatchEvent(new CustomEvent("api-key-changed"));
    toast(keyInput.value.trim() ? "接口密钥已保存在本浏览器" : "已清除接口密钥");
  };
  const keyCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "接口密钥"),
      h("span", { class: "sub" }, info.api_key_required ? "服务端已启用密钥：上传、修改、删除等操作需要填写" : "服务端未启用密钥（内网模式），可以不填")),
    h("div", { class: "row" }, h("div", { style: { flex: "1", minWidth: "220px" } }, field("X-API-Key", keyInput)),
      h("button", { class: "btn primary", style: { alignSelf: "flex-end" }, onclick: save }, "保存")),
    h("div", { class: "muted small", style: { marginTop: "8px" } }, "密钥只保存在当前浏览器中。服务端通过环境变量 ORCHARD_API_KEY 设置密钥。"));

  const infoCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "系统信息")),
    h("dl", { class: "kv" },
      [["系统名称", info.app_name], ["版本", info.version], ["分析算法", info.analyzer],
        ["AI 模型", info.model_loaded ? "已加载自定义 ONNX 模型" : "未启用（使用可见光植被指数规则分析）"],
        ["后台分析线程", `${info.workers} 个`], ["单张照片上限", `${info.max_upload_mb} MB`],
        ["果树自动关联距离", `${info.tree_match_radius_m} 米`], ["接口文档", h("a", { href: "/docs", target: "_blank", rel: "noopener" }, `${origin}/docs`)]]
        .map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));

  const keyHeader = info.api_key_required ? ' \\\n  -H "X-API-Key: 你的密钥"' : "";
  const keyArg = info.api_key_required ? " --api-key 你的密钥" : "";
  const droneCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "无人机对接"), h("span", { class: "sub" }, "三种方式任选其一")),
    h("h3", {}, "方式一：网页上传"),
    h("p", { class: "muted", style: { margin: "4px 0 14px" } }, "航线结束后，把无人机存储卡或大疆司空/DJI Pilot 2 导出的照片在“上传分析”页面批量上传。"),
    h("h3", {}, "方式二：目录监控自动推送（推荐）"),
    h("p", { class: "muted", style: { margin: "4px 0 0" } },
      "在地面站电脑或照片同步服务器上运行仓库中的 scripts/drone_uploader.py（仅依赖 Python 标准库）。它会监控照片目录，发现新照片自动上传；每个子文件夹视为一次巡检任务，断网会自动重试，不会重复上传。"),
    h("pre", { class: "code" }, `python scripts/drone_uploader.py --server ${origin} --dir "D:/DJI/航线照片"${keyArg} --orchard 示范果园 --plot A1区`),
    h("h3", { style: { marginTop: "14px" } }, "方式三：调用推送接口"),
    h("p", { class: "muted", style: { margin: "4px 0 0" } }, "机场、飞控平台或自研系统可直接调用接口，按任务编号（mission_code）归档；同一照片重复推送会被自动识别。"),
    h("pre", { class: "code" }, `curl -X POST ${origin}/api/v1/drone/ingest${keyHeader} \\\n  -F mission_code=20261125-A1 \\\n  -F orchard=示范果园 \\\n  -F files=@DJI_0001.JPG -F files=@DJI_0002.JPG`),
    h("p", { class: "muted", style: { margin: "8px 0 0" } }, "查询结果："),
    h("pre", { class: "code" }, `curl ${origin}/api/v1/drone/missions/20261125-A1`),
    h("div", { class: "notice", style: { marginTop: "14px" } },
      "拍摄建议：云台垂直向下（-90°）、相对高度 10~30 米、光照均匀的时段（上午 9~11 点或下午 3~5 点）、照片保留原始 EXIF/XMP 信息。正射照片可额外估算树冠面积和冠幅。"));

  view.replaceChildren(h("div", { class: "stack" }, keyCard, droneCard, infoCard));
}
