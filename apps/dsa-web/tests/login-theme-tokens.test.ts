// @vitest-environment node

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const REQUIRED_LOGIN_TOKENS = [
  '--login-button-text',
  '--login-label-text',
  '--login-hint-text',
  '--login-input-icon',
  '--login-input-toggle-bg',
  '--login-input-toggle-border',
  '--login-input-toggle-text',
  '--login-input-toggle-border-hover',
  '--login-input-toggle-bg-hover',
  '--login-input-toggle-text-hover',
  '--login-input-toggle-ring',
  '--login-input-toggle-active-bg',
  '--login-input-toggle-active-border',
  '--login-input-toggle-active-text',
];

describe('login theme tokens', () => {
  it('defines all login-specific tokens in the light theme root block', () => {
    const css = readFileSync(resolve(__dirname, '..', 'src', 'index.css'), 'utf8');
    const rootMatch = css.match(/:root\s*\{([\s\S]*?)\n\}/);

    expect(rootMatch).not.toBeNull();
    const rootBlock = rootMatch?.[1] ?? '';

    for (const token of REQUIRED_LOGIN_TOKENS) {
      expect(rootBlock).toContain(token);
    }
  });

  it('does not fork login tokens into a separate dark block (one token set for both themes)', () => {
    const css = readFileSync(resolve(__dirname, '..', 'src', 'index.css'), 'utf8');
    expect(css).not.toMatch(/\.dark\s*\{[^}]*--login-/);
    const tokens = readFileSync(resolve(__dirname, '..', 'src', 'styles', 'design-tokens.css'), 'utf8');
    expect(tokens).toMatch(/\.dark\s*\{/);
  });
});
