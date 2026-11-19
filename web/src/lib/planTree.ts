// The planned files of an import as a folder tree on the target (im-7, D-110). Reuses the rv-26 tree builder.
import { buildTree, type FolderNode, type TreeNode } from './filetree';

/** One planned file: where it comes from on the card, where it lands on the target, and its size. */
export interface PlannedItem {
  src_rel: string;
  dst_rel: string;
  size: number;
}

/** A folder row of the plan: its path on the target, depth, and the files and bytes below it. */
export interface PlanFolder {
  key: string;
  /** Path on the target, no trailing slash. */
  path: string;
  name: string;
  depth: number;
  files: number;
  bytes: number;
}

/** `planned_items` from the result event ([src, dst, size] triples); anything malformed is dropped. */
export function readItems(v: unknown): PlannedItem[] {
  if (!Array.isArray(v)) return [];
  const out: PlannedItem[] = [];
  for (const t of v) {
    if (Array.isArray(t) && typeof t[0] === 'string' && typeof t[1] === 'string') out.push({ src_rel: t[0], dst_rel: t[1], size: typeof t[2] === 'number' ? t[2] : 0 });
  }
  return out;
}

type Row = { rel_path: string; size: number };

/** Folders of the plan in tree order, merged single-child chains ("2026/2026-11-18") on one row. Files outside any folder are not listed. */
export function planFolders(items: PlannedItem[], closed: Set<string> = new Set()): PlanFolder[] {
  const tree = buildTree<Row>(items.map((i) => ({ rel_path: i.dst_rel, size: i.size })));
  const out: PlanFolder[] = [];
  const walk = (nodes: Array<TreeNode<Row>>, depth: number) => {
    for (const n of nodes) {
      if (n.kind !== 'folder') continue;
      const f = n as FolderNode<Row>;
      out.push({ key: f.key, path: f.key.replace(/\/$/, ''), name: f.name, depth, files: f.rows.length, bytes: f.rows.reduce((a, r) => a + r.size, 0) });
      if (!closed.has(f.key)) walk(f.children, depth + 1);
    }
  };
  walk(tree, 0);
  return out;
}
