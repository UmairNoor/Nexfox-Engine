import Link from "next/link";
import { APP_NAME } from "@/lib/brand";
import { Logo } from "./ui";

export function PublicNav() {
  return (
    <div className="flex flex-wrap items-center gap-5 py-4">
      <Logo />
      <Link className="mu font-semibold" href="/">Product</Link>
      <Link className="mu font-semibold" href="/pricing">Pricing</Link>
      <Link className="mu font-semibold" href="/risk">Risk</Link>
      <span className="ml-auto" />
      <Link className="mu font-semibold" href="/login">Log in</Link>
      <Link className="btn btn-p" href="/signup">Start 7-day trial</Link>
    </div>
  );
}

export function Footer() {
  return (
    <div className="mt-9 flex flex-wrap justify-between gap-4 border-t border-line pt-4 text-[11.5px] text-faint">
      <span>© {new Date().getFullYear()} {APP_NAME} · <Link href="/terms">Terms</Link> · <Link href="/privacy">Privacy</Link> · <Link href="/risk">Risk</Link></span>
      <span className="max-w-[70ch]">Trading leveraged products such as gold CFDs carries a high risk of losing money. Past results do not guarantee future results. We provide software, not investment advice.</span>
    </div>
  );
}
