const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('templates/index.html', 'utf8');
const start = html.indexOf('        function openExportHistory()');
const end = html.indexOf('        async function downloadLocalFile(', start);
assert.ok(start >= 0 && end > start);
const code = html.slice(start, end);

function setup(fetch) {
    const elements = Object.fromEntries(['exportHistoryList', 'downloadsList', 'exportTasksTab', 'exportFilesTab'].map(id => [id, {
        style: {}, textContent: '', innerHTML: '', attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        replaceChildren() {}, appendChild() {},
    }]));
    const opened = [];
    const context = vm.createContext({
        document: {getElementById: id => elements[id]},
        PureUI: {modal: {open: id => opened.push(id)}},
        fetch, console, setTimeout, Date,
    });
    vm.runInContext(code, context);
    return {context, elements, opened};
}

test('unified entry opens tasks and allows switching to files', async () => {
    const urls = [];
    const {context, elements, opened} = setup(async url => {
        urls.push(url);
        return {ok: true, status: 200, json: async () => url.includes('/export/') ? {success: true, tasks: []} : {success: true, files: []}};
    });
    context.openExportHistory();
    await new Promise(setImmediate);
    assert.equal(opened[0], 'exportHistoryModal');
    assert.match(elements.exportHistoryList.innerHTML, /暂无导出记录/);
    context.showExportPanel('files');
    await new Promise(setImmediate);
    assert.equal(elements.downloadsList.style.display, 'block');
    assert.match(elements.downloadsList.innerHTML, /暂无下载文件/);
    assert.deepEqual(urls, ['/api/export/list', '/api/pro/list_downloads']);
});

test('files panel clearly reports missing permission', async () => {
    const {context, elements} = setup(async () => ({status: 403, json: async () => ({status: 'error'})}));
    await context.openDownloadsManager();
    assert.match(elements.downloadsList.textContent, /权限/);
});

test('existing modal close controls suppress duplicate injection', () => {
    const pure = fs.readFileSync('purecss/pure2.1.js', 'utf8');
    assert.match(pure, /\.p-modal-close-btn, \[data-modal-close\]/);
    assert.match(html, /data-modal-close onclick="PureUI\.modal\.close\('exportHistoryModal'\)"/);
    assert.doesNotMatch(html, /id="downloadsManagerModal"/);
});
