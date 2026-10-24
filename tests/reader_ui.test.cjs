// Behavioral checks for asynchronous reader interactions without a live source site.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('templates/reader_pc.html', 'utf8');
function section(start, end) {
    return template.slice(template.indexOf(start), template.indexOf(end, template.indexOf(start)))
        .replace(/{{[\s\S]*?}}/g, '"https://example.com/toc#dir"');
}
function element() {
    const classes = new Set();
    return {style: {}, innerHTML: '', innerText: '', value: '', parentElement: {},
        classList: {add: x => classes.add(x), remove: x => classes.delete(x)},
        querySelectorAll: () => [], classes};
}
function environment() {
    const elements = Object.fromEntries(['tocOverlay', 'tocList', 'tocFilter', 'statsModal',
        'sourceModal', 'sourceList', 'sourceLoading', 'chapterTitle'].map(x => [x, element()]));
    const timers = new Map(); let timer = 0;
    const ctx = vm.createContext({document: {getElementById: id => elements[id]},
        AbortController, URL, console: {log(){}, error(){}, warn(){}},
        setTimeout: fn => {timers.set(++timer, fn); return timer;},
        clearTimeout: id => timers.delete(id),
        currentUrl: 'https://example.com/one', dbKey: 'book', switchContext: {},
        window: {location: {href: ''}}, showToast(){}, loadStats(){},
        normalizeUrl: x => x, filterPcTOC(){}, refreshTocHighlight(){},
        getManualSourceTitle: () => 'book', saveManualSourceTitle(){},
        escapeHtml: x => String(x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    });
    vm.runInContext(section('function showModal(id)', '// 强制刷新') +
        section('let tocLoadController = null;', 'function filterPcTOC') +
        section('let sourceRequestController = null;', '// async function confirmSwitch'), ctx);
    return {ctx, elements, timers};
}
const response = data => ({ok: true, headers: {get: () => 'application/json'}, json: async () => data});
const deferred = () => {let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve};};

test('opening loads the directory; closing aborts without starting another request', async () => {
    const {ctx} = environment(); let count = 0; let signal;
    ctx.fetch = (_url, options) => {count++; signal = options.signal; return new Promise(() => {});};
    ctx.showModal('tocOverlay');
    assert.equal(count, 1);
    ctx.hideModal('tocOverlay');
    assert.equal(count, 1); assert.equal(signal.aborted, true);
});

test('directory keeps volume order, escapes titles, and ignores superseded responses', async () => {
    const {ctx, elements, timers} = environment(); const old = deferred(); let count = 0;
    ctx.fetch = () => ++count === 1 ? old.promise : Promise.resolve(response({chapters: [
        {id: 8, title: '<unsafe>', url: '/eight'}, {id: 1, title: 'volume two', url: '/one'}]}));
    const first = ctx.loadTOC(); await ctx.loadTOC(true);
    const markup = elements.tocList.innerHTML;
    assert.ok(markup.indexOf('%2Feight') < markup.indexOf('%2Fone'));
    assert.ok(markup.includes('&lt;unsafe&gt;')); assert.ok(!markup.includes('<unsafe>'));
    old.resolve(response({chapters: [{id: 99, title: 'stale', url: '/stale'}]})); await first;
    assert.equal(elements.tocList.innerHTML, markup); assert.equal(timers.size, 0);
});

test('source results use functional event listeners, including keyboard activation', async () => {
    const {ctx, elements} = environment(); const handlers = {}; let clicks = 0;
    const card = {dataset: {sourceIndex: '0'}, addEventListener: (key, fn) => handlers[key] = fn,
        click: () => handlers.click()};
    elements.sourceList.style.display = 'none'; elements.sourceList.querySelectorAll = () => [card];
    ctx.fetch = async () => response({status: 'success', data: [{url: 'https://example.com/"x', source: 'source'}]});
    ctx.requestSwitch = url => {assert.equal(url, 'https://example.com/"x'); clicks++;};
    await ctx.loadSourceList(); handlers.click();
    let prevented = false;
    handlers.keydown({key: ' ', preventDefault: () => prevented = true});
    assert.equal(clicks, 2); assert.equal(prevented, true);
    assert.equal(elements.sourceList.style.display, 'block');
    assert.ok(!elements.sourceList.innerHTML.includes('onclick="requestSwitch('));
});

