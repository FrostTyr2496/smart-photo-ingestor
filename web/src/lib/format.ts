// Numbers, sizes and times for people. Decimal units (1 KB = 1000 B).

const NUM = new Intl.NumberFormat();
export const fmtInt = (n: number | null | undefined): string => NUM.format(Math.round(Number(n) || 0));

export const plural = (n: number | null | undefined, word: string): string =>
  `${fmtInt(n)} ${word}${Number(n) === 1 ? '' : 's'}`;

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '';
  let v = Number(n);
  let i = 0;
  while (v >= 1000 && i < 4) {
    v /= 1000;
    i++;
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  return i === 0 ? `${fmtInt(v)} B` : `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

const UNIT_BYTES: Record<string, number> = { B: 1, KB: 1e3, MB: 1e6, GB: 1e9, TB: 1e12 };
/** Sizes arrive as text such as "1.0 TB"; parse them back for capacity bars. */
export function parseSize(text: string | null | undefined): number | null {
  const m = /([\d.]+)\s*(B|KB|MB|GB|TB)/.exec(text || '');
  return m && m[1] && m[2] ? Number(m[1]) * (UNIT_BYTES[m[2]] ?? 1) : null;
}

const DATE_SHORT = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const DATE_LONG = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});
const RTF = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return String(iso);
  const d = new Date(ms);
  return (d.getFullYear() === new Date().getFullYear() ? DATE_SHORT : DATE_LONG).format(d);
}

export function relTime(iso: string | null | undefined, now = Date.now()): string {
  const ms = Date.parse(iso || '');
  if (Number.isNaN(ms)) return '';
  const s = (ms - now) / 1000;
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['year', 31536000],
    ['month', 2592000],
    ['week', 604800],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(s) >= size) return RTF.format(Math.round(s / size), unit);
  }
  return 'just now';
}

export function fmtElapsed(sec: number): string {
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${s}s`;
}

/** "3h 20m left": short, for one-line task cards. */
export function fmtLeft(sec: number): string {
  const t = Math.max(0, Math.round(sec));
  if (t < 60) return `${t}s left`;
  if (t < 3600) return `${Math.round(t / 60)} min left`;
  return `${Math.floor(t / 3600)}h ${Math.round((t % 3600) / 60)}m left`;
}

const FS_NAMES: Record<string, string> = {
  apfs: 'APFS',
  exfat: 'exFAT',
  msdos: 'FAT32',
  fat32: 'FAT32',
  vfat: 'FAT32',
  ntfs: 'NTFS',
  hfs: 'HFS+',
  'hfs+': 'HFS+',
  smbfs: 'SMB',
  afpfs: 'AFP',
  nfs: 'NFS',
};
/** A drive's file system as people know it ("apfs" → "APFS", "msdos" → "FAT32"); '' when unknown. */
export function fsLabel(fs: string | null | undefined): string {
  const key = (fs || '').trim().toLowerCase();
  if (!key) return '';
  return FS_NAMES[key] ?? key.toUpperCase();
}
