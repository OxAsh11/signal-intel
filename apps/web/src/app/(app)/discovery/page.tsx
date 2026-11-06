import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Discovery' };

export default function DiscoveryPage() {
  return <ScreenPlaceholder screen={screen('/discovery')} />;
}
