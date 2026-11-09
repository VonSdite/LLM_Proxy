import { describe, expect, it } from 'vitest'
import { composeProxyUrl, isProxyUrl, splitProxyUrl } from '@/features/shared/proxy'
import { providerDraft, providerErrors } from '@/features/providers/provider'

describe('代理凭据与端点校验', () => {
  it.each([
    'http://user:p/ss?word#x@127.0.0.1:7890',
    'https://user:p@ss:word@proxy.example:443',
    'socks5h://用 户:密 码@proxy.example:1080',
    'socks5://user%3Aname:p%2540ss@[::1]:1080',
    'http://127.0.0.1:7890',
  ])('允许服务端可编码的凭据并保留原输入：%s', (proxy) => {
    const provider = {
      ...providerDraft(),
      name: 'main',
      api: 'https://example.test/v1',
      model_list: ['model'],
      proxy_mode: 'custom',
      proxy,
    }
    expect(isProxyUrl(proxy)).toBe(true)
    expect(providerErrors(provider)).toEqual({})
    expect(provider.proxy).toBe(proxy)
  })

  it.each([
    '',
    '127.0.0.1:7890',
    'ftp://host:21',
    'http://user:pass@',
    'http://user:pass@host:noport',
  ])('拒绝无效端点：%s', (proxy) => {
    expect(isProxyUrl(proxy)).toBe(false)
  })
})

describe('代理认证字段', () => {
  it.each(['p@ss/#?:', '%40', '%2540', '密 码', "!a'()*+", 'p%ss'])(
    '原文凭据只编码一次：%s',
    (password) => {
      const fields = {
        address: 'socks5h://[::1]:1080',
        authenticated: true,
        username: '用:户@',
        password,
      }
      const url = composeProxyUrl(fields)
      expect(isProxyUrl(url)).toBe(true)
      expect(splitProxyUrl(url)).toEqual(fields)
      expect(new URL(url).password).toBe(
        encodeURIComponent(password).replace(
          /[!'()*]/g,
          (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
        ),
      )
    },
  )

  it('已有编码解码一次，原始 URL 分隔符按最后一个 @ 拆分', () => {
    expect(splitProxyUrl('http://user%3Aname:p%2540ss@host:80')).toEqual({
      address: 'http://host:80',
      authenticated: true,
      username: 'user:name',
      password: 'p%40ss',
    })
    expect(splitProxyUrl('http://user:p/ss?word#x@host:80').password).toBe('p/ss?word#x')
    expect(splitProxyUrl('http://user:p%ss@host:80').password).toBe('p%ss')
  })

  it('关闭认证时输出中不包含凭据', () => {
    expect(
      composeProxyUrl({
        address: 'http://host:80',
        authenticated: false,
        username: 'u',
        password: 'p',
      }),
    ).toBe('http://host:80')
  })
})
