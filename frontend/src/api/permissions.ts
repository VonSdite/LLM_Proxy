export type ModelPermissions = '*' | string[]
export interface UsageTotals {
  total_request_count: number
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
}
export interface ListQuery {
  page: number
  page_size: number
  keyword: string
  sort_key: string
  sort_direction: 'asc' | 'desc'
}
export interface PagedResult<T> {
  items: T[]
  total: number
  models: string[]
}
