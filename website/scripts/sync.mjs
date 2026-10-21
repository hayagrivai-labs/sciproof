// Pre-build step: turns the repository's own files into website content.
//
// Single source of truth: README.md, docs/*.md, SECURITY.md, CONTRIBUTING.md, MAINTAINERS.md,
// pyproject.toml, src/ and examples/ are read from the repository root. Nothing is copied by
// hand into website/. Everything generated here is git-ignored (see website/.gitignore).
//
// Network access (GitHub API, PyPI) is optional: when a request fails the site is still built
// from what is available and the affected section says so.

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE, REPO, REPO_URL, DEFAULT_BRANCH, SITE } from '../site.config.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(here, '..');
const ROOT = path.resolve(WEB, '..');
const DOCS_OUT = path.join(WEB, 'src/content/docs/docs');
const DATA_OUT = path.join(WEB, 'src/data');
const AVATARS_OUT = path.join(WEB, 'public/avatars');

const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8').replace(/\r\n/g, '\n');
const exists = (rel) => fs.existsSync(path.join(ROOT, rel));
const writeData = (name, value) => {
  fs.mkdirSync(DATA_OUT, { recursive: true });
  fs.writeFileSync(path.join(DATA_OUT, name), JSON.stringify(value, null, 2) + '\n');
};
const fail = (message) => {
  console.error(`\nsync: ${message}\n`);
  process.exit(1);
};

// ---------------------------------------------------------------------------------------------
// Which repository file becomes which page
// ---------------------------------------------------------------------------------------------

const PAGES = [
  {
    slug: 'getting-started',
    title: 'Getting started',
    source: 'README.md',
    sections: ['Install', 'Quick start', 'Commands'],
    description: 'Install SciProof, capture an experiment, inspect and verify its evidence.',
  },
  { slug: 'use-cases', title: 'Use cases', source: 'docs/USE_CASES.md', description: 'Where SciProof fits: ML experiments, scientific software, simulations, pipelines.' },
  { slug: 'concepts', title: 'Concepts', source: 'docs/CONCEPTS.md', description: 'What SciProof can and cannot tell you.' },
  {
    slug: 'tutorial-numpy-pandas',
    title: 'Tutorial: NumPy & pandas',
    source: 'docs/TUTORIAL_NUMPY_PANDAS.md',
    description: 'Record an experiment that uses NumPy and pandas and choose tolerances.',
  },
  { slug: 'manifest', title: 'Manifest format', source: 'docs/MANIFEST.md', description: 'The evidence manifest, field by field.' },
  { slug: 'exit-codes', title: 'Exit codes', source: 'docs/EXIT_CODES.md', description: 'Exit codes and verdicts of every command.' },
  { slug: 'python-api', title: 'Python API', source: 'docs/PYTHON_API.md', description: 'Capture and verify from Python: functions, types and stability.' },
  { slug: 'policy', title: 'Policies', source: 'docs/POLICY.md', description: 'Rules about the evidence itself, checked on every verification.' },
  { slug: 'update', title: 'Updating a baseline', source: 'docs/UPDATE.md', description: 'sciproof update: re-baseline an experiment and see what changed.' },
  { slug: 'claims', title: 'Claims', source: 'docs/CLAIMS.md', description: 'Checkable statements about values in output files.' },
  { slug: 'signing', title: 'Signing manifests', source: 'docs/SIGNING.md', description: 'Sign a manifest and check the signature with OpenSSH.' },
  { slug: 'export', title: 'Listing and exporting', source: 'docs/LIST_AND_EXPORT.md', description: 'sciproof list and sciproof export (in-toto Statement).' },
  { slug: 'diff', title: 'Comparing two runs', source: 'docs/DIFF.md', description: 'sciproof diff: what changed between two recorded runs.' },
  { slug: 'report', title: 'Verification report', source: 'docs/REPORT.md', description: 'What a report contains, how verdicts are combined and the JSON report schema.' },
  { slug: 'numeric-comparison', title: 'Numeric comparison rules', source: 'docs/COMPARISON.md', description: 'The exact rules of the tolerance comparison for JSON, CSV and .npy outputs.' },
  { slug: 'ci', title: 'Using SciProof in CI', source: 'docs/CI.md', description: 'Verify evidence in a GitHub Actions job.' },
  { slug: 'security', title: 'Security & threat model', source: 'SECURITY.md', description: 'What SciProof protects against and what it does not.' },
  { slug: 'signing-design', title: 'Signing: design notes', source: 'docs/DESIGN_SIGNING.md', description: 'Design note on signing and timestamping manifests.' },
  { slug: 'ro-crate', title: 'RO-Crate design notes', source: 'docs/DESIGN_RO_CRATE.md', description: 'Mapping a manifest to the Process Run Crate profile.' },
  { slug: 'benchmarks', title: 'Benchmarks', source: 'docs/BENCHMARKS.md', description: 'Ten verification scenarios, run end to end and measured.' },
  { slug: 'product-roadmap', title: 'Product roadmap', source: 'docs/PRODUCT_ROADMAP.md', description: 'Personas, priorities, exclusions and a validation plan.' },
  { slug: 'comparison', title: 'Comparison with other tools', source: 'docs/COMPETITIVE_ANALYSIS.md', description: 'How SciProof compares with DVC and MLflow, the most widely used tools in the area.' },
  {
    slug: 'roadmap',
    title: 'Roadmap',
    source: 'README.md',
    sections: ['Roadmap'],
    dropHeading: true,
    description: 'What might come next, decided by feedback.',
  },
  { slug: 'faq', title: 'FAQ', source: 'docs/FAQ.md', description: 'Frequently asked questions.' },
  { slug: 'badge', title: 'Show your evidence', source: 'docs/BADGE.md', description: 'Tell readers your results come with checkable evidence.' },
  { slug: 'architecture', title: 'Architecture for contributors', source: 'docs/ARCHITECTURE.md', description: 'A map of the code for new contributors.' },
  { slug: 'contributing', title: 'Contributing', source: 'CONTRIBUTING.md', description: 'How to help.' },
];

