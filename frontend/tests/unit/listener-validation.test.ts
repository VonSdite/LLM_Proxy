import { describe, expect, it } from 'vitest'
import { isListenerHost } from '@/features/settings/listenerValidation'

describe('监听地址校验', () => {
  it.each([
    '0.0.0.0',
    ' 127.0.0.1 ',
    '192.168.1.10',
    '::',
    '::1',
    '2001:db8::1',
    'fe80::1%eth0',
    '::ffff:192.168.1.1',
  ])('允许完整 IP：%s', (host) => expect(isListenerHost(host)).toBe(true))
  it.each([
    '',
    '0.',
    '127.1',
    '123',
    '256.1.1.1',
    '01.2.3.4',
    'localhost',
    'http://127.0.0.1',
    '127.0.0.1:80',
    '[::1]',
    '1::2::3',
    '::1/path',
    '::1%',
    '::1%a%b',
  ])('拒绝不完整地址和非 IP：%s', (host) => expect(isListenerHost(host)).toBe(false))
})
