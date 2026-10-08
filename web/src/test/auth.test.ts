import { can, homeFor, pkceChallenge, randomString, type Me } from '@/lib/auth'

const me = (roles: string[], permissions: string[] = []): Me => ({ sub: 'u', name: 'u', roles, permissions, role_labels: {}, kind: 'user' })

describe('auth', () => {
  it('PKCE S256 — RFC 7636 부록 B 벡터', async () => {
    expect(await pkceChallenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).toBe('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')
  })
  it('난수 문자열은 base64url', () => {
    const s = randomString(32)
    expect(s).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(randomString(32)).not.toBe(s)
  })
  it('역할별 첫 화면 (UI 설계서 10장)', () => {
    expect(homeFor(me(['operator']))).toBe('/console')
    expect(homeFor(me(['security']))).toBe('/console')
    expect(homeFor(me(['developer']))).toBe('/console/agents')
    expect(homeFor(me(['finops']))).toBe('/console/observe/cost')
    expect(homeFor(me(['user']))).toBe('/chat')
    expect(homeFor(me(['user', 'developer']))).toBe('/console/agents') // 가장 넓은 역할
  })
  it('권한 판정 — * 는 전체', () => {
    expect(can(me(['admin'], ['*']), 'policy:write')).toBe(true)
    expect(can(me(['user'], ['chat:use']), 'policy:write')).toBe(false)
    expect(can(null, 'chat:use')).toBe(false)
    expect(can(null)).toBe(true)
  })
})
