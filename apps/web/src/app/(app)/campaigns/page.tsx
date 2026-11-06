import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Campaigns' };

export default function CampaignsPage() {
  return <ScreenPlaceholder screen={screen('/campaigns')} />;
}
