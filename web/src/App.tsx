import { AnimatePresence, MotionConfig, motion } from 'framer-motion';
import { AmbientGlow } from './components/AmbientGlow';
import { DevTools } from './components/DevTools';
import { ConfirmDialog, Toasts } from './components/Overlays';
import { routeKey, useRoute } from './lib/route';
import { Cards } from './views/Cards';
import { StoreProvider, useStore } from './lib/store';
import { ThemeProvider } from './lib/theme';
import { DriveView } from './views/DriveView';
import { Header } from './views/Header';
import { Home } from './views/Home';
import { Sidebar } from './views/Sidebar';

function Shell() {
  const route = useRoute();
  const { tasks, elsewhere } = useStore();
  const active = tasks.some((t) => !t.final) || elsewhere.length > 0;
  return (
    <>
      <AmbientGlow active={active} />
      <div className="relative z-[1] min-h-screen">
        <Header route={route} />
        <main className="mx-auto grid max-w-[1600px] gap-5 px-4 py-5 lg:grid-cols-[340px_minmax(0,1fr)]">
          <Sidebar route={route} />
          <div className="min-w-0">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={route.view === 'cards' ? 'cards' : routeKey(route)}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6, transition: { duration: 0.12 } }}
                transition={{ type: 'spring', stiffness: 420, damping: 34 }}
              >
                {route.view === 'drive' ? <DriveView id={route.id} /> : route.view === 'cards' ? <Cards id={route.id} /> : <Home />}
              </motion.div>
            </AnimatePresence>
          </div>
        </main>
      </div>
      <Toasts />
      <ConfirmDialog />
      <DevTools />
    </>
  );
}

export default function App() {
  return (
    <MotionConfig reducedMotion="user">
      <ThemeProvider>
        <StoreProvider>
          <Shell />
        </StoreProvider>
      </ThemeProvider>
    </MotionConfig>
  );
}
