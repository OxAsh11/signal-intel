import path from 'node:path';
import { loadEnvConfig } from '@next/env';
import type { NextConfig } from 'next';

// One env file for the whole repo: load <repo>/.env so web and worker share it.
// Variables already set (Railway, CI) always win; Next still reads apps/web/.env* if present.
// forceReload: Next has already loaded (and cached) env files from apps/web by the time this runs.
loadEnvConfig(path.resolve(process.cwd(), '../..'), process.env.NODE_ENV !== 'production', undefined, true);

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  transpilePackages: ['@signal/ui', '@signal/contracts', '@signal/formula'],
  poweredByHeader: false,
  // CSV radar uploads go through a server action; the default 1 MB would refuse most lists.
  experimental: { serverActions: { bodySizeLimit: '11mb' } },
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
