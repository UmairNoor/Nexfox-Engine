"use client";
import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { APP_NAME } from "@/lib/brand";

// ---------- toasts ----------
type Toast = { id: number; kind: "ok" | "bad" | "warn" | "info"; title: string; sub?: string };
const ToastCtx = createContext<(t: Omit<Toast, "id">) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
const DOT = { ok: "var(--good)", bad: "var(--bad)", warn: "var(--warn)", info: "var(--info)" };

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { ...t, id }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 3800);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" className="fixed bottom-4 right-4 z-[90] flex max-w-[calc(100vw-32px)] flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className="flex min-w-[250px] items-start gap-2.5 rounded-[10px] border border-line bg-[#1A2230] px-3 py-2.5 shadow-xl">
            <span className="mt-1.5 h-2 w-2 flex-none rounded-full" style={{ background: DOT[t.kind] }} />
            <div><b>{t.title}</b>{t.sub && <div className="mu text-[11.5px]">{t.sub}</div>}</div>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

// ---------- modal ----------
export function Modal({ open, onClose, children, wide }: { open: boolean; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[80] grid place-items-center bg-[rgba(3,6,10,.7)] p-4" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" className={`max-h-[90vh] w-full overflow-auto rounded-[14px] border border-line bg-surface p-5 ${wide ? "max-w-[760px]" : "max-w-[440px]"}`}>{children}</div>
    </div>
  );
}

// ---------- bits ----------
export function Logo({ label = APP_NAME, href = "/" }: { label?: string; href?: string }) {
  return <Link href={href} className="flex items-center gap-2 text-[15px] font-extrabold"><span className="mark">N</span>{label}</Link>;
}

export function Kpi({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "up" | "dn" | "" }) {
  return <div className="card"><div className="kpi-l">{label}</div><div className={`kpi-v ${tone || ""}`}>{value}</div>{sub !== undefined && <div className="kpi-d">{sub}</div>}</div>;
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    running: ["pill-ok", "Running"], paused: ["pill-warn", "Paused"], stopped: ["pill-bad", "Stopped"], offline: ["pill-bad", "Offline"],
    waiting: ["pill-info", "Waiting for EA"], trialing: ["pill-info", "Trial"], active: ["pill-ok", "Active"], past_due: ["pill-warn", "Past due"],
    suspended: ["pill-bad", "Suspended"], canceled: ["pill-bad", "Canceled"], paid: ["pill-ok", "Paid"], failed: ["pill-bad", "Failed"],
    applied: ["pill-ok", "Applied"], pending: ["pill-warn", "Pending"], starting: ["pill-info", "Starting"], stopping: ["pill-warn", "Stopping"],
    expired: ["pill-bad", "Expired"], unverified: ["pill-warn", "Not verified"], ok: ["pill-ok", "Connected"],
    checking: ["pill-info", "Checking…"], auth_failed: ["pill-bad", "Login failed"], terminal_unavailable: ["pill-bad", "Terminal down"],
    account_mismatch: ["pill-bad", "Account mismatch"], symbol_unavailable: ["pill-bad", "Symbol missing"], revoked: ["pill-bad", "Revoked"],
    error: ["pill-bad", "Error"], approved: ["pill-ok", "Approved"], draft: ["pill-info", "Draft"], halted: ["pill-bad", "Halted"],
    retired: ["pill-info", "Retired"], filled: ["pill-ok", "Filled"], rejected: ["pill-bad", "Rejected"], uncertain: ["pill-bad", "Uncertain"],
    simulated: ["pill-info", "Simulated"], reconciled_filled: ["pill-ok", "Reconciled · filled"], reconciled_absent: ["pill-info", "Reconciled · not filled"],
    queued: ["pill-info", "Queued"], passed: ["pill-ok", "Passed"], succeeded: ["pill-ok", "Done"], disabled: ["pill-bad", "Disabled"],
  };
  const [c, l] = map[status] || ["pill-info", status];
  return <span className={`pill dot ${c}`}>{l}</span>;
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return <button type="button" aria-label={label} aria-pressed={on} className={`tog ${on ? "on" : ""}`} onClick={() => onChange(!on)} />;
}

export function Otp({ onComplete }: { onComplete: (code: string) => void }) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  const [v, setV] = useState(["", "", "", "", "", ""]);
  const set = (i: number, d: string) => {
    const n = [...v]; n[i] = d; setV(n);
    if (d && i < 5) refs.current[i + 1]?.focus();
    if (n.every(Boolean)) onComplete(n.join(""));
  };
  return (
    <div className="my-2 mb-4 flex gap-2">
      {v.map((d, i) => (
        <input key={i} ref={(el) => { refs.current[i] = el; }} value={d} inputMode="numeric" maxLength={1} aria-label={`Digit ${i + 1}`} autoFocus={i === 0}
          className="h-[50px] w-[44px] rounded-[9px] border border-line bg-[#0B1119] text-center font-mono text-xl focus:border-gold focus:outline-none"
          onChange={(e) => set(i, e.target.value.replace(/\D/g, "").slice(-1))}
          onKeyDown={(e) => { if (e.key === "Backspace" && !v[i] && i) refs.current[i - 1]?.focus(); }}
          onPaste={(e) => { const t = e.clipboardData.getData("text").replace(/\D/g, ""); if (t.length >= 6) { e.preventDefault(); const n = t.slice(0, 6).split(""); setV(n); onComplete(n.join("")); } }} />
      ))}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return <div className="py-6 text-center"><b className="text-[13px]">{title}</b><div className="mu mt-1.5">{children}</div></div>;
}

// ---------- typed confirmation for high-risk actions ----------
export function ConfirmModal({ open, onClose, title, body, phrase, action, danger = true, onConfirm, reason }:
  { open: boolean; onClose: () => void; title: string; body: React.ReactNode; phrase?: string; action: string; danger?: boolean;
    onConfirm: (reason: string) => Promise<void> | void; reason?: boolean }) {
  const [typed, setTyped] = useState("");
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setTyped(""); setWhy(""); } }, [open]);
  const ok = (!phrase || typed.trim().toUpperCase() === phrase.toUpperCase()) && (!reason || why.trim().length >= 3);
  return (
    <Modal open={open} onClose={onClose}>
      <h3 className="mb-1.5 text-[17px] font-bold">{title}</h3>
      <div className="mu">{body}</div>
      {reason && <div className="field mt-3"><label htmlFor="cm-why">Reason (kept in the audit log)</label><input id="cm-why" className="inp" value={why} onChange={(e) => setWhy(e.target.value)} /></div>}
      {phrase && <div className="field mt-3"><label htmlFor="cm-ph">Type <b className="text-ink">{phrase}</b> to confirm</label><input id="cm-ph" className="inp mono" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} /></div>}
      <div className="mt-3 flex gap-2"><button className="btn btn-g flex-1" onClick={onClose}>Cancel</button>
        <button className={`btn flex-1 ${danger ? "btn-d" : "btn-p"}`} disabled={!ok || busy} onClick={async () => { setBusy(true); try { await onConfirm(why); } finally { setBusy(false); } }}>{busy ? "Working…" : action}</button></div>
    </Modal>
  );
}

export function Field({ label, children, hint, htmlFor }: { label: React.ReactNode; children: React.ReactNode; hint?: React.ReactNode; htmlFor?: string }) {
  return <div className="field"><label htmlFor={htmlFor}>{label}</label>{children}{hint && <span className="mu text-[11px]">{hint}</span>}</div>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <p className="mu py-4" aria-live="polite">{what}…</p>;
}