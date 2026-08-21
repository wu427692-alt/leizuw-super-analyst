import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { InvestmentMonitorNav } from '../InvestmentMonitorNav';

describe('InvestmentMonitorNav', () => {
  it('keeps the intelligence desk focused on channels, live feed, and dragon tiger data', () => {
    render(<MemoryRouter><InvestmentMonitorNav /></MemoryRouter>);

    expect(screen.getByRole('link', { name: '全渠道情报' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '数据源 BI' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '实时流水' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '龙虎榜' })).toBeInTheDocument();
    expect(screen.queryByText('超级关注股')).not.toBeInTheDocument();
    expect(screen.queryByText('市场结构')).not.toBeInTheDocument();
    expect(screen.queryByText('公司与机构')).not.toBeInTheDocument();
    expect(screen.queryByText('综合研判')).not.toBeInTheDocument();
  });
});
