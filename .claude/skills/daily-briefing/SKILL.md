---
name: daily-briefing
description: Patent Radar의 하루 브리핑. 이차전지·반도체 뉴스를 새로 수집하고, 중요한 이야기를 골라 한국어로 요약하고, 특허 검색에 쓸 기술 키워드를 뽑아 저장한다. "오늘 브리핑", "뉴스 브리핑", "daily briefing"을 요청받거나 매일 아침 예약 작업에서 실행된다.
allowed-tools: PowerShell(.venv\Scripts\python.exe -m patent_radar *), Edit(data/**), Read(data/**)
---

# 하루 브리핑

이 프로젝트(Patent Radar)의 루트 폴더에서 아래 순서대로 진행한다. 명령은 항상 프로젝트 루트에서
`.venv\Scripts\python.exe`로 실행한다. `.venv`가 없으면 멈추고 README의 설치 방법을 안내한다.

## 1. 뉴스 수집

```
.venv\Scripts\python.exe -m patent_radar news --json
```

출력은 JSON 한 덩어리다.

- `day`: 오늘 날짜(한국 시간)
- `errors`: 실패한 검색어. 있으면 마지막 보고에 적는다.
- `articles`: 오늘 처음 수집된 기사 목록. 각 항목은 `id`, `topic`(이차전지/반도체), `query`, `title`, `source`, `published_at`

`articles`가 비어 있으면 "오늘 수집된 기사가 없어요"라고 보고하고 끝낸다.

도박·광고처럼 주제와 상관없는 스팸 기사는 무시한다. 보고할 때 그 출처(`source`)를 알려 주고
`config/topics.toml`의 `exclude_sources`에 넣으면 다음부터 걸러진다고 안내한다.

## 2. 주제별로 분석하기

주제(`topic`)마다 따로 한다.

1. **같은 사건끼리 묶는다.** 언론사만 다르고 같은 사건을 다룬 기사는 하나의 이야기다.
2. **중요한 이야기 3~5개를 고른다.** 우선순위:
   - 기술·산업에 실제 변화가 있는 소식: 신기술 개발, 양산·수율, 대규모 투자·증설, 공급 계약, 신제품, 규제·정책 변화
   - 여러 언론사가 함께 다룬 이야기
   - 최신 기사
   - 주가·시황만 다룬 기사는 기술 이벤트가 원인이 아니면 뺀다.
3. **이야기마다 작성한다.**
   - `headline`: 직접 쓴 짧은 한국어 제목 (40자 안쪽)
   - `summary`: 1~2문장 요약. **기사 제목에 드러난 사실만** 쓴다. 제목에 없는 숫자·회사·일정은 지어내지 않는다.
   - `article_ids`: 그 이야기에 속한 기사 `id`. 가장 대표적인 것부터 최대 5개
4. **특허 검색 키워드 3~6개를 뽑는다.**
   - 특허 청구항에 나올 만한 **구체적인 기술 용어**만 고른다. 예: "황화물계 고체전해질", "하이브리드 본딩", "TSV", "건식 전극 공정"
   - 회사 이름, 주식·시황 용어, 정책 이름, "배터리"·"반도체"처럼 너무 넓은 말은 넣지 않는다.
   - `ko`: 한국어 용어
   - `en`: 미국 특허에서 쓰는 표준 영어 기술 용어 (예: "sulfide-based solid electrolyte")
   - `reason`: 어느 이야기에서 나온 키워드인지 한 문장

마지막으로 `overview`에 두 주제를 통틀어 오늘의 흐름을 2~3문장으로 쓴다.

## 3. 저장하기

`data/briefing-draft.json` 파일에 아래 형식으로 쓴다 (UTF-8). `day`는 1단계 출력의 `day`를 그대로 쓴다.

```json
{
  "day": "2026-10-19",
  "overview": "...",
  "topics": [
    {
      "name": "이차전지",
      "stories": [
        {"headline": "...", "summary": "...", "article_ids": [12, 40]}
      ],
      "keywords": [
        {"ko": "황화물계 고체전해질", "en": "sulfide-based solid electrolyte", "reason": "..."}
      ]
    }
  ]
}
```

그다음 저장한다.

```
.venv\Scripts\python.exe -m patent_radar briefing save data/briefing-draft.json
```

"형식 오류"가 나오면 메시지대로 JSON을 고치고 다시 저장한다. 같은 날 다시 실행하면 새 브리핑으로 바뀐다.

## 4. 보고하기

사용자에게 한국어로 짧게 보고한다.

- 오늘의 흐름(`overview`)
- 주제별 주요 이야기 제목
- 특허 검색 키워드 (한국어 · 영어)
- 실패한 검색어가 있었다면 그 목록
- 웹페이지(http://127.0.0.1:8000)의 "오늘의 뉴스" 탭 맨 위에서도 볼 수 있다는 안내
