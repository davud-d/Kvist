import test from 'node:test';
import assert from 'node:assert/strict';
import { COMMANDS, matchCommands, parseCommand } from '../../kvist_web/client/features/commands.js';

// /council and /best-of are turns of the agent (UI_API.md section 8c): with a question the whole line is a message
// the core reads as the command; bare, the menu completes the name instead of sending nothing.

test('both commands are in the menu', () => {
    assert.deepEqual(matchCommands('cou').map(c => c.name), ['council']);
    assert.deepEqual(matchCommands('best').map(c => c.name), ['best-of']);
    assert.ok(COMMANDS.filter(c => c.turn).every(c => ['council', 'best-of'].includes(c.name)));
});

test('a turn command with a question is a message, so it reaches the core as a goal line', () => {
    assert.equal(parseCommand('/council is it simple?'), null);
    assert.equal(parseCommand('/best-of 3 fix the bug'), null);
    assert.equal(parseCommand('/council first line\nsecond line'), null);
});

test('a bare turn command is a command of the menu, not a message and not unknown', () => {
    assert.equal(parseCommand('/council').command.name, 'council');
    assert.equal(parseCommand('  /best-of  ').command.name, 'best-of');
    assert.equal(parseCommand('/council   ').command.turn, true);
});

test('the other commands read as before, arguments included', () => {
    assert.equal(parseCommand('/effort high').command.name, 'reasoning');
    assert.deepEqual(parseCommand('/xyz now'), { unknown: '/xyz' });
    assert.equal(parseCommand('/tmp/notes is broken'), null);
});
