import { inject, type ComputedRef, type InjectionKey } from 'vue'

export const fieldKey: InjectionKey<
  | {
      id?: string
      required?: ComputedRef<boolean | undefined>
      error: ComputedRef<string | undefined>
      describedBy: ComputedRef<string | undefined>
    }
  | undefined
> = Symbol('field')
export function useField() {
  return inject(fieldKey, undefined)
}
