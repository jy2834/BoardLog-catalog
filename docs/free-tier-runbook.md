# Supabase 무료 한도 운영 런북

BoardLog 공용 카탈로그는 자동 결제 전환이나 유료 플랜 업그레이드를 하지 않는다. 무료 한도에 가까워지면 기능을 단계적으로 줄이고, 마지막으로 확인된 정상 측정값을 보존한다.

## 공식 무료 한도 기준

2026-08-13 기준 Supabase 공식 문서에 표시된 프로젝트별 무료 한도는 다음과 같다.

- Database: 500 MB
- Storage: 1 GB
- Authentication: 50,000 MAU
- Egress: 5 GB uncached + 5 GB cached
- Edge Functions: 500,000 Edge Function 호출
- Realtime: 2 million Realtime 메시지, 200 peak connections

공식 출처:

- <https://supabase.com/docs/guides/platform/billing-on-supabase>
- <https://supabase.com/docs/guides/platform/manage-your-usage>
- <https://supabase.com/docs/guides/platform/manage-your-usage/monthly-active-users>
- <https://supabase.com/docs/guides/platform/cost-control>

## 자동 측정 범위

현재 자동 상태 전환에는 비공개 SQL 함수로 직접 확인한 DB와 Storage만 사용한다. MAU, egress, Edge Functions, Realtime은 Supabase Dashboard의 billing-cycle usage를 사람이 확인한다. Management API의 일반 요청 카운트를 청구 한도와 같은 값으로 추정하지 않는다.

측정 실패, 응답 누락, 잘못된 형식에서는 `service_status`를 갱신하지 않는다. 마지막 검증값과 상태가 유지되어 네트워크 오류가 “한도 소진”으로 잘못 표시되지 않는다.

## 단계별 동작

| 최대 검증 비율 | usage level | 앱 제출 상태 | 운영 조치 |
| --- | --- | --- | --- |
| 80% 미만 | `NORMAL` | `NORMAL` | 일일 감시 |
| 80% 이상 | `NOTICE_80` | `NORMAL` | Dashboard 수동 확인 |
| 90% 이상 | `WARNING_90` | `NORMAL` | GitHub `free-tier-usage` 이슈 생성/갱신 |
| 95% 이상 | `CRITICAL_95` | `IMAGE_LIMITED` | 새 커버 이미지 접수 중지, 글 제출 유지 |
| 100% 이상 | `EXHAUSTED_100` | `SUBMISSION_CLOSED` | 신규 공용 카탈로그 제출 중지 |

운영자가 `MAINTENANCE`를 설정한 경우 자동 감시는 해당 상태를 덮어쓰지 않는다. `operator_message`도 자동화가 수정하지 않는다.

## 임시 이미지 정리

일일 작업은 한 번에 최대 100개만 처리한다.

- `REJECTED` 상태로 검수된 뒤 30일이 지난 커버
- 어떤 제출 행에서도 참조하지 않고 Storage 생성 후 24시간이 지난 고아 커버

`PENDING`, 미내보내기 `APPROVED`/`MERGED`, 참조 중인 이미지는 삭제하지 않는다. Storage가 선택한 모든 경로의 삭제를 확인한 뒤에만 거절 행의 `image_object_path`를 비운다. 삭제나 확인이 실패하면 DB 참조를 유지하고 다음 일일 실행에서 재시도한다.

## 운영 확인 순서

1. GitHub Actions의 `Monitor Supabase free usage` 로그에서 측정 시각과 상태만 확인한다. 비밀키나 원본 응답을 로그에 출력하지 않는다.
2. 90% 이상 이슈가 열리면 Supabase Dashboard의 Database, Storage, Auth, Egress, Edge Functions, Realtime 사용량을 직접 대조한다.
3. 비정상 증가 원인을 확인하고, 승인 exporter와 임시 이미지 정리가 정상인지 확인한다.
4. 수동 점검이 필요하면 관리자 화면에서 `MAINTENANCE`와 안내 문구를 설정한다.
5. 정리 후 자동 측정이 낮은 단계로 돌아왔는지 확인한다. 결제 수단 추가나 유료 업그레이드는 별도 명시적 결정 없이는 수행하지 않는다.

검증된 DB·Storage 최대 비율이 90% 아래로 회복되면 자동화가 열린 `free-tier-usage` 이슈를 종료한다.

## 활성화 전 체크리스트

- `202608130007_admin_console_reads.sql`과 `202608130008_free_usage_monitor.sql`을 원격에 적용하고 pgTAP을 통과시킨다.
- GitHub Actions에 `SUPABASE_URL`, 새 형식 `SUPABASE_SECRET_KEY`(`sb_secret_…`)만 등록한다.
- 구형 `anon`/`service_role` JWT, PAT, DB 비밀번호를 workflow secret으로 재사용하지 않는다.
- `workflow_dispatch`로 1회 실행해 상태 갱신, 무후보 정리, 비밀값 비노출을 확인한다.
