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

이미 운영 중인 D1에는 Worker 배포 전에 다음 마이그레이션을 순서대로 먼저
적용해야 합니다.

- `0002_add_reporter_and_resolution.sql`
- `0003_add_payload_deduplication.sql`

`npm run deploy`는 실제 Worker 변경 전에 `predeploy`에서 테스트와 원격 D1
스키마를 **읽기 전용 SELECT**로 확인합니다. 배포가 끝나면 `postdeploy`가
`GET /health`만 호출해 응답 contract와 `REPORTS_DB`·`REPORT_RATE_LIMITER`
binding readiness를 확인합니다. 검증 대상 D1 이름은 하드코딩하지 않고
`wrangler.toml`의 유일한 `REPORTS_DB` binding에서 읽습니다. 스키마가 덜 적용된
상태, payload hash unique index의 대상 column/partial predicate가 정확히 다른
상태, binding 누락, 구버전 Worker 응답은 실패로 처리합니다. 운영 상태만 다시 확인하려면 다음
명령을 사용합니다.

```bash
npm run verify:deploy:remote
```

이 검증은 리포트를 제출하지 않으며 D1 데이터를 쓰거나 수정하지 않습니다.

Worker의 Custom Domain은 `reports.classin.cloud`이며 기존 Vercel 사이트와
Cloudflare Tunnel을 사용하지 않습니다.

## 운영 정보

- Worker: `classin-edb-reports`
- Custom Domain: `https://reports.classin.cloud`
- D1 database: `classin-edb-reports`
- D1 binding: `REPORTS_DB`
- Rate Limit binding: `REPORT_RATE_LIMITER` (IP별 분당 20회, 공유망을 고려한 상한)

동일 payload는 key 순서를 정규화한 SHA-256 hash와 D1 unique index로 한 번만
저장됩니다. 전송 중 응답을 받지 못해 **같은 payload를 그대로 재전송**하면 기존
접수번호와 `duplicate: true`를 돌려받습니다. 새로 작성한 신고는 `submittedAt`도
달라지므로 내용이 우연히 같더라도 별도 신고로 접수됩니다. 클라이언트
앱에는 인증 비밀값을 넣지 않습니다. Rate Limit binding이 누락되거나 호출에
실패하면 Worker는 신고를 D1에 쓰지 않고 `503 rate_limiter_unavailable`로
fail-closed합니다. `/health`도 D1의 `bug_reports` 테이블을 읽기 전용으로 조회하고
별도 readiness key로 Rate Limit binding을 실제 호출해, 두 probe가 모두 정상적인
응답 contract를 반환할 때만 readiness 성공을 반환합니다.

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
