import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { ListQuery, PagedResult } from '@/api/permissions'
import type { TableSort } from '@/components/ui'
import { useRequest } from './useRequest'

export function usePagedList<T extends { id: string | number }>(
  load: (query: ListQuery, signal: AbortSignal) => Promise<PagedResult<T>>,
  initialSort: TableSort,
) {
  const page = ref(1)
  const pageSize = ref(50)
  const keyword = ref('')
  const effectiveKeyword = ref('')
  const sort = ref(initialSort)
  const selected = ref<string[]>([])
  const query = computed<ListQuery>(() => ({
    page: page.value,
    page_size: pageSize.value,
    keyword: effectiveKeyword.value,
    sort_key: sort.value.key,
    sort_direction: sort.value.direction,
  }))
  const request = useRequest((signal) => load(query.value, signal))
  let timer: ReturnType<typeof setTimeout> | undefined
  watch(keyword, (value) => {
    clearTimeout(timer)
    selected.value = []
    timer = setTimeout(() => {
      page.value = 1
      effectiveKeyword.value = value.trim()
    }, 240)
  })
  watch(
    [sort, pageSize],
    () => {
      page.value = 1
    },
    { deep: true },
  )
  watch(
    query,
    () => {
      selected.value = []
      void request.refresh()
    },
    { immediate: true },
  )
  watch(request.data, (value) => {
    selected.value = selected.value.filter((id) =>
      value?.items.some((item) => String(item.id) === id),
    )
    if (value && page.value > Math.max(1, Math.ceil(value.total / pageSize.value)))
      page.value = Math.max(1, Math.ceil(value.total / pageSize.value))
  })
  onBeforeUnmount(() => clearTimeout(timer))
  return {
    ...request,
    page,
    pageSize,
    keyword,
    filterPending: computed(
      () => request.loading.value || keyword.value.trim() !== effectiveKeyword.value,
    ),
    sort,
    selected,
    rows: computed(() => request.data.value?.items || []),
    total: computed(() => request.data.value?.total || 0),
    models: computed(() => request.data.value?.models || []),
  }
}
