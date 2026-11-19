/**
 * A file list as a folder tree (rv-26). Folders nest by `rel_path`; a chain of folders with one child folder and
 * no files merges into one row ("DCIM/100NIKON"). Inside a folder, files whose names differ only in their digits
 * and extension ("DSC_0001.NEF", "DSC_0002.JPG") form a run, which the view shows collapsed.
 */

/** Fewer files than this with the same name pattern stay as plain rows. */
export const RUN_MIN = 5;

export interface Row {
  rel_path: string;
}

export interface FileNode<T> {
  kind: 'file';
  key: string;
  name: string;
  row: T;
}
export interface RunNode<T> {
  kind: 'run';
  key: string;
  /** e.g. "DSC_####" */
  pattern: string;
  /** e.g. "DSC_0001 – DSC_0150" */
  label: string;
  exts: string[];
  rows: T[];
  children: Array<FileNode<T>>;
}
export interface FolderNode<T> {
  kind: 'folder';
  key: string;
  /** Shown name; merged chains joined by "/". */
  name: string;
  rows: T[];
  children: Array<TreeNode<T>>;
}
export type TreeNode<T> = FileNode<T> | RunNode<T> | FolderNode<T>;

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

export const baseName = (p: string): string => p.slice(p.lastIndexOf('/') + 1);

/** "DSC_0001.NEF" → { stem: "DSC_0001", ext: "NEF" }; a leading dot is not an extension. */
export function splitExt(name: string): { stem: string; ext: string } {
  const i = name.lastIndexOf('.');
  return i > 0 ? { stem: name.slice(0, i), ext: name.slice(i + 1) } : { stem: name, ext: '' };
}

/** The name with every run of digits as "#" of the same length; null when the name has no digits. */
export function namePattern(name: string): string | null {
  const { stem } = splitExt(name);
  return /\d/.test(stem) ? stem.replace(/\d/g, '#') : null;
}

interface Dir<T> {
  dirs: Map<string, Dir<T>>;
  files: T[];
}

function groupFiles<T extends Row>(files: T[], prefix: string): Array<TreeNode<T>> {
  const sorted = [...files].sort((a, b) => collator.compare(baseName(a.rel_path), baseName(b.rel_path)));
  const byPattern = new Map<string, T[]>();
  for (const f of sorted) {
    const p = namePattern(baseName(f.rel_path));
    if (p !== null) byPattern.set(p, [...(byPattern.get(p) ?? []), f]);
  }
  const out: Array<TreeNode<T>> = [];
  const done = new Set<string>();
  for (const f of sorted) {
    const name = baseName(f.rel_path);
    const p = namePattern(name);
    const run = p !== null ? byPattern.get(p)! : null;
    if (run && run.length >= RUN_MIN) {
      if (done.has(p!)) continue;
      done.add(p!);
      const first = splitExt(baseName(run[0]!.rel_path)).stem;
      const last = splitExt(baseName(run[run.length - 1]!.rel_path)).stem;
      const exts = [...new Set(run.map((r) => splitExt(baseName(r.rel_path)).ext.toUpperCase()).filter(Boolean))].sort();
      out.push({
        kind: 'run',
        key: `${prefix}#${p}`,
        pattern: p!,
        label: first === last ? first : `${first} – ${last}`,
        exts,
        rows: run,
        children: run.map((r) => ({ kind: 'file', key: r.rel_path, name: baseName(r.rel_path), row: r })),
      });
    } else {
      out.push({ kind: 'file', key: f.rel_path, name, row: f });
    }
  }
  return out;
}

function toNodes<T extends Row>(dir: Dir<T>, prefix: string): Array<TreeNode<T>> {
  const folders: Array<FolderNode<T>> = [...dir.dirs.keys()].sort(collator.compare).map((name) => {
    let sub = dir.dirs.get(name)!;
    let shown = name;
    let path = prefix ? `${prefix}/${name}` : name;
    while (sub.files.length === 0 && sub.dirs.size === 1) {
      const [only, next] = [...sub.dirs.entries()][0]!;
      shown += `/${only}`;
      path += `/${only}`;
      sub = next;
    }
    const children = toNodes(sub, path);
    return { kind: 'folder', key: `${path}/`, name: shown, rows: collect(children), children };
  });
  return [...folders, ...groupFiles(dir.files, prefix)];
}

function collect<T>(nodes: Array<TreeNode<T>>): T[] {
  return nodes.flatMap((n) => (n.kind === 'file' ? [n.row] : n.rows));
}

/** Rows as a tree: folders first (natural order), then this folder's files and runs. */
export function buildTree<T extends Row>(rows: T[]): Array<TreeNode<T>> {
  const root: Dir<T> = { dirs: new Map(), files: [] };
  for (const r of rows) {
    const parts = r.rel_path.split('/');
    let d = root;
    for (const part of parts.slice(0, -1)) {
      if (!d.dirs.has(part)) d.dirs.set(part, { dirs: new Map(), files: [] });
      d = d.dirs.get(part)!;
    }
    d.files.push(r);
  }
  return toNodes(root, '');
}

export interface VisibleRow<T> {
  node: TreeNode<T>;
  depth: number;
}

/**
 * The rows to draw. Folders are open unless their key is in `closed`; runs are closed unless their key is in
 * `opened`. Both sets hold node keys.
 */
export function visibleRows<T>(nodes: Array<TreeNode<T>>, closed: Set<string>, opened: Set<string>, depth = 0): Array<VisibleRow<T>> {
  const out: Array<VisibleRow<T>> = [];
  for (const n of nodes) {
    out.push({ node: n, depth });
    if (n.kind === 'folder' && !closed.has(n.key)) out.push(...visibleRows(n.children, closed, opened, depth + 1));
    if (n.kind === 'run' && opened.has(n.key)) out.push(...visibleRows(n.children, closed, opened, depth + 1));
  }
  return out;
}

/** Every folder and run key, for Collapse all / Expand all. */
export function allKeys<T>(nodes: Array<TreeNode<T>>): { folders: string[]; runs: string[] } {
  const folders: string[] = [];
  const runs: string[] = [];
  const walk = (ns: Array<TreeNode<T>>) => {
    for (const n of ns) {
      if (n.kind === 'folder') {
        folders.push(n.key);
        walk(n.children);
      } else if (n.kind === 'run') runs.push(n.key);
    }
  };
  walk(nodes);
  return { folders, runs };
}
