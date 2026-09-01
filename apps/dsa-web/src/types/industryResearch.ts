export type ResearchStage = {
  stage: string;
  hours: string;
  title: string;
  goal: string;
  deliverables: string[];
};

export type AIResearchFlowStage = {
  stage: string;
  role: string;
  title: string;
  input: string;
  output: string;
  gate: string;
};

export type ResearchQuality = {
  status: 'ready' | 'limited' | 'insufficient';
  overallScore: number;
  dimensions: Record<string, number>;
  criticalGaps: string[];
  warnings: string[];
  metrics: Record<string, number | string | null>;
  rule: string;
};

export type EvidenceCoverage = {
  key: string;
  name: string;
  count: number;
  status: 'covered' | 'missing' | 'partial' | 'failed' | 'unavailable';
  evidenceLevel: string;
  mediaFiles?: number;
};

export type IndustryEvidence = {
  evidenceId: string;
  kind: string;
  source: string;
  title: string;
  summary?: string;
  date?: string;
  url?: string;
  symbol?: string;
  company?: string;
  evidenceLevel: string;
  originalAvailable: boolean;
  importance?: number;
};

export type IndustryResearchSnapshot = {
  topic: string;
  researchType: 'industry' | 'company';
  subject?: {
    researchType?: 'industry' | 'company';
    name?: string;
    symbol?: string;
    resolved?: boolean;
    companyName?: string;
    industry?: string;
    mainBusiness?: string;
  };
  lookbackDays: number;
  queryTerms: string[];
  totals: {
    evidence: number; reports: number; notes: number; events: number; mediaFiles: number;
    directSources?: number; audioCandidates?: number; audioTranscripts?: number; images?: number;
    evidenceStored?: number; evidenceModelReady?: number;
  };
  coverage: EvidenceCoverage[];
  sourceStatus?: Array<{ key: string; name: string; status: string; count: number; message?: string }>;
  companies: Array<{ symbol: string; name: string; evidenceCount: number }>;
  timeline: Array<{ month: string; count: number }>;
  financialSeries?: Array<Record<string, string | number | null>>;
  marketSeries?: Array<Record<string, string | number | null>>;
  mediaGallery?: Array<{ kind: string; title: string; source: string; date?: string; evidenceId: string; url: string }>;
  audioCandidates?: Array<{ topicId: string; fileId: string; filename: string; noteTitle: string; createdAt?: string }>;
  audioPipeline?: {
    status: string; taskId?: string; candidateCount?: number; transcribedCount?: number;
    provider?: string; reportUrl?: string; message?: string;
  };
  evidence: IndustryEvidence[];
  sourceHash: string;
  cutoff?: string;
  collectedAt?: string;
  reportLibrary?: { status: string; total: number; pdfCount: number };
  valuationSeries?: Array<Record<string, string | number | null>>;
  ownershipGovernance?: Array<Record<string, unknown>>;
  capitalMarketActivity?: Array<Record<string, unknown>>;
  filingDocuments?: Array<{ announcementId?: string; title?: string; date?: string; url?: string; textChars?: number; excerptChars?: number }>;
  brokerReportDocuments?: Array<{ reportKey?: string; title?: string; date?: string; url?: string; textChars?: number; textStatus?: string }>;
  conceptContext?: { status: string; marketDate?: string; items?: Array<Record<string, unknown>>; stockMatches?: Array<Record<string, unknown>>; message?: string };
  researchContract?: Record<string, unknown>;
  sourcePlan?: Array<{
    key: string; name: string; required: boolean; status: string; count: number;
    message?: string; evidenceLevel?: string; metadataCount?: number;
  }>;
  dataQuality?: ResearchQuality;
  factLedger?: Array<{
    factId: string; claimType: string; entity: string; metric: string;
    value: string | number; unit: string; period: string; asOf?: string;
    source: string; evidenceIds: string[];
  }>;
};

export type IndustryResearchMethodology = {
  name: string;
  principles: string[];
  stages: ResearchStage[];
  aiFlow: AIResearchFlowStage[];
  requiredQuestions: string[];
  evidenceRule: string;
  qualityGate?: { readyScore: number; criticalRules: string[] };
  dataRequirements?: Array<{ layer: string; inputs: string[]; use: string; currentBoundary: string }>;
  reportStandard?: { targetChars: number; chapters: number; mustInclude: string[] };
  methodReferences?: Array<{ name: string; url: string }>;
};

