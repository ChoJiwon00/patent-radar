---
name: patent-briefing
description: Patent Radar의 특허 브리핑. 뉴스 브리핑 키워드(또는 사용자가 준 키워드)와 고정 관심 키워드로 KIPRIS에서 한국·미국·중국 최신 특허를 찾고, 주제에 맞지 않는 분류·이미 본 특허는 빼고, 🔥 주목 특허는 순위를 매겨 맨 위에 올린 뒤, 청구항을 한국어로 자세히 해설하고 관련도를 판정해 claude.ai 페이지·GitHub 보고서·공개 사이트에 올린다. "특허 브리핑", "특허 찾아줘", "patent briefing"을 요청받거나 매일 0시 예약 작업에서 실행된다.
allowed-tools: PowerShell(.venv\Scripts\python.exe -m patent_radar *), Edit(data/**), Read(data/**), ArtifactData, Bash(git add reports docs), Bash(git diff --cached *), Bash(git commit *), Bash(git push origin main)
---

# 특허 브리핑

프로젝트(Patent Radar) 루트 폴더에서 아래 순서대로 진행한다. Python 명령은 `.venv\Scripts\python.exe`로 실행한다.
사용자에게 보내는 말은 모두 한국어로 쓴다. claude.ai 브리핑 페이지 주소는 https://claude.ai/artifact/1BqRNiZ2mYqMX2t9bkegTd 이다.
`ArtifactData` 도구가 보이지 않으면 ToolSearch로 `select:ArtifactData`를 불러온다.

## 0. ★ 관심 특허 가져오기

사용자가 페이지에서 별표한 특허를 로컬과 맞춘다.

1. `ArtifactData`: `action: "list"`, 위 `url`, `collection: "stars"`, `out_dir`는 `data/page-db`의 절대 경로.
2. `.venv\Scripts\python.exe -m patent_radar stars sync data/page-db`

실패하면 건너뛰고 보고에 적는다 (별표가 하나도 없으면 0건으로 정상이다).

## 1. 특허 찾기

**사용자가 키워드를 함께 줬으면** (예: `/patent-briefing 전고체 전해질`) 키워드마다 미국 특허에서 쓰는 표준 영어 기술 용어를 정해
`"한국어|영어"` 형식으로 넘긴다. 한국어는 특허 문장처럼 띄어 쓴다. 여러 개면 `--keyword`를 반복한다.

```
.venv\Scripts\python.exe -m patent_radar patents search --keyword "전고체 전해질|solid electrolyte"
```

**키워드가 없으면** 오늘 뉴스 브리핑의 키워드를 쓴다 (오늘 브리핑이 없으면 가장 최근 브리핑).
주제(이차전지·ESS·반도체)마다 앞의 4개로 한국 특허를, 그중 앞의 2개로 미국·중국 특허도 찾고,
`config/topics.toml`의 고정 관심 키워드도 함께 찾는다. 주제에 맞지 않는 특허 분류(IPC)는 자동으로 빠지고,
한국 특허가 0건이면 브리핑의 `alt` 표현으로 다시 찾는다.

```
.venv\Scripts\python.exe -m patent_radar patents search
```

"뉴스 브리핑이 없어서" 메시지가 나오면 사용자에게 키워드를 묻거나 /daily-briefing 을 먼저 실행하자고 안내하고 멈춘다.

결과는 `data/patents-today.json`에 저장된다. 각 항목:

- `id`, `country`(KR/US/CN), `number`, `title`(원어 제목), `applicant`, `published_at`, `keyword`, `ipc`(특허 분류)
- `rank`: 🔥 주목 특허 순위 (없으면 null), `score`·`reasons`: 주목도 점수와 이유
- `new`: 오늘 처음 넣은 특허인지
- `needs_summary`: 한국어 해설이나 관련도 판정이 필요한지
- `claims`: 청구항 원문 (KR·US, 최대 8,000자), `abstract`: 초록 원문 (KR·CN, 최대 3,000자). 더 길면 잘려 있다.

검색 없이 그날 목록만 다시 만들려면 `.venv\Scripts\python.exe -m patent_radar patents list`를 쓴다 (KIPRIS 호출 없음).

## 2. 한국어로 해설하고 관련도 판정하기

`needs_summary`가 true인 특허마다 쓴다. **청구항·초록에 있는 내용만** 쓰고, 없는 효과·수치·용도·회사 사정을 지어내지 않는다.
사용자는 특허 원문을 직접 열어 보기 어려우니, 원문을 읽지 않아도 내용을 알 수 있을 만큼 자세히 쓴다. 길어도 괜찮다.

- `relevant`: 이 특허가 검색 키워드(`keyword`)와 그 뉴스 주제의 **기술 자체**를 다루면 `true`,
  키워드 단어만 겹치고 다른 기술(예: 배터리 교환 중 화면 광고, 작업 기계 운전 제어)이면 `false`.
- `relevance_note`: 판정 이유 한 줄. 예: "황화물 고체 전해질 조성 자체를 청구", "배터리 교환이 아니라 화면 표시 기술"
- `title_ko`: 자연스러운 한국어 제목. 한국 특허는 원래 제목을 그대로 써도 된다.
- `summary_ko`: 2~3문장 요약. 무엇에 관한 발명인지, 무엇을 어떻게 했는지. 개발 초보도 읽을 수 있게 쉬운 말로.
- `key_claim_ko`: 청구항 1(가장 넓은 권리)의 핵심을 쉬운 한국어 한 문장으로.
  중국(CN)처럼 `claims`가 없으면 초록에서 핵심 한 문장을 쓴다.
- `detail_ko`: 자세한 설명. `{"heading": 제목, "body": 본문}` 꼭지 3~8개짜리 배열. 아래 꼭지를 기본으로 하고, 원문에 없는 꼭지는 뺀다.
  - **어떤 발명인가요**: 무엇을 만들거나 하는 발명인지, 원문에 적힌 목적·해결하려는 문제. 3~5문장.
  - **구성·방법 자세히**: 청구항 1을 구성 요소나 단계별로 하나씩 풀어 쓴다. 각 줄을 `- `로 시작하면 목록으로 보인다.
  - **청구항으로 보호하는 범위**: 독립항(다른 항을 인용하지 않는 항)이 몇 개이고 각각 무엇(물건·방법·장치)을 보호하는지,
    종속항이 더하는 주요 조건(재료 종류, 온도·압력·비율 같은 수치 범위)을 목록으로.
  - **눈여겨볼 점**: 원문에 나온 수치 조건이나 특징, 오늘 뉴스 키워드(`keyword`)와 어떻게 이어지는지.
  - **용어 풀이**: 어려운 용어 2~5개를 `- 용어: 쉬운 설명` 형식으로.
  - 청구항이 잘려 있으면(`…(생략)`) 읽은 범위까지만 쓰고, 중국 특허는 "청구항 원문이 없어 초록만 보고 썼어요"라고 밝힌다.
  - 관련도가 낮은 특허는 짧게 써도 된다 (꼭지 3개).

이미 해설이 있고 관련도만 없는 특허는 `id`, `relevant`, `relevance_note`만 보내도 된다.
`data/patent-summaries.json`에 저장한다 (UTF-8).

```json
{"items": [{"id": 12, "relevant": true, "relevance_note": "...", "title_ko": "...", "summary_ko": "...", "key_claim_ko": "...",
            "detail_ko": [{"heading": "어떤 발명인가요", "body": "..."},
                          {"heading": "구성·방법 자세히", "body": "- 첫째 구성...\n- 둘째 구성..."}]}]}
```

```
.venv\Scripts\python.exe -m patent_radar patents save-summaries data/patent-summaries.json
```

"형식 오류"가 나오면 메시지대로 고쳐 다시 저장한다. 저장이 끝나면 순위를 다시 매긴다 (KIPRIS 호출 없음).
'관련 낮음'으로 판정한 특허는 이때 🔥 주목 순위에서 빠진다.

```
.venv\Scripts\python.exe -m patent_radar patents rescore
```

## 3. claude.ai 브리핑 페이지에 올리기

1. `.venv\Scripts\python.exe -m patent_radar patents export data/patents-page.json`
2. `ArtifactData`: `action: "get"`, 위 `url`, `collection: "patent_briefings"`, `doc_id`는 1단계 결과의 `day`.
3. `action: "set"`, 같은 `url`·`collection`·`doc_id`, `file_path`는 `data/patents-page.json`의 절대 경로.
   2에서 문서가 있었으면 그 `version`을 `if_version`으로 넘기고, 없었으면 뺀다.

실패해도 다시 시도하지 말고 보고에 적는다.

## 4. GitHub에 올리기 (보고서와 공개 사이트)

1. `.venv\Scripts\python.exe -m patent_radar patents report` → `reports/날짜.md`
2. `.venv\Scripts\python.exe -m patent_radar site export` → `docs/` (GitHub Pages 공개 사이트)
3. git 명령은 Bash 도구로 실행한다.
   ```
   git add reports docs
   git diff --cached --name-only
   ```
   목록에 `reports/`·`docs/` 밖의 파일이 있으면 커밋하지 말고 멈춰서 보고한다. 바뀐 것이 없으면 커밋하지 않는다.
4. `git commit -m "특허 브리핑 YYYY-MM-DD"` 로 커밋한다. 세션에 커밋 끝에 붙이라고 안내된 Co-Authored-By 줄이 있으면 붙인다.
5. `git push origin main`. 실패하면 다시 시도하지 말고 오류를 보고한다.

## 5. 보고하기

한국어로 짧게 보고한다.

- 🔥 주목 특허 순위: 순위, 한국어 제목, 나라, 주목 이유
- 새 특허: 나라별 개수와 한 줄씩 (한국어 제목 · 출원인), 관련도 낮아 접은 특허 수
- 검색 경고(결과 없음, 다시 찾음, 실패)가 있었다면 그 목록
- 이번 달 KIPRIS 호출 수 (1단계 출력 끝에 나온다)
- claude.ai 브리핑 페이지, 공개 사이트(https://chojiwon00.github.io/patent-radar/),
  GitHub(https://github.com/ChoJiwon00/patent-radar/tree/main/reports)에서 볼 수 있다는 안내
