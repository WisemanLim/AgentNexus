// ST-11 세션 만료 — 화면을 떠나지 않고 재로그인 모달(새 창 로그인)로 입력을 보존한다 (MSG-024)
import { Button, Modal } from '@/components/ui'
import { useAuth } from '@/lib/auth'

export function ReloginModal() {
  const { expired, relogin } = useAuth()
  return (
    <Modal open={expired} onClose={() => {}} title="세션이 만료되었습니다"
      footer={<><Button onClick={() => location.assign(`/login?expired=1&next=${encodeURIComponent(location.pathname + location.search)}`)}>로그인 화면으로</Button>
        <Button variant="primary" onClick={relogin}>다시 로그인 (새 창)</Button></>}>
      <p className="text-sm">로그인이 만료되었습니다 (MSG-024). 새 창에서 다시 로그인하면 <strong>이 화면의 입력 내용을 그대로 둔 채</strong> 이어서 작업합니다.</p>
      <p className="mt-2 text-xs text-fg-muted">새 창이 막히면 브라우저의 팝업 허용을 켜거나 [로그인 화면으로] 를 누르세요 — 이 경우 입력 중인 내용은 사라질 수 있습니다.</p>
    </Modal>
  )
}
