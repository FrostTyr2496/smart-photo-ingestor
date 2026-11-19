import { describe, expect, it } from 'vitest';
import { isTypingTarget } from './keys';

describe('isTypingTarget', () => {
  it('is true for fields and editable elements', () => {
    for (const tag of ['input', 'textarea', 'select']) expect(isTypingTarget(document.createElement(tag))).toBe(true);
    const d = document.createElement('div');
    d.setAttribute('contenteditable', 'true');
    expect(isTypingTarget(d)).toBe(true);
  });
  it('is false for other elements and null', () => {
    expect(isTypingTarget(document.createElement('button'))).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});
