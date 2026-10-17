// Vitest 全局铺装（环境修复）。
//
// 背景：Node 25 默认启用实验性 Web Storage，并先在 globalThis 上定义了
// localStorage / sessionStorage（未给 `--localstorage-file` 时是个空壳，
// 连 clear/setItem 都没有）。vitest 的 jsdom 环境在 populateGlobal 时只拷贝
// 白名单内的键，且对「已存在于 global 的键」不再覆盖，于是这层空壳会遮蔽
// jsdom 的真实 Storage，导致 `localStorage.clear is not a function`。
//
// 处理：仅在现有实现不是可用 Storage 时，替换为 vitest jsdom 环境挂到
// `globalThis.jsdom` 上的真实 Storage；若环境本身已可用（旧 Node / 已提供
// 真实实现），则原样不动。

type StorageKind = 'localStorage' | 'sessionStorage'

interface JsdomLike {
  window?: Window & typeof globalThis
}

function isUsableStorage(value: unknown): value is Storage {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as Storage).getItem === 'function' &&
    typeof (value as Storage).setItem === 'function' &&
    typeof (value as Storage).clear === 'function'
  )
}

function installRealStorage(kind: StorageKind): void {
  const current: unknown = Reflect.get(globalThis, kind)
  if (isUsableStorage(current)) return

  const jsdom = Reflect.get(globalThis, 'jsdom') as JsdomLike | undefined
  const real = jsdom?.window?.[kind]
  if (isUsableStorage(real)) {
    Object.defineProperty(globalThis, kind, {
      value: real,
      configurable: true,
      writable: true,
    })
  }
}

installRealStorage('localStorage')
installRealStorage('sessionStorage')

// ---------------------------------------------------------------------------
// 组件测试会真实挂载 Naive UI，需要少量浏览器 API 兜底（jsdom 未实现）。
// ---------------------------------------------------------------------------

interface ResizeObserverLike {
  observe(): void
  unobserve(): void
  disconnect(): void
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub implements ResizeObserverLike {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, 'ResizeObserver', {
    value: ResizeObserverStub,
    configurable: true,
    writable: true,
  })
}

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  })
}
