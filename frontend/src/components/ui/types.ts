export interface SelectOption {
  value: string
  label: string
  description?: string
  disabled?: boolean
}
export interface TableColumn {
  key: string
  label: string
  sortable?: boolean
  width?: number
  align?: 'left' | 'right'
}
export interface TableSort {
  key: string
  direction: 'asc' | 'desc'
}
