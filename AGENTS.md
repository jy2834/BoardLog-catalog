# BoardLog Public Catalog 작업 규칙

## 저장소 역할

이 저장소는 관리자 검수를 통과한 BoardLog 공용 카탈로그만 배포한다. Android 개인 기록 저장소 `jy2834/BoardLog`와 웹 관리자 저장소 `jy2834/BoardLog-web`의 소스나 비밀값을 복사하지 않는다.

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

커밋 전 반드시 실행한다.

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_catalog.py \
  --catalog catalog/catalog.json \
  --schema catalog/schema.json \
  --images-dir catalog/images
git diff --check
```

## 현재 상태

- schemaVersion 2, revision 1, 승인 게임 0개
- Python validator와 GitHub Actions/Pages 초기 구조만 구현됨
- Supabase 검수 대기열, Edge Function, exporter, 사용량 감시는 아직 구현되지 않음
