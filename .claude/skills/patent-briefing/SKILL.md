---
name: patent-briefing
description: Patent Radar의 특허 브리핑. 뉴스 브리핑 키워드(또는 사용자가 준 키워드)로 KIPRIS에서 한국·미국·중국 최신 특허를 찾고, 이미 본 특허는 빼고, 🔥 주목 특허는 순위를 매겨 맨 위에 올린 뒤, 청구항을 한국어로 요약해 claude.ai 페이지와 GitHub에 올린다. "특허 브리핑", "특허 찾아줘", "patent briefing"을 요청받거나 매일 0시 예약 작업에서 실행된다.
allowed-tools: PowerShell(.venv\Scripts\python.exe -m patent_radar *), Edit(data/**), Read(data/**), ArtifactData, Bash(git add reports*), Bash(git diff --cached *), Bash(git commit *), Bash(git push origin main)
---

# 특허 브리핑

프로젝트(Patent Radar) 루트 폴더에서 아래 순서대로 진행한다. Python 명령은 `.venv\Scripts\python.exe`로 실행한다.
사용자에게 보내는 말은 모두 한국어로 쓴다.

## 1. 특허 찾기

**사용자가 키워드를 함께 줬으면** (예: `/patent-briefing 전고체 전해질`) 키워드마다 미국 특허에서 쓰는 표준 영어 기술 용어를 정해
`"한국어|영어"` 형식으로 넘긴다. 여러 개면 `--keyword`를 반복한다.

```
.venv\Scripts\python.exe -m patent_radar patents search --keyword "전고체 전해질|solid electrolyte"
```

**키워드가 없으면** 오늘 뉴스 브리핑의 키워드를 쓴다 (오늘 브리핑이 없으면 가장 최근 브리핑).

```
.venv\Scripts\python.exe -m patent_radar patents search
```

"뉴스 브리핑이 없어서" 메시지가 나오면 사용자에게 키워드를 묻거나 /daily-briefing 을 먼저 실행하자고 안내하고 멈춘다.

결과는 `data/patents-today.json`에 저장된다. 각 항목:

- `id`, `country`(KR/US/CN), `number`, `title`(원어 제목), `applicant`, `published_at`, `keyword`
- `rank`: 🔥 주목 특허 순위 (없으면 null), `score`·`reasons`: 주목도 점수와 이유
- `new`: 오늘 처음 넣은 특허인지
- `needs_summary`: 한국어 요약이 필요한지 (전에 요약한 특허는 false)
- `claims`: 청구항 원문 (KR·US), `abstract`: 초록 원문 (KR·CN). 길면 잘려 있다.

## 2. 한국어로 요약하기

`needs_summary`가 true인 특허마다 세 가지를 쓴다. **청구항·초록에 있는 내용만** 쓰고, 없는 효과·수치·용도를 지어내지 않는다.

- `title_ko`: 자연스러운 한국어 제목. 한국 특허는 원래 제목을 그대로 써도 된다.
- `summary_ko`: 2~3문장. 어떤 문제를 풀려는 발명인지, 무엇을 어떻게 했는지. 개발 초보도 읽을 수 있게 쉬운 말로.
- `key_claim_ko`: 청구항 1(가장 넓은 권리)의 핵심을 쉬운 한국어 한 문장으로.
  중국(CN)처럼 `claims`가 없으면 초록에서 핵심 한 문장을 쓴다.

`data/patent-summaries.json`에 저장한다 (UTF-8).

```json
{"items": [{"id": 12, "title_ko": "...", "summary_ko": "...", "key_claim_ko": "..."}]}
```

```
.venv\Scripts\python.exe -m patent_radar patents save-summaries data/patent-summaries.json
```

"형식 오류"가 나오면 메시지대로 고쳐 다시 저장한다. 요약할 특허가 없으면 이 단계를 건너뛴다.

## 3. claude.ai 브리핑 페이지에 올리기

사용자가 claude.ai 사이드바에 고정해 둔 비공개 페이지(https://claude.ai/artifact/1BqRNiZ2mYqMX2t9bkegTd)에 올린다.

1. `.venv\Scripts\python.exe -m patent_radar patents export data/patents-page.json`
2. `ArtifactData` 도구를 쓴다. 보이지 않으면 ToolSearch로 `select:ArtifactData`를 불러온다.
3. `action: "get"`, 위 `url`, `collection: "patent_briefings"`, `doc_id`는 1단계 결과의 `day`로 읽는다.
4. `action: "set"`, 같은 `url`·`collection`·`doc_id`, `file_path`는 `data/patents-page.json`의 절대 경로.
   3에서 문서가 있었으면 그 `version`을 `if_version`으로 넘기고, 없었으면 뺀다.

실패해도 다시 시도하지 말고 보고에 적는다.

## 4. GitHub에 올리기

1. `.venv\Scripts\python.exe -m patent_radar patents report` 로 `reports/날짜.md`와 `reports/README.md`를 만든다.
2. git 명령은 Bash 도구로 실행한다 (`reports/` 폴더만 올린다).
   ```
   git add reports
   git diff --cached --name-only
   ```
   목록에 `reports/` 밖의 파일이 있으면 커밋하지 말고 멈춰서 보고한다. 바뀐 것이 없으면 커밋하지 않는다.
3. `git commit -m "특허 브리핑 YYYY-MM-DD"` 로 커밋한다. 세션에 커밋 끝에 붙이라고 안내된 Co-Authored-By 줄이 있으면 붙인다.
4. `git push origin main`. 실패하면 다시 시도하지 말고 오류를 보고한다.

## 5. 보고하기

한국어로 짧게 보고한다.

- 🔥 주목 특허 순위: 순위, 한국어 제목, 나라, 주목 이유
- 새 특허: 나라별 개수와 한 줄씩 (한국어 제목 · 출원인)
- 검색 경고(결과 없음, 실패)가 있었다면 그 목록
- 이번 달 KIPRIS 호출 수 (1단계 출력 끝에 나온다)
- claude.ai 브리핑 페이지와 GitHub(https://github.com/ChoJiwon00/patent-radar/tree/main/reports)에서 볼 수 있다는 안내
