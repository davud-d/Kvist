import test from 'node:test';
import assert from 'node:assert/strict';
import { COMMANDS, matchCommands, parseCommand, slashQuery } from '../../kvist_web/client/features/commands.js';

test('slashQuery: "/" and one word open the menu', () => {
    assert.equal(slashQuery('/'), '');
    assert.equal(slashQuery('/mo'), 'mo');
});

test('slashQuery: a slash inside a message, a path or after a space opens nothing', () => {
    for (const text of ['', 'fix /tmp/a', '/tmp/a', '/model gpt', ' /model', 'hello'])
        assert.equal(slashQuery(text), null, text);
});

test('slashQuery: a command typed on the Russian layout reads as the Latin keys', () => {
    assert.equal(slashQuery('/ьщ'), 'mo');
    assert.equal(slashQuery('/ЬЩВУД'), 'model');
});

test('matchCommands: filters by the start of a name or an alias', () => {
    assert.deepEqual(matchCommands('').map(c => c.name), COMMANDS.map(c => c.name));
    assert.deepEqual(matchCommands('r').map(c => c.name), ['reasoning']);
    assert.deepEqual(matchCommands('eff').map(c => c.name), ['reasoning']);
    assert.deepEqual(matchCommands('xyz'), []);
});

test('parseCommand: a known command, with or without arguments, is never a message', () => {
    assert.equal(parseCommand('/model').command.name, 'model');
    assert.equal(parseCommand('  /new  ').command.name, 'new');
    assert.equal(parseCommand('/effort high').command.name, 'reasoning');
    assert.equal(parseCommand('/ьщвуд').command.name, 'model');
});

test('parseCommand: an unknown "/word" is reported, as typed', () => {
    assert.deepEqual(parseCommand('/xyz'), { unknown: '/xyz' });
    assert.deepEqual(parseCommand('/modelz please'), { unknown: '/modelz' });
    assert.equal(parseCommand('/models').command.name, 'model', 'the TUI spelling');
    assert.deepEqual(parseCommand('/фыв'), { unknown: '/фыв' });
});

test('parseCommand: messages and paths stay messages', () => {
    for (const text of ['Explain this project', '/tmp/notes is broken', '/', 'see /model'])
        assert.equal(parseCommand(text), null, text);
});
