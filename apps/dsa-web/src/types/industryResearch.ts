export type ResearchStage = {
  stage: string;
  hours: string;
  title: string;
  goal: string;
  deliverables: string[];
};

export type EvidenceCoverage = {
  key: string;
  name: string;
  count: number;
  status: 'covered' | 'missing';
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
  lookbackDays: number;
  queryTerms: string[];
  totals: { evidence: number; reports: number; notes: number; events: number; mediaFiles: number };
  coverage: EvidenceCoverage[];
  companies: Array<{ symbol: string; name: string; evidenceCount: number }>;
  timeline: Array<{ month: string; count: number }>;
  evidence: IndustryEvidence[];
  sourceHash: string;
  cutoff?: string;
  collectedAt?: string;
  reportLibrary?: { status: string; total: number; pdfCount: number };
};

export type IndustryResearchMethodology = {
  name: string;
  principles: string[];
  stages: ResearchStage[];
  requiredQuestions: string[];
  evidenceRule: string;
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
  status: 'queued' | 'collecting' | 'analyzing' | 'completed' | 'failed';
  progress: number;
  stage: string;
  message: string;
  query: { terms?: string[] };
  snapshot?: IndustryResearchSnapshot;
  report?: IndustryResearchReport;
  sourceHash?: string;
  error?: string;
  createdAt: string;
  updatedAt: string;
  completedAt?: string;
};
