// Contract fixtures are deliberately test-only. Production never imports them.
export function fixture() {
    const workspace = '/work/Kvist';
    const records = events => events.map((event, index) => ({ seq: index + 1, turn: 1, ts: 1780000000 + index, event }));
    const histories = {
        review: records([
            { type: 'user', text: 'Make session recovery reliable when the connection drops.' },
            { type: 'message', markdown: 'I’ll check the event cursor and reconnect flow, then verify that no messages are lost.' },
            { type: 'step', step: 1, call_id: 'read-1', action_type: 'read_multiple_files', action_args: { paths: ['src/session.ts', 'src/recovery.ts'] }, action_description: 'Read files' },
            { type: 'action_result', step: 1, call_id: 'read-1', action_type: 'read_multiple_files', ok: true, summary: 'Read 8 files', output: 'export function recover() { return cursor; }', path: 'src/recovery.ts' },
            { type: 'step', step: 2, call_id: 'edit-1', action_type: 'replace_in_file', action_args: { path: 'src/recovery.ts' } },
            { type: 'action_result', step: 2, call_id: 'edit-1', action_type: 'replace_in_file', ok: true, path: 'src/recovery.ts', output: 'Cursor recovery updated.' },
            { type: 'step', step: 3, call_id: 'test-1', action_type: 'run_shell', action_args: { command: 'npm test' } },
            { type: 'action_result', step: 3, call_id: 'test-1', action_type: 'run_shell', ok: true, summary: '24 tests passed', output: 'PASS session recovery\n24 tests passed\n0 failed' },
            { type: 'usage', input_tokens: 1200, output_tokens: 350, context_tokens: 69120, context_limit: 128000, model: 'test-model' },
            { type: 'usage', input_tokens: 100, output_tokens: 50, context_tokens: 0, context_limit: 0, model: 'test-model', kind: 'report' },
            { type: 'final', message: '## Session recovery is now consistent\n\nThe event cursor is preserved before reconnecting, so the conversation resumes from the last confirmed event.\n\n- Recover missed events without duplicating messages.\n- Keep background sessions running when switching workspaces.\n- Verify reconnect, cancellation and unread state.\n\nUpdated `src/recovery.ts`. All **24 tests** passed.' },
            { type: 'turn_end' },
        ]),
        unread: records([{ type: 'user', text: 'Review the browser API boundary.' }, { type: 'final', message: 'The browser uses a single versioned API adapter. No runtime imports were found.' }, { type: 'turn_end' }]),
        running: records([{ type: 'user', text: 'Run the regression suite.' }, { type: 'stream_start', stream_id: 'background-stream' }]),
        waiting: records([{ type: 'user', text: 'Update the configuration.' }]),
        failed: records([{ type: 'user', text: 'Inspect the workspace.' }, { type: 'error', message: 'The workspace is unavailable. Check that its directory is still mounted.' }]),
        long: records(Array.from({ length: 2200 }, (_, i) => ({ type: i % 2 ? 'message' : 'user', ...(i % 2 ? { markdown: `Result ${i}: checked the requested file and preserved the session state.\n\nsrc/module-${i}.ts` } : { text: `Inspect module ${i} and explain the result.` }) }))),
        remote: records([{ type: 'user', text: 'Inspect the remote workspace.' }, { type: 'final', message: 'Remote session history restored.' }]),
    };
    const pending = { type: 'input_request', request_id: 'approval-1', kind: 'confirm', title: 'Run the test suite?', body: 'Kvist needs permission to execute this command.', subject: { kind: 'command', command: 'npm test', reason: 'Verify the changes' }, remember_pattern: 'npm test' };
    const sessions = [['review', 'Improve session recovery', 'finished', false], ['unread', 'Review API boundaries', 'finished', true], ['running', 'Run regression tests', 'running', false], ['waiting', 'Update configuration', 'waiting_for_user', false], ['failed', 'Inspect unavailable workspace', 'failed', false], ['long', 'Long conversation', 'idle', false]].map(([name, title, execution, unread]) => ({ name, title, execution, unread, last_event_id: histories[name].length, last_read_event_id: unread ? 0 : histories[name].length, pending_input: name === 'waiting' ? pending : null }));
    const navigation = { servers: [
            { server: { id: 'local', name: 'local', kind: 'local' }, connection: { state: 'connected' }, workspaces: [{ path: workspace, sessions: { session_items: sessions } }] },
            { server: { id: 'server-gz8q', name: 'server-gz8q', kind: 'remote', ssh_alias: 'server-gz8q', host: '', auth_method: 'ssh_config' }, connection: { state: 'connected' }, workspaces: [{ path: '/srv/project', sessions: { session_items: [{ name: 'remote', title: 'Remote workspace review', execution: 'finished', unread: false }] } }] },
            { server: { id: 'gpu-box', name: 'gpu-box', kind: 'remote', host: 'gpu.example', auth_method: 'key', private_key_path: '/keys/test', username: 'dev', port: 22 }, connection: { state: 'offline' }, workspaces: [] },
        ], inbox: [] };
    const inbox = () => { navigation.inbox = sessions.filter(s => s.unread || ['waiting_for_user', 'failed'].includes(s.execution)).map(s => ({ server_id: 'local', server_name: 'local', workspace, session: s.name, title: s.title, state: s.execution, last_event_id: s.last_event_id })); return navigation; };
    return { workspace, histories, sessions, navigation, inbox, records };
}
