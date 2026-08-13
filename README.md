# BoardLog Public Catalog

BoardLog 사용자가 제출하고 관리자가 검수한 보드게임만 배포하는 공개 카탈로그입니다. Android 앱의 개인 기록·가격·메모는 이 저장소로 전송되지 않습니다.

## 공개 주소

- 카탈로그: <https://jy2834.github.io/BoardLog-catalog/catalog/catalog.json>
- 스키마 계약: <https://jy2834.github.io/BoardLog-catalog/catalog/schema.json>

`catalog/catalog.json`은 `schemaVersion`, 단조 증가하는 `revision`, UTC `generatedAt`, 정렬된 `games`를 갖습니다. 초기 revision은 승인 게임이 없는 빈 목록입니다.

일반 신규 게임은 안정적인 새 `key`로 추가합니다. 기존 내장/공개 항목을 검수된 정보로 교체해야 할 때만 선택적 `updateTargetKey`에 대상의 안정적인 key를 기록합니다. Android는 대상 key가 존재하고 다른 게임의 BGG ID를 침범하지 않을 때만 해당 항목을 제자리 갱신합니다.

## 개인정보 경계

공개 게임은 이름, 원제, 별칭, 인원수, 시간, 태그, 난이도 계산용 weight, 공개 별점, 검증된 정가와 출처만 포함할 수 있습니다. 다음 값은 validator가 거부합니다.

- 구매가·본판가·구성물가·오거나이저가
- 개인 메모와 플레이 기록 메모
- 소유자 ID, 로컬 DB ID와 로컬 파일 경로
- 개인 별점

`listPriceWon`은 개인 구매가가 아니라 출처가 확인된 공개 정가 메타데이터일 때만 허용됩니다.

## 로컬 검증

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_catalog.py \
  --catalog catalog/catalog.json \
  --schema catalog/schema.json \
  --images-dir catalog/images
