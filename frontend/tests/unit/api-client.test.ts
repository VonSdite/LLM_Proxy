import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, errorMessage, onUnauthorized } from '@/api/client'
import { agentsApi } from '@/api/agents'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})
describe('同源 API 客户端', () => {
  it('Agent 文件操作保留专用请求头、同源 Cookie 和后端票据', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    const controller = new AbortController()
    await agentsApi.apply('remote id', 'preview-ticket', controller.signal)
    expect(fetch).toHaveBeenCalledWith(
      '/api/agent-config/machines/remote%20id/apply',
      expect.objectContaining({
        credentials: 'same-origin',
        signal: controller.signal,
        headers: expect.objectContaining({ 'X-Agent-Config': '1' }),
        body: '{"token":"preview-ticket"}',
      }),
    )
  })
  it('常见后端错误提供中文说明，上游异常保留原始信息', () => {
    expect(errorMessage(new Error('Invalid IP address'))).toBe('IP 地址格式无效')
    expect(errorMessage(new Error('https://example.test/v1/models returned 401'))).toContain(
      '鉴权失败',
    )
    expect(errorMessage(new Error('https://example.test/v1/models returned 401'))).toContain(
      '原始信息：https://example.test/v1/models returned 401',
    )
  })
  it('SSH 和 OAuth 账号认证错误不触发后台退出', async () => {
    const unauthorized = vi.fn()
    onUnauthorized(unauthorized)
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response('{"error":"SSH failed","code":"authentication_failed"}', { status: 401 }),
        )
        .mockResolvedValueOnce(
          new Response('{"error":"OAuth failed","auth_failed":true}', { status: 401 }),
        ),
    )
    await expect(api('/api/agent-config/connect')).rejects.toBeInstanceOf(ApiError)
    await expect(api('/api/oauth/codex/auth-files/a/quota')).rejects.toBeInstanceOf(ApiError)
    expect(unauthorized).not.toHaveBeenCalled()
  })

  it('读取 SSH 认证错误时取消请求，不把未读完的响应当作后台会话失效', async () => {
    const unauthorized = vi.fn()
    onUnauthorized(unauthorized)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_url: string, options: RequestInit) =>
        Promise.resolve({
          ok: false,
          status: 401,
          json: () =>
            new Promise((_resolve, reject) => {
              options.signal!.addEventListener(
                'abort',
                () => reject(new DOMException('Canceled', 'AbortError')),
                { once: true },
              )
            }),
        }),
      ),
    )
    const controller = new AbortController()
    const pending = api('/api/agent-config/machines/server/connect', { signal: controller.signal })
    const assertion = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    await Promise.resolve()
    await Promise.resolve()
    controller.abort()
    await assertion
    expect(unauthorized).not.toHaveBeenCalled()
  })

  it('读取超时覆盖响应正文；主动离开页面仍作为取消处理', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_url: string, options: RequestInit) =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            new Promise((_resolve, reject) =>
              options.signal!.addEventListener(
                'abort',
                () => reject(new DOMException('aborted', 'AbortError')),
                { once: true },
              ),
            ),
        }),
      ),
    )
    const pending = api('/api/read', { timeoutMs: 60_000 })
    const assertion = expect(pending).rejects.toMatchObject({ status: 408 })
    await vi.advanceTimersByTimeAsync(60_000)
    await assertion
    const controller = new AbortController()
    const canceled = api('/api/read', { timeoutMs: 60_000, signal: controller.signal })
    const canceledAssertion = expect(canceled).rejects.toMatchObject({ name: 'AbortError' })
    await Promise.resolve()
    await Promise.resolve()
    controller.abort()
    await canceledAssertion
    expect(vi.getTimerCount()).toBe(0)
  })
  it('使用同源 Cookie、结构化请求体和多值查询参数', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"ok":true}'))
    vi.stubGlobal('fetch', fetch)
    expect(
      await api('/api/test', {
        method: 'POST',
        body: { value: '原始值' },
        query: { models: ['main/a', 'main/b'], empty: '' },
      }),
    ).toEqual({ ok: true })
    expect(fetch).toHaveBeenCalledWith(
      '/api/test?models=main%2Fa&models=main%2Fb',
      expect.objectContaining({ credentials: 'same-origin', body: '{"value":"原始值"}' }),
    )
  })

  it('阻止向外部 URL 发送管理请求', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(api('https://external.example/api/key')).rejects.toThrow('same-origin')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('会话失效触发登录，但登录凭据错误留在原表单', async () => {
    const unauthorized = vi.fn()
    onUnauthorized(unauthorized)
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(new Response('{"error":"Unauthorized"}', { status: 401 })),
        ),
    )
    await expect(api('/api/users')).rejects.toBeInstanceOf(ApiError)
    expect(unauthorized).toHaveBeenCalledOnce()
    await expect(api('/api/login', { method: 'POST', authRedirect: false })).rejects.toBeInstanceOf(
      ApiError,
    )
    expect(unauthorized).toHaveBeenCalledOnce()
  })
})
