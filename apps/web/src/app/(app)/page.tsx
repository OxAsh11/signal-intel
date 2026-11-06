import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Home' };

export default function HomePage() {
  return <ScreenPlaceholder screen={screen('/')} />;
}
