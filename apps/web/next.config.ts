import path from 'node:path';
import { loadEnvConfig } from '@next/env';
import type { NextConfig } from 'next';

// One env file for the whole repo: load <repo>/.env so web and worker share it.
// Variables already set (Railway, CI) always win; Next still reads apps/web/.env* if present.
loadEnvConfig(path.resolve(process.cwd(), '../..'), process.env.NODE_ENV !== 'production');

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  transpilePackages: ['@signal/ui', '@signal/contracts'],
  poweredByHeader: false,
  turbopack: {
    rules: {
      '*.css': {
        loaders: ['@tailwindcss/turbopack'],
        as: '*.css',
      },
    },
  },
};

export default nextConfig;