```

모든 pull request와 `main` push에서 같은 검증이 실행됩니다. GitHub Pages는 검증이 성공한 `main`만 배포합니다.

## 이미지와 라이선스

저장소의 검증 코드와 스키마는 MIT License입니다. 제3자 게임명, 상표, 설명, 메타데이터와 커버 이미지는 각각의 권리자·원출처 조건을 따르며 MIT License로 재허가되지 않습니다. 공개 이미지는 관리자 검수와 출처 확인을 통과한 항목만 추가합니다.

일반 사용자의 제출은 곧바로 공개되지 않습니다. Supabase의 비공개 검수 대기열과 관리자 승인을 거친 뒤 `export-approved.yml`이 최대 25건씩 공개 JSON과 커버를 갱신합니다. 승인 행의 임시 커버는 입력 2 MiB 제한을 다시 확인하고 EXIF를 제거한 최대 1,200px WebP로 변환합니다. 전체 스키마 검증과 Git push 후 같은 작업에서 GitHub Pages 배포까지 성공해야 서버의 `exported_at`을 기록합니다. 검증·변환·push·Pages·서버 확인 중 실패하면 승인 행과 임시 이미지를 남겨 다음 15분 실행에서 안전하게 재시도합니다. 서버 확인 이후 임시 Storage 삭제만 실패한 경우 공개 결과는 유지하며, 남은 고아 파일은 사용량 정리 작업에서 다시 확인합니다.

워크플로 활성화에는 GitHub Actions secret `SUPABASE_URL`과 새 형식의 `SUPABASE_SECRET_KEY`(`sb_secret_…`)가 필요합니다. 구형 `service_role` JWT는 사용하지 않으며, secret은 워크플로 로그·Android·웹 번들·Git에 넣지 않습니다. 수동 실행에서는 특정 승인 UUID 하나 또는 다음 미내보내기 묶음을 선택할 수 있습니다.

무료 한도 감시는 하루 한 번 DB와 Storage의 실제 사용량을 측정합니다. 80/90/95/100% 단계에 따라 공지·이슈·이미지 제한·제출 중지를 적용하며, 운영자의 `MAINTENANCE` 상태와 안내문은 덮어쓰지 않습니다. 거절 후 30일이 지난 이미지와 24시간 이상 참조되지 않은 고아 이미지는 삭제 확인 뒤 정리합니다. 수동 지표 확인과 장애 대응은 [`docs/free-tier-runbook.md`](docs/free-tier-runbook.md)를 따릅니다.

## Supabase 검수 서버

무료 Supabase 프로젝트 `BoardLog-catalog`에 검수 대기열이 구성되어 있습니다.

- 리전: Seoul (`ap-northeast-2`)
- 프로젝트 대시보드: <https://supabase.com/dashboard/project/xlinubftvqaxpwrtowvk>
- 제출 원본과 이미지는 비공개이며 승인된 공개 필드만 `approved_catalog_games`에서 조회됩니다. 원본 제출 테이블은 Realtime에 게시하지 않습니다. 제출자는 RLS가 소유자로 제한한 불투명 변경 신호를 받은 뒤 `my_game_submissions` 안전 뷰를 다시 조회합니다.
- 이미지는 비공개 `submission-images` 버킷에 저장하며 JPEG/WebP, 파일당 2 MiB로 제한합니다.
- 사용자는 24시간당 최대 3건을 제출할 수 있습니다.
- 서버 상태는 `NORMAL`, `IMAGE_LIMITED`, `SUBMISSION_CLOSED`, `MAINTENANCE`로 구분합니다.

`202608120001`~`202608120006` DB 마이그레이션이 원격 프로젝트에 적용되었습니다. 제출자 Realtime은 원본 제출 테이블이 아니라 소유자별 불투명 변경 신호만 사용합니다. 익명 로그인과 `submit-game` Edge Function, Turnstile 운영 위젯이 활성화되어 있고 Android 제출·본인 상태 조회 UI도 연결되어 있습니다. 관리자 검수 화면, exporter, 무료 한도 감시 코드는 준비됐으며, `202608130007`~`202608130008` 마이그레이션과 GitHub Actions secret은 운영 승인 후 활성화합니다.

### 제출 Edge Function

`submit-game`은 `payload`, `turnstileToken`, 선택적 `cover`만 받는 multipart API입니다. JSON은 32 KiB, 전체 요청은 약 2.1 MiB, JPEG/WebP 커버는 2 MiB로 제한하며 확장자가 아닌 실제 magic bytes를 확인합니다. 비공개 필드, 알 수 없는 multipart 필드, 중복 필드, 잘못된 수치·태그·URL은 DB에 닿기 전에 거부됩니다.

함수는 bearer 토큰을 `auth.getUser`로 직접 검증하고, 사용자의 RLS 클라이언트로만 비공개 Storage/RPC를 호출합니다. 이미지 업로드 뒤 DB 저장이 실패하면 즉시 삭제를 시도하며, 남은 고아 파일은 사용량 정리 작업이 재확인합니다. 요청 내용·인증 헤더·Turnstile 토큰은 로그에 남기지 않습니다.

```bash
npm run test:functions
npm run test:functions:deno
npm run check:functions
```

`catalog/captcha.html`은 GitHub Pages에서 Turnstile 토큰만 Android의 `BoardLogTurnstile` 브리지로 돌려주는 최소 페이지입니다. 운영 위젯은 `jy2834.github.io` 호스트로 제한되어 있습니다.

1. 공개 site key만 `captcha.html`에 포함합니다.
2. 비공개 secret은 Supabase의 `TURNSTILE_SECRET_KEY` 비밀 저장소에만 둡니다.

Cloudflare의 테스트 키를 운영에 사용하지 않습니다. secret은 Git과 Android/Web 번들에 절대 넣지 않습니다.

플랫폼 JWT 사전 검증을 켠 상태로 두고, 함수 안에서도 `auth.getUser`로 bearer를 다시 확인합니다. Android는 공개 publishable key와 익명 사용자 JWT를 함께 보냅니다. 함수의 사용자·관리 클라이언트도 각각 `SUPABASE_PUBLISHABLE_KEYS`와 `SUPABASE_SECRET_KEYS`의 `default` 키를 사용하며 구형 `anon`·`service_role` 키를 읽지 않습니다. 배포 명령은 다음과 같습니다.

2026-08-13 운영 보안 전환을 완료했습니다. Supabase의 구형 JWT 기반 `anon`·`service_role` API 키는 비활성화했고, 이전 Legacy HS256 서명 키도 폐기했습니다. 새 클라이언트 세션은 publishable key로 발급된 ES256 토큰만 사용해야 하며, 폐기된 키나 이전 토큰을 다시 활성화·복원하지 않습니다.

```bash
npx supabase functions deploy submit-game \
  --project-ref xlinubftvqaxpwrtowvk \
  --use-api \
  --agent no
```

### 개발 환경 재현

Node.js와 npm만 설치한 뒤 저장소 루트에서 실행합니다. Supabase CLI 버전은 `package-lock.json`으로 고정되어 있으며 전역 설치가 필요하지 않습니다.

```bash
npm ci
npx supabase login
npx supabase link --project-ref xlinubftvqaxpwrtowvk
npx supabase migration list --linked --agent no
npx supabase db push --linked --agent no
npm run db:test:linked
```

마지막 명령은 Docker 없이 Management API를 통해 원격 DB에서 64개 pgTAP 검증을 실행합니다. 하나라도 실패하면 SQL 예외로 명령 자체가 실패합니다. Docker가 준비된 환경에서는 `npx supabase test db --linked supabase/tests/public_catalog_rls.test.sql --agent no`도 사용할 수 있습니다.

익명 로그인은 2026-08-12에 대시보드의 `Authentication → Sign In / Providers → Allow anonymous sign-ins` 항목만 활성화했습니다. 전체 로컬 설정을 덮어쓸 필요가 있을 때만 아래 명령을 사용하며, 실행 전 변경 내용을 검토합니다.

```bash
npx supabase config push --project-ref xlinubftvqaxpwrtowvk --agent no
```

DB 비밀번호, access token, anon/service-role 키, 관리자 UUID·이메일은 Git에 저장하지 않습니다. 운영 관리자는 실제 Auth 계정을 만든 뒤 `admin_users`에 별도로 등록합니다.