// repository file -> site route (used to rewrite relative links between source files)
const ROUTES = new Map();
for (const page of PAGES) if (!page.sections) ROUTES.set(page.source, `${BASE}/docs/${page.slug}/`);
ROUTES.set('README.md', `${BASE}/docs/getting-started/`);

// ---------------------------------------------------------------------------------------------
// Markdown helpers
// ---------------------------------------------------------------------------------------------

/** Rewrite a link found in `fromSource` so that it works on the site. */
function rewriteHref(href, fromSource) {
  if (/^([a-z][a-z0-9+.-]*:|#|\/\/)/i.test(href)) return href;
  const [target, anchor = ''] = href.split('#');
  const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(fromSource), target));
  const suffix = anchor ? `#${anchor}` : '';
  if (ROUTES.has(resolved)) return ROUTES.get(resolved) + suffix;
  if (resolved.startsWith('..')) return href;
  const isDir = fs.existsSync(path.join(ROOT, resolved)) && fs.statSync(path.join(ROOT, resolved)).isDirectory();
  return `${REPO_URL}/${isDir ? 'tree' : 'blob'}/${DEFAULT_BRANCH}/${resolved}${suffix}`;
}

/** Apply `fn` to every line outside fenced code blocks. */
function outsideFences(markdown, fn) {
  let inFence = false;
  return markdown
    .split('\n')
    .map((line) => {
      if (/^\s*(```|~~~)/.test(line)) {
        inFence = !inFence;
        return line;
      }
      return inFence ? line : fn(line);
    })
    .join('\n');
}

function rewriteLinks(markdown, fromSource) {
  return outsideFences(markdown, (line) =>
    line.replace(/\]\(([^)\s]+)((?:\s+"[^"]*")?)\)/g, (_m, href, title) => `](${rewriteHref(href, fromSource)}${title})`),
  );
}

const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** Tiny inline-markdown to HTML converter for table cells and list items (code, bold, em, links). */
function inlineHtml(text, fromSource) {
  const spans = [];
  let out = text.replace(/`([^`]+)`/g, (_m, code) => {
    spans.push(`<code>${escapeHtml(code)}</code>`);
    return `\u0000${spans.length - 1}\u0000`;
  });
  out = escapeHtml(out);
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, href) => {
    const target = rewriteHref(href.replace(/&amp;/g, '&'), fromSource);
    const external = /^https?:/.test(target) && !target.startsWith(SITE);
    return `<a href="${escapeHtml(target)}"${external ? ' rel="noopener"' : ''}>${label}</a>`;
  });
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*\s][^*]*)\*/g, '<em>$1</em>');
  return out.replace(/\u0000(\d+)\u0000/g, (_m, i) => spans[Number(i)]);
}

