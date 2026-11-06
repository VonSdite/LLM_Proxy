import type { Provider } from '@/api/providers'

export type ProviderDraft = Omit<Provider, 'api_key' | 'auth_group' | 'proxy' | 'hook'> & {
  api_key: string
  auth_group: string
  proxy: string
  hook: string
}
export function providerDraft(provider?: Provider): ProviderDraft {
  return {
    name: provider?.name || '',
    enabled: provider?.enabled ?? true,
    api: provider?.api || '',
    source_format: provider?.source_format || 'openai_chat',
    api_key: provider?.api_key || '',
    auth_group: provider?.auth_group || '',
    proxy_mode: provider?.proxy_mode || (provider?.proxy ? 'custom' : 'direct'),
    proxy: provider?.proxy || '',
    timeout_seconds: provider?.timeout_seconds ?? null,
    max_retries: provider?.max_retries ?? null,
    verify_ssl: provider?.verify_ssl ?? false,
    force_upstream_stream: provider?.force_upstream_stream ?? false,
    safe_desensitization_enabled: provider?.safe_desensitization_enabled ?? false,
    model_list: [...(provider?.model_list || [])],
    hidden_model_list: [...(provider?.hidden_model_list || [])],
    hook: provider?.hook || '',
  }
}
export function providerErrors(provider: Provider): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(provider.name.trim()))
    errors.name = '英文开头，最多 64 位，仅支持字母、数字和下划线'
  try {
    const url = new URL(provider.api)
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error()
  } catch {
    errors.api = '请输入完整的 HTTP 或 HTTPS 地址'
  }
  for (const key of ['timeout_seconds', 'max_retries'] as const) {
    const value = provider[key]
    if (value != null && (!Number.isInteger(value) || value < 1)) errors[key] = '请输入正整数'
  }
  if (provider.proxy_mode === 'custom') {
    try {
      if (
        !['http:', 'https:', 'socks5:', 'socks5h:'].includes(new URL(provider.proxy || '').protocol)
      )
        throw new Error()
    } catch {
      errors.proxy = '请输入完整的 HTTP、HTTPS 或 SOCKS5 代理地址'
    }
  }
  if (!provider.model_list.length) errors.models = '至少添加一个模型'
  return errors
}
