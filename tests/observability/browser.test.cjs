const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('@playwright/test');

test('Chromium correlates fetch/XHR failures and safely reports browser errors', async () => {
  const reports = [];
  const requests = [];
  const id = '7e11d850-e851-4511-bad8-c29b798e35ba';
  const source = fs.readFileSync(path.join(__dirname, '../../web/observability.js'));
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      if (req.url === '/api/client-events') {
        reports.push(JSON.parse(body)); res.writeHead(204).end();
      } else if (req.url === '/observability.js') {
        res.writeHead(200, { 'Content-Type': 'text/javascript' }).end(source);
      } else if (req.url.startsWith('/api/')) {
        requests.push({ body, requestId: req.headers['x-request-id'] });
        res.writeHead(503, { 'X-Request-ID': id }).end('synthetic-response');
      } else {
        res.writeHead(200, { 'Content-Type': 'text/html' }).end('<script src="/observability.js"></script>');
      }
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.evaluate(async () => {
      const response = await fetch('/api/orders?private=synthetic-private-marker', { method: 'POST', body: 'synthetic-private-marker' });
      if (await response.text() !== 'synthetic-response') throw new Error('Response changed');
      await new Promise(resolve => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/portfolio?private=synthetic-private-marker');
        xhr.onloadend = resolve;
        xhr.send('synthetic-private-marker');
      });
      window.dispatchEvent(new ErrorEvent('error', { error: new TypeError('synthetic-private-marker'), message: 'synthetic-private-marker' }));
      window.dispatchEvent(new PromiseRejectionEvent('unhandledrejection', { promise: Promise.resolve(), reason: new Error('synthetic-private-marker') }));
    });
    await page.waitForFunction(() => window.PulseTelemetry.stats().accepted === 4);
    assert.deepEqual(reports.map(row => row.event).sort(), ['api_error', 'api_error', 'js_error', 'promise_rejection']);
    assert.equal(reports.filter(row => row.request_id === id).length, 2);
    assert.equal(JSON.stringify(reports).includes('synthetic-private-marker'), false);
    assert.equal(requests.length, 2);
    assert.ok(requests.every(row => row.body === 'synthetic-private-marker' && row.requestId === undefined));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
