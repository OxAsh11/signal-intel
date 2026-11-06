import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Brief Studio' };

export default function BriefStudioPage() {
  return <ScreenPlaceholder screen={screen('/briefs')} />;
}
