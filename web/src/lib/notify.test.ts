import { describe, expect, it } from 'vitest';
import { dialogForTask, dockLine, dockOrder, notificationText, popupTtl, shouldNotify, type DialogRef } from './notify';
import type { TaskRec } from './types';

const task = (over: Partial<TaskRec>): TaskRec => ({
  id: 1, argv: ['py', '-m', 'photovault', '--json', 'cards', 'clear', '/mnt/CARD', '--dry-run'], state: 'done', exitCode: 0, lastSeq: 0,
  keepAwake: false, progress: null, final: true, startMs: 0, note: '', error: '', result: null, ...over,
});
const dlg = (over: Partial<DialogRef>): DialogRef => ({ key: 'clear:/mnt/CARD', kind: 'clear', mount: '/mnt/CARD', volumeId: 'v1', minimized: false, openedAt: 1, ...over });

describe('shouldNotify', () => {
  it('needs a finished run of 20 s or more', () => {
    expect(shouldNotify(task({ final: false }), 60_000, { dialogVisibleFor: false })).toBe(false);
    expect(shouldNotify(task({}), 19_000, { dialogVisibleFor: false })).toBe(false);
    expect(shouldNotify(task({}), 20_000, { dialogVisibleFor: false })).toBe(true);
  });
  it('skips a dry run only while its dialog is visible', () => {
    expect(shouldNotify(task({}), 60_000, { dialogVisibleFor: true })).toBe(false);
    const real = task({ argv: ['--json', 'cards', 'clear', '/mnt/CARD', '--confirm', '3'] });
    expect(shouldNotify(real, 60_000, { dialogVisibleFor: true })).toBe(true);
  });
});

describe('dialogForTask', () => {
  it('matches a clear dry run to the dialog on that card', () => {
    expect(dialogForTask(task({}), [dlg({})])?.key).toBe('clear:/mnt/CARD');
    expect(dialogForTask(task({}), [dlg({ mount: '/mnt/OTHER' })])).toBeUndefined();
  });
  it('matches make green by card id and import by source', () => {
    const green = task({ argv: ['--json', 'backup', '--card', 'v1', '--make-green'] });
    expect(dialogForTask(green, [dlg({ kind: 'green', key: 'g' })])?.key).toBe('g');
    const imp = task({ argv: ['--json', 'ingest', '--source', '/mnt/CARD', '--target', '/mnt/X', '--dry-run'] });
    expect(dialogForTask(imp, [dlg({ kind: 'import', key: 'i' })])?.key).toBe('i');
  });
});

describe('text and dock', () => {
  it('words the outcome', () => {
    const f = notificationText(task({ state: 'failed', exitCode: 2, error: 'Disk gone' }), []);
    expect(f.outcome).toBe('failed');
    expect(f.title.startsWith('Failed: ')).toBe(true);
    expect(f.body).toContain('Disk gone');
    expect(notificationText(task({ state: 'paused' }), []).outcome).toBe('paused');
    expect(notificationText(task({}), []).outcome).toBe('done');
  });
  it('keeps failures until dismissed', () => {
    expect(popupTtl('failed')).toBeNull();
    expect(popupTtl('done')).toBe(15_000);
  });
  it('orders the dock by when each opened and lists only minimized ones', () => {
    const list = [dlg({ key: 'b', openedAt: 5, minimized: true }), dlg({ key: 'a', openedAt: 2, minimized: true }), dlg({ key: 'c', minimized: false })];
    expect(dockOrder(list).map((d) => d.key)).toEqual(['a', 'b']);
  });
  it('shows the title and a status line', () => {
    expect(dockLine('clear', { line: 'Re-reading copies 4 of 9', value: 0.4 })).toBe('Clear card · Re-reading copies 4 of 9');
    expect(dockLine('import', undefined)).toBe('Import · working…');
  });
});
