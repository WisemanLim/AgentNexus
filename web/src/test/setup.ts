import '@testing-library/jest-dom/vitest'
import { webcrypto } from 'node:crypto'

if (!globalThis.crypto?.subtle) Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })

// jsdom 미구현 API (cmdk·xyflow)
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver
Element.prototype.scrollIntoView ??= function () {}
