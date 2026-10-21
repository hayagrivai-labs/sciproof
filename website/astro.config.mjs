// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { SITE, BASE, REPO_URL } from './site.config.mjs';

export default defineConfig({
  site: SITE,
  base: BASE,
  trailingSlash: 'always',
  integrations: [
    starlight({
      title: 'SciProof',
      description:
        'Record a computational experiment once, then let anyone check the evidence: PASS, FAIL or INCONCLUSIVE.',
      favicon: '/favicon.svg',
      head: [
        {
          tag: 'script',
          attrs: { type: 'application/ld+json' },
          content: JSON.stringify({
            '@context': 'https://schema.org',
            '@type': 'SoftwareApplication',
            name: 'SciProof',
            applicationCategory: 'DeveloperApplication',
            operatingSystem: 'Linux, macOS, Windows',
            license: 'https://opensource.org/license/mit',
            url: SITE + BASE + '/',
            codeRepository: REPO_URL,
            description:
              'Record a computational experiment once, then let anyone check the evidence: PASS, FAIL or INCONCLUSIVE.',
          }),
        },
      ],
      disable404Route: true, // src/pages/404.astro is used instead (same look as the landing page)
      social: [{ icon: 'github', label: 'GitHub', href: REPO_URL }],
      // "Last updated" is set per page from git history by scripts/sync.mjs;
      // "Edit this page" points to the original file via the generated editUrl frontmatter.
      lastUpdated: true,
      customCss: [
        '@fontsource-variable/inter',
        '@fontsource-variable/jetbrains-mono',
        './src/styles/theme.css',
        './src/styles/docs.css',
      ],
      components: {
        SiteTitle: './src/components/SiteTitle.astro',
        Footer: './src/components/DocsFooter.astro',
      },
      sidebar: [
        {
          label: 'Start here',
          items: [
            { label: 'Getting started', slug: 'docs/getting-started' },
            { label: 'Use cases', slug: 'docs/use-cases' },
            { label: 'Concepts', slug: 'docs/concepts' },
            { label: 'Tutorial: NumPy & pandas', slug: 'docs/tutorial-numpy-pandas' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'CLI reference', slug: 'docs/cli-reference' },
            { label: 'Manifest format', slug: 'docs/manifest' },
            { label: 'Exit codes', slug: 'docs/exit-codes' },
            { label: 'Python API', slug: 'docs/python-api' },
            { label: 'Policies', slug: 'docs/policy' },
            { label: 'Updating a baseline', slug: 'docs/update' },
            { label: 'Claims', slug: 'docs/claims' },
            { label: 'Show your evidence', slug: 'docs/badge' },
            { label: 'Signing manifests', slug: 'docs/signing' },
            { label: 'Comparing two runs', slug: 'docs/diff' },
            { label: 'Listing and exporting', slug: 'docs/export' },
            { label: 'Verification report', slug: 'docs/report' },
            { label: 'Numeric comparison rules', slug: 'docs/numeric-comparison' },
            { label: 'Using SciProof in CI', slug: 'docs/ci' },
          ],
        },
        {
          label: 'Trust and design',
          items: [
            { label: 'Security & threat model', slug: 'docs/security' },
            { label: 'Signing: design notes', slug: 'docs/signing-design' },
            { label: 'RO-Crate design notes', slug: 'docs/ro-crate' },
            { label: 'Comparison with other tools', slug: 'docs/comparison' },
            { label: 'Benchmarks', slug: 'docs/benchmarks' },
          ],
        },
        {
          label: 'Project',
          items: [
            { label: 'Roadmap', slug: 'docs/roadmap' },
            { label: 'Product roadmap', slug: 'docs/product-roadmap' },
            { label: 'FAQ', slug: 'docs/faq' },
            { label: 'Contributing', slug: 'docs/contributing' },
            { label: 'Architecture', slug: 'docs/architecture' },
            { label: 'Changelog', slug: 'docs/changelog' },
          ],
        },
      ],
    }),
  ],
});
