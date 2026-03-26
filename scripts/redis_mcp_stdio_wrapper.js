#!/usr/bin/env node

const { spawn } = require('child_process');

const BANNER_PREFIX = 'Redis MCP server is running and connected to Redis using url ';

const child = spawn('/usr/local/bin/redis-mcp', process.argv.slice(2), {
  stdio: ['pipe', 'pipe', 'pipe'],
  env: process.env,
});

function writeFramed(jsonText) {
  const body = Buffer.from(jsonText, 'utf8');
  const header = Buffer.from(`Content-Length: ${body.length}\r\n\r\n`, 'utf8');
  process.stdout.write(Buffer.concat([header, body]));
}

let parentBuffer = Buffer.alloc(0);
process.stdin.on('data', (chunk) => {
  parentBuffer = Buffer.concat([parentBuffer, chunk]);

  while (true) {
    const sep = parentBuffer.indexOf('\r\n\r\n');
    if (sep === -1) return;

    const headerText = parentBuffer.slice(0, sep).toString('utf8');
    const lenMatch = /Content-Length:\s*(\d+)/i.exec(headerText);
    if (!lenMatch) {
      process.stderr.write(`redis_mcp_wrapper: invalid MCP header: ${headerText}\n`);
      parentBuffer = Buffer.alloc(0);
      return;
    }

    const contentLength = Number(lenMatch[1]);
    const start = sep + 4;
    const end = start + contentLength;
    if (parentBuffer.length < end) return;

    const body = parentBuffer.slice(start, end).toString('utf8');
    child.stdin.write(body + '\n');
    parentBuffer = parentBuffer.slice(end);
  }
});

process.stdin.on('end', () => child.stdin.end());

let childStdoutBuffer = '';
child.stdout.on('data', (chunk) => {
  childStdoutBuffer += chunk.toString('utf8');

  while (true) {
    const newline = childStdoutBuffer.indexOf('\n');
    if (newline === -1) return;

    const line = childStdoutBuffer.slice(0, newline).replace(/\r$/, '');
    childStdoutBuffer = childStdoutBuffer.slice(newline + 1);

    if (!line) continue;
    if (line.startsWith(BANNER_PREFIX)) continue;

    const trimmed = line.trim();
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      writeFramed(trimmed);
    } else {
      process.stderr.write(`redis_mcp_wrapper: non-json stdout dropped: ${line}\n`);
    }
  }
});

child.stderr.on('data', (chunk) => process.stderr.write(chunk));

child.on('error', (err) => {
  process.stderr.write(`redis_mcp_wrapper failed: ${err.message}\n`);
  process.exit(1);
});

child.on('close', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
