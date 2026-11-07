// Run with `npm test`. The keys here are made up.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { importedKeys, keysBadge, parseKeys } from '../src/lib/geminiKeys.ts'

test('lines, commas and api_keyNN= labels all parse to the bare keys, without repeats', () => {
  assert.deepEqual(parseKeys('api_key01=aaaaaa\r\napi_key02 = bbbbbb\n\ncccccc, aaaaaa\n'), ['aaaaaa', 'bbbbbb', 'cccccc'])
  assert.deepEqual(parseKeys(''), [])
})

test('an imported file replaces the box with one key per line and says how many', () => {
  assert.deepEqual(importedKeys('api_key01=aaaaaa\napi_key02=bbbbbb\n'), { text: 'aaaaaa\nbbbbbb', count: 2 })
  assert.deepEqual(importedKeys('nothing here\n'), { text: 'nothing here', count: 1 })   // the server rejects what isn't a key
})

test('the badge counts keys', () => {
  assert.equal(keysBadge(0), 'Not set')
  assert.equal(keysBadge(1), '1 key active')
  assert.equal(keysBadge(3), '3 keys active')
})
