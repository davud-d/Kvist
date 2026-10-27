const extensions: Record<string, string> = {
  py: 'python', pyi: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript',
  jsx: 'javascript-react', ts: 'typescript', tsx: 'typescript-react',
  kt: 'kotlin', kts: 'kotlin', java: 'java', rs: 'rust', go: 'go',
  c: 'c', h: 'c-header', cpp: 'cpp', cc: 'cpp', hpp: 'cpp-header', cs: 'csharp',
  html: 'html', css: 'css', scss: 'sass', sass: 'sass', json: 'json', jsonc: 'json',
  md: 'markdown', mdx: 'markdown-mdx', yaml: 'yaml', yml: 'yaml', toml: 'toml',
  sh: 'bash', bash: 'bash', zsh: 'bash', fish: 'bash', ps1: 'powershell',
  sql: 'database', xml: 'xml', svg: 'svg', png: 'image', jpg: 'image', webp: 'image',
  vue: 'vue', svelte: 'svelte', rb: 'ruby', php: 'php', swift: 'swift', dart: 'dart',
}
const filenames: Record<string, string> = {
  'package.json': 'package-json', 'package-lock.json': 'npm-lock',
  'tsconfig.json': 'typescript-config', 'dockerfile': 'docker',
  'docker-compose.yml': 'docker-compose', 'compose.yaml': 'docker-compose',
  'cargo.toml': 'cargo', 'cargo.lock': 'cargo-lock', 'go.mod': 'go-mod',
  '.gitignore': 'git', 'license': 'license', 'makefile': 'makefile',
}

export function fileIconName(path: string): string {
  const name = path.replaceAll('\\', '/').split('/').at(-1)?.toLowerCase() ?? ''
  if (filenames[name]) return filenames[name]
  if (/^readme(?:\.|$)/.test(name)) return 'readme'
  if (/\.d\.ts$/.test(name)) return 'typescript-def'
  if (/\.test\.tsx?$/.test(name)) return 'typescript-test'
  return extensions[name.split('.').at(-1) ?? ''] ?? '_file'
}

export function actionIconName(action: string, path: string): string {
  if (['list_dir', 'list_tree'].includes(action)) return '_folder_open'
  if (action === 'run_shell') return 'bash'
  if (action === 'git_status' || action === 'git_diff') return 'git'
  if (action === 'find_files' || action === 'search_code') return 'search'
  return fileIconName(path)
}
