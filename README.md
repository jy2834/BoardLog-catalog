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