/** Split a markdown file into { heading -> body } for its `## ` sections (keeps order). */
function sectionsOf(markdown) {
  const result = [];
  let current = null;
  for (const line of markdown.split('\n')) {
    const match = /^## (.+)$/.exec(line);
    if (match) {
      current = { heading: match[1].trim(), lines: [] };
      result.push(current);
    } else if (current) {
      current.lines.push(line);
    }
  }
  return result.map((s) => ({ heading: s.heading, body: s.lines.join('\n').trim() }));
}

const findSection = (markdown, prefix) => {
  const hit = sectionsOf(markdown).find((s) => s.heading.startsWith(prefix));
  if (!hit) fail(`section "${prefix}" not found. The site reads it from the repository; was it renamed?`);
  return hit;
};

function tableRows(body) {
  const rows = body.split('\n').filter((l) => /^\s*\|/.test(l));
  const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  return rows.filter((l) => !/^\s*\|\s*-/.test(l)).map(cells).slice(1);
}

function bullets(body) {
  const items = [];
  for (const line of body.split('\n')) {
    if (/^(\*|-|\d+\.) /.test(line)) items.push(line.replace(/^(\*|-|\d+\.) /, ''));
    else if (/^\s{2,}\S/.test(line) && items.length) items[items.length - 1] += ' ' + line.trim();
  }
  return items;
}

// ---------------------------------------------------------------------------------------------
// Shell / git / network helpers
// ---------------------------------------------------------------------------------------------

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', ...options });
  return result;
}

function gitDate(rel) {
  const result = run('git', ['log', '-1', '--format=%cI', '--', rel], { cwd: ROOT });
  const value = (result.stdout || '').trim();
  return result.status === 0 && value ? value : null;
}

async function getJson(url, { allow404 = false } = {}) {
  const headers = { Accept: 'application/vnd.github+json', 'User-Agent': 'sciproof-website-build' };
  if (url.startsWith('https://api.github.com') && process.env.GITHUB_TOKEN) {
    headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
  }
  try {
    const response = await fetch(url, { headers, signal: AbortSignal.timeout(20000) });
    if (response.status === 404 && allow404) return null;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    console.warn(`sync: could not fetch ${url}: ${error.message}`);
    return undefined;
  }
}

// ---------------------------------------------------------------------------------------------
// 1. Version
// ---------------------------------------------------------------------------------------------

const pyproject = read('pyproject.toml');
const version = /^version\s*=\s*"([^"]+)"/m.exec(pyproject)?.[1];
const projectName = /^name\s*=\s*"([^"]+)"/m.exec(pyproject)?.[1];
if (!version || !projectName) fail('could not read name/version from pyproject.toml');
writeData('version.json', { name: projectName, version });

// ---------------------------------------------------------------------------------------------
// 2. Docs pages generated from repository files
// ---------------------------------------------------------------------------------------------

function frontmatter({ title, description, source, lastUpdated, editUrl }) {
  const lines = ['---', `title: ${JSON.stringify(title)}`, `description: ${JSON.stringify(description)}`];
  if (editUrl === false) lines.push('editUrl: false');
  else lines.push(`editUrl: ${editUrl ?? `${REPO_URL}/edit/${DEFAULT_BRANCH}/${source}`}`);
  if (lastUpdated) lines.push(`lastUpdated: ${lastUpdated}`);
  else lines.push('lastUpdated: false');
  lines.push('---', '');
  return lines.join('\n');
}

fs.rmSync(DOCS_OUT, { recursive: true, force: true });
fs.mkdirSync(DOCS_OUT, { recursive: true });

