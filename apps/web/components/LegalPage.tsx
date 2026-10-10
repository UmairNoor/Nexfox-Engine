import { Footer, PublicNav } from "./PublicNav";

export default function LegalPage({ title, updated, children }: { title: string; updated: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-[860px] px-5 pb-10">
      <PublicNav />
      <article className="card mt-4 p-6 leading-relaxed [&_h2]:mb-1.5 [&_h2]:mt-5 [&_h2]:text-[15px] [&_h2]:font-bold [&_li]:mb-1 [&_p]:mb-2 [&_ul]:mb-2 [&_ul]:list-disc [&_ul]:pl-5">
        <h1 className="text-[24px] font-extrabold">{title}</h1>
        <p className="mu text-xs">Last updated {updated}</p>
        <div className="banner banner-warn my-3">Template text. It describes how this software actually works, but it has not been reviewed by a lawyer. Have it reviewed for the countries you operate in before accepting customers.</div>
        {children}
      </article>
      <Footer />
    </div>
  );
}
