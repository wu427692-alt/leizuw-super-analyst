import { useEffect } from 'react';

const InvestmentMeetingPage = () => {
  useEffect(() => {
    document.title = '投资会议 - 乐子乌超级价值';
  }, []);

  return (
    <div className="h-[calc(100vh-8.5rem)] min-h-[520px] w-full overflow-hidden lg:h-[calc(100vh-2rem)]">
      <iframe
        src="/meeting-embed?embed=1"
        title="投资会议"
        className="h-full w-full border-0"
      />
    </div>
  );
};

export default InvestmentMeetingPage;