for (const page of PAGES) {
  if (!exists(page.source)) fail(`${page.source} (source of "${page.title}") does not exist`);
  let body = read(page.source);
  if (page.sections) {
    body = page.sections
      .map((prefix) => {
        const section = findSection(body, prefix);
        return page.dropHeading ? section.body : `## ${section.heading}\n\n${section.body}`;
      })
      .join('\n\n');
  } else {
    body = body.replace(/^# .*\n+/, ''); // the page title comes from the frontmatter
  }
  body = rewriteLinks(body, page.source);
  fs.writeFileSync(
    path.join(DOCS_OUT, `${page.slug}.md`),
    frontmatter({ ...page, lastUpdated: gitDate(page.source) }) + body.trim() + '\n',
  );
}

// ---------------------------------------------------------------------------------------------
// 3. CLI reference, generated from the real CLI
// ---------------------------------------------------------------------------------------------

function sciproof(args) {
  const env = { ...process.env, COLUMNS: '88', PYTHONIOENCODING: 'utf-8' };
  const candidates = [
    [process.env.SCIPROOF_BIN || 'sciproof', args],
    [process.platform === 'win32' ? 'python' : 'python3', ['-m', 'sciproof', ...args]],
  ];
  for (const [command, commandArgs] of candidates) {
    const result = run(command, commandArgs, { env, cwd: os.tmpdir() });
    if (!result.error && result.status === 0) return result.stdout.replace(/\r\n/g, '\n').trimEnd();
  }
  fail(
    `could not run "sciproof ${args.join(' ')}". Install the package first (from the repository root: ` +
      '`python -m pip install -e .`) or point SCIPROOF_BIN at the executable.',
  );
}

const installedVersion = /(\d+\.\d+\.\d+\S*)/.exec(sciproof(['--version']))?.[1];
if (installedVersion !== version) {
  console.warn(`sync: installed sciproof is ${installedVersion} but pyproject.toml says ${version}`);
}
const helpPages = [
  ['sciproof --help', ['--help'], ''],
  ['sciproof capture --help', ['capture', '--help'], 'Runs the command once and writes `NAME.sciproof.json`.'],
  ['sciproof verify --help', ['verify', '--help'], 'Integrity by default; `--rerun` executes the recorded command.'],
  ['sciproof inspect --help', ['inspect', '--help'], 'Never executes anything.'],
];
const cliBody = [
  'This page is generated at build time from the output of the installed CLI, so it always matches the code.',
  `Version: \`${version}\`. For exit codes see [Exit codes](${BASE}/docs/exit-codes/).`,
  '',
  ...helpPages.flatMap(([title, args, note]) => [
    `## \`${title}\``,
    '',
    ...(note ? [note, ''] : []),
    '```text',
    sciproof(args),
    '```',
    '',
  ]),
].join('\n');
fs.writeFileSync(
  path.join(DOCS_OUT, 'cli-reference.md'),
  frontmatter({
    title: 'CLI reference',
    description: 'Generated from the output of `sciproof --help` and its subcommands.',
    source: 'src/sciproof/cli.py',
    lastUpdated: gitDate('src/sciproof/cli.py'),
  }) + cliBody,
);

// ---------------------------------------------------------------------------------------------
// 4. Landing page data from the README
// ---------------------------------------------------------------------------------------------

const readme = read('README.md');
const quick = findSection(readme, 'Quick start').body;

// terminal demo: the exact commands and outputs of the README quick start
const demoSteps = [];
{
  let title = '';
  const lines = quick.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const heading = /^\*\*(\d+\.\s[^*]+)\*\*/.exec(lines[i]);
    if (heading) title = heading[1].replace(/^\d+\.\s*/, '').replace(/\.$/, '').trim();
    if (!/^```console\s*$/.test(lines[i])) continue;
    const block = [];
    for (i++; i < lines.length && !/^```\s*$/.test(lines[i]); i++) block.push(lines[i]);
    const command = [];
    const output = [];
    let continuing = false;
    for (const line of block) {
      if (line.startsWith('$ ') || continuing) {
        command.push(line.replace(/^\$ /, ''));
        continuing = /\\\s*$/.test(line);
      } else {
        output.push(line);
      }
    }
    demoSteps.push({ title, command, output });
  }
}
if (demoSteps.length < 5) fail(`expected 5 console blocks in the README quick start, found ${demoSteps.length}`);

const levelsTable = tableRows(findSection(readme, 'What it does and does not prove').body);
const relatedTable = tableRows(findSection(readme, 'Related tools').body);
const limitations = bullets(findSection(readme, 'Threat model and limitations').body);
const intro = readme.split('\n').slice(0, 12).join('\n');
const tagline = /\n\n(Record a computational experiment once[\s\S]*?)\n\n/.exec(intro)?.[1].replace(/\n/g, ' ');
const problem = findSection(readme, 'The problem').body.replace(/\n/g, ' ');

writeData('landing.json', {
  tagline: inlineHtml(tagline ?? '', 'README.md'),
  problem: inlineHtml(problem, 'README.md'),
  levels: levelsTable.map(([level, question, how]) => ({
    level: inlineHtml(level, 'README.md'),
    question: inlineHtml(question, 'README.md'),
    how: inlineHtml(how, 'README.md'),
    notAddressed: /not addressed/i.test(how),
  })),
  related: relatedTable.map(([tool, relation]) => ({
    tool: inlineHtml(tool, 'README.md'),
    relation: inlineHtml(relation, 'README.md'),
  })),
  limitations: limitations.map((item) => inlineHtml(item, 'README.md')),
  demo: demoSteps,
});

