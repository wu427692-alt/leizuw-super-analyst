import type React from 'react';
import { cn } from '../../utils/cn';

interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title,
  description,
  icon,
  action,
  className = '',
}) => {
  return (
    <div className={cn('rounded-[var(--radius-card)] border border-dashed border-[var(--line-2)] bg-[var(--bg-1)] px-6 py-10 text-center', className)}>
      {icon ? <div className="mb-4 flex justify-center text-[color:var(--text-3)]">{icon}</div> : null}
      <h3 className="text-[length:var(--fs-h2)] font-semibold leading-[var(--lh-h2)] text-[color:var(--text-1)]">{title}</h3>
      {description ? <p className="mx-auto mt-2 max-w-md text-[length:var(--fs-body)] leading-[var(--lh-body)] text-[color:var(--text-2)]">{description}</p> : null}
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
};
