export function isListenerHost(value: string): boolean {
  const host = value.trim()
  if (!host.includes(':')) {
    const parts = host.split('.')
    return (
      parts.length === 4 &&
      parts.every((part) => /^(0|[1-9]\d{0,2})$/.test(part) && Number(part) <= 255)
    )
  }
  const [address = '', scope, ...extra] = host.split('%')
  if (extra.length || (scope !== undefined && (!scope || /[\s/[\]]/.test(scope)))) return false
  if (!/^[\da-f:.]+$/i.test(address)) return false
  try {
    return new URL(`http://[${address}]/`).hostname.startsWith('[')
  } catch {
    return false
  }
}
