export async function api(path, { method = "GET", body, headers = {} } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", ...headers },
      credentials: "include",
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (networkErr) {
    // 네트워크 자체 오류 (서버 다운, CORS 등)
    throw new Error("서버에 연결할 수 없습니다. 네트워크 상태를 확인해 주세요.");
  }

  // 응답 본문 파싱 (JSON 실패해도 계속)
  let data = {};
  try { data = await res.json(); } catch (_) {}

  if (!res.ok) {
    // FastAPI HTTPException → detail 필드
    // 일반 에러 → error 또는 message 필드
    const msg =
      data?.detail ||
      data?.error  ||
      data?.message ||
      `서버 오류 (HTTP ${res.status})`;
    throw new Error(msg);
  }
  return data;
}

export async function getMe() {
  return api("/api/me");
}

export function setToast(msg, type = "ok") {
  // 기존 toast 제거 후 새로 만들기 (CDN Tailwind @apply 파싱 문제 우회)
  const existing = document.getElementById("_toast_el");
  if (existing) existing.remove();

  const el = document.createElement("div");
  el.id = "_toast_el";

  // 인라인 스타일로 완전히 제어 (Tailwind CDN @apply 의존 없음)
  Object.assign(el.style, {
    position:    "fixed",
    top:         "72px",
    left:        "50%",
    transform:   "translateX(-50%)",
    zIndex:      "9999",
    padding:     "12px 20px",
    borderRadius:"12px",
    fontSize:    "14px",
    fontWeight:  "500",
    maxWidth:    "480px",
    whiteSpace:  "pre-wrap",
    boxShadow:   "0 4px 24px rgba(0,0,0,0.5)",
    border:      "1px solid",
    transition:  "opacity 0.3s ease",
    opacity:     "1",
  });

  if (type === "error") {
    el.style.background   = "#1e0a0a";
    el.style.color        = "#f87171";
    el.style.borderColor  = "rgba(239,68,68,0.4)";
  } else {
    el.style.background   = "#0a1e12";
    el.style.color        = "#34d399";
    el.style.borderColor  = "rgba(52,211,153,0.4)";
  }

  el.textContent = msg;
  document.body.appendChild(el);

  // 4초 후 페이드아웃 → 제거
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 350);
  }, 4000);
}

export function escHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function fmt(n, digits = 0) {
  if (n == null || n === "") return "-";
  const num = parseFloat(n);
  if (isNaN(num)) return String(n);
  return num.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(n) {
  if (n == null) return "-";
  const v = parseFloat(n);
  if (isNaN(v)) return "-";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

export function colorPct(n) {
  if (n == null) return "";
  return parseFloat(n) >= 0 ? "text-emerald-400" : "text-red-400";
}
