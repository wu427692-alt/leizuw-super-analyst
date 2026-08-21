import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { Drawer } from '../Drawer';

describe('Drawer', () => {
  it('portals the fixed panel to document.body instead of a transformed page container', () => {
    const { container } = render(
      <UiLanguageProvider>
        <div data-testid="page-container" style={{ transform: 'translateZ(0)', overflow: 'hidden' }}>
          <Drawer isOpen onClose={vi.fn()} title="原文窗口">
            <p>完整原文内容</p>
          </Drawer>
        </div>
      </UiLanguageProvider>,
    );

    const dialog = screen.getByRole('dialog', { name: '原文窗口' });
    expect(dialog).toHaveTextContent('完整原文内容');
    expect(container.querySelector('[role="dialog"]')).not.toBeInTheDocument();
    expect(document.body).toContainElement(dialog);
    expect(dialog).toHaveClass('h-full', 'min-h-0');
  });
});
