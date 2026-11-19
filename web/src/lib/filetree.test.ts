import { describe, expect, it } from 'vitest';
import { allKeys, buildTree, namePattern, RUN_MIN, splitExt, visibleRows, type FolderNode, type RunNode } from './filetree';

const rows = (...paths: string[]) => paths.map((rel_path) => ({ rel_path }));
const seq = (dir: string, prefix: string, from: number, n: number, ext: string) =>
  Array.from({ length: n }, (_, i) => `${dir}/${prefix}${String(from + i).padStart(4, '0')}.${ext}`);

describe('namePattern', () => {
  it('masks digits and drops the extension', () => {
    expect(namePattern('DSC_0001.NEF')).toBe('DSC_####');
    expect(namePattern('GOPR1234.MP4')).toBe('GOPR####');
    expect(namePattern('notes.txt')).toBeNull();
    expect(splitExt('.DS_Store')).toEqual({ stem: '.DS_Store', ext: '' });
  });
});

describe('buildTree', () => {
  it('merges single-folder chains and puts folders before files', () => {
    const t = buildTree(rows('DCIM/100NIKON/a.txt', 'readme.txt', 'DCIM/100NIKON/b.txt'));
    expect(t.map((n) => n.kind)).toEqual(['folder', 'file']);
    const f = t[0] as FolderNode<{ rel_path: string }>;
    expect(f.name).toBe('DCIM/100NIKON');
    expect(f.rows).toHaveLength(2);
  });

  it('collapses a run of similar names, RAW and JPEG together', () => {
    const paths = [...seq('D', 'DSC_', 1, 6, 'NEF'), ...seq('D', 'DSC_', 1, 6, 'JPG'), 'D/notes.txt'];
    const t = buildTree(rows(...paths));
    const d = t[0] as FolderNode<{ rel_path: string }>;
    expect(d.children.map((n) => n.kind)).toEqual(['run', 'file']);
    const run = d.children[0] as RunNode<{ rel_path: string }>;
    expect(run.rows).toHaveLength(12);
    expect(run.exts).toEqual(['JPG', 'NEF']);
    expect(run.label).toBe('DSC_0001 – DSC_0006');
  });

  it('leaves short runs as plain files', () => {
    const t = buildTree(rows(...seq('D', 'IMG_', 1, RUN_MIN - 1, 'JPG')));
    expect((t[0] as FolderNode<{ rel_path: string }>).children.every((n) => n.kind === 'file')).toBe(true);
  });
});

describe('visibleRows', () => {
  const t = buildTree(rows(...seq('A', 'DSC_', 1, 6, 'NEF'), 'A/x.txt'));
  it('opens folders and closes runs by default', () => {
    expect(visibleRows(t, new Set(), new Set()).map((r) => [r.node.kind, r.depth])).toEqual([
      ['folder', 0],
      ['run', 1],
      ['file', 1],
    ]);
  });
  it('honours closed folders and opened runs', () => {
    const { folders, runs } = allKeys(t);
    expect(visibleRows(t, new Set(folders), new Set())).toHaveLength(1);
    expect(visibleRows(t, new Set(), new Set(runs))).toHaveLength(9);
  });
});
