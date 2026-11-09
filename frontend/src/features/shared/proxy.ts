export interface ProxyFields {
  address: string
  authenticated: boolean
  username: string
  password: string
}

function decodeCredential(value: string): string {
  // 兼容历史地址中的原始百分号和已编码内容，仅解码有效的 UTF-8 序列。
  return value.replace(/(?:%[\da-f]{2})+/gi, (encoded) => {
    try {
      return decodeURIComponent(encoded)
    } catch {
      return encoded
    }
  })
}

export function splitProxyUrl(value: string): ProxyFields {
  const text = value.trim()
  const separator = text.indexOf('://')
  const start = separator < 0 ? 0 : separator + 3
  const at = text.lastIndexOf('@')
  if (at < start) return { address: text, authenticated: false, username: '', password: '' }
  const credentials = text.slice(start, at)
  const colon = credentials.indexOf(':')
  return {
    address: text.slice(0, start) + text.slice(at + 1),
    authenticated: true,
    username: decodeCredential(colon < 0 ? credentials : credentials.slice(0, colon)),
    password: decodeCredential(colon < 0 ? '' : credentials.slice(colon + 1)),
  }
}

export function composeProxyUrl(fields: ProxyFields): string {
  const address = fields.address.trim()
  if (!fields.authenticated) return address
  const encode = (value: string) =>
    encodeURIComponent(value).replace(
      /[!'()*]/g,
      (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
    )
  const credentials = `${encode(fields.username)}:${encode(fields.password)}@`
  const separator = address.indexOf('://')
  const start = separator < 0 ? 0 : separator + 3
  return address.slice(0, start) + credentials + address.slice(start)
}

export function isProxyUrl(value: string): boolean {
  // 与服务端一致，最后一个 @ 之前为凭据；验证端点时保留原始凭据交由服务端编码。
  const text = value.trim()
  const separator = text.indexOf('://')
  if (separator < 0) return false
  const rest = text.slice(separator + 3)
  const at = rest.lastIndexOf('@')
  const endpoint = at >= 0 ? text.slice(0, separator + 3) + rest.slice(at + 1) : text
  try {
    const parsed = new URL(endpoint)
    return ['http:', 'https:', 'socks5:', 'socks5h:'].includes(parsed.protocol) && !!parsed.hostname
  } catch {
    return false
  }
}
