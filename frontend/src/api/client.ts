import { localizedError } from './errors'
export type QueryValue = string | number | boolean | undefined | null | readonly string[]
export type Query = Record<string, QueryValue>

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

let unauthorized: (() => void) | undefined
export function onUnauthorized(handler: () => void) {
  unauthorized = handler
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  query?: Query
  signal?: AbortSignal
  headers?: Record<string, string>
  authRedirect?: boolean
  timeoutMs?: number
}

function requestUrl(path: string, query?: Query) {
  const url = new URL(path, window.location.origin)
  if (url.origin !== window.location.origin || !path.startsWith('/api/')) {
    throw new Error('Management requests must use the same-origin API.')
  }
  for (const [key, value] of Object.entries(query || {})) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) value.forEach((entry) => url.searchParams.append(key, entry))
    else url.searchParams.set(key, String(value))
  }
  return url.pathname + url.search
}

async function response(path: string, options: RequestOptions = {}) {
  const isForm = options.body instanceof FormData
  const result = await fetch(requestUrl(path, options.query), {
    method: options.method || 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    signal: options.signal,
    headers: {
      Accept: 'application/json',
      ...(options.body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
    body:
      options.body === undefined
        ? undefined
        : isForm
          ? (options.body as FormData)
          : JSON.stringify(options.body),
  })
  if (!result.ok) {
    const details = (await result.json().catch((reason: unknown) => {
      if (isAbort(reason)) throw reason
      return {}
    })) as Record<string, unknown>
    if (
      result.status === 401 &&
      options.authRedirect !== false &&
      details.auth_failed !== true &&
      (!details.code || details.code === 'unauthorized')
    )
      unauthorized?.()
    const raw = details.error
    const message =
      typeof raw === 'string'
        ? raw
        : raw && typeof raw === 'object' && 'message' in raw
          ? String(raw.message)
          : `请求失败（${result.status}）`
    throw new ApiError(message, result.status, details)
  }
  return result
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = options.timeoutMs ? new AbortController() : undefined
  const timer = controller ? setTimeout(() => controller.abort(), options.timeoutMs) : undefined
  const signal = controller
    ? options.signal
      ? AbortSignal.any([options.signal, controller.signal])
      : controller.signal
    : options.signal
  try {
    const result = await response(path, { ...options, signal })
    if (result.status === 204) return undefined as T
    return (await result.json()) as T
  } catch (reason) {
    if (controller?.signal.aborted && !options.signal?.aborted)
      throw new ApiError('读取超时，请重试', 408)
    throw reason
  } finally {
    clearTimeout(timer)
  }
}

export async function download(path: string, filename: string, options: RequestOptions = {}) {
  const result = await response(path, options)
  const blob = await result.blob()
  const disposition = result.headers.get('Content-Disposition') || ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="([^"]+)"/i)?.[1]
  let name = plain || filename
  if (encoded) {
    try {
      name = decodeURIComponent(encoded)
    } catch {
      /* 使用默认文件名。 */
    }
  }
  saveBlob(blob, name)
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? localizedError(error.message) : '操作失败，请重试'
}
export function isAbort(error: unknown) {
  return (
    typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError'
  )
}
