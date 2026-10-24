---
name: weekly-report
description: Patent Radar의 주간 트렌드 리포트. 지난 7일 동안의 뉴스·특허 브리핑에서 통계(기사 수, 자주 나온 기술 키워드, 출원인·기술 분류 순위, 주목 특허)를 뽑고, ★ 관심 특허의 진행 상태를 확인한 뒤, 일주일의 흐름을 한국어로 분석해 claude.ai 페이지·GitHub 보고서·공개 사이트에 올린다. "주간 리포트", "이번 주 정리", "weekly report"를 요청받거나 월요일 0시 예약 작업에서 실행된다.
allowed-tools: PowerShell(.venv\Scripts\python.exe -m patent_radar *), Edit(data/**), Read(data/**), ArtifactData, Bash(git add reports docs), Bash(git diff --cached *), Bash(git commit *), Bash(git pull --rebase origin main), Bash(git rebase --abort), Bash(git push origin main)
---

# 주간 트렌드 리포트

프로젝트(Patent Radar) 루트 폴더에서 아래 순서대로 진행한다. Python 명령은 `.venv\Scripts\python.exe`로 실행한다.
사용자에게 보내는 말은 모두 한국어로 쓴다. claude.ai 브리핑 페이지 주소는 https://claude.ai/artifact/1BqRNiZ2mYqMX2t9bkegTd 이다.

## 1. ★ 관심 특허 상태 확인

```
.venv\Scripts\python.exe -m patent_radar stars check
```

한국 관심 특허의 진행 상태(공개 → 등록 등)를 KIPRIS에서 다시 확인한다 (관심 특허 하나에 한 번 호출).
관심 특허가 없으면 바로 끝난다.

## 2. 통계 뽑기

```
.venv\Scripts\python.exe -m patent_radar weekly stats
```

오늘을 마지막 날로 하는 7일 통계가 `data/weekly-stats.json`에 저장된다. 다른 주간이면 `--end YYYY-MM-DD`를 붙인다.
파일에는 날짜별 뉴스 브리핑의 흐름(`overview`)과 주요 이야기 제목, 자주 나온 키워드, 특허 통계, 주목 특허, 관심 특허가 들어 있다.

## 3. 일주일 흐름 분석하기

통계 파일만 보고 쓴다. 파일에 없는 사실·숫자·전망은 지어내지 않는다.

- `summary`: 일주일을 통틀어 3~5문장. 세 주제(이차전지·ESS·반도체)에서 무엇이 이어졌고 무엇이 새로 나왔는지.
- `trends`: 이번 주 흐름 2~6개. `{"title": 짧은 제목, "body": 2~4문장, "topic": "이차전지"|"ESS"|"반도체"}`.
  여러 날 이어진 이야기, 자주 나온 키워드, 특허가 몰린 기술 분류·출원인을 근거로 쓴다.
- `watch`: 다음 주에 지켜볼 것 1~5개 (한 문장씩). 이번 주 이야기에서 이어질 만한 것.

`data/weekly-commentary.json`에 저장한다 (UTF-8).

```json
{"summary": "...", "trends": [{"title": "...", "body": "...", "topic": "ESS"}], "watch": ["..."]}
```

```
.venv\Scripts\python.exe -m patent_radar weekly save data/weekly-commentary.json --page data/weekly-page.json
```

"형식 오류"가 나오면 메시지대로 고쳐 다시 저장한다. `--end`를 썼으면 여기에도 같은 `--end`를 붙인다.

## 4. claude.ai 페이지에 올리기

1. `ArtifactData`: `action: "get"`, 위 `url`, `collection: "weekly_reports"`, `doc_id`는 마지막 날(YYYY-MM-DD).
2. `action: "set"`, 같은 `url`·`collection`·`doc_id`, `file_path`는 `data/weekly-page.json`의 절대 경로.
   1에서 문서가 있었으면 그 `version`을 `if_version`으로 넘긴다.

실패해도 다시 시도하지 말고 보고에 적는다.

## 5. GitHub에 올리기

1. `.venv\Scripts\python.exe -m patent_radar site export`
2. Bash 도구로 `git add reports docs`, `git diff --cached --name-only`. `reports/`·`docs/` 밖의 파일이 있으면 멈추고 보고한다.
3. `git commit -m "주간 리포트 YYYY-MM-DD"` (안내된 Co-Authored-By 줄이 있으면 붙인다).
4. `git pull --rebase origin main`. 다른 컴퓨터에서 올린 커밋을 먼저 받고 이번 커밋을 그 뒤에 붙인다.
   실패하면 push하지 말고 오류를 보고한다. 충돌로 멈췄으면 `git rebase --abort`로 되돌린 뒤 보고한다.
5. `git push origin main`. 실패하면 다시 시도하지 말고 오류를 보고한다.

## 6. 보고하기

한국어로 짧게: 이번 주 흐름 요약, 흐름 제목들, 다음 주에 지켜볼 것, 관심 특허 상태 변화,
페이지의 "주간 리포트" 탭과 공개 사이트(https://chojiwon00.github.io/patent-radar/#weekly)에서 볼 수 있다는 안내.