// ---------------------------------------------------------------------------------------------
// 5. The tolerance rule, read from the code
// ---------------------------------------------------------------------------------------------

{
  const source = read('src/sciproof/numeric.py');
  const match = /def _numbers_close\([\s\S]*?\n(?=\n\n)/.exec(source);
  const expected = 'return abs(a - b) <= max(rtol * max(abs(a), abs(b)), atol)';
  if (!match || !match[0].includes(expected)) {
    fail(
      'src/sciproof/numeric.py:_numbers_close no longer has the expected comparison rule. ' +
        'Update website/src/components/TolerancePlayground.astro so that it matches, then this check.',
    );
  }
  writeData('tolerance.json', { file: 'src/sciproof/numeric.py', function: '_numbers_close', source: match[0].trimEnd() });
}

// ---------------------------------------------------------------------------------------------
// 6. A real manifest produced by the README quick start, and field descriptions from MANIFEST.md
// ---------------------------------------------------------------------------------------------

{
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sciproof-site-'));
  fs.mkdirSync(path.join(tmp, 'examples'));
  for (const f of ['experiment.py', 'params.json']) fs.copyFileSync(path.join(ROOT, 'examples', f), path.join(tmp, 'examples', f));
  const joined = demoSteps[0].command.map((l) => l.replace(/\\\s*$/, '').trim()).join(' ');
  const args = joined.split(/\s+/);
  if (args[0] !== 'sciproof' || args[1] !== 'capture') fail(`unexpected capture command in README: ${joined}`);
  const bin = process.env.SCIPROOF_BIN || 'sciproof';
  let result = run(bin, args.slice(1), { cwd: tmp, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  if (result.error) result = run(process.platform === 'win32' ? 'python' : 'python3', ['-m', 'sciproof', ...args.slice(1)], { cwd: tmp });
  if (result.status !== 0) fail(`the README capture command failed:\n${result.stdout}\n${result.stderr}`);
  const manifest = JSON.parse(fs.readFileSync(path.join(tmp, 'baseline.sciproof.json'), 'utf8'));
  fs.rmSync(tmp, { recursive: true, force: true });

  const notes = [];
  const manifestDoc = read('docs/MANIFEST.md');
  for (const heading of ['Notes', 'Directories']) {
    const section = sectionsOf(manifestDoc).find((s) => s.heading.startsWith(heading));
    if (!section) continue;
    for (const item of bullets(section.body)) {
      const tokens = [...item.slice(0, 70).matchAll(/`([^`]+)`/g)].map((m) => m[1]);
      if (tokens.length) notes.push({ keys: tokens, html: inlineHtml(item, 'docs/MANIFEST.md') });
    }
  }
  const fieldSection = sectionsOf(manifestDoc).find((s) => s.heading.startsWith('Field reference'));
  if (!fieldSection) fail('docs/MANIFEST.md has no "Field reference" section (used by the manifest explorer)');
  const fields = tableRows(fieldSection.body).map(([field, meaning]) => ({
    path: field.replace(/^`|`$/g, ''),
    html: inlineHtml(meaning, 'docs/MANIFEST.md'),
  }));
  writeData('sample-manifest.json', { command: joined, manifest, fields, notes });
}

// ---------------------------------------------------------------------------------------------
// 7. Community data: GitHub, PyPI, maintainers
// ---------------------------------------------------------------------------------------------

const apiBase = `https://api.github.com/repos/${REPO}`;
const [repoInfo, contributorsRaw, latestRelease, releasesRaw] = await Promise.all([
  getJson(apiBase),
  getJson(`${apiBase}/contributors?per_page=100`),
  getJson(`${apiBase}/releases/latest`, { allow404: true }),
  getJson(`${apiBase}/releases?per_page=30`),
]);

const isBot = (c) => c.type === 'Bot' || /\[bot\]$/i.test(c.login);
const contributors = (Array.isArray(contributorsRaw) ? contributorsRaw : [])
  .filter((c) => !isBot(c))
  .map((c) => ({ login: c.login, url: c.html_url, avatar: c.avatar_url, contributions: c.contributions }));

fs.rmSync(AVATARS_OUT, { recursive: true, force: true });
fs.mkdirSync(AVATARS_OUT, { recursive: true });
for (const c of contributors) {
  try {
    const response = await fetch(`${c.avatar}${c.avatar.includes('?') ? '&' : '?'}s=96`, { signal: AbortSignal.timeout(20000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    // Avatars are copied at build time so that visitors never contact a third-party image host.
    fs.writeFileSync(path.join(AVATARS_OUT, `${c.login}.png`), Buffer.from(await response.arrayBuffer()));
    c.localAvatar = `${BASE}/avatars/${c.login}.png`;
  } catch (error) {
    console.warn(`sync: no avatar for ${c.login}: ${error.message}`);
  }
  delete c.avatar;
}

function parseMaintainers() {
  if (!exists('MAINTAINERS.md')) return [];
  const withoutComments = read('MAINTAINERS.md').replace(/<!--[\s\S]*?-->/g, '');
  return withoutComments
    .split('\n')
    .filter((l) => /^- @[A-Za-z0-9-]+/.test(l))
    .map((l) => {
      const [login, name, role] = l.slice(3).split(' - ').map((s) => s.trim());
      return { login: login.replace(/^@/, ''), name: name || null, role: role || null };
    });
}
function parseCodeowners() {
  const file = ['.github/CODEOWNERS', 'CODEOWNERS', 'docs/CODEOWNERS'].find(exists);
  if (!file) return [];
  const logins = new Set();
  for (const line of read(file).split('\n')) {
    if (line.trim().startsWith('#')) continue;
    for (const m of line.matchAll(/@([A-Za-z0-9-]+)(?!\/)/g)) logins.add(m[1]);
  }
  return [...logins];
}
const maintainers = parseMaintainers();
const codeowners = parseCodeowners();
for (const m of maintainers) m.codeOwner = codeowners.includes(m.login);

const releaseSummary = (r) => (r ? { tag: r.tag_name, name: r.name || r.tag_name, url: r.html_url, publishedAt: r.published_at } : null);
writeData('community.json', {
  fetchedAt: repoInfo ? new Date().toISOString() : null,
  repo: repoInfo
    ? { stars: repoInfo.stargazers_count, forks: repoInfo.forks_count, openIssues: repoInfo.open_issues_count }
    : null,
  contributors,
  maintainers,
  codeowners: codeowners.filter((l) => !maintainers.some((m) => m.login === l)),
  release: releaseSummary(latestRelease || null),
});

// changelog page from GitHub Releases
const releases = (Array.isArray(releasesRaw) ? releasesRaw : []).filter((r) => !r.draft);
const changelogBody = releases.length
  ? releases
      .map((r) => `## [${r.name || r.tag_name}](${r.html_url})\n\n_${(r.published_at || '').slice(0, 10)}${r.prerelease ? ' (pre-release)' : ''}_\n\n${(r.body || '').trim() || 'No release notes.'}`)
      .join('\n\n')
  : releasesRaw === undefined
    ? 'The list of releases could not be loaded when this page was built. See [all releases on GitHub](' + `${REPO_URL}/releases).`
    : `No releases have been published yet. Watch [the releases page](${REPO_URL}/releases) on GitHub.`;
fs.writeFileSync(
  path.join(DOCS_OUT, 'changelog.md'),
  frontmatter({
    title: 'Changelog',
    description: 'Release notes, generated from GitHub Releases at build time.',
    source: '',
    editUrl: false,
  }).replace('lastUpdated: false', 'lastUpdated: false') + changelogBody + '\n',
);

// PyPI: only trust a package that points back to this repository
let pypi = { published: false };
{
  const info = await getJson(`https://pypi.org/pypi/${projectName}/json`, { allow404: true });
  if (info?.info) {
    const urls = JSON.stringify(info.info.project_urls ?? {}) + (info.info.home_page ?? '');
    if (urls.includes(REPO)) {
      const files = info.urls ?? [];
      pypi = { published: true, version: info.info.version, releasedAt: files[0]?.upload_time_iso_8601 ?? null };
    } else {
      console.warn(`sync: "${projectName}" exists on PyPI but does not point to ${REPO}; ignoring it`);
    }
  }
}
writeData('pypi.json', pypi);
writeData('install.json', {
  published: pypi.published,
  command: pypi.published ? `pipx install ${projectName}` : `pipx install git+${REPO_URL}.git`,
  // TODO(owner): once the package is published on PyPI this switches to `pipx install sciproof` by itself.
  note: pypi.published ? null : 'Not on PyPI yet, so this installs from GitHub.',
});
writeData('meta.json', { builtAt: new Date().toISOString(), site: SITE + BASE });

console.log(`sync: ${PAGES.length + 2} pages, ${demoSteps.length} demo steps, ${contributors.length} contributors, version ${version}`);
