export interface AceEditor {
  getValue(): string
  setValue(value: string, cursor?: number): void
  getCursorPosition(): { row: number; column: number }
  moveCursorToPosition(position: { row: number; column: number }): void
  setOptions(options: Record<string, unknown>): void
  setReadOnly(value?: boolean): void
  setTheme(theme: string): void
  resize(): void
  focus(): void
  destroy(): void
  on(event: string, callback: () => void): void
  commands: {
    addCommand(command: {
      name: string
      bindKey: { win: string; mac: string }
      exec: () => void
    }): void
  }
  session: {
    setMode(mode: string): void
    setUseWorker(value: boolean): void
    getScrollTop(): number
    setScrollTop(value: number): void
  }
}
interface Ace {
  edit(element: HTMLElement): AceEditor
  config: { set(name: string, value: string): void }
}
declare global {
  interface Window {
    ace?: Ace
  }
}
let pending: Promise<Ace> | undefined
export function loadAce(): Promise<Ace> {
  if (window.ace) return Promise.resolve(window.ace)
  pending ||= new Promise<Ace>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = '/static/vendor/ace/ace.js'
    script.onload = () => {
      if (!window.ace) {
        pending = undefined
        reject(new Error('Ace is unavailable'))
        return
      }
      window.ace.config.set('basePath', '/static/vendor/ace')
      resolve(window.ace)
    }
    script.onerror = () => {
      pending = undefined
      script.remove()
      reject(new Error('Ace failed to load'))
    }
    document.head.append(script)
  })
  return pending
}
