/**
 * Regression tests for outbound document MIME mapping (#89074).
 *
 * The bridge's /send-media handler labels every document payload with
 * MIME_MAP[ext] || 'application/octet-stream'. Plain text-ish documents
 * (html/htm/txt/csv, ...) were absent from MIME_MAP, so WhatsApp received
 * them as application/octet-stream and every phone rendered them as an
 * unopenable "BIN" instead of a text/HTML attachment.
 *
 * These tests exercise the pure helper — they do NOT require a live
 * WhatsApp socket (no Baileys import, no bridge.js side effects).
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { mediaPayloadForFile, MIME_MAP } from './bridge_helpers.js';

const asDocument = (filePath, extra = {}) =>
  mediaPayloadForFile({ buffer: Buffer.from('body'), filePath, ...extra });

test('html/htm/txt/csv documents keep their real MIME type', () => {
  const cases = [
    ['/tmp/report.html', 'text/html'],
    ['/tmp/index.htm', 'text/html'],
    ['/tmp/notes.txt', 'text/plain'],
    ['/tmp/rows.csv', 'text/csv'],
  ];
  for (const [filePath, mime] of cases) {
    const payload = asDocument(filePath);
    assert.equal(payload.mimetype, mime, filePath);
    // The reported repro sends a document, so the payload must stay a
    // document attachment with the original basename, not a binary blob.
    assert.ok(Buffer.isBuffer(payload.document), filePath);
    assert.equal(payload.fileName, filePath.split('/').pop(), filePath);
  }
});

test('extension matching is case-insensitive', () => {
  assert.equal(asDocument('/tmp/REPORT.HTML').mimetype, 'text/html');
  assert.equal(asDocument('/tmp/Notes.TXT').mimetype, 'text/plain');
  assert.equal(asDocument('/tmp/ROWS.CSV').mimetype, 'text/csv');
});

test('multi-dot filenames use the last extension', () => {
  assert.equal(asDocument('/tmp/report.final.html').mimetype, 'text/html');
  assert.equal(asDocument('/tmp/data.2026.q3.csv').mimetype, 'text/csv');
  assert.equal(asDocument('/tmp/notes.backup.txt').mimetype, 'text/plain');
});

test('extensionless paths still fall back to application/octet-stream', () => {
  const payload = asDocument('/tmp/README');
  assert.equal(payload.mimetype, 'application/octet-stream');
  assert.equal(payload.fileName, 'README');
  // A dotted directory above an extensionless file must not be mistaken for
  // an extension either ("/tmp.reports/Makefile: no ext").
  assert.equal(asDocument('/tmp.reports/Makefile').mimetype, 'application/octet-stream');
});

test('explicit document mediaType uses the same mapping', () => {
  const payload = mediaPayloadForFile({
    buffer: Buffer.from('body'),
    filePath: '/tmp/report.html',
    mediaType: 'document',
    caption: 'see attached',
  });
  assert.equal(payload.mimetype, 'text/html');
  assert.equal(payload.caption, 'see attached');
});

test('image/video MIME handling is unchanged', () => {
  assert.equal(asDocument('/tmp/pic.png', { mediaType: 'image' }).mimetype, 'image/png');
  assert.equal(asDocument('/tmp/clip.mp4', { mediaType: 'video' }).mimetype, 'video/mp4');
  assert.equal(
    asDocument('/tmp/unknown.zzz', { mediaType: 'image' }).mimetype,
    'image/jpeg',
    'unknown image extension keeps the image/jpeg default',
  );
  assert.equal(asDocument('/tmp/loop.gif', { mediaType: 'image' }).mimetype, 'image/gif');
});

test('MIME_MAP keeps the previously mapped types intact', () => {
  assert.equal(MIME_MAP.pdf, 'application/pdf');
  assert.equal(MIME_MAP.png, 'image/png');
  assert.equal(MIME_MAP.docx, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
});