export type IndustryResearchBlueprint = {
  topic: string;
  researchType: string;
  lookbackDays: number;
  queryTerms: string[];
  methodology: IndustryResearchMethodology;
  snapshot: IndustryResearchSnapshot;
  generatedAt: string;
};

export type IndustryResearchReport = Record<string, unknown> & {
  oneSentence?: string;
  executiveSummary?: string;
  industryBoundary?: { included?: string[]; excluded?: string[]; definition?: string };
  chainNodes?: Array<{ stage?: string; role?: string; economics?: string; participants?: string[]; evidenceIds?: string[] }>;
  trends?: Array<{ claim?: string; horizon?: string; drivers?: string[]; confidence?: string; evidenceIds?: string[] }>;
  leaders?: Array<{ name?: string; symbol?: string; rationale?: string; openQuestions?: string[]; evidenceIds?: string[] }>;
  bottlenecks?: Array<{ issue?: string; whyItMatters?: string; validation?: string; evidenceIds?: string[] }>;
  applications?: Array<{ scenario?: string; demandLogic?: string; evidenceIds?: string[] }>;
  disagreements?: Array<{ question?: string; sides?: string[]; evidenceIds?: string[] }>;
  falsificationConditions?: string[];
  monitoringIndicators?: Array<{ indicator?: string; frequency?: string; source?: string }>;
  interviewQuestions?: string[];
  openQuestions?: string[];
  caveats?: string[];
  researchType?: 'industry' | 'company';
  subject?: IndustryResearchSnapshot['subject'];
  coverage?: EvidenceCoverage[];
  sourceStatus?: IndustryResearchSnapshot['sourceStatus'];
  audioPipeline?: IndustryResearchSnapshot['audioPipeline'];
  mediaGallery?: IndustryResearchSnapshot['mediaGallery'];
  evidenceSnapshotHash?: string;
  researchCutoff?: string;
  researchContract?: IndustryResearchSnapshot['researchContract'];
  sourcePlan?: IndustryResearchSnapshot['sourcePlan'];
  dataQuality?: ResearchQuality;
  aiWorkflow?: AIResearchFlowStage[];
  qualityAssurance?: {
    status: 'ready' | 'limited'; score: number; criticalFailures: string[]; warnings: string[];
    metrics: Record<string, number | string | string[] | null>; rule: string;
  };
  editorialReview?: {
    status: 'completed' | 'failed'; releaseRecommendation?: 'ready' | 'limited';
    contradictions?: Array<{ issue?: string; resolution?: string }>;
    unsupportedClaims?: Array<{ claim?: string; chapter?: string; reason?: string }>;
    numericConflicts?: Array<{ metric?: string; values?: unknown[]; resolution?: string }>;
    missingQuestions?: string[]; strongestCounterarguments?: string[]; editorNote?: string;
  };
  visualizations?: Array<{
    id: string; type: 'bar' | 'line' | 'area' | 'scatter'; title: string; subtitle?: string;
    analyticalQuestion?: string; insight?: string; unit?: string; labelKey?: string;
    data: Array<Record<string, string | number | null>>; xKey: string; yKeys: string[]; source?: string;
  }>;
  chapters?: Array<{
    chapterId: string;
    title: string;
    summary?: string;
    bodyMarkdown: string;
    evidenceIds: string[];
    openQuestions: string[];
    charCount: number;
  }>;
  longFormReport?: string;
  longFormCharCount?: number;
  narrativeCharCount?: number;
  generation?: {
    targetChars: number;
    actualChars: number;
    narrativeChars: number;
    chapterCount: number;
    model?: string;
    provider?: string;
    channel?: string;
    status: string;
    completedAt?: string;
  };
};

export type IndustryResearchProject = {
  projectId: string;
  topic: string;
  researchType: 'industry' | 'company';
  objective: string;
  lookbackDays: number;
  status: 'queued' | 'collecting' | 'analyzing' | 'completed' | 'limited' | 'failed';
  progress: number;
  stage: string;
  message: string;
  query: { terms?: string[]; subject?: IndustryResearchSnapshot['subject'] };
  snapshot?: IndustryResearchSnapshot;
  report?: IndustryResearchReport;
  sourceHash?: string;
  error?: string;
  createdAt: string;
  updatedAt: string;
  completedAt?: string;
};
