// Tiny API client: bearer token, refresh rotation on 401, readable error messages with the server's correlation id.
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const ACCESS = "nx_access";
const REFRESH = "nx_refresh";

function read(k: string): string | null {
  try { return typeof window === "undefined" ? null : window.localStorage.getItem(k); } catch { return null; }
}
function write(k: string, v: string | null) {
  try { if (v === null) window.localStorage.removeItem(k); else window.localStorage.setItem(k, v); } catch { /* storage blocked */ }
}

export const tokens = {
  access: () => read(ACCESS),
  refresh: () => read(REFRESH),
  set: (t: { access_token: string; refresh_token: string }) => { write(ACCESS, t.access_token); write(REFRESH, t.refresh_token); },
  clear: () => { write(ACCESS, null); write(REFRESH, null); },
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public correlationId?: string) { super(message); }
}

let refreshing: Promise<boolean> | null = null;
async function refresh(): Promise<boolean> {
  const r = read(REFRESH);
  if (!r) return false;
  // one refresh at a time: refresh tokens rotate, and re-using an old one signs the user out everywhere
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(`${API_URL}/api/v1/auth/refresh`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: r }) });
        if (!res.ok) { tokens.clear(); return false; }
        tokens.set(await res.json());
        return true;
      } catch { return false; }
    })().finally(() => { refreshing = null; });  // callers already waiting share this result; later ones start fresh
  }
  return refreshing;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; raw?: boolean } = {}, retry = true): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const t = tokens.access();
  if (t) headers.Authorization = `Bearer ${t}`;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { method: opts.method || "GET", headers, body: opts.body === undefined ? undefined : JSON.stringify(opts.body) });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 401 && retry && (await refresh())) return api<T>(path, opts, false);
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    let code: string | undefined, cid: string | undefined;
    try {
      const j = await res.json();
      code = j.code; cid = j.correlation_id;
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail) && j.detail[0]?.msg) msg = `${j.detail[0].loc?.slice(-1)[0] ?? "field"}: ${j.detail[0].msg}`;
    } catch { /* not json */ }
    if (res.status >= 500 && cid) msg += ` (reference ${cid})`;
    throw new ApiError(res.status, msg, code, cid);
  }
  if (opts.raw) return (await res.text()) as unknown as T;
  return res.json();
}

export async function logout(): Promise<void> {
  const r = tokens.refresh();
  tokens.clear();
  if (r) { try { await fetch(`${API_URL}/api/v1/auth/logout`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: r }) }); } catch { /* offline */ } }
}

export const fmt = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
export const sgn = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "—" : (v >= 0 ? "+" : "") + fmt(v, d));
export const money = (cents: number) => `$${(cents / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
export const when = (v: string | null | undefined) => (v ? new Date(v).toLocaleString("en-US") : "—");
export const ago = (v: string | null | undefined) => {
  if (!v) return "never";
  const s = Math.max(0, Math.round((Date.now() - new Date(v).getTime()) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
};
