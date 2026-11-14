/**
 * 研投团队插画：六位研究员围坐在会议桌旁。
 *
 * 图是像素风插画（原图已抠掉背景并降低饱和度/亮度），所以能直接贴在深色底上，
 * 不需要额外的卡片包裹，也不会抢数据的注意力。
 */
import type React from 'react';
import teamRoom from '../../assets/team-room.png';

export const PixelTeamRoom: React.FC<{ className?: string }> = ({ className }) => (
  <img
    className={['pixel-room', className].filter(Boolean).join(' ')}
    src={teamRoom}
    alt="六位研究员围坐在会议桌旁，桌上摆着地球仪、数据屏幕和资料"
    decoding="async"
  />
);

export default PixelTeamRoom;
