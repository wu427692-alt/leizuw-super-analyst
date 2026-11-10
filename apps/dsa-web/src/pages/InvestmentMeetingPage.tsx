import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';

const InvestmentMeetingPage = () => {
  const [searchParams] = useSearchParams();
  const taskId = searchParams.get('export_task') || '';
  const embedParams = new URLSearchParams({ embed: '1' });
  if (/^essay-export-[a-zA-Z0-9-]{1,100}$/.test(taskId)) embedParams.set('export_task', taskId);
  useEffect(() => {
    document.title = '投研团队 - 乐子乌智能投研平台';
  }, []);

  return (
    <div className="meeting-frame w-full overflow-hidden">
      <iframe
        src={`/meeting-embed?${embedParams}`}
        title="投研团队"
        className="h-full w-full border-0"
      />
    </div>
  );
};

export default InvestmentMeetingPage;
