// 后端接口封装：统一处理接口密钥、错误信息和上传进度。

const BASE = "/api/v1";
const KEY_STORAGE = "orchard.apiKey";

export function getApiKey() {
  try {
    return localStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setApiKey(value) {
  try {
    if (value) localStorage.setItem(KEY_STORAGE, value);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* 隐私模式下 localStorage 不可用时忽略 */
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  return headers;
}

function errorMessage(status, data) {
  if (data && typeof data.detail === "string") return data.detail;
  if (data && Array.isArray(data.detail)) {
    return data.detail.map((d) => `${(d.loc || []).slice(1).join(".")} ${d.msg}`.trim()).join("；");
  }
  if (status === 413) return "文件过大";
  return `请求失败（${status}）`;
}

function buildUrl(path, query) {
  let url = BASE + path;
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") params.set(k, v);
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

async function request(method, path, { body, query, form } = {}) {
  const init = { method, headers: authHeaders() };
  if (form) {
    init.body = form;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["Content-Type"] = "application/json";
  }
  let res;
  try {
    res = await fetch(buildUrl(path, query), init);
  } catch {
    throw new ApiError(0, "无法连接服务器，请检查网络或服务是否启动");
  }
  if (!res.ok) {
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* 非 JSON 错误 */
    }
    const err = new ApiError(res.status, errorMessage(res.status, data));
    if (res.status === 401) window.dispatchEvent(new CustomEvent("api-unauthorized"));
    throw err;
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res;
}

export const api = {
  get: (path, query) => request("GET", path, { query }),
  post: (path, body) => request("POST", path, { body }),
  patch: (path, body) => request("PATCH", path, { body }),
  del: (path) => request("DELETE", path),
  postForm: (path, form) => request("POST", path, { form }),
};

/** 用 XHR 上传单个文件，以便显示上传进度。 */
export function uploadWithProgress(path, field, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", BASE + path);
    for (const [k, v] of Object.entries(authHeaders())) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let data = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        if (xhr.status === 401) window.dispatchEvent(new CustomEvent("api-unauthorized"));
        reject(new ApiError(xhr.status, errorMessage(xhr.status, data)));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "网络错误，上传失败"));
    const form = new FormData();
    form.append(field, file, file.name);
    xhr.send(form);
  });
}
