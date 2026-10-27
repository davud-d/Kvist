import assert from 'node:assert/strict'
import test from 'node:test'
import { actionIconName, fileIconName } from './file-icon-name'

test('file icons resolve names, language extensions and Windows paths', () => {
  assert.equal(fileIconName('kernel/agent.py'), 'python')
  assert.equal(fileIconName('C:\\project\\Panel.kt'), 'kotlin')
  assert.equal(fileIconName('src/App.tsx'), 'typescript-react')
  assert.equal(fileIconName('types/api.d.ts'), 'typescript-def')
  assert.equal(fileIconName('package.json'), 'package-json')
  assert.equal(fileIconName('README.md'), 'readme')
  assert.equal(fileIconName('unknown.custom'), '_file')
})
test('directory and terminal actions take priority over path suffixes', () => {
  assert.equal(actionIconName('list_dir', 'folder.py'), '_folder_open')
  assert.equal(actionIconName('run_shell', 'npm test'), 'bash')
  assert.equal(actionIconName('search_code', '*.py'), 'search')
  assert.equal(actionIconName('read_file', 'agent.py'), 'python')
})
