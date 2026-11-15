import test from 'node:test';
import assert from 'node:assert/strict';
import { isWinter } from '../../kvist_web/client/mobile/season.js';

const on = (year, month, day) => new Date(year, month, day, 12);

test('winter starts on the first of December and lasts through February', () => {
    assert.equal(isWinter(on(2026, 10, 30)), false);
    assert.equal(isWinter(on(2026, 11, 1)), true);
    assert.equal(isWinter(on(2026, 11, 31)), true);
    assert.equal(isWinter(on(2027, 0, 15)), true);
    assert.equal(isWinter(on(2027, 1, 28)), true);
    assert.equal(isWinter(on(2027, 2, 1)), false);
});

test('?winter=1 shows winter in summer and ?winter=0 turns it off in the season', () => {
    assert.equal(isWinter(on(2026, 6, 1), '?winter=1'), true);
    assert.equal(isWinter(on(2026, 11, 20), '?winter=0'), false);
    assert.equal(isWinter(on(2026, 6, 1), '?x=1'), false);
});
