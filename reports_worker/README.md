# ClassIn EDB report collector

`reports.classin.cloud`에서 EDB 메이크의 개인정보 제거된 버그 리포트를
받는 Cloudflare Worker입니다. 신고 메타데이터는 D1의 `bug_reports` 테이블에
저장합니다. 원본 시험지, 세션 JSON, API 키, 전체 로컬 경로는 앱에서
전송하지 않습니다. 회신 연락처는 사용자가 직접 입력하고 연락 동의에
체크한 경우에만 저장합니다.

## Endpoints

- `GET /health`
- `POST /v1/edb-reports`

## Deploy

1. `npm install`
2. `npm run db:create`
3. 출력된 D1 database ID를 `wrangler.toml`에 입력
4. `npm run db:migrate:remote`
5. `npm run deploy`

이미 운영 중인 D1에는 Worker 배포 전에 `0002_add_reporter_and_resolution.sql`
마이그레이션을 먼저 적용해야 합니다.

Worker의 Custom Domain은 `reports.classin.cloud`이며 기존 Vercel 사이트와
Cloudflare Tunnel을 사용하지 않습니다.

## 운영 정보

- Worker: `classin-edb-reports`
- Custom Domain: `https://reports.classin.cloud`
- D1 database: `classin-edb-reports`
- D1 binding: `REPORTS_DB`

접수된 리포트는 Cloudflare 대시보드의
`Storage & databases` → `D1` → `classin-edb-reports` → `Console`에서
다음 쿼리로 확인할 수 있습니다.

```sql
SELECT id, created_at, app_version, platform, description, error_code,
       failed_operation, reporter_contact, status, resolution_note, resolved_at
FROM bug_reports
ORDER BY created_at DESC
LIMIT 100;
```
