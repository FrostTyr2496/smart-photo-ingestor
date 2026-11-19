import { AnimatePresence, motion } from 'framer-motion';
import { Icon, Logo } from '../components/bits';
import { ProgressBar } from '../components/ProgressBar';
import { extVM, taskVM } from '../components/TaskCard';
import { springs } from '../lib/motion';
import { type Route } from '../lib/route';
import { useStore } from '../lib/store';
import { worstTier } from '../lib/tiers';

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
  const { tasks, elsewhere, drives, now, driveFiles } = useStore();
  const running = [
    ...tasks.filter((t) => !t.final).map((t) => taskVM(t, drives, now, driveFiles)),
    ...elsewhere.map((e) => extVM(e, drives, now, driveFiles)),
  ].map((vm) => ({ title: vm.title, p: vm.pace }));
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

export function Header({ route, sidebarHidden, onToggleSidebar }: { route: Route; sidebarHidden: boolean; onToggleSidebar: () => void }) {
  const { cards } = useStore();
  const worst = worstTier(cards);
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg/70 backdrop-blur-xl">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-4 px-4">
        <button
          type="button"
          onClick={onToggleSidebar}
          aria-pressed={!sidebarHidden}
          aria-label={sidebarHidden ? 'Show drives sidebar' : 'Hide drives sidebar'}
          title={sidebarHidden ? 'Show the drives sidebar ( [ )' : 'Hide the drives sidebar ( [ )'}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted hover:bg-ink/5 hover:text-ink"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="2" y="3" width="12" height="10" rx="2" />
            <path d="M6 3v10" />
            {!sidebarHidden && <path d="M3.6 5.5v5" strokeWidth="2" />}
          </svg>
        </button>
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
          <NavItem label="Photos" href="#/photos" active={route.view === 'photos'} title="Photos (being rebuilt)" />
          <NavItem
            label="Cards"
            href="#/cards"
            active={route.view === 'cards'}
            dot={worst ? TIER_DOT[worst] : undefined}
            title={worst ? `Connected cards: ${TIER_WORD[worst]}` : 'Cards'}
          />
          <NavItem label="Settings" href="#/settings" active={route.view === 'settings'} title="Settings" />
        </nav>
      </div>
    </header>
  );
}