test('closing source matching ignores a late response and never redirects', async () => {
    const {ctx} = environment(); const pending = deferred(); let count = 0; let signal;
    ctx.fetch = (_url, options) => {count++; signal = options.signal; return pending.promise;};
    const task = ctx.requestSwitch('https://example.com/new', 'source');
    ctx.hideModal('sourceModal'); assert.equal(signal.aborted, true);
    pending.resolve(response({status: 'success', new_url: 'https://example.com/new'})); await task;
    assert.equal(count, 1); assert.equal(ctx.window.location.href, '');
});

test('failed progress persistence restores source choices instead of redirecting', async () => {
    const {ctx, elements, timers} = environment(); let count = 0;
    ctx.fetch = async () => response(++count === 1
        ? {status: 'success', new_url: 'https://example.com/new'}
        : {status: 'error', message: 'database unavailable'});
    await ctx.requestSwitch('https://example.com/new', 'source');
    assert.equal(count, 2); assert.equal(ctx.window.location.href, '');
    assert.equal(elements.sourceList.style.display, 'block');
    assert.equal(elements.sourceLoading.style.display, 'none'); assert.equal(timers.size, 0);
});

function searchEnvironment() {
    const source = fs.readFileSync('templates/search.html', 'utf8');
    const extract = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
    const elements = Object.fromEntries(['searchInput', 'resultsContainer', 'statsBar', 'resultCount',
        'historySection', 'historyTags'].map(id => [id, element()]));
    elements.searchInput.value = 'first';
    const loading = {shown: 0, hidden: 0, show(){this.shown++;}, hide(){this.hidden++;}};
    const rendered = [];
    const ctx = vm.createContext({AbortController, setTimeout, clearTimeout, Date,
        console: {log(){}, warn(){}, error(){}},
        window: {location: {origin: 'https://reader.test'}},
        localStorage: {getItem(){throw new Error('storage blocked');}, setItem(){throw new Error('quota');}},
        document: {getElementById: id => elements[id]}, PureUI: {loading},
        alert(){}, renderResults: results => rendered.push(results)});
    vm.runInContext(extract('const API_URL =', '// 初始化') +
        extract('function renderHistory()', '// 渲染结果') +
        extract('async function pollSearchTask', '</script>'), ctx);
    return {ctx, elements, loading, rendered};
}

test('superseded search cannot overwrite current results or dismiss current loading', async () => {
    const {ctx, elements, loading, rendered} = searchEnvironment();
    const first = deferred(); const second = deferred(); let count = 0; let firstSignal;
    ctx.fetch = (_url, options) => {
        if (++count === 1) {firstSignal = options.signal; return first.promise;}
        return second.promise;
    };
    const oldTask = ctx.doSearch(); elements.searchInput.value = 'second'; const newTask = ctx.doSearch();
    assert.equal(firstSignal.aborted, true);
    first.resolve(response({data: [{title: 'old'}]})); await oldTask;
    assert.equal(rendered.length, 0); assert.equal(loading.hidden, 0);
    second.resolve(response({data: [{title: 'new'}]})); await newTask;
    assert.equal(rendered[0][0].title, 'new'); assert.equal(loading.hidden, 1);
});

test('search waits for pending task completion before hiding loading', async () => {
    const {ctx, loading, rendered} = searchEnvironment(); const pending = deferred(); let count = 0;
    ctx.fetch = async () => ++count === 1 ? response({status: 'pending', task_id: 'task'}) : pending.promise;
    const task = ctx.doSearch();
    // Drain the POST and enter the actual polling request.
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(count, 2); assert.equal(loading.hidden, 0);
    pending.resolve(response({status: 'completed', result: [{title: 'polled'}]})); await task;
    assert.equal(rendered[0][0].title, 'polled'); assert.equal(loading.hidden, 1);
});

test('business failure shows escaped diagnostic detail, rather than no-results state', async () => {
    const {ctx, elements} = searchEnvironment();
    ctx.fetch = async () => response({status: 'error', message: '<img src=x onerror=alert(1)>'});
    await ctx.doSearch();
    assert.ok(elements.resultsContainer.innerHTML.includes('&lt;img'));
    assert.ok(!elements.resultsContainer.innerHTML.includes('<img'));
    assert.ok(!elements.resultsContainer.innerHTML.includes('没有找到相关结果'));
});
