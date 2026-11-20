import { AnimatePresence, MotionConfig, motion } from 'framer-motion';
import { AmbientGlow } from './components/AmbientGlow';
import { ContextMenuHost } from './components/ContextMenu';
import { DevTools } from './components/DevTools';
import { DialogHost } from './components/DialogHost';
import { DoneNotifier } from './components/DoneNotifier';
import { ConfirmDialog, Toasts } from './components/Overlays';
import { DialogsProvider } from './lib/dialogs';
import { DriveActivityProvider } from './lib/driveActivity';
import { useEffect, useRef, useState } from 'react';
import { isTypingTarget, modalOpen } from './lib/keys';
import { usePref } from './lib/prefs';
import { routeKey, useRoute } from './lib/route';
import { Actions } from './views/Actions';
import { Cards } from './views/Cards';
import { StoreProvider, useStore } from './lib/store';
import { ThemeProvider } from './lib/theme';
import { DriveView } from './views/DriveView';
import { Header } from './views/Header';
import { Home } from './views/Home';
import { SetupView } from './views/SetupView';
import { Photos } from './views/Photos';
import { ReviewFrame, ReviewShoots } from './views/review/ReviewShoots';
import { Settings } from './views/Settings';
import { Sidebar } from './views/Sidebar';

const WIDE = '(min-width: 1024px)';

function useWide(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  useEffect(() => {
    const m = window.matchMedia(WIDE);
    const on = () => setWide(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, []);
  return wide;
}

function Shell() {
  const route = useRoute();
  const [savedHidden, setHidden] = usePref('shell.sidebarHidden', false);
  // Review (sh-3) is its own mode: the drives sidebar is hidden there without touching the saved preference.
  const review = route.view === 'review';
  const hidden = savedHidden || review;
  const wide = useWide();
  // Photos wants the width: entering it hides the sidebar; entering Tasks or Cards shows it again. Only on a change of
  // mode, so [ and the Drives tab still toggle it freely within a page.
  const lastView = useRef(route.view);
  useEffect(() => {
    const prev = lastView.current;
    lastView.current = route.view;
    if (prev === route.view) return;
    if (route.view === 'photos') setHidden(true);
    else if (route.view === 'home' || route.view === 'cards') setHidden(false);
  }, [route.view, setHidden]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key !== '[' || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      if (isTypingTarget(e.target) || modalOpen() || window.location.hash === '#/review/shoots') return;
      setHidden((h) => !h);
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [setHidden]);
  const { tasks, elsewhere } = useStore();
  const active = tasks.some((t) => !t.final) || elsewhere.length > 0;
  return (
    <DriveActivityProvider route={route}>
      <AmbientGlow active={active} />
      {review && <ReviewFrame />}
      <div className="relative z-[1] min-h-screen">
        <Header route={route} sidebarHidden={hidden} onToggleSidebar={() => setHidden((h) => !h)} />
        <main className="mx-auto flex max-w-[1600px] flex-col gap-5 px-4 py-5 lg:flex-row lg:gap-0">
          {wide ? (
            // overflow: clip (not hidden) keeps the sidebar's own sticky positioning working.
            <motion.div
              initial={false}
              animate={{ width: hidden ? 0 : 360, opacity: hidden ? 0 : 1 }}
              transition={{ type: 'spring', stiffness: 420, damping: 38 }}
              style={{ overflow: 'clip' }}
              className="shrink-0"
              aria-hidden={hidden}
              {...(hidden ? ({ inert: '' } as object) : {})}
            >
              {/* Sticky on this box, not only on the aside: a sticky element needs a taller parent to stick in. */}
              <div className="sticky top-[72px] w-[340px]">
                <Sidebar route={route} />
              </div>
            </motion.div>
          ) : (
            !hidden && <Sidebar route={route} />
          )}
          {wide && hidden && !review && (
            <button
              type="button"
              onClick={() => setHidden(false)}
              title="Show the drives sidebar ( [ )"
              className="fixed left-0 top-1/2 z-20 -translate-y-1/2 rounded-r-lg border border-l-0 border-line bg-surface/90 py-3 pl-1 pr-1.5 text-2xs text-muted hover:text-ink"
              style={{ writingMode: 'vertical-rl' }}
            >
              Drives
            </button>
          )}
          <div className="min-w-0 flex-1">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={route.view === 'cards' ? 'cards' : route.view === 'home' ? 'home' : routeKey(route)}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6, transition: { duration: 0.12 } }}
                transition={{ type: 'spring', stiffness: 420, damping: 34 }}
              >
                {route.view === 'drive' ? (
                  <DriveView id={route.id} />
                ) : route.view === 'cards' ? (
                  <Cards id={route.id} />
                ) : route.view === 'setup' ? (
                  <SetupView driveKey={route.key} />
                ) : route.view === 'photos' ? (
                  <Photos missing={route.missing} day={route.day} photo={route.photo} shoot={route.shoot} />
                ) : route.view === 'review' ? (
                  <ReviewShoots />
                ) : route.view === 'actions' ? (
                  <Actions />
                ) : route.view === 'settings' ? (
                  <Settings />
                ) : (
                  <Home task={route.task} />
                )}
              </motion.div>
            </AnimatePresence>
          </div>
        </main>
      </div>
      <DialogHost />
      <DoneNotifier />
      <Toasts />
      <ConfirmDialog />
      <ContextMenuHost />
      <DevTools />
    </DriveActivityProvider>
  );
}

export default function App() {
  return (
    <MotionConfig reducedMotion="user">
      <ThemeProvider>
        <StoreProvider>
          <DialogsProvider>
            <Shell />
          </DialogsProvider>
        </StoreProvider>
      </ThemeProvider>
    </MotionConfig>
  );
}
