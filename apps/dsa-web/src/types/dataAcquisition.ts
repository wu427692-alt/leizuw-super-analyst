export type AcquisitionTask = {
  id: string;
  source: string;
  resource: string;
  label: string;
  reason: string;
  params: Record<string, unknown>;
  fields: string[];
};

export type AcquisitionPlan = {
  title: string;
  objective: string;
  tasks: AcquisitionTask[];
  scope: {
    symbols: string[];
    companyNames: string[];
    keywords: string[];
    startDate: string;
    endDate: string;
    marketWide: boolean;
  };
  includeFiles?: boolean;
  outputFormats: string[];
  caveats: string[];
  model: string;
  generatedAt: string;
};

export type AcquisitionDataset = {
  taskId: string;
  label: string;
  source: string;
  resource: string;
  status: 'success' | 'failed';
  rowCount: number;
  error?: string | null;
  files: string[];
};

export type AcquisitionJob = {
  jobId: string;
  contractVersion?: string;
  title: string;
  request: string;
  status: 'success' | 'partial' | 'failed';
  generatedAt: string;
  plan: AcquisitionPlan;
  datasets: AcquisitionDataset[];
  summary: { taskCount: number; successCount: number; failedCount: number; rowCount: number; includeFiles?: boolean; downloadedFileCount?: number; failedFileCount?: number };
  downloadUrl: string;
  formats: string[];
};

export type AcquisitionCapabilities = {
  sources: Array<{
    key: string;
    name: string;
    mode: string;
    available: boolean;
    scope?: string;
    resources: Array<{ key: string; name: string }>;
  }>;
  planner: { available: boolean; model: string; maxTasks: number; maxRowsPerDataset: number };
};
