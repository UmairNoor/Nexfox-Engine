import { Logo } from "./ui";

export default function AuthFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="hidden flex-col border-r border-line p-9 lg:flex" style={{ background: "radial-gradient(circle at 25% 15%,#2B2210,var(--bg) 62%)" }}>
        <Logo />
        <div className="mt-auto">
          <h2 className="text-2xl font-bold tracking-tight">Your trading bots, always in view.</h2>
          <p className="mu mt-1.5 max-w-[40ch]">Bot status, orders, risk limits and a kill switch for every MT5 account you connect.</p>
        </div>
      </div>
      <div className="flex items-center justify-center px-5 py-7"><div className="w-full max-w-[360px]">{children}</div></div>
    </div>
  );
}
