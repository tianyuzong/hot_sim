import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('src/thermoflow/web/assets/viewport.mjs', 'utf8');
const method = source.slice(source.indexOf('  async fetchSurface('), source.indexOf('\n  update(options)'));
const pending = [];
const calls = [];
const context = vm.createContext({
  fetch: (path, options) => {
    calls.push({path, options});
    return new Promise(resolve => pending.push(() => resolve({ok: true, json: async () => ({path})})));
  },
});
vm.runInContext(`this.viewport = new (class { constructor() { this.cache = new Map(); } ${method} })();`, context);
const [a, b, c] = [new AbortController(), new AbortController(), new AbortController()];
const first = context.viewport.fetchSurface('/first', {}, a.signal, 'first');
await new Promise(resolve => setImmediate(resolve));
const skipped = context.viewport.fetchSurface('/obsolete', {}, b.signal, 'obsolete').catch(error => error);
const latest = context.viewport.fetchSurface('/latest', {}, c.signal, 'latest');
a.abort(); b.abort();
await new Promise(resolve => setImmediate(resolve));
assert.equal(calls.length, 1, 'A fast slice drag must not send overlapping backend reads');
assert.equal(calls[0].options.signal, undefined, 'An already-started read must finish server-side before the next read');
pending.shift()();
await first;
assert.equal((await skipped).name, 'AbortError');
await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(calls.map(call => call.path), ['/first', '/latest']);
pending.shift()();
assert.equal((await latest).path, '/latest');
assert.equal((await context.viewport.fetchSurface('/latest', {}, c.signal, 'latest')).path, '/latest');
assert.equal(calls.length, 2, 'Cached views must not be refetched');
console.log('Rapid view changes are coalesced; only the latest pending section is fetched.');
