import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import { Icon, Logo } from '../components/bits';
import { ProgressBar } from '../components/ProgressBar';
import { extVM, taskVM } from '../components/TaskCard';
import { springs } from '../lib/motion';
import { type Route } from '../lib/route';
import { badgeCount } from '../lib/actions';
import { api } from '../lib/api';
import { useStore } from '../lib/store';
import { useActions, useActionsPolling } from '../lib/useActions';
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

interface SleepStatus {
  enabled: boolean;
  running: number;
  sleeps_in_s: number | null;
  slept: boolean;
}

/** Sleep when done (ui-17, D-149): a top-bar switch, shown while anything runs (or while it is on). The server does
 * the work, so it still sleeps the Mac if this page is closed: once nothing has run for a minute, `pmset sleepnow`,
 * once, and the switch turns itself off. */
function SleepWhenDone() {
  const { tasks, elsewhere, toast } = useStore();
  const anyRunning = tasks.some((t) => !t.final) || elsewhere.length > 0;
  const [st, setSt] = useState<SleepStatus | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = () => api<SleepStatus>('GET', '/api/sleep-when-done').then((d) => alive && setSt(d)).catch(() => {});
    void load();
    const timer = window.setInterval(load, st?.enabled ? 5000 : 30000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [st?.enabled]);
  if (!st || (!st.enabled && !anyRunning)) return null;
  const toggle = async () => {
    setBusy(true);
    try {
      const d = await api<SleepStatus>('POST', '/api/sleep-when-done', { enabled: !st.enabled });
      setSt(d);
      toast(d.enabled ? 'The Mac will sleep once every run has finished.' : 'Sleep when done is off.');
    } catch (err) {
      toast((err as Error).message, 'err');
    } finally {
      setBusy(false);
    }
  };
  const label = !st.enabled ? 'Sleep when done' : st.sleeps_in_s !== null ? `Sleeps in ${st.sleeps_in_s} s` : 'Sleeps when done';
  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
      aria-pressed={st.enabled}
      title={
        st.enabled
          ? `On: the Mac sleeps a minute after the last run ends (${st.running} running now). Click to cancel.`
          : 'Put the Mac to sleep once every PhotoVault run has finished (works with this page closed).'
      }
      className={`flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-xs disabled:opacity-50 ${
        st.enabled ? 'border-accent/60 bg-accent/15 text-ink' : 'border-line text-muted hover:border-line-strong hover:text-ink'
      }`}
    >
      <Icon name="moon" size={12} />
      <span className="hidden lg:inline">{label}</span>
    </button>
  );
}

function NavItem({ label, href, active, external, dot, title, badge }: { label: string; href: string; active: boolean; external?: boolean; dot?: string; title?: string; badge?: number }) {
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
        {badge ? (
          <span className="min-w-[1.125rem] rounded-full bg-err px-1 text-center font-mono text-2xs leading-[1.125rem] text-white" aria-label={`${badge} need attention`}>
            {badge}
          </span>
        ) : null}
      </span>
    </a>
  );
}

export function Header({ route, sidebarHidden, onToggleSidebar }: { route: Route; sidebarHidden: boolean; onToggleSidebar: () => void }) {
  const { cards } = useStore();
  const worst = worstTier(cards);
  useActionsPolling();
  const { actions } = useActions();
  const urgent = badgeCount(actions, Date.now());
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
        <div className="flex min-w-0 flex-1 items-center justify-center gap-2">
          <GlobalProgress />
          <SleepWhenDone />
        </div>
        <nav
          className="no-scrollbar flex min-w-0 shrink items-center gap-0.5 overflow-x-auto rounded-full border border-line bg-surface/60 p-0.5"
          aria-label="Main"
        >
          <NavItem label="Tasks" href="#/" active={route.view === 'home'} />
          <NavItem label="Actions" href="#/actions" active={route.view === 'actions'} badge={urgent} title="What to do next" />
          <NavItem label="Photos" href="#/photos" active={route.view === 'photos'} title="Photos (being rebuilt)" />
          <NavItem label="Review" href="#/review/shoots" active={route.view === 'review'} dot="bg-maybe" title="Review shoots (dry run)" />
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
