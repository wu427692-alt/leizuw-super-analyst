import type React from 'react';
import { Database, RadioTower, ShieldCheck, TimerReset } from 'lucide-react';
import { cn } from '../../utils/cn';

export type EvidenceRailItem = {
  label: string;
  value: React.ReactNode;
  note?: React.ReactNode;
  tone?: 'default' | 'live' | 'verified' | 'warning';
  icon?: React.ReactNode;
};

type EvidenceRailProps = {
  items: EvidenceRailItem[];
  className?: string;
  label?: string;
};

const DEFAULT_ICONS = [RadioTower, Database, TimerReset, ShieldCheck];

export const EvidenceRail: React.FC<EvidenceRailProps> = ({ items, className, label = '数据证据轨道' }) => (
  <section className={cn('evidence-rail', className)} aria-label={label}>
    {items.map((item, index) => {
      const Icon = DEFAULT_ICONS[index % DEFAULT_ICONS.length];
      return (
        <div className={cn('evidence-rail__item', `is-${item.tone ?? 'default'}`)} key={`${item.label}-${index}`}>
          <span className="evidence-rail__icon" aria-hidden="true">{item.icon ?? <Icon />}</span>
          <span className="evidence-rail__copy">
            <small>{item.label}</small>
            <strong>{item.value}</strong>
            {item.note ? <em>{item.note}</em> : null}
          </span>
        </div>
      );
    })}
  </section>
);
