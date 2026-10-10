import Link from "next/link";
import { Footer, PublicNav } from "@/components/PublicNav";
import DemoChart from "@/components/DemoChart";
import { APP_NAME } from "@/lib/brand";

const STEPS = [
  ["Start a free trial", "Create your workspace. The trial runs on demo accounts only."],
  ["Connect MT5", "Add your MT5 login. We verify the connection against the real terminal before anything runs."],
  ["Assign a bot", "Pick a bot, adjust the settings it allows, start it, and watch every order it sends."],
];
const SAFETY = [
  ["Server-approved orders", "Every order a bot wants to send is checked against your plan, risk limits and the platform kill switch first."],
  ["No blind retries", "If the broker doesn't confirm an order, new entries pause until the order is found or proven absent."],
  ["Stop ≠ close", "Stopping a bot never closes your trades. Closing positions is a separate, confirmed action."],
  ["Live is opt-in", "Real-money trading needs a paid plan, platform approval and your own authorization per bot."],
];

export default function Landing() {
  return (
    <div className="mx-auto max-w-[1160px] px-5 pb-10">
      <PublicNav />
      <section className="grid items-center gap-9 py-8 lg:grid-cols-[1fr_1.05fr]">
        <div>
          <span className="pill pill-gold">MetaTrader 5 · automated trading bots</span>
          <h1 className="mt-3.5 text-[clamp(30px,4.2vw,46px)] font-extrabold leading-[1.08] tracking-tight">Run trading bots on your MT5 accounts, with the controls you need.</h1>
          <p className="mu mt-3.5 max-w-[50ch] text-[15px]">{APP_NAME} connects your MT5 accounts to vetted bots, enforces risk limits on every order, and shows you exactly what each bot did and why.</p>
          <div className="mt-5 flex flex-wrap gap-2.5"><Link className="btn btn-p" href="/signup">Start free trial</Link><Link className="btn btn-g" href="/pricing">See plans</Link></div>
          <p className="mt-3.5 max-w-[60ch] text-[11.5px] text-faint">We do not publish or promise performance. Trading can lose money. Read the <Link className="underline" href="/risk">risk disclosure</Link>.</p>
        </div>
        <div className="card"><div className="card-h">XAUUSD · M1 <span className="r"><span className="pill dot pill-info">Illustrative demo feed</span></span></div><DemoChart /></div>
      </section>
      <h2 className="mb-3 mt-4 text-xl font-bold">Built for safety first</h2>
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
        {SAFETY.map(([t, d]) => <div key={t} className="card"><div className="text-[14px] font-bold">{t}</div><div className="mu mt-1">{d}</div></div>)}
      </div>
      <h2 className="mb-3 mt-6 text-xl font-bold">Up and running in three steps</h2>
      <div className="grid gap-3 md:grid-cols-3">
        {STEPS.map(([t, d], i) => (
          <div key={t} className="card"><div className="gd font-mono font-semibold">Step {i + 1}</div><div className="my-1 text-[15px] font-bold">{t}</div><div className="mu">{d}</div></div>
        ))}
      </div>
      <Footer />
    </div>
  );
}
