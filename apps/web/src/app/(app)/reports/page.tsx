import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Reports' };

export default function ReportsPage() {
  return <ScreenPlaceholder screen={screen('/reports')} />;
}
