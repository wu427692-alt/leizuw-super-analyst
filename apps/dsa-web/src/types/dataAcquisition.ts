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

export type AcquisitionRunTask = {
  taskId: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress: number;
  phase: 'queued' | 'starting' | 'validating' | 'fetching' | 'exporting' | 'packaging' | 'finalizing' | 'completed' | 'failed' | 'interrupted';
  message: string;
  completedTasks: number;
  totalTasks: number;
  tasks: Array<{ id: string; label: string; source: string }>;
  currentTaskId?: string | null;
  currentSource?: string | null;
  jobId?: string | null;
  result?: AcquisitionJob | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AcquisitionDownloadProgress = {
  loaded: number;
  total?: number;
  percent?: number;
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

export type ResearchReportLibraryStatus = {
  status: 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'interrupted';
  progress: number;
  message: string;
  startDate?: string | null;
  endDate?: string | null;
  totalWindows: number;
  completedWindows: number;
  scannedRows: number;
  savedRows: number;
  lastError?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  total: number;
  pdfCount: number;
  earliestTradeDate?: string | null;
  latestTradeDate?: string | null;
  source: string;
  searchMode: 'local_sqlite_only';
};

export type ResearchReportFacet = { value: string; count: number };

export type ResearchReportFacets = {
  brokers: ResearchReportFacet[];
  reportTypes: ResearchReportFacet[];
  industries: ResearchReportFacet[];
  companies: ResearchReportFacet[];
  tags: ResearchReportFacet[];
};

export type ResearchReportItem = {
  id: number;
  tradeDate: string;
  title: string;
  abstract: string;
  reportType?: string | null;
  author?: string | null;
  companyName?: string | null;
  tsCode?: string | null;
  broker?: string | null;
  industry?: string | null;
  pdfUrl?: string | null;
  tags: string[];
  syncedAt?: string | null;
};

export type ResearchReportSearchFilters = {
  titleQuery: string;
  contentQuery: string;
  broker: string;
  company: string;
  tsCode: string;
  reportType: string;
  industry: string;
  author: string;
  tag: string;
  startDate: string;
  endDate: string;
  hasPdf: boolean;
  sort: 'latest' | 'oldest';
};

export type ResearchReportSearchResult = {
  items: ResearchReportItem[];
  total: number;
  page: number;
  pageSize: number;
  source: 'local_sqlite';
};
