# BoardLog Public Catalog

BoardLog 사용자가 제출하고 관리자가 검수한 보드게임만 배포하는 공개 카탈로그입니다. Android 앱의 개인 기록·가격·메모는 이 저장소로 전송되지 않습니다.

## 공개 주소

- 카탈로그: <https://jy2834.github.io/BoardLog-catalog/catalog/catalog.json>
- 스키마 계약: <https://jy2834.github.io/BoardLog-catalog/catalog/schema.json>

`catalog/catalog.json`은 `schemaVersion`, 단조 증가하는 `revision`, UTC `generatedAt`, 정렬된 `games`를 갖습니다. 초기 revision은 승인 게임이 없는 빈 목록입니다.

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

일반 사용자의 제출은 곧바로 공개되지 않습니다. Supabase의 비공개 검수 대기열과 관리자 승인을 거친 뒤 별도 exporter가 이 저장소를 갱신합니다.

## Supabase 검수 서버

무료 Supabase 프로젝트 `BoardLog-catalog`에 검수 대기열이 구성되어 있습니다.

- 리전: Seoul (`ap-northeast-2`)
- 프로젝트 대시보드: <https://supabase.com/dashboard/project/xlinubftvqaxpwrtowvk>
- 제출 원본과 이미지는 비공개이며 승인된 공개 필드만 `approved_catalog_games`에서 조회됩니다.
- 이미지는 비공개 `submission-images` 버킷에 저장하며 JPEG/WebP, 파일당 2 MiB로 제한합니다.
- 사용자는 24시간당 최대 3건을 제출할 수 있습니다.
- 서버 상태는 `NORMAL`, `IMAGE_LIMITED`, `SUBMISSION_CLOSED`, `MAINTENANCE`로 구분합니다.

현재 DB 마이그레이션과 RLS 검증은 원격 프로젝트에 적용되었습니다. 익명 로그인 원격 활성화는 보안 경계 변경 승인을 받은 뒤 `config push`로 반영해야 합니다. Edge Function, Android 제출 UI, 관리자 exporter는 다음 구현 단계입니다.

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

마지막 명령은 Docker 없이 Management API를 통해 원격 DB에서 16개 pgTAP 검증을 실행합니다. 하나라도 실패하면 SQL 예외로 명령 자체가 실패합니다. Docker가 준비된 환경에서는 `npx supabase test db --linked supabase/tests/public_catalog_rls.test.sql --agent no`도 사용할 수 있습니다.

익명 로그인을 포함한 `supabase/config.toml`을 원격에 반영하는 명령은 다음과 같습니다. 이 명령은 인증 보안 경계를 변경하므로 운영자가 변경 내용을 검토하고 명시적으로 승인한 경우에만 실행합니다.

```bash
npx supabase config push --project-ref xlinubftvqaxpwrtowvk --agent no
```

DB 비밀번호, access token, anon/service-role 키, 관리자 UUID·이메일은 Git에 저장하지 않습니다. 운영 관리자는 실제 Auth 계정을 만든 뒤 `admin_users`에 별도로 등록합니다.
