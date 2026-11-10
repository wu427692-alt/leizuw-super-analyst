import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import InvestmentMeetingPage from '../InvestmentMeetingPage';

afterEach(cleanup);

describe('investment meeting export task links', () => {
  it('passes a task-center export id to the existing meeting iframe', () => {
    render(<MemoryRouter initialEntries={['/meeting?export_task=essay-export-1234-abcd']}><InvestmentMeetingPage /></MemoryRouter>);
    expect(screen.getByTitle('投研团队')).toHaveAttribute('src', '/meeting-embed?embed=1&export_task=essay-export-1234-abcd');
  });

  it('does not forward arbitrary URLs or unrelated query parameters to the iframe', () => {
    render(<MemoryRouter initialEntries={['/meeting?export_task=https%3A%2F%2Funtrusted.example%2Ftask&poll_path=%2Fapi%2Fprivate']}><InvestmentMeetingPage /></MemoryRouter>);
    expect(screen.getByTitle('投研团队')).toHaveAttribute('src', '/meeting-embed?embed=1');
  });
});
