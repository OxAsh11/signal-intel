import type { Metadata } from 'next';
import { DM_Mono, DM_Sans, Source_Serif_4 } from 'next/font/google';
import './globals.css';

const dmSans = DM_Sans({ variable: '--font-dm-sans', subsets: ['latin'], weight: ['400', '500'] });
const dmMono = DM_Mono({ variable: '--font-dm-mono', subsets: ['latin'], weight: ['400', '500'] });
const sourceSerif = Source_Serif_4({ variable: '--font-source-serif', subsets: ['latin'], style: ['normal', 'italic'] });

export const metadata: Metadata = {
  title: { default: 'Signal Intel', template: '%s · Signal Intel' },
  description: 'Evidence-backed account intelligence: which companies match, how sure we are, how to reach them.',
};

export default function RootLayout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" className={`${dmSans.variable} ${dmMono.variable} ${sourceSerif.variable}`}>
      <body>{children}</body>
    </html>
  );
}
