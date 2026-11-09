export interface SelectOption {
  value: string
  label: string
  description?: string
  disabled?: boolean
}
export interface TableColumn {
  key: string
  label: string
  help?: string | readonly string[]
  sortable?: boolean
  width?: number
  minWidth?: number
  maxWidth?: number
  fixed?: boolean
  grow?: number
  align?: 'left' | 'right'
}
export interface TableSort {
  key: string
  direction: 'asc' | 'desc'
}
