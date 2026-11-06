import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Review' };

export default function ReviewPage() {
  return <ScreenPlaceholder screen={screen('/review')} />;
}
