import { AnimatePresence, motion } from 'framer-motion';
import { Icon, Logo } from '../components/bits';
import { ProgressBar } from '../components/ProgressBar';
import { pace, taskTitle } from '../lib/labels';
import { springs } from '../lib/motion';
import { CLASSIC, type Route } from '../lib/route';
import { useStore } from '../lib/store';

const TIER_ORDER = ['only_on_card', 'unchecked', 'needs_backup', 'backed_up'];
const TIER_DOT: Record<string, string> = {
  only_on_card: 'bg-err',
  unchecked: 'bg-skip',
  needs_backup: 'bg-maybe',
  backed_up: 'bg-go',
  empty: 'bg-skip',
};
const TIER_WORD: Record<string, string> = {
  only_on_card: 'only on this card',
  unchecked: 'not checked',
  needs_backup: 'needs backup',
  backed_up: 'backed up',
  empty: 'empty',
};

/** The overall progress of everything running: the mean when every run is countable, else a sliding bar. */
function GlobalProgress() {
  const { tasks, elsewhere, drives, now } = useStore();
  const running = [
    ...tasks.filter((t) => !t.final).map((t) => ({ title: taskTitle(t.argv, drives), p: pace(t.progress, t.startMs, now) })),
    ...elsewhere.map((e) => ({
      title: taskTitle(['--json', ...(e.words || [])], drives),
      p: pace(e.progress || (e.job && e.job.current != null ? { current: e.job.current, total: e.job.total || 0 } : null), null, now),
    })),
  ];
  const counted = running.filter((r) => r.p.fraction !== null);
  const value = running.length && counted.length === running.length ? counted.reduce((s, r) => s + (r.p.fraction ?? 0), 0) / counted.length : null;
  const text = running.length === 1 ? running[0]!.title : `${running.length} tasks running`;
  return (
    <AnimatePresence>
      {running.length > 0 && (
        <motion.a
          href="#/"
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={springs.snappy}
          className="hidden min-w-0 flex-1 items-center gap-3 rounded-full px-3 py-1.5 hover:bg-ink/5 md:flex lg:max-w-xl"
          title="Show running tasks"
        >
          <span className="live-dot h-2 w-2 shrink-0 rounded-full bg-accent" aria-hidden="true" />
          <ProgressBar value={value} live size="xs" label="Overall progress" className="min-w-[80px] flex-1" />
          <span className="truncate text-xs text-muted">{text}</span>
          {value !== null && <span className="font-mono text-2xs text-ink/80">{Math.floor(value * 100)}%</span>}
        </motion.a>
      )}
    </AnimatePresence>
  );
}

function NavItem({ label, href, active, external, dot, title }: { label: string; href: string; active: boolean; external?: boolean; dot?: string; title?: string }) {
  return (
    <a
      href={href}
      title={title}
      aria-current={active ? 'page' : undefined}
      className={`relative flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3 text-sm ${active ? 'text-ink' : 'text-muted hover:text-ink'}`}
    >
      {active && <motion.span layoutId="nav-pill" transition={springs.snappy} className="absolute inset-0 rounded-full border border-line-strong bg-raised" />}
      <span className="relative flex items-center gap-1.5">
        {dot && <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden="true" />}
        {label}
        {external && <Icon name="external" size={11} className="opacity-50" />}
      </span>
    </a>
  );
}

export function Header({ route }: { route: Route }) {
  const { cards } = useStore();
  const connected = cards.filter((c) => c.mounted);
  const worst = connected.length ? TIER_ORDER.find((t) => connected.some((c) => c.tier === t)) || 'empty' : null;
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg/70 backdrop-blur-xl">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-4 px-4">
        <a href="#/" className="flex shrink-0 items-center gap-2.5">
          <Logo />
          <span className="hidden text-[1.05rem] font-semibold tracking-tight sm:inline">PhotoVault</span>
        </a>
        <div className="flex min-w-0 flex-1 justify-center">
          <GlobalProgress />
        </div>
        <nav
          className="no-scrollbar flex min-w-0 shrink items-center gap-0.5 overflow-x-auto rounded-full border border-line bg-surface/60 p-0.5"
          aria-label="Main"
        >
          <NavItem label="Tasks" href="#/" active={route.view === 'home'} />
          <NavItem label="Photos" href={CLASSIC} active={false} external title="Opens in the classic UI (not ported yet)" />
          <NavItem
            label="Cards"
            href={CLASSIC}
            active={false}
            external
            dot={worst ? TIER_DOT[worst] : undefined}
            title={worst ? `Connected cards: ${TIER_WORD[worst]}. Opens in the classic UI.` : 'Opens in the classic UI (not ported yet)'}
          />
          <NavItem label="Settings" href={CLASSIC} active={false} external title="Opens in the classic UI (not ported yet)" />
        </nav>
      </div>
    </header>
  );
}
