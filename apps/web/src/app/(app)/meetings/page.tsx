import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Meetings and Tasks' };

export default function MeetingsTasksPage() {
  return <ScreenPlaceholder screen={screen('/meetings')} />;
}
