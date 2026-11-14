import apiClient from './index';

/** 投研指挥台角色（与后端 AGENTS 同源，只读）。 */
export interface CommandAgentRole {
  name: string;
  title: string;
  color?: string;
  desc?: string;
}

/**
 * 投研会议用的指挥台能力目录。
 *
 * 只有 `/api/v1/command/catalog` 会返回真实的 `agents`，团队页与
 * 指挥台共用同一份角色定义；前端不再另存一份角色文案。
 */
export const commandApi = {
  async getAgentRoles(): Promise<Record<string, CommandAgentRole>> {
    const response = await apiClient.get<{ agents?: Record<string, CommandAgentRole> }>(
      '/api/v1/command/catalog',
    );
    return response.data?.agents ?? {};
  },
};

export default commandApi;
