# BoardLog Public Catalog 작업 규칙

## 저장소 역할

이 저장소는 BoardLog의 검수 완료 GitHub 카탈로그와 Supabase community/moderation server contract를 관리한다. Android 개인 기록 저장소 `jy2834/BoardLog`와 과거 웹 관리자 저장소 `jy2834/BoardLog-web`의 소스나 비밀값을 복사하지 않는다. BoardLog-web 검수 계획은 2026-08-16 Android 인앱 관리자 설계로 대체되었으며 운영에 필요하지 않다.

## 절대 포함하지 않는 값

- 사용자 구매 가격, 구성물·오거나이저 가격
- 개인 메모, 플레이 기록 메모, 개인 별점
- 소유자 ID, 익명 사용자 ID, 관리자 이메일
- 로컬 DB ID, 로컬 파일 경로, 사진 EXIF
- Supabase service-role 키, GitHub PAT, 관리자 인증정보

공개 정가 `listPriceWon`은 출처가 확인된 카탈로그 메타데이터이므로 개인 구매 가격과 구분해 허용한다.

## 변경 절차

1. validator 또는 exporter 변경은 실패하는 Python 테스트를 먼저 작성한다.
2. `catalog/catalog.json`은 스크립트로 결정적으로 정렬하고 수동 대량 편집하지 않는다.
3. 승인되지 않은 제출은 `catalog/catalog.json`이나 `catalog/images/`에 넣지 않는다.
4. 공개 이미지에는 권리·공식 출처 근거가 있어야 하며 임시 제출 이미지는 Supabase 비공개 버킷에만 둔다.
5. 무료 한도 오류는 마지막으로 확인된 서비스 상태와 일반 네트워크 오류를 구분한다.
6. 기존 항목 수정은 새 중복 행을 만들지 말고 선택적 `updateTargetKey`로 대상을 명시한다. 대상 key와 BGG ID 충돌 여부를 검수한다.
7. PENDING community 행은 safe metadata만 Android 전체 게임에 즉시 노출할 수 있지만, 검수 완료 GitHub `catalog/catalog.json`과 `catalog/images/`에는 승인/병합된 데이터만 넣는다.
8. 소유자의 공개 중단 요청은 삭제가 아니다. 실제 숨김·복구·삭제는 non-anonymous `admin_users` 관리자만 수행하며 suppression tombstone을 보존한다.

커밋 전 반드시 실행한다.

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_catalog.py \
  --catalog catalog/catalog.json \
  --schema catalog/schema.json \
  --images-dir catalog/images
git diff --check
```

Supabase 변경이 있으면 추가로 실행한다.

```bash
npm ci
python3 -m unittest tests.test_supabase_migration_contract -v
npm run test:functions:deno
npm run check:functions
npm run db:test:linked
```

원격 설정 변경(`supabase config push`), migration/function 배포, Auth 사용자 생성, 관리자 UUID 등록, secret 사용, 원격 smoke는 일반 코드 변경과 구분해 각각 명시적 운영 승인을 받은 뒤 수행한다. 사용자는 단일 영구 관리자 계정을 Dashboard에서 비공개로 만들고 비밀번호를 공유하지 않는다. 그 계정 UUID도 fresh approval 전에는 `admin_users`에 넣지 않는다. 마이그레이션 파일은 원격 적용 후 수정하지 않고 후속 번호의 보강 마이그레이션을 추가한다.

## 현재 상태

- schemaVersion 2, revision 1, 승인 게임 0개
- Python validator와 GitHub Actions/Pages 배포가 구현됨
- Supabase 검수 대기열, 비공개 이미지 버킷, RLS/RPC와 원격 DB 검증이 구현됨
- 제출 Edge Function, Turnstile 운영 호스트와 익명 로그인은 원격 활성화됨
- Android 제출·본인 상태 조회 UI, 승인 결과 exporter, 무료 한도 감시·임시 이미지 정리는 구현됨. `202608120001`~`202608130009` 원격 적용과 pgTAP 64개 검증 완료. Actions secret 활성화와 첫 수동 실행만 남음
- 클라이언트와 Edge Function은 새 `sb_publishable_` / `sb_secret_` API 키 경로만 사용하고 구형 `anon` / `service_role` 키에 새 의존성을 만들지 않음
- 2026-08-13 운영 프로젝트에서 구형 JWT 기반 `anon` / `service_role` API 키를 비활성화하고 이전 Legacy HS256 서명 키를 폐기함. 이후에는 새 publishable/secret API 키와 ES256 사용자 토큰만 사용함
- 2026-08-16~18 release candidate에는 safe `community-catalog`, authenticated `admin-moderate`, public suppression, owner removal request, Android 관리자 queue/편집/승인·반려·병합·숨김·복구·삭제가 구현됨
- `20260816023225`, `20260817234207` migration과 두 새 Edge Function은 로컬 검증 상태이며 아직 운영 배포·hosted Auth 변경·영구 관리자 생성/등록·원격 smoke를 수행하지 않음
- 일반 사용자는 회원가입하지 않는다. guest 선택은 Auth 사용자를 만들지 않고, Android가 제출/상태 조회를 실제로 사용할 때만 익명 세션을 지연 생성한다.
