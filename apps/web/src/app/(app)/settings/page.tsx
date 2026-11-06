import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Settings' };

export default function SettingsPage() {
  return <ScreenPlaceholder screen={screen('/settings')} />;
}
