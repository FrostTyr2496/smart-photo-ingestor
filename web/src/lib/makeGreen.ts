// Pure helpers for "Make green" (bk-7, D-107): read the plan from `GET /api/cards/<id>/make-green` and say whether
// (and why not) the button is enabled.

export interface GreenDrive {
  volume_id: string | null;
  label: string;
  role: string | null;
  mount: string | null;
  connected?: boolean;
}

export interface GreenSource {
  source: GreenDrive;
  files: number;
  bytes: number;
  ok: boolean;
  reason: string | null;
  target: (GreenDrive & { fs: string; free_bytes: number | null; reasons: string[] }) | null;
}

export interface GreenPlan {
  card_volume_id: string;
  label: string;
  files: number;
  bytes: number;
  possible: boolean;
  reason: string | null;
  sources: GreenSource[];
}

/** Whether Make green can run now, and the tooltip: what it will do, or the reason it cannot. */
export function greenButton(plan: GreenPlan | null): { enabled: boolean; title: string } {
  if (!plan) return { enabled: false, title: 'Checking which drives can take a second copy…' };
  if (!plan.files) return { enabled: false, title: 'No file on this card has just one copy' };
  if (!plan.possible) return { enabled: false, title: plan.reason ? sentence(plan.reason) : 'No connected drive can take a second copy' };
  const to = [...new Set(plan.sources.filter((s) => s.ok && s.target).map((s) => s.target!.label))];
  return { enabled: true, title: `Copy the one-copy files onto ${to.join(' and ')} to turn them green` };
}

/** "plug in x" -> "Plug in x" (reasons come lower-case from some rules). */
export const sentence = (s: string): string => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** The files and bytes the plan can copy now (sources with a target). */
export function copyable(plan: GreenPlan): { files: number; bytes: number } {
  return plan.sources.filter((s) => s.ok).reduce((n, s) => ({ files: n.files + s.files, bytes: n.bytes + s.bytes }), { files: 0, bytes: 0 });
}
