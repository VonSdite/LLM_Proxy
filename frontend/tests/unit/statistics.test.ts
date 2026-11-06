import { afterEach, describe, expect, it, vi } from 'vitest'
import { statisticsApi, type UsageRow } from '@/api/statistics'
import {
  cacheRate,
  dateErrors,
  datePreset,
  duration,
  rowKey,
  totals,
} from '@/features/statistics/statistics'

afterEach(() => vi.unstubAllGlobals())
describe('统计数据与筛选契约', () => {
  it('使用本地自然日，允许一年范围并正确处理闰年', () => {
    expect(datePreset(7, new Date(2026, 0, 3)).start_date).toBe('2025-12-28')
    const filters = {
      start_date: '2024-02-29',
      end_date: '2025-02-28',
      username: [],
      request_model: [],
    }
    expect(dateErrors(filters)).toEqual({})
    expect(dateErrors({ ...filters, end_date: '2025-03-01' }).end).toBe('查询范围最多一年')
    expect(dateErrors({ ...filters, end_date: '2024-02-28' }).end).toContain('早于')
    expect(dateErrors({ ...filters, start_date: '2023-02-29' }).start).toBeTruthy()
  })
  it('缓存命中率按已知输入 Token 加权，未知值保持未知', () => {
    const rows = [
      {
        prompt_tokens: 1000,
        total_tokens: 1100,
        completion_tokens: 100,
        cache_read_input_tokens: 50,
        cache_known_prompt_tokens: 100,
        request_count: 1,
      },
      {
        prompt_tokens: 900,
        total_tokens: 1000,
        completion_tokens: 100,
        cache_read_input_tokens: 90,
        cache_known_prompt_tokens: 900,
        request_count: 2,
      },
    ] as UsageRow[]
    expect(totals(rows, 'stats')).toMatchObject({ cache_hit_rate: '14.00%', request_count: '3' })
    expect(totals([{ ...rows[0]!, cache_known_prompt_tokens: 0 }], 'stats')?.cache_hit_rate).toBe(
      '未知',
    )
    expect(cacheRate(0, 'unknown')).toBe('未知')
    expect(cacheRate(0, 'known')).toBe('0.00%')
  })
  it('模型流向中的目标参与稳定行标识，耗时非负且保留两位小数', () => {
    const row = {
      id: 1,
      ip_address: '127.0.0.1',
      request_model: 'auto',
      target_model_id: 'a/model',
      response_model: 'model',
      start_time: '2026-11-06 10:00:00.000',
      end_time: '2026-11-06 10:00:01.250',
    } as UsageRow
    expect(rowKey(row, 'stats')).not.toBe(rowKey({ ...row, target_model_id: 'b/model' }, 'stats'))
    expect(duration(row)).toBe('1.25')
    expect(duration({ ...row, end_time: '2026-11-06 09:59:00' })).toBe('0.00')
  })
  it('日志查询保留多选筛选、服务器排序和分页参数', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"logs":[],"total":123}'))
    vi.stubGlobal('fetch', fetch)
    const signal = new AbortController().signal
    expect(
      await statisticsApi.list(
        'logs',
        {
          start_date: '2026-11-01',
          end_date: '2026-11-06',
          username: ['one', 'two'],
          request_model: ['provider/full/model'],
          sort_key: 'duration',
          sort_direction: 'desc',
          page: 2,
          page_size: 50,
        },
        signal,
      ),
    ).toEqual({ rows: [], total: 123 })
    const url = new URL(String(fetch.mock.calls[0]![0]), 'http://localhost')
    expect(url.searchParams.getAll('username')).toEqual(['one', 'two'])
    expect(url.searchParams.getAll('request_model')).toEqual(['provider/full/model'])
    expect(url.searchParams.get('page')).toBe('2')
    expect(url.searchParams.get('sort_key')).toBe('duration')
  })
})
