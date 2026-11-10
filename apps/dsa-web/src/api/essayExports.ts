import apiClient, { BACKGROUND_ROUTE_HEADERS } from './index';
import { toCamelCase } from './utils';

export type EssayExportStatus = 'queued' | 'indexing' | 'collecting' | 'packaging' | 'retrying' | 'completed' | 'failed' | 'no_results';

export type EssayExportTask = {
  taskId: string;
  workspaceId: string;
  status: EssayExportStatus;
  title?: string;
  message?: string;
  filters?: { query?: string };
  progress: number | null;
  matchedTotal: number | null;
  targetTotal: number | null;
  retrievedCount: number;
  packagedCount: number;
  bytesWritten: number;
  bytesTotal: number | null;
  requestedLimit: number | null;
  createdAt?: string;
  updatedAt?: string;
};

export const essayExportsApi = {
  tasks: async (): Promise<{ items: EssayExportTask[]; total: number }> => {
    // Authentication and ownership are enforced by the shared client/server.
    // Omit workspace_id: this ledger includes the current owner's workspaces.
    // No cached snapshot may cross an account change or disguise a read failure.
    const response = await apiClient.get('/api/v1/financial-data/essays/package/tasks', {
      headers: BACKGROUND_ROUTE_HEADERS,
    });
    return toCamelCase(response.data);
  },
};
