import type { Metadata } from 'next';
import { Instrument_Sans, JetBrains_Mono, Source_Serif_4 } from 'next/font/google';
import './globals.css';

const instrumentSans = Instrument_Sans({ variable: '--font-instrument-sans', subsets: ['latin'] });
const jetbrainsMono = JetBrains_Mono({ variable: '--font-jetbrains-mono', subsets: ['latin'] });
const sourceSerif = Source_Serif_4({ variable: '--font-source-serif', subsets: ['latin'], style: ['normal', 'italic'] });

export const metadata: Metadata = {
  title: { default: 'Signal Intel', template: '%s · Signal Intel' },
  description: 'Evidence-backed account intelligence: which companies match, how sure we are, how to reach them.',
};

export default function RootLayout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" className={`${instrumentSans.variable} ${jetbrainsMono.variable} ${sourceSerif.variable}`}>
      <body>{children}</body>
    </html>
  );
}
