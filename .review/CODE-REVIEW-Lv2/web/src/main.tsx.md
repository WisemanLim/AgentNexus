> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [src_Abstract.md](src_Abstract.md)

### 07. web/src/main.tsx
**파일 요약 / File Summary**
- 역할 / Role: 콘솔 SPA(React 19 + Vite) 부트스트랩. 테마 복원 후 Provider 체인(React Query → i18n → 인증 → 토스트 → 라우터)으로 `<App/>` 을 렌더.
- 진입점 거리 / Distance: 0 (`web/index.html` 의 모듈 스크립트 → 이 파일)
- LOC: 34
- 주요 export: 없음(부트스트랩 스크립트)
- 의존(내부): `./App`, `./components/ui`(ToastProvider), `./lib/auth`(AuthProvider), `./lib/i18n`, `./lib/query`
- 외부 패키지: react, react-dom, react-router, @tanstack/react-query, pretendard(로컬 폰트 번들)
- 부수효과 / Side effects: `localStorage` 읽기, `<html data-theme>` 속성 설정, DOM 마운트
- 동시성 / Concurrency: none(브라우저 단일 스레드)
- 테스트 커버리지 흔적: 직접 없음(`web/e2e/*.spec.ts` 가 전체 앱을 브라우저로 구동)

## 라인 리뷰 / Line Walk (Level 2)

```tsx path=web/src/main.tsx lines=1-11
import 'pretendard/dist/web/variable/pretendardvariable.css'
import './styles.css'
import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { App } from './App'
import { ToastProvider } from './components/ui'
import { AuthProvider } from './lib/auth'
import { I18nProvider } from './lib/i18n'
import { queryClient } from './lib/query'
```
**분석 / Analysis (Level 2)**
- L1: 폰트 CSS 를 번들에 포함 — 외부 CDN 없이(에어갭 요구, Dockerfile 주석 "폰트 로컬 번들") 동작.
- L2: 전역 스타일(Tailwind v4 `@import "tailwindcss"`, `web/src/styles.css:1`).
- L3-11: Provider 모듈들. `queryClient` 는 모듈 싱글턴(`web/src/lib/query.ts`).

```tsx path=web/src/main.tsx lines=13-18
try {
  const theme = localStorage.getItem('anx.theme')
  if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme)
} catch {
  /* 저장소 없음 */
}
```
**분석 / Analysis (Level 2)**
- L13-18: 렌더 **이전에** 저장된 테마를 `<html data-theme>` 에 적용 — 첫 페인트에서 밝은/어두운 테마가 깜빡이는 현상(FOUC)을 줄인다. 허용값(`light`/`dark`) 화이트리스트 검사 후에만 속성을 쓴다(임의 값 주입 방지).
- L16-18: 사생활 모드·저장소 차단 시 `localStorage` 접근이 예외를 던질 수 있어 `try/catch` 로 감싼다(좋은 습관).

```tsx path=web/src/main.tsx lines=20-34
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <AuthProvider>
          <ToastProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </ToastProvider>
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
)
```
**분석 / Analysis (Level 2)**
- L20: `document.getElementById('root')!` — non-null 단언. index.html 에 `#root` 가 없으면 런타임 오류(정상 빌드에선 존재).
- L21 `StrictMode`: 개발 모드에서 effect 를 두 번 실행해 부수효과 버그를 드러낸다. `AuthProvider` 의 `useEffect`(`web/src/lib/auth.tsx:129-141`)도 개발 중 두 번 `reload()` 를 호출한다 — 운영 빌드에는 영향 없음.
- L22-32 Provider 순서: `AuthProvider` 가 `BrowserRouter` **바깥**에 있다 → 인증 컨텍스트 안에서는 `useNavigate` 를 쓸 수 없고, 그래서 만료 처리 시 `location.pathname` 을 직접 읽는다(`web/src/lib/auth.tsx:137`). `I18nProvider` 가 Auth 보다 바깥이라 로그인 화면도 번역된다.
**짚어볼 점 / Watch-outs**
- 전역 오류 경계(Error Boundary)가 없다 — 렌더 중 예외 하나로 전체 화면이 비게 된다. 지연 로딩 청크 실패(`App.tsx` 의 `lazy`)도 여기까지 전파된다.

## 파일 평가 / File Assessment
- 강점: (1) 테마 FOUC 방지와 저장소 예외 처리. (2) Provider 순서가 의존 관계(번역 → 인증 → 라우팅)를 반영.
- 위험: (1) 최상위 Error Boundary 부재. (2) Auth 가 Router 밖이라 인증 로직이 `location` 전역에 의존.
- 개선 권고(영향순): (1) `<ErrorBoundary>` 로 `<App/>` 감싸기(청크 로드 실패 시 새로고침 안내). (2) 필요 시 AuthProvider 를 Router 안으로 옮겨 `useNavigate` 사용.
- 재읽기 권고 레벨: 4 — 부트스트랩 코드.
