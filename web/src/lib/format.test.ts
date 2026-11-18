import { describe, expect, it } from 'vitest';
import { fsLabel } from './format';

describe('fsLabel', () => {
  it('names the file systems people know', () => {
    expect(fsLabel('apfs')).toBe('APFS');
    expect(fsLabel('exfat')).toBe('exFAT');
    expect(fsLabel('msdos')).toBe('FAT32');
    expect(fsLabel('NTFS')).toBe('NTFS');
    expect(fsLabel('hfs')).toBe('HFS+');
  });
  it('upper-cases an unknown one and hides a missing one', () => {
    expect(fsLabel('zfs')).toBe('ZFS');
    expect(fsLabel('')).toBe('');
    expect(fsLabel(null)).toBe('');
  });
});
