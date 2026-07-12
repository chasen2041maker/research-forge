import Link from "next/link";

const modes = [
  {
    eyebrow: "01 · EXPLORE",
    title: "Research Studio",
    description:
      "Use the legacy multi-agent workspace to refine a question, map literature, critique an idea, and draft an experiment plan.",
    boundary: "Everything here is a direction, not a verified result.",
    href: "/studio",
    action: "Open Studio",
    tone: "border-violet-300/25 bg-violet-300/[0.06] hover:border-violet-300/50",
  },
  {
    eyebrow: "02 · VERIFY",
    title: "Forge Runtime",
    description:
      "Freeze a reproduction specification, bind it to a pinned commit and image, then produce an evidence-gated Bundle.",
    boundary: "Only completed evidence closure is presented as verified.",
    href: "/forge",
    action: "Open Forge",
    tone: "border-cyan-300/25 bg-cyan-300/[0.06] hover:border-cyan-300/50",
  },
  {
    eyebrow: "03 · HAND OFF",
    title: "Full research loop",
    description:
      "Export an UNVERIFIED Proposal from Studio, confirm every missing pin and budget, then create a normal Forge Mission.",
    boundary: "The handoff is JSON-only; the two systems do not share internals.",
    href: "/forge",
    action: "Verify a Proposal",
    tone: "border-emerald-300/25 bg-emerald-300/[0.06] hover:border-emerald-300/50",
  },
];

export default function HomePage() {
  return (
    <main className="min-h-screen px-5 py-6 text-slate-100 sm:px-8 lg:px-12">
      <section className="mx-auto max-w-6xl">
        <nav className="flex items-center justify-between rounded-2xl border border-white/10 bg-slate-950/65 px-5 py-4 backdrop-blur">
          <span className="text-sm font-semibold tracking-[0.18em] text-cyan-200">RESEARCH FORGE</span>
          <div className="flex gap-4 text-sm text-slate-300">
            <Link className="transition hover:text-white" href="/studio">Studio</Link>
            <Link className="transition hover:text-white" href="/forge">Forge</Link>
          </div>
        </nav>

        <header className="relative mt-6 overflow-hidden rounded-3xl border border-white/10 bg-slate-950/75 px-6 py-12 shadow-2xl shadow-black/25 sm:px-10 sm:py-16">
          <div className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-cyan-400/10 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-36 left-1/3 h-80 w-80 rounded-full bg-violet-500/10 blur-3xl" />
          <div className="relative max-w-4xl">
            <p className="text-sm font-medium tracking-[0.2em] text-cyan-300">THINK FREELY · VERIFY RIGOROUSLY</p>
            <h1 className="mt-5 text-4xl font-semibold tracking-[-0.045em] text-white sm:text-6xl">
              One research product with two honest modes.
            </h1>
            <p className="mt-6 max-w-3xl text-base leading-7 text-slate-300 sm:text-lg">
              Research Studio explores possibilities. Forge Runtime proves reproducible claims. Their boundary makes
              uncertainty visible instead of hiding it behind a polished answer.
            </p>
          </div>
        </header>

        <section aria-label="Research Forge modes" className="mt-6 grid gap-4 lg:grid-cols-3">
          {modes.map((mode) => (
            <article key={mode.title} className={`rounded-2xl border p-6 transition ${mode.tone}`}>
              <p className="text-xs font-semibold tracking-[0.16em] text-slate-400">{mode.eyebrow}</p>
              <h2 className="mt-4 text-2xl font-semibold text-white">{mode.title}</h2>
              <p className="mt-3 min-h-24 text-sm leading-6 text-slate-300">{mode.description}</p>
              <p className="mt-5 border-t border-white/10 pt-4 text-xs leading-5 text-slate-400">{mode.boundary}</p>
              <Link
                className="mt-6 inline-flex rounded-lg bg-white px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-100"
                href={mode.href}
              >
                {mode.action} <span aria-hidden="true">→</span>
              </Link>
            </article>
          ))}
        </section>

        <section className="mt-6 rounded-2xl border border-white/10 bg-slate-950/60 p-6 text-sm text-slate-300 sm:p-8">
          <p className="font-medium text-white">The product contract</p>
          <div className="mt-4 grid gap-4 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-center">
            <p><span className="font-mono text-violet-200">UNVERIFIED Proposal</span><br />Studio output remains a research direction.</p>
            <span aria-hidden="true" className="text-center text-cyan-300">→</span>
            <p><span className="font-mono text-cyan-200">Human completion</span><br />Pins, commands, metric, and budgets are explicitly confirmed.</p>
            <span aria-hidden="true" className="text-center text-cyan-300">→</span>
            <p><span className="font-mono text-emerald-200">Verified Result</span><br />Forge closes the normal evidence gate and seals a Bundle.</p>
          </div>
        </section>
      </section>
    </main>
  );
}
