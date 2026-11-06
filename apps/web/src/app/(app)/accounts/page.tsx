import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Accounts' };

export default function AccountsPage() {
  return <ScreenPlaceholder screen={screen('/accounts')} />;
}
