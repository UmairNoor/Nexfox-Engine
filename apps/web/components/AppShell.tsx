"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { isStaff, useAuth } from "@/lib/auth";
import { Logo } from "./ui";

const IC: Record<string, string> = {
  dash: "M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z",
  bot: "M5 8h14v11H5zM12 4v4M9 13h.01M15 13h.01M9 17h6",
  acct: "M3 4h18v14H3zM8 21h8M12 18v3",
  perf: "M3 20h18M6 16l4-5 3 3 5-7",
  trades: "M4 6h16M4 12h16M4 18h10",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 21h4",
  card: "M2 5h20v14H2zM2 10h20",
  gear: "M12 8.8a3.2 3.2 0 1 0 0 6.4 3.2 3.2 0 0 0 0-6.4zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1",
  help: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
};
export const Icon = ({ k, className = "h-4 w-4" }: { k: string; className?: string }) => (
  <svg viewBox="0 0 24 24" className={className} aria-hidden="true"><path d={IC[k]} fill="none" stroke="currentColor" strokeWidth="1.8" /></svg>
);
const NAV = [
  ["/app/dashboard", "Dashboard", "dash"], ["/app/bots", "Bots", "bot"], ["/app/accounts", "MT5 accounts", "acct"],
  ["/app/performance", "Performance", "perf"], ["/app/trades", "Trades", "trades"], ["/app/alerts", "Alerts", "bell"],
  ["/app/billing", "Billing", "card"], ["/app/settings", "Settings", "gear"], ["/app/support", "Help", "help"],
] as const;

function session() {
  const h = new Date().getUTCHours();
  if (h >= 7 && h < 12) return "London session";
  if (h >= 12 && h < 16) return "London–NY overlap";
  if (h >= 16 && h < 21) return "New York session";
  return "Asian session";
}

export default function AppShell({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { me, loading, signOut } = useAuth();

  useEffect(() => { if (!loading && !me) router.replace(`/login?next=${encodeURIComponent(path)}`); }, [loading, me, path, router]);
  if (loading || !me) return <div className="mu grid min-h-screen place-items-center">Loading…</div>;
  const ent = me.entitlement;
  const trialDays = ent?.trial && ent.trial_ends_at ? Math.max(0, Math.ceil((new Date(ent.trial_ends_at).getTime() - Date.now()) / 86400000)) : null;

  return (
    <div className="grid min-h-screen md:grid-cols-[212px_1fr]">
      <aside className="sticky top-0 hidden h-screen flex-col gap-1 border-r border-line bg-rail px-3 py-4 md:flex">
        <div className="mx-1.5 mb-4"><Logo href="/app/dashboard" /></div>
        {NAV.map(([href, label, ic]) => {
          const on = path.startsWith(href);
          return <Link key={href} href={href} aria-current={on ? "page" : undefined} className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 font-semibold ${on ? "bg-surface2 text-ink" : "text-muted hover:bg-surface hover:text-ink"}`}>
            <span className={on ? "gd" : ""}><Icon k={ic} /></span>{label}</Link>;
        })}
        {isStaff(me) && <Link href="/admin" className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 font-semibold text-muted hover:text-ink"><Icon k="shield" />Admin console</Link>}
        <div className="mt-auto rounded-[9px] border border-line p-2.5 text-[11.5px] text-muted">
          <b className="text-ink">{me.subscription?.plan_name ?? "No"} plan</b><br />
          {trialDays !== null ? `Trial · ${trialDays} day${trialDays === 1 ? "" : "s"} left` : me.subscription?.status ?? "Choose a plan to start"}<br />
          <Link className="gd" href="/app/billing">Manage plan →</Link>
        </div>
      </aside>
      <div className="flex min-w-0 flex-col">
        <div className="flex min-h-[56px] flex-wrap items-center gap-2.5 border-b border-line px-4 py-2.5 md:px-5">
          <h1 className="mr-auto text-[17px] font-bold">{title}</h1>
          {right}
          <span className="pill pill-gold hidden sm:inline-flex">{session()}</span>
          <button className="grid h-[30px] w-[30px] place-items-center rounded-full bg-[#2B3547] text-[11px] font-bold" title="Log out" aria-label="Log out"
            onClick={async () => { await signOut(); router.replace("/login"); }}>{me.name.split(" ").map((p) => p[0]).join("").slice(0, 2).toUpperCase()}</button>
        </div>
        {!ent?.valid && <div className="banner banner-warn mx-4 mt-3 md:mx-5">
          {ent?.reason === "trial_expired" ? "Your free trial has ended. Bots stop opening new trades until you choose a plan." : "You have no active plan. Bots cannot open trades."}
          <Link className="ml-auto font-bold underline" href={me.subscription ? "/app/billing" : "/pricing"}>Choose a plan</Link></div>}
        {!me.email_verified && <div className="banner banner-info mx-4 mt-3 md:mx-5">Please verify your email address. <Link className="ml-auto font-bold underline" href="/verify">Enter code</Link></div>}
        <main className="flex min-w-0 flex-col gap-3 px-4 pb-24 pt-4 md:px-5 md:pb-7">{children}</main>
      </div>
      <nav className="fixed inset-x-0 bottom-0 z-40 flex justify-around border-t border-line bg-rail px-2 pb-[calc(8px+env(safe-area-inset-bottom))] pt-2 md:hidden">
        {NAV.slice(0, 5).map(([href, label, ic]) => (
          <Link key={href} href={href} className={`flex min-w-[52px] flex-col items-center gap-1 text-[10.5px] font-semibold ${path.startsWith(href) ? "gd" : "mu"}`}><Icon k={ic} className="h-[18px] w-[18px]" />{label.split(" ")[0]}</Link>
        ))}
      </nav>
    </div>
  );
}
