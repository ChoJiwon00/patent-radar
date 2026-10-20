---
name: daily-briefing
description: Patent Radar의 하루 브리핑. 이차전지·ESS·반도체 뉴스를 새로 수집하고, 중요한 이야기를 골라 한국어로 요약하고, 특허 검색에 쓸 기술 키워드를 뽑아 저장한다. "오늘 브리핑", "뉴스 브리핑", "daily briefing"을 요청받거나 매일 아침 예약 작업에서 실행된다.
allowed-tools: PowerShell(.venv\Scripts\python.exe -m patent_radar *), Edit(data/**), Read(data/**), ArtifactData
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
- `topics`: 주제 목록. 브리핑에도 이 순서대로 쓴다 (지금은 이차전지, ESS, 반도체).
- `errors`: 실패한 검색어. 있으면 마지막 보고에 적는다.
- `articles`: 오늘 처음 수집된 기사 목록. 각 항목은 `id`, `topic`, `query`, `title`, `source`, `published_at`

`articles`가 비어 있으면 "오늘 수집된 기사가 없어요"라고 보고하고 끝낸다.

도박·광고처럼 주제와 상관없는 스팸 기사는 무시한다. 보고할 때 그 출처(`source`)를 알려 주고
`config/topics.toml`의 `exclude_sources`에 넣으면 다음부터 걸러진다고 안내한다.

## 2. 주제별로 분석하기

`topics`의 주제마다 따로 한다.

0. **기사를 알맞은 주제로 나눈다.** 기사의 `topic`은 어느 검색어로 찾았는지일 뿐이라, 여러 주제에 걸치는 기사는 내용을 보고 옮긴다.
   - ESS(에너지저장장치·BESS·전력망 배터리)가 중심인 기사는 ESS로. 예: "美 ESS 수요에 배터리 3사 실적 개선"
   - 셀·소재·공정(양극재, 음극재, 전고체, 전극 공정)이 중심이면 이차전지로.
   - 태양광 발전소 준공처럼 저장장치와 관계없는 기사는 뺀다.
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
4. **특허 검색 키워드 5~6개를 뽑는다.** 중요한 것부터 적는다. 특허 검색은 주제마다 앞의 5개로 한국 특허를,
   그중 앞의 2개로 미국·중국 특허도 찾는다 (KIPRIS 무료 한도 때문). 그러니 해외에서도 찾을 만한 핵심 기술을 맨 앞에 둔다.
   - 특허 청구항에 나올 만한 **구체적인 기술 용어**만 고른다. 예: "황화물계 고체전해질", "하이브리드 본딩", "TSV", "건식 전극 공정",
     ESS라면 "배터리 열폭주 방지", "나트륨이온 배터리", "전력변환장치(PCS)"
   - 회사 이름, 주식·시황 용어, 정책 이름, "배터리"·"반도체"·"ESS"처럼 너무 넓은 말은 넣지 않는다.
   - `ko`: 한국어 용어 (2~4단어)
   - `en`: 미국 특허에서 쓰는 표준 영어 기술 용어, **2~4단어로 짧게** (예: "sulfide solid electrolyte").
     해외 특허는 이 표현이 그대로 들어간 특허만 찾으므로 길면 결과가 0건이 된다.
   - `reason`: 어느 이야기에서 나온 키워드인지 한 문장

마지막으로 `overview`에 세 주제를 통틀어 오늘의 흐름을 2~3문장으로 쓴다.

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

## 4. claude.ai 브리핑 페이지에 올리기

사용자가 claude.ai 사이드바에 고정해 둔 비공개 페이지
(https://claude.ai/artifact/1BqRNiZ2mYqMX2t9bkegTd)에도 같은 브리핑을 올린다.

1. 페이지용 파일을 만든다.
   ```
   .venv\Scripts\python.exe -m patent_radar briefing export data/briefing-page.json
   ```
2. `ArtifactData` 도구를 쓴다. 보이지 않으면 ToolSearch로 `select:ArtifactData`를 불러온다.
3. 먼저 `action: "get"`, `url`은 위 주소, `collection: "briefings"`, `doc_id`는 1단계의 `day`로 읽는다.
4. `action: "set"`, 같은 `url`·`collection`·`doc_id`, `file_path`는 `data/briefing-page.json`의 절대 경로로 쓴다.
   3에서 문서가 있었으면 그 `version`을 `if_version`으로 넘기고, 없었으면 `if_version`을 뺀다.

여기서 실패해도 로컬 저장은 이미 끝났으니 다시 시도하지 말고, 실패 내용만 보고에 적는다.

## 5. 보고하기

사용자에게 한국어로 짧게 보고한다.

- 오늘의 흐름(`overview`)
- 주제별 주요 이야기 제목
- 특허 검색 키워드 (한국어 · 영어)
- 실패한 검색어가 있었다면 그 목록
- claude.ai 사이드바의 "Patent Radar 브리핑" 페이지와 로컬 웹페이지(http://127.0.0.1:8000)에서도 볼 수 있다는 안내
